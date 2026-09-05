import json
from copy import deepcopy

import httpx2 as httpx
import pytest

from papertrail.model import ModelClient, ModelConfig, ModelError
from papertrail.qa import answer_question, validate_claims
from papertrail.retrieval import build_chunks

PAPER = {"id": "paper-one", "sha256": "immutable-source-hash"}
TEXT = "Experiments use HotpotQA and FEVER benchmarks. Results are limited to these settings."
PAGES = [{"page_index": 0, "text": ""}, {"page_index": 1, "text": TEXT}]


def pipeline_client(*, generate=None, verify=None, search=None):
    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload["messages"][1]["content"])
        if "claims" in data:
            result = (
                verify
                if verify is not None
                else {
                    "verdicts": [
                        {"claim_index": 0, "supported": True, "reason": "片段明确列出了数据集。"}
                    ]
                }
            )
        elif "passages" in data:
            result = {
                "status": "answered",
                "message": "",
                "claims": [
                    {
                        "text": "实验使用 HotpotQA 和 FEVER 数据集。",
                        "citations": [
                            {
                                "chunk_id": data["passages"][0]["chunk_id"],
                                "quote": "Experiments use HotpotQA and FEVER benchmarks.",
                            }
                        ],
                    }
                ],
            }
            if generate:
                result = generate(result)
        else:
            result = {"search_queries": search or ["experiments datasets benchmarks"]}
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
            },
        )

    return ModelClient(
        ModelConfig(
            base_url="https://test.invalid/v1", api_key="test-only", model_name="unit-test"
        ),
        transport=httpx.MockTransport(handler),
    )


def test_complete_fixed_pipeline_resolves_page_and_records_separate_checks():
    stages = []
    result = answer_question(
        PAPER, PAGES, "实验用什么数据集？", client=pipeline_client(), progress=stages.append
    )
    assert result["status"] == "answered"
    assert result["claims"][0]["citations"][0]["page_index"] == 1
    assert result["claims"][0]["citations"][0]["paper_id"] == PAPER["id"]
    assert result["support_status"] == "ai_checked"
    assert result["human_review"] is None
    trace = result["trace"]
    assert trace["citation_validation"] == "passed"
    assert trace["support_verdicts"][0]["supported"] is True
    assert trace["call_count"] == 3
    assert [c["stage"] for c in trace["calls"]] == ["query", "generate", "verify"]
    assert trace["usage"] == {"prompt_tokens": 300, "completion_tokens": 120, "complete": True}
    assert trace["retrieval"]["baseline_selected"] == []
    assert trace["retrieval"]["selected"][0]["page_index"] == 1
    assert stages == ["translating", "retrieving", "generating", "validating", "verifying"]


@pytest.mark.parametrize("change", ["unknown_chunk", "forged_quote", "missing_citation"])
def test_bad_citations_fail_closed_without_support_call(change):
    def corrupt(result):
        if change == "unknown_chunk":
            result["claims"][0]["citations"][0]["chunk_id"] = "other-paper-chunk"
        elif change == "forged_quote":
            result["claims"][0]["citations"][0]["quote"] = "Experiments achieve 100% success."
        else:
            result["claims"][0]["citations"] = []
        return result

    result = answer_question(PAPER, PAGES, "实验？", client=pipeline_client(generate=corrupt))
    assert result["status"] == "failed" and result["error_code"] == "invalid_citation"
    assert result["claims"] == []
    assert result["trace"]["citation_validation"] == "failed"
    assert result["trace"]["call_count"] == 2
    assert result["trace"]["generated_claims"]
    assert "candidate_claims" not in result["trace"]


