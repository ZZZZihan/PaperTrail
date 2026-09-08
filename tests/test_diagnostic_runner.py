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
                "dataset_id": "papertrail-development-v0.1",
                "extractor": {"version": runner.version("pypdf")},
                "papers": papers,
            }
        )
    )
    (dataset / "questions.json").write_text(
        json.dumps({"dataset_id": "papertrail-development-v0.1", "questions": questions})
    )
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


def synthetic_holdout(tmp_path, monkeypatch):
    """No real held-out answer is loaded into test output or used as a tuning fixture."""
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    papers, questions, contexts = [], [], []
    for number in range(4):
        paper_id = f"synthetic{number}"
        text = f"Synthetic frozen source {number}"
        pdf = pdf_bytes((text,))
        path = tmp_path / f"{paper_id}.pdf"
        path.write_bytes(pdf)
        papers.append(
            {
                "id": paper_id,
                "local_path": path.name,
                "sha256": runner.digest(pdf),
                "page_count": 1,
                "page_text_sha256": [runner.digest(text.encode())],
            }
        )
        evidence = {
            "id": f"evidence-{number}",
            "paper_id": paper_id,
            "page_index": 0,
            "pdf_page_number": 1,
            "context_start": 0,
            "context_end": len(text),
            "context_sha256": runner.digest(text.encode()),
            "char_start": 0,
            "char_end": 9,
            "quote": "Synthetic",
            "section": "Synthetic section",
        }
        contexts.append(evidence)
        for category in (
            "fact",
            "cross_section_conditions",
            "false_premise",
            "insufficient_evidence",
            "table_footnote_probe",
        ):
            question_id = f"{paper_id}-{category}"
            answer = category != "insufficient_evidence"
            points = (
                [
                    {
                        "id": f"{question_id}-point",
                        "text": "Synthetic answer point",
                        "evidence_ids": [evidence["id"]],
                    }
                ]
                if answer
                else []
            )
            question = {
                "id": question_id,
                "paper_id": paper_id,
                "question": "Synthetic diagnostic question",
                "category": category,
                "expected_status": "answered" if answer else "insufficient_evidence",
                "expected_answer_points": [point["text"] for point in points],
                "answer_points": points,
                "required_conditions": [],
                "human_review": {"status": "pending"},
                "evidence_ids": [evidence["id"]],
                "expected_evidence": [
                    {
                        key: evidence[key]
                        for key in (
                            "id",
                            "page_index",
                            "pdf_page_number",
                            "quote",
                            "char_start",
                            "char_end",
                            "section",
                        )
                    }
                ],
                "insufficient_reason": None,
            }
            if not answer:
                question.update(
                    insufficient_reason="Synthetic unsupported premise",
                    insufficiency_audit={
                        "boundary": "Only this synthetic page",
                        "search_page_indices": [0],
                        "keyword_page_indices": {"Synthetic": [0]},
                    },
                )
            if category == "table_footnote_probe":
                question["table_probe"] = {
                    "row_labels": ["Synthetic row"],
                    "column_labels": ["Synthetic column"],
                    "note_evidence_ids": [evidence["id"]],
                    "visual_review_page_indices": [0],
                }
            questions.append(question)
    objects = {
        "manifest.json": {
            "dataset_id": "papertrail-candidate-holdout-v0.2",
            "dataset_status": "candidate_holdout",
            "tuning_status_at_freeze": "never_used_for_tuning",
            "model_runs_at_freeze": 0,
            "extractor": {"version": runner.version("pypdf")},
            "papers": papers,
        },
        "questions.json": {
            "dataset_id": "papertrail-candidate-holdout-v0.2",
            "questions": questions,
        },
        "evidence.json": {"contexts": contexts},
        "rubric.json": {
            "frozen_denominators": {
                "papers": 4,
                "questions": 20,
                "answerable": 16,
                "insufficient": 4,
                "answer_points": 16,
                "required_conditions": 0,
                "contexts": 4,
            }
        },
    }
    for name, value in objects.items():
        (dataset / name).write_text(json.dumps(value))
    development = tmp_path / "evals/development-v0.1/manifest.json"
    development.parent.mkdir(parents=True)
    development.write_text(json.dumps({"papers": [{"sha256": "f" * 64}]}))
    write_holdout_checksums(dataset)
    return dataset


def write_holdout_checksums(dataset):
    (dataset / "checksums.sha256").write_text(
        "".join(
            f"{runner.digest((dataset / name).read_bytes())}  {name}\n"
            for name in ("manifest.json", "questions.json", "evidence.json", "rubric.json")
        )
    )


def test_prepare_holdout_snapshots_all_rules_and_validates_without_app_calls(tmp_path, monkeypatch):
    dataset = synthetic_holdout(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "request_json", lambda *a, **k: pytest.fail("No app call allowed"))
    run = tmp_path / "run"
    _, questions, sources = runner.prepare(dataset, run)
    assert len(questions) == 20 and len(sources) == 4
    preparation = json.loads((run / "preparation.json").read_text())
    assert preparation["expected_status_counts"] == {"answered": 16, "insufficient_evidence": 4}
    assert preparation["holdout_annotation_check"]["questions"] == 20
    assert preparation["ai_review"]["status"] == preparation["human_review"]["status"] == "pending"
    for name in (
        "manifest.json",
        "questions.json",
        "evidence.json",
        "rubric.json",
        "checksums.sha256",
    ):
        assert (run / "dataset" / name).read_bytes() == (dataset / name).read_bytes()
        assert (run / "dataset" / name).stat().st_mode & 0o222 == 0


