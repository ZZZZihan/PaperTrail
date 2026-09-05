"""Diagnostic infrastructure tests; synthetic fixtures never stand in for model evaluation."""

import argparse
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import httpx2 as httpx
import pytest
from pdf_fixtures import pdf_bytes

SPEC = importlib.util.spec_from_file_location(
    "development_diagnostic",
    Path(__file__).resolve().parents[1] / "scripts/evaluate_development.py",
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_artifact_is_write_once(tmp_path):
    path = tmp_path / "record.json"
    runner.write_once(path, {"status": "failed"})
    with pytest.raises(FileExistsError):
        runner.write_once(path, {"status": "passed"})
    assert json.loads(path.read_text()) == {"status": "failed"}
    assert path.stat().st_mode & 0o222 == 0


def frozen_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    papers = []
    for paper_id in ("first", "second", "third"):
        content = pdf_bytes(("Synthetic evidence",))
        path = tmp_path / f"{paper_id}.pdf"
        path.write_bytes(content)
        papers.append(
            {
                "id": paper_id,
                "local_path": path.name,
                "sha256": runner.digest(content),
                "page_count": 1,
                "page_text_sha256": [runner.digest(b"Synthetic evidence")],
            }
        )
    questions = [
        {
            "id": f"Q{index}",
            "paper_id": "first",
            "question": "Synthetic question",
            "expected_status": "answered" if index < 10 else "insufficient_evidence",
            "expected_evidence": [{"page_index": 0, "quote": "Synthetic evidence"}]
            if index < 10
            else [],
        }
        for index in range(15)
    ]
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "extractor": {"version": runner.version("pypdf")},
                "papers": papers,
            }
        )
    )
    (dataset / "questions.json").write_text(json.dumps({"questions": questions}))
    (dataset / "checksums.sha256").write_text(
        "".join(
            f"{runner.digest((dataset / name).read_bytes())}  {name}\n"
            for name in ("manifest.json", "questions.json")
        )
    )
    return dataset


def test_prepare_freezes_same_bytes_and_rejects_source_change(tmp_path, monkeypatch):
    dataset = frozen_fixture(tmp_path, monkeypatch)
    manifest, questions, sources = runner.prepare(dataset, tmp_path / "run")
    assert len(manifest["papers"]) == 3 and len(questions) == 15
    assert sources["first"]["pages"] == ["Synthetic evidence"]
    snapshot = tmp_path / "run/sources/first.pdf"
    assert snapshot.read_bytes() == (tmp_path / "first.pdf").read_bytes()
    (tmp_path / "first.pdf").write_bytes(b"changed source")
    with pytest.raises(runner.DiagnosticError, match="Source PDF checksum mismatch"):
        runner.prepare(dataset, tmp_path / "other-run")
    assert snapshot.read_bytes() != (tmp_path / "first.pdf").read_bytes()


def test_prepare_rejects_changed_questions_before_calls(tmp_path, monkeypatch):
    dataset = frozen_fixture(tmp_path, monkeypatch)
    (dataset / "questions.json").write_text('{"questions": []}')
    with pytest.raises(runner.DiagnosticError, match="Dataset checksum mismatch: questions.json"):
        runner.prepare(dataset, tmp_path / "run")


def evidence_result():
    paper_id, sha, text = "paper-current", "a" * 64, "Physical page evidence."
    chunk_version = "page-char-v1-1400-200"
    chunk_id = (
        "pt_"
        + runner.digest(f"{chunk_version}|{paper_id}|{sha}|0|0|{len(text)}|{text}".encode())[:32]
    )
    chunk = {
        "chunk_id": chunk_id,
        "paper_id": paper_id,
        "paper_sha256": sha,
        "page_index": 0,
        "start_char": 0,
        "end_char": len(text),
        "text": text,
        "chunk_version": chunk_version,
    }
    citation = {
        "chunk_id": chunk_id,
        "paper_id": paper_id,
        "page_index": 0,
        "quote": "page evidence",
    }
    result = {
        "id": "question-server",
        "paper_id": paper_id,
        "status": "answered",
        "claims": [{"text": "A synthetic fact", "citations": [citation]}],
        "trace": {"retrieval": {"selected": [chunk], "baseline_selected": []}, "calls": []},
    }
    source = {"paper": {"sha256": sha}, "pages": [text]}
    return result, source