def test_failed_citation_preserves_only_unvalidated_claims_for_diagnosis():
    captured = []

    def corrupt(result):
        result["claims"][0]["citations"][0]["quote"] = "A fabricated quotation for diagnosis."
        captured.append(deepcopy(result["claims"]))
        result["message"] = "DO-NOT-STORE-THIS-MESSAGE"
        result["debug"] = {"header": "DO-NOT-STORE-THIS-HEADER"}
        return result

    result = answer_question(PAPER, PAGES, "实验？", client=pipeline_client(generate=corrupt))
    assert result["status"] == "failed"
    assert result["error_code"] == "invalid_citation"
    assert result["claims"] == []
    assert result["trace"]["generated_claims"] == captured[0]
    assert result["trace"]["citation_validation"] == "failed"
    assert result["trace"]["call_count"] == 2
    assert "DO-NOT-STORE" not in json.dumps(result, ensure_ascii=False)


def test_real_quote_from_wrong_paper_is_rejected_and_whitespace_is_only_normalization():
    chunks = build_chunks(PAPER["id"], PAPER["sha256"], PAGES)
    claim = [
        {
            "text": "实验设置",
            "citations": [
                {
                    "chunk_id": chunks[0]["chunk_id"],
                    "quote": "Experiments\nuse HotpotQA and FEVER benchmarks.",
                }
            ],
        }
    ]
    assert validate_claims(claim, chunks, PAPER)[0]["citations"][0]["quote"].startswith(
        "Experiments use"
    )
    wrong_paper = deepcopy(chunks)
    wrong_paper[0]["paper_id"] = "another-paper"
    with pytest.raises(ModelError) as error:
        validate_claims(claim, wrong_paper, PAPER)
    assert error.value.code == "invalid_citation"
    wrong_hash = deepcopy(chunks)
    wrong_hash[0]["paper_sha256"] = "different-source"
    with pytest.raises(ModelError):
        validate_claims(claim, wrong_hash, PAPER)
    claim[0]["citations"][0]["quote"] = "Experiments use HotpotQA ... benchmarks."
    with pytest.raises(ModelError):
        validate_claims(claim, chunks, PAPER)


def test_semantically_unsupported_claim_is_withheld_despite_valid_quote():
    client = pipeline_client(
        verify={
            "verdicts": [{"claim_index": 0, "supported": False, "reason": "该引文不支持这个结论。"}]
        }
    )
    result = answer_question(PAPER, PAGES, "所有任务都有提升吗？", client=client)
    assert result["status"] == "insufficient_evidence"
    assert result["claims"] == []
    assert "当前已检索证据" in result["message"]
    assert result["support_status"] == "ai_checked"
    assert result["trace"]["citation_validation"] == "passed"
    assert result["trace"]["candidate_claims"]


@pytest.mark.parametrize(
    "verify",
    [
        {"verdicts": []},
        {"verdicts": [{"claim_index": 0, "supported": "true", "reason": "wrong boolean"}]},
    ],
)
def test_incomplete_support_check_fails_closed(verify):
    result = answer_question(PAPER, PAGES, "实验？", client=pipeline_client(verify=verify))
    assert result["status"] == "failed" and result["error_code"] == "verification_failed"
    assert result["claims"] == []


def test_no_retrieval_or_generation_insufficient_does_not_invent_answer():
    no_matches = answer_question(
        PAPER, PAGES, "不存在的词", client=pipeline_client(search=["xyzzy"])
    )
    assert no_matches["status"] == "insufficient_evidence"
    assert no_matches["trace"]["call_count"] == 1
    generated = answer_question(
        PAPER,
        PAGES,
        "实验？",
        client=pipeline_client(
            generate=lambda _: {
                "status": "insufficient_evidence",
                "claims": [],
                "message": "paper never discusses this",
            }
        ),
    )
    assert generated["status"] == "insufficient_evidence"
    assert generated["claims"] == []
    assert "paper never" not in generated["message"]
    assert generated["trace"]["call_count"] == 2


def test_model_configuration_missing_returns_saved_failure_shape():
    result = answer_question(PAPER, PAGES, "实验？", client=ModelClient(ModelConfig()))
    assert result["status"] == "failed" and result["error_code"] == "model_not_configured"
    assert result["trace"]["calls"] == []
