"""The demo audit must rebuild evidence rather than trust a generated trace."""

import importlib
from copy import deepcopy
from pathlib import Path

import pytest

from papertrail.introduction import build_introduction_chunks


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("evaluate_introductions")


def introduction_fixture():
    paper = {"id": "current-paper", "sha256": "a" * 64}
    text = "Stored source evidence for this introduction."
    source = {"pages": [text]}
    chunk = build_introduction_chunks(
        paper["id"], paper["sha256"], [{"page_index": 0, "text": text}]
    )[0]
    citation = {
        "paper_id": paper["id"],
        "page_index": 0,
        "chunk_id": chunk["chunk_id"],
        "quote": text,
    }
    claim = {"text": "测试说明", "citations": [citation]}
    intro = {
        key: deepcopy(claim)
        for key in ("summary", "problem", "contribution", "mechanism", "evidence_and_limits")
    }
    intro["terms"] = [{"term": "术语", "explanation": "解释", "citations": [deepcopy(citation)]}]
    return {"status": "answered", "introduction": intro}, paper, source


def test_audit_detects_forged_term_quote_and_wrong_paper(runner):
    result, paper, source = introduction_fixture()
    assert runner.check_citations(result, paper, source)["status"] == "passed"
    result["introduction"]["terms"][0]["citations"][0]["quote"] = "Invented evidence"
    result["introduction"]["summary"]["citations"][0]["paper_id"] = "other-paper"
    checked = runner.check_citations(result, paper, source)
    assert checked["status"] == "failed"
    assert len(checked["errors"]) == 2
    assert checked["citations_checked"] == 6


def test_audit_detects_source_change_without_trusting_trace(runner):
    result, paper, source = introduction_fixture()
    result["trace"] = {"citation_validation": "passed"}
    source["pages"][0] = "Different source evidence."
    assert runner.check_citations(result, paper, source)["status"] == "failed"


def test_audit_requires_complete_program_supplied_span(runner):
    result, paper, source = introduction_fixture()
    citation = result["introduction"]["summary"]["citations"][0]
    citation["quote"] = citation["quote"].replace(" ", "  ", 1)
    assert runner.check_citations(result, paper, source)["status"] == "failed"


def test_audit_does_not_grade_failed_generation_as_valid_citations(runner):
    assert runner.check_citations({"status": "failed"}, {}, {})["status"] == "not_applicable"