def test_independent_citation_check_rejects_wrong_paper_and_fabricated_offsets():
    result, source = evidence_result()
    assert runner.citation_check(result, "paper-current", source)["status"] == "passed"
    changed = deepcopy(result)
    changed["claims"][0]["citations"][0]["paper_id"] = "other-paper"
    assert (
        "citation_1:wrong_paper"
        in runner.citation_check(changed, "paper-current", source)["errors"]
    )
    changed = deepcopy(result)
    changed["trace"]["retrieval"]["selected"][0]["start_char"] = 1
    assert (
        "citation_1:chunk_source_identity_mismatch"
        in runner.citation_check(changed, "paper-current", source)["errors"]
    )
    changed = deepcopy(result)
    changed["claims"][0]["citations"][0]["quote"] = "fabricated quotation"
    assert (
        "citation_1:quote_not_in_source_page"
        in runner.citation_check(changed, "paper-current", source)["errors"]
    )


def test_runner_never_sends_expected_answers_and_keeps_quality_pending(tmp_path):
    result, source = evidence_result()
    requests = []
    question = {
        "id": "Q1",
        "question": "研究问题是什么？",
        "expected_status": "answered",
        "expected_answer_points": ["SECRET_EXPECTED_ANSWER_DO_NOT_SEND"],
        "expected_evidence": [{"page_index": 0, "quote": "page evidence"}],
    }

    def respond(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(202, json=result)
        return httpx.Response(200, json=[result])

    with httpx.Client(
        transport=httpx.MockTransport(respond), base_url="http://localhost"
    ) as client:
        record = runner.execute_question(client, question, source, "paper-current", tmp_path, 1)
    post = json.loads(requests[0].content)
    assert set(post) == {"question", "request_id"}
    assert b"SECRET_EXPECTED_ANSWER" not in requests[0].content
    assert record["runner_status"] == "completed"
    assert record["ai_review"]["status"] == record["human_review"]["status"] == "pending"
    assert record["history_check"]["identical"] is True
    assert record["retrieval_comparison"]["raw_question_bm25"]["evidence_recall"] == 0
    assert record["retrieval_comparison"]["expanded_query_bm25"]["evidence_recall"] == 1


def test_http_failure_is_saved_without_body_or_hidden_retry(tmp_path):
    _, source = evidence_result()
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(503, text="upstream-secret-token-should-never-appear")

    question = {"id": "Q1", "question": "研究问题是什么？"}
    with httpx.Client(
        transport=httpx.MockTransport(respond), base_url="http://localhost"
    ) as client:
        record = runner.execute_question(client, question, source, "paper-current", tmp_path, 1)
    assert len(requests) == 1
    assert record["runner_status"] == "failed"
    artifact = (tmp_path / "Q1.result.json").read_text()
    assert "503" in artifact and "upstream-secret-token" not in artifact
    assert record["calls_may_have_occurred"] is True


def test_unknown_cost_and_usage_are_not_zero_or_provider_invoice():
    result = {
        "trace": {
            "ledger": {
                "calls": [
                    {"details": {"usage": None}},
                    {
                        "details": {
                            "usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 5,
                                "total_tokens": 15,
                            }
                        }
                    },
                ],
                "estimated_cost": "0.0123",
                "unknown_cost_calls": 1,
                "currency": "CNY",
            }
        }
    }
    metrics = runner.metrics(result)
    assert metrics["calls"] == 2 and metrics["unknown_usage_calls"] == 1
    summary = runner.aggregate(
        [
            {"runner_status": "completed", "result": result, "metrics": metrics},
            {"runner_status": "failed"},
        ]
    )
    assert summary["unknown_cost_calls"] == 1 and summary["unmeasured_questions"] == 1
    assert summary["estimated_cost_known_subtotals"] == {"CNY": "0.0123"}
    assert runner.retrieval_score({}, None)["evidence_recall"] is None


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com",
        "http://localhost?key=secret",
        "http://user:secret@localhost",
    ],
)
def test_app_origin_does_not_accept_remote_or_embedded_credentials(url):
    with pytest.raises(argparse.ArgumentTypeError):
        runner.local_url(url)