def test_holdout_wrong_context_is_rejected_even_with_consistent_file_checksums(
    tmp_path, monkeypatch
):
    dataset = synthetic_holdout(tmp_path, monkeypatch)
    path = dataset / "evidence.json"
    content = json.loads(path.read_text())
    content["contexts"][0]["context_sha256"] = "0" * 64
    path.write_text(json.dumps(content))
    write_holdout_checksums(dataset)
    with pytest.raises(runner.DiagnosticError, match="annotation contract validation failed"):
        runner.prepare(dataset, tmp_path / "run")
    assert not (tmp_path / "run/dataset").exists()


@pytest.mark.parametrize("change", ["changed_bytes", "omitted_file", "source_overlap"])
def test_holdout_contract_cannot_drop_rules_or_include_development_source(
    tmp_path, monkeypatch, change
):
    dataset = synthetic_holdout(tmp_path, monkeypatch)
    if change == "changed_bytes":
        with (dataset / "rubric.json").open("a") as file:
            file.write(" ")
        message = "Dataset checksum mismatch: rubric.json"
    elif change == "omitted_file":
        path = dataset / "checksums.sha256"
        path.write_text(
            "\n".join(line for line in path.read_text().splitlines() if "rubric.json" not in line)
            + "\n"
        )
        message = "complete dataset contract"
    else:
        manifest = json.loads((dataset / "manifest.json").read_text())
        path = tmp_path / "evals/development-v0.1/manifest.json"
        path.write_text(json.dumps({"papers": [{"sha256": manifest["papers"][0]["sha256"]}]}))
        message = "overlaps the development corpus"
    with pytest.raises(runner.DiagnosticError, match=message):
        runner.prepare(dataset, tmp_path / "run")


def test_dataset_identity_and_original_development_counts_remain_strict(tmp_path, monkeypatch):
    dataset = frozen_fixture(tmp_path, monkeypatch)
    path = dataset / "questions.json"
    questions = json.loads(path.read_text())
    questions["questions"].pop()
    path.write_text(json.dumps(questions))
    (dataset / "checksums.sha256").write_text(
        "".join(
            f"{runner.digest((dataset / name).read_bytes())}  {name}\n"
            for name in ("manifest.json", "questions.json")
        )
    )
    with pytest.raises(runner.DiagnosticError, match="count differs"):
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


def test_provider_quota_preserves_unknown_cost_even_with_known_usage():
    result = {
        "trace": {
            "ledger": {
                "calls": [
                    {
                        "details": {
                            "usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 5,
                                "total_tokens": 15,
                            },
                            "cost_source": "unknown_provider_rates",
                            "estimated_cost": None,
                        },
                        "actual_cost": None,
                    }
                ],
                "estimated_cost": None,
                "known_cost_subtotal": "0",
                "unknown_cost_calls": 1,
                "currency": "USD",
            }
        }
    }
    metrics = runner.metrics(result)
    assert metrics["cost_source"] == "unknown_provider_rates"
    assert metrics["cost_sources"] == ["unknown_provider_rates"]
    assert metrics["estimated_cost_known_subtotal"] == "0"
    assert metrics["estimated_cost_total"] is None
    assert metrics["unknown_cost_calls"] == 1
    summary = runner.aggregate([{"runner_status": "completed", "metrics": metrics}])
    assert summary["cost_sources"] == ["unknown_provider_rates"]
    assert summary["estimated_cost_known_subtotals"] == {"USD": "0"}
    assert summary["estimated_cost_totals"] is None
    assert summary["usage_known_subtotals"]["total_tokens"] == 15


def test_priced_and_unknown_cost_sources_remain_separate():
    def result(cost_source, subtotal, unknown):
        return {
            "trace": {
                "ledger": {
                    "calls": [{"details": {"cost_source": cost_source}}],
                    "estimated_cost": subtotal,
                    "unknown_cost_calls": unknown,
                    "currency": "USD",
                }
            }
        }

    priced = runner.metrics(result("configured_token_rates", "0.0123", 0))
    unknown = runner.metrics(result("unknown", "0", 1))
    assert priced["cost_source"] == "configured_token_rates"
    priced_summary = runner.aggregate([{"runner_status": "completed", "metrics": priced}])
    assert priced_summary["estimated_cost_totals"] == {"USD": "0.0123"}
    summary = runner.aggregate(
        [
            {"runner_status": "completed", "metrics": priced},
            {"runner_status": "completed", "metrics": unknown},
        ]
    )
    assert summary["cost_source"] == "mixed"
    assert summary["cost_sources"] == ["configured_token_rates", "unknown"]
    assert summary["estimated_cost_known_subtotals"] == {"USD": "0.0123"}
    assert summary["unknown_cost_calls"] == 1
    assert summary["estimated_cost_totals"] is None
