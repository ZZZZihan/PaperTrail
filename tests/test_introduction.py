"""Deterministic introduction safety checks; mock verdicts are not product acceptance."""

import json
from copy import deepcopy

import httpx2 as httpx
import pytest

from papertrail.introduction import FIELDS, MAX_SOURCE_CHARS, introduce_paper
from papertrail.model import ModelClient, ModelConfig, ModelError
from papertrail.qa import validate_claims
from papertrail.retrieval import build_chunks

PAPER = {"id": "introduction-paper", "sha256": "fixed-source-hash"}
QUOTE = "A frozen model uses manually written trajectories and tool observations."
PAGES = [{"page_index": 0, "text": ""}, {"page_index": 1, "text": QUOTE}]


def introduction_output(chunk_id, *, quote=QUOTE):
    citation = {"chunk_id": chunk_id, "quote": quote}
    return {
        "status": "answered",
        "introduction": {
            **{
                field: {"text": "冻结模型参考人工编写的轨迹与工具观察。", "citations": [citation]}
                for field in FIELDS
            },
            "terms": [
                {
                    "term": term,
                    "explanation": "仅用于确定性测试的术语解释。",
                    "citations": [citation],
                }
                for term in ("冻结模型", "轨迹")
            ],
        },
    }


def introduction_client(*, generate=None, verify=None, observed=None, **kwargs):
    def handler(request):
        data = json.loads(json.loads(request.content)["messages"][1]["content"])
        if observed is not None:
            observed.append(data)
        if "claims" in data:
            result = (
                verify
                if verify is not None
                else {
                    "verdicts": [
                        {"claim_index": i, "supported": True, "reason": "测试固定判定。"}
                        for i in range(len(data["claims"]))
                    ]
                }
            )
        else:
            result = introduction_output(data["passages"][0]["chunk_id"])
            if generate:
                result = generate(result)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    return ModelClient(
        ModelConfig(base_url="https://test.invalid/v1", api_key="test-only", model_name="test"),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_complete_intro_uses_all_chunks_and_checks_every_body_field_and_term():
    stages, inputs = [], []
    pages = [*PAGES, {"page_index": 2, "text": "Additional complete page without truncation."}]
    result = introduce_paper(
        PAPER, pages, client=introduction_client(observed=inputs), progress=stages.append
    )
    assert result["status"] == "answered"
    intro = result["introduction"]
    assert set(intro) == {*FIELDS, "terms"}
    assert len(result["claims"]) == len(inputs[1]["claims"]) == 7
    assert intro["problem"]["citations"][0]["page_index"] == 1
    assert intro["terms"][0]["citations"][0]["paper_id"] == PAPER["id"]
    assert result["claims"][5]["text"].startswith(intro["terms"][0]["term"] + "：")
    assert stages == ["retrieving", "generating", "validating", "verifying"]
    assert result["trace"]["source"]["truncated"] is False
    assert [c["text"] for c in inputs[0]["passages"]] == [pages[1]["text"], pages[2]["text"]]
    assert result["trace"]["citation_validation"] == "passed"
    assert result["trace"]["call_count"] == 2
    assert [c["stage"] for c in result["trace"]["calls"]] == [
        "introduction_generate",
        "introduction_verify",
    ]
    assert result["human_review"] is None and result["support_status"] == "ai_checked"


def test_ten_intro_claims_do_not_widen_ordinary_qa_limit():
    def five_terms(result):
        term = result["introduction"]["terms"][0]
        result["introduction"]["terms"] = [{**term, "term": f"术语{i}"} for i in range(5)]
        return result

    result = introduce_paper(PAPER, PAGES, client=introduction_client(generate=five_terms))
    assert result["status"] == "answered" and len(result["claims"]) == 10
    chunks = build_chunks(PAPER["id"], PAPER["sha256"], PAGES)
    with pytest.raises(ModelError, match="有效的事实列表"):
        validate_claims(result["claims"][:9], chunks, PAPER)


@pytest.mark.parametrize("location", ["problem", "term"])
def test_unknown_citation_blocks_entire_intro_before_semantic_call(location):
    def corrupt(result):
        intro = result["introduction"]
        claim = intro["terms"][0] if location == "term" else intro[location]
        claim["citations"] = [{"chunk_id": "another-paper-chunk", "quote": QUOTE}]
        return result

    result = introduce_paper(PAPER, PAGES, client=introduction_client(generate=corrupt))
    assert result["status"] == "failed" and result["error_code"] == "invalid_citation"
    assert result["introduction"] is None and result["claims"] == []
    assert result["trace"]["call_count"] == 1


@pytest.mark.parametrize("mismatch", ["paper_id", "paper_sha256"])
def test_actual_citation_with_wrong_paper_ownership_is_withheld(monkeypatch, mismatch):
    chunks = build_chunks(PAPER["id"], PAPER["sha256"], PAGES)
    chunks[0][mismatch] = "other-source"
    monkeypatch.setattr("papertrail.introduction.build_chunks", lambda *args: chunks)
    result = introduce_paper(PAPER, PAGES, client=introduction_client())
    assert result["error_code"] == "invalid_citation"
    assert result["introduction"] is None and result["trace"]["call_count"] == 1


@pytest.mark.parametrize("unsupported_index", [0, 3, 6])
def test_semantic_rejection_of_any_field_or_term_withholds_everything(unsupported_index):
    result = introduce_paper(
        PAPER,
        PAGES,
        client=introduction_client(
            verify={
                "verdicts": [
                    {"claim_index": i, "supported": i != unsupported_index, "reason": "测试判定。"}
                    for i in range(7)
                ]
            }
        ),
    )
    assert result["status"] == "insufficient_evidence"
    assert result["introduction"] is None and result["claims"] == []
    assert result["trace"]["citation_validation"] == "passed"
    assert result["trace"]["candidate_claims"]
    assert result["trace"]["call_count"] == 2


@pytest.mark.parametrize(
    "mutation", ["missing_field", "missing_terms", "duplicate_term", "bad_status"]
)
def test_incomplete_intro_is_not_published(mutation):
    def corrupt(result):
        if mutation == "missing_field":
            del result["introduction"]["mechanism"]
        elif mutation == "missing_terms":
            result["introduction"]["terms"] = []
        elif mutation == "duplicate_term":
            term = result["introduction"]["terms"][0]
            result["introduction"]["terms"] = [term, deepcopy(term)]
        else:
            result["status"] = "done"
        return result

    result = introduce_paper(PAPER, PAGES, client=introduction_client(generate=corrupt))
    assert result["error_code"] == "invalid_output"
    assert result["introduction"] is None and result["trace"]["call_count"] == 1


def test_support_check_needs_exactly_one_boolean_verdict_for_each_intro_claim():
    result = introduce_paper(PAPER, PAGES, client=introduction_client(verify={"verdicts": []}))
    assert result["status"] == "failed" and result["error_code"] == "verification_failed"
    assert result["introduction"] is None and result["claims"] == []
    assert result["trace"]["call_count"] == 2


def test_too_long_source_fails_without_model_call_or_silent_truncation():
    result = introduce_paper(
        PAPER,
        [{"page_index": 0, "text": "x" * (MAX_SOURCE_CHARS + 1)}],
        client=introduction_client(),
    )
    assert result["error_code"] == "introduction_too_long"
    assert result["trace"]["call_count"] == 0
    assert result["trace"]["source"]["source_chars"] == MAX_SOURCE_CHARS + 1
    assert result["trace"]["source"]["truncated"] is False


def test_context_overlap_limit_is_checked_before_model_call(monkeypatch):
    monkeypatch.setattr("papertrail.introduction.MAX_CONTEXT_CHARS", len(QUOTE) - 1)
    result = introduce_paper(PAPER, PAGES, client=introduction_client())
    assert result["error_code"] == "introduction_too_long"
    assert result["trace"]["call_count"] == 0 and result["introduction"] is None


def test_empty_paper_and_model_insufficient_do_not_publish_model_explanation():
    empty = introduce_paper(PAPER, [], client=introduction_client())
    assert empty["status"] == "insufficient_evidence" and empty["trace"]["call_count"] == 0
    result = introduce_paper(
        PAPER,
        PAGES,
        client=introduction_client(
            generate=lambda _: {
                "status": "insufficient_evidence",
                "introduction": None,
                "message": "Unsupported claim must not be published.",
            }
        ),
    )
    assert result["status"] == "insufficient_evidence" and result["trace"]["call_count"] == 1
    assert "Unsupported" not in result["message"] and result["introduction"] is None
