"""Deterministic introduction safety checks; mock verdicts are not product acceptance."""

import json
from copy import deepcopy

import httpx2 as httpx
import pytest

from papertrail.introduction import (
    FIELDS,
    INTRODUCTION_CHUNK_VERSION,
    MAX_SOURCE_CHARS,
    build_introduction_chunks,
    introduce_paper,
)
from papertrail.model import ModelClient, ModelConfig, ModelError
from papertrail.qa import validate_claims
from papertrail.retrieval import build_chunks

PAPER = {"id": "introduction-paper", "sha256": "fixed-source-hash"}
QUOTE = "A frozen model uses manually written trajectories and tool observations."
PAGES = [{"page_index": 0, "text": ""}, {"page_index": 1, "text": QUOTE}]


def introduction_output(chunk_id):
    citation = {"chunk_id": chunk_id}
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
        claim["citations"] = [{"chunk_id": "another-paper-chunk"}]
        return result

    result = introduce_paper(PAPER, PAGES, client=introduction_client(generate=corrupt))
    assert result["status"] == "failed" and result["error_code"] == "invalid_citation"
    assert result["introduction"] is None and result["claims"] == []
    assert result["trace"]["call_count"] == 1


@pytest.mark.parametrize("mismatch", ["paper_id", "paper_sha256"])
def test_actual_citation_with_wrong_paper_ownership_is_withheld(monkeypatch, mismatch):
    chunks = build_introduction_chunks(PAPER["id"], PAPER["sha256"], PAGES)
    chunks[0][mismatch] = "other-source"
    monkeypatch.setattr("papertrail.introduction.build_introduction_chunks", lambda *args: chunks)
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


def test_context_limit_is_checked_before_model_call(monkeypatch):
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


@pytest.mark.parametrize("length", [1, 7, 8, 999, 1000, 1001, 1007, 1999, 2000, 3007])
def test_intro_spans_cover_entire_pages_once_and_fit_existing_quote_limit(length):
    pages = [
        {"page_index": 4, "text": "x" * length},
        {"page_index": 0, "text": ""},
        {"page_index": 2, "text": ("完整文字与空格。\nA complete sentence. " * 170) + "\t "},
    ]
    chunks = build_introduction_chunks(PAPER["id"], PAPER["sha256"], pages)
    assert chunks == build_introduction_chunks(PAPER["id"], PAPER["sha256"], pages[::-1])
    assert [chunk["page_index"] for chunk in chunks] == sorted(c["page_index"] for c in chunks)
    for page in pages:
        spans = [chunk for chunk in chunks if chunk["page_index"] == page["page_index"]]
        assert "".join(span["text"] for span in spans) == page["text"]
        position = 0
        for span in spans:
            assert span["start_char"] == position
            position = span["end_char"]
            assert span["text"] == page["text"][span["start_char"] : position]
            assert 1 <= len(span["text"]) <= 1000
            if len(page["text"]) >= 8:
                assert len(span["text"]) >= 8
            assert span["chunk_version"] == INTRODUCTION_CHUNK_VERSION


def test_intro_span_ids_bind_paper_hash_page_offsets_and_exact_text():
    pages = [{"page_index": 0, "text": "x" * 2000}]
    original = build_introduction_chunks("paper-a", "hash-a", pages)
    assert original[0]["text"] == original[1]["text"]
    assert original[0]["chunk_id"] != original[1]["chunk_id"]
    variants = [
        build_introduction_chunks("paper-b", "hash-a", pages),
        build_introduction_chunks("paper-a", "hash-b", pages),
        build_introduction_chunks("paper-a", "hash-a", [{**pages[0], "page_index": 1}]),
        build_introduction_chunks("paper-a", "hash-a", [{"page_index": 0, "text": "y" * 2000}]),
    ]
    assert all(variant[0]["chunk_id"] != original[0]["chunk_id"] for variant in variants)


@pytest.mark.parametrize(
    "extra", [{"quote": "A forged quote."}, {"page_index": 9}, {"paper_id": "other"}]
)
def test_model_cannot_override_program_owned_citation_content(extra):
    def corrupt(result):
        result["introduction"]["mechanism"]["citations"][0].update(extra)
        return result

    result = introduce_paper(PAPER, PAGES, client=introduction_client(generate=corrupt))
    assert result["error_code"] == "invalid_citation"
    assert result["claims"] == [] and result["introduction"] is None
    assert result["trace"]["call_count"] == 1


def test_every_published_quote_is_exact_program_supplied_span_including_symbols_and_whitespace():
    text = '  The model uses "thoughts".\n\nContext c_t+1 includes \\ observations.  '
    pages = [{"page_index": 3, "text": text}]
    inputs = []
    result = introduce_paper(PAPER, pages, client=introduction_client(observed=inputs))
    assert result["status"] == "answered"
    for claim in result["claims"]:
        assert claim["citations"][0]["quote"] == text
    for claim in inputs[1]["claims"]:
        assert claim["citations"][0]["quote"] == text
    for candidate in result["trace"]["generated_claims"]:
        assert set(candidate["citations"][0]) == {"chunk_id"}
    assert result["trace"]["source"]["selected"] == build_introduction_chunks(
        PAPER["id"], PAPER["sha256"], pages
    )


def test_repeated_spaces_alone_are_not_treated_as_paper_evidence():
    result = introduce_paper(
        PAPER, [{"page_index": 0, "text": " \n\t" * 500}], client=introduction_client()
    )
    assert result["status"] == "insufficient_evidence" and result["trace"]["call_count"] == 0
