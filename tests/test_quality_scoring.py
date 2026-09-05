"""Offline grading contracts use synthetic artifacts, never claim model quality."""

import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "quality_scoring", Path(__file__).resolve().parents[1] / "scripts/score_quality.py"
)
scorer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scorer)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def checksums(run):
    (run / "dataset/checksums.sha256").write_text(
        "".join(
            f"{scorer.digest((run / 'dataset' / name).read_bytes())}  {name}\n"
            for name in ("manifest.json", "questions.json")
        )
    )


def fixture_run(tmp_path, selection=None):
    run = tmp_path / "run"
    questions = [
        {
            "id": f"q{number}",
            "paper_id": "paper",
            "question": "Synthetic question; q3 contains a rebuttable false premise.",
            "expected_status": "insufficient_evidence" if number in {4, 5} else "answered",
            "expected_answer_points": ["First required point", "Second required point"]
            if number == 1
            else ["Answer or supported rebuttal"]
            if number not in {4, 5}
            else [],
            "required_conditions": [{"id": "condition-1", "text": "Frozen model condition"}]
            if number == 1
            else [],
        }
        for number in range(1, 7)
    ]
    write_json(run / "dataset/questions.json", {"dataset_id": "synthetic", "questions": questions})
    source = b"synthetic source bytes, not a real paper"
    pages = ["Synthetic reference context"]
    write_json(
        run / "dataset/manifest.json",
        {
            "papers": [
                {
                    "id": "paper",
                    "sha256": scorer.digest(source),
                    "page_text_sha256": [scorer.digest(pages[0].encode())],
                }
            ]
        },
    )
    write_json(run / "sources/paper.pages.json", pages)
    (run / "sources/paper.pdf").write_bytes(source)
    write_json(run / "invocation.json", {"mode": "run", "git": {"sha": "a" * 40, "dirty": False}})
    chosen = selection or [question["id"] for question in questions]
    write_json(run / "selection.json", {"question_ids": chosen})
    statuses = ["answered", "failed", "insufficient_evidence", "answered", "insufficient_evidence"]
    for question, status in zip(questions, statuses, strict=False):
        if question["id"] not in chosen:
            continue
        record = {
            "question_id": question["id"],
            "input": question,
            "runner_status": "completed",
            "result": {
                "status": status,
                "claims": [{"text": "One fact"}] if status == "answered" else [],
                "support_status": "ai_checked",
                "support_verdicts": [True],
            },
            "wall_elapsed_seconds": 4.5,
            "metrics": {
                "calls": 3,
                "usage_known_subtotals": {"total_tokens": 30},
                "unknown_usage_calls": 0,
                "estimated_cost_known_subtotal": "0",
                "estimated_cost_total": None,
                "unknown_cost_calls": 3,
                "currency": "USD",
            },
            "deterministic_citation_check": {"status": "passed", "citations_checked": 1},
        }
        if question["id"] == "q2":
            del record["metrics"]
        write_json(run / f"questions/{question['id']}.result.json", record)
    checksums(run)
    return run


def independent_review(context):
    review = scorer.template(context, "ai")
    review.update(
        reviewer="Independent test reviewer",
        method="Read source and output",
        reviewed_at="2026-09-05T00:00:00Z",
    )
    for item in review["items"]:
        key = item["question_id"]
        if key in {"q1", "q4"}:
            item["fact_inventory_complete"] = True
            item["atomic_facts"] = [
                {
                    "id": "atom-1",
                    "text": "One independently identified atomic fact",
                    "supported": key == "q1",
                    "rationale": "Compared exact claim with surrounding source context",
                }
            ]
        if key == "q1":
            for point in item["answer_points"]:
                point.update(covered=point["id"] == "point-1", rationale="Second point omitted")
            item["required_conditions"][0].update(
                covered=False, rationale="Model condition omitted"
            )
            item.update(
                retrieval_sufficient=True,
                notes="The retrieved source contains both points and the model condition",
            )
        if key in {"q4", "q5"}:
            item.update(
                unjustified_answer=key == "q4",
                notes="Independent check of the full output against the allowed paper",
            )
    return review


def test_supported_short_answer_does_not_become_complete_or_omit_failures(tmp_path):
    context = scorer.load_run(fixture_run(tmp_path, ["q1", "q2", "q3"]))
    report = scorer.summarize(context, independent_review(context))
    metrics = report["metrics"]
    assert metrics["fact_support"]["rate"] == 1
    assert metrics["complete_supported_answer"] == {
        "numerator": 0,
        "denominator": 3,
        "negative": 3,
        "pending": 0,
        "rate": 0,
        "confirmed_fraction_of_all": 0,
    }
    assert metrics["necessary_condition_coverage"]["numerator"] == 0
    assert metrics["necessary_condition_coverage"]["denominator"] == 1
    assert metrics["engineering_failure"]["denominator"] == 3
    assert metrics["engineering_failure"]["numerator"] == 1
    assert metrics["answer_point_coverage"]["denominator"] == 4


def test_pending_and_missing_are_neither_false_nor_pass_and_self_checks_are_ignored(tmp_path):
    context = scorer.load_run(fixture_run(tmp_path))
    report = scorer.summarize(context, scorer.template(context, "human"))
    metrics = report["metrics"]
    assert metrics["complete_supported_answer"]["denominator"] == 4
    assert metrics["complete_supported_answer"]["pending"] == 2
    assert metrics["complete_supported_answer"]["rate"] is None
    assert metrics["fact_support"]["denominator"] == 0
    assert metrics["fact_support"]["rate"] is None
    assert metrics["fact_support"]["questions_with_unfinished_fact_inventory"] == ["q1", "q4", "q6"]
    assert metrics["engineering_failure"]["denominator"] == 6
    assert metrics["engineering_failure"]["pending"] == 1
    assert metrics["unjustified_answer_on_unanswerable"]["pending"] == 2
    assert report["missing_result_question_ids"] == ["q6"]
    assert report["review_source"] == "human" and report["reviewer"] is None


def test_false_refusal_unjustified_answer_and_unknown_costs_remain_separate(tmp_path):
    context = scorer.load_run(fixture_run(tmp_path))
    report = scorer.summarize(context, independent_review(context))
    metrics = report["metrics"]
    assert metrics["false_refusal_on_answerable"]["numerator"] == 1
    assert metrics["false_refusal_on_answerable"]["denominator"] == 4
    assert metrics["unjustified_answer_on_unanswerable"]["numerator"] == 1
    assert metrics["unjustified_answer_on_unanswerable"]["denominator"] == 2
    assert metrics["unjustified_answer_on_unanswerable"]["rate"] == 0.5
    assert metrics["citation_source_compliance"]["rate"] == 1
    assert metrics["fact_support"]["numerator"] == 1
    assert metrics["fact_support"]["negative"] == 1
    resources = report["resources"]
    assert resources["calls_known_subtotal"] == 12
    assert resources["calls_total"] is None
    assert resources["questions_with_unknown_call_count"] == 2
    assert resources["unknown_cost_calls"] == 12
    assert resources["estimated_cost_known_subtotals"] == {"USD": "0"}
    assert resources["estimated_cost_totals"] is None
    assert resources["elapsed_questions_missing"] == 1


def test_supported_rebuttal_is_answerable_but_partial_status_cannot_pass(tmp_path):
    run = fixture_run(tmp_path, ["q3"])
    path = run / "questions/q3.result.json"
    record = json.loads(path.read_text())
    record["result"].update(
        status="answered", claims=[{"text": "The premise is false; supported rebuttal."}]
    )
    write_json(path, record)
    context = scorer.load_run(run)
    review = independent_review(context)
    item = review["items"][0]
    item["fact_inventory_complete"] = True
    item["atomic_facts"] = [
        {
            "id": "rebuttal",
            "text": "Supported rebuttal",
            "supported": True,
            "rationale": "Source explicitly contradicts premise",
        }
    ]
    item["answer_points"][0].update(
        covered=True, rationale="Fully corrects premise with source support"
    )
    assert scorer.summarize(context, review)["metrics"]["complete_supported_answer"]["rate"] == 1
    record["result"]["status"] = "partial_answer"
    write_json(path, record)
    changed = scorer.load_run(run)
    review["bindings"] = changed["bindings"]
    assert scorer.summarize(changed, review)["metrics"]["complete_supported_answer"]["rate"] == 0


def test_reviews_are_bound_to_question_and_result_bytes(tmp_path):
    run = fixture_run(tmp_path)
    context = scorer.load_run(run)
    review = independent_review(context)
    changed_review = deepcopy(review)
    changed_review["items"].pop()
    with pytest.raises(scorer.QualityError, match="exactly match"):
        scorer.summarize(context, changed_review)
    path = run / "questions/q1.result.json"
    result = json.loads(path.read_text())
    result["result"]["claims"][0]["text"] = "Changed output"
    write_json(path, result)
    with pytest.raises(scorer.QualityError, match="bindings drifted"):
        scorer.summarize(scorer.load_run(run), review)
    result["question_id"] = "q2"
    write_json(path, result)
    with pytest.raises(scorer.QualityError, match="Result question ID mismatch"):
        scorer.load_run(run)


def test_frozen_question_drift_and_newly_arrived_output_are_rejected(tmp_path):
    run = fixture_run(tmp_path)
    context = scorer.load_run(run)
    review = scorer.template(context, "ai")
    path = run / "dataset/questions.json"
    data = json.loads(path.read_text())
    data["questions"][0]["expected_answer_points"][0] = "Relaxed criterion"
    write_json(path, data)
    with pytest.raises(scorer.QualityError, match="Dataset checksum mismatch"):
        scorer.load_run(run)
    checksums(run)
    with pytest.raises(scorer.QualityError, match="input differs"):
        scorer.load_run(run)
    # A different complete run cannot inherit an old review even if a missing result arrives.
    other = fixture_run(tmp_path / "other")
    record = {"question_id": "q6", "input": context["questions"]["q6"], "runner_status": "failed"}
    write_json(other / "questions/q6.result.json", record)
    with pytest.raises(scorer.QualityError, match="bindings drifted"):
        scorer.summarize(scorer.load_run(other), review)


def test_missing_condition_rubric_stays_unknown_and_supplement_is_bound(tmp_path):
    run = fixture_run(tmp_path, ["q1"])
    dataset_path = run / "dataset/questions.json"
    dataset = json.loads(dataset_path.read_text())
    del dataset["questions"][0]["required_conditions"]
    write_json(dataset_path, dataset)
    result_path = run / "questions/q1.result.json"
    result = json.loads(result_path.read_text())
    result["input"] = dataset["questions"][0]
    write_json(result_path, result)
    checksums(run)
    context = scorer.load_run(run)
    metric = scorer.summarize(context, scorer.template(context, "ai"))["metrics"][
        "necessary_condition_coverage"
    ]
    assert metric["rate"] is None
    assert metric["questions_without_frozen_condition_rubric"] == ["q1"]
    rubric = tmp_path / "rubric.json"
    write_json(
        rubric,
        {
            "schema_version": 1,
            "questions": {
                "q1": {"required_conditions": [{"id": "frozen-model", "text": "Model was frozen"}]}
            },
        },
    )
    bound = scorer.load_run(run, rubric)
    review = scorer.template(bound, "ai")
    write_json(rubric, {"schema_version": 1, "questions": {"q1": {"required_conditions": []}}})
    with pytest.raises(scorer.QualityError, match="bindings drifted"):
        scorer.summarize(scorer.load_run(run, rubric), review)


@pytest.mark.parametrize("value", [0, 1, "passed", "pending"])
def test_judgment_types_do_not_coerce_numbers_or_strings(tmp_path, value):
    context = scorer.load_run(fixture_run(tmp_path))
    review = scorer.template(context, "ai")
    review["items"][0]["answer_points"][0]["covered"] = value
    with pytest.raises(scorer.QualityError, match="true, false or null"):
        scorer.summarize(context, review)


def test_fact_inventory_cannot_be_empty_complete_or_hide_pending_atoms(tmp_path):
    context = scorer.load_run(fixture_run(tmp_path, ["q1"]))
    review = independent_review(context)
    review["items"][0]["atomic_facts"] = []
    with pytest.raises(scorer.QualityError, match="complete empty fact inventory"):
        scorer.summarize(context, review)
    review["items"][0]["atomic_facts"] = [
        {"id": "atom", "text": "Unreviewed fact", "supported": None, "rationale": None}
    ]
    report = scorer.summarize(context, review)
    assert report["metrics"]["fact_support"]["pending"] == 1
    assert report["metrics"]["fact_support"]["rate"] is None


def test_additional_evidence_files_are_bound_and_source_drift_fails(tmp_path):
    run = fixture_run(tmp_path)
    evidence_path = run / "dataset/evidence.json"
    write_json(evidence_path, {"context": "Frozen surrounding evidence"})
    checksum_path = run / "dataset/checksums.sha256"
    checksum_path.write_text(
        checksum_path.read_text() + f"{scorer.digest(evidence_path.read_bytes())}  evidence.json\n"
    )
    context = scorer.load_run(run)
    assert context["bindings"]["file_sha256"]["dataset/evidence.json"] == scorer.digest(
        evidence_path.read_bytes()
    )
    write_json(evidence_path, {"context": "Changed interpretation"})
    with pytest.raises(scorer.QualityError, match="Dataset checksum mismatch: evidence.json"):
        scorer.load_run(run)
    checksums(run)
    (run / "sources/paper.pdf").write_bytes(b"Different source version")
    with pytest.raises(scorer.QualityError, match="Source PDF checksum mismatch"):
        scorer.load_run(run)


def test_unfinished_inventory_has_no_invented_denominator_ratio(tmp_path):
    context = scorer.load_run(fixture_run(tmp_path, ["q1"]))
    review = independent_review(context)
    review["items"][0]["fact_inventory_complete"] = False
    metric = scorer.summarize(context, review)["metrics"]["fact_support"]
    assert metric["numerator"] == metric["denominator"] == 1
    assert metric["denominator_is_complete"] is False
    assert metric["rate"] is metric["confirmed_fraction_of_all"] is None


def test_review_identity_and_frozen_points_cannot_be_bypassed(tmp_path):
    context = scorer.load_run(fixture_run(tmp_path, ["q1"]))
    review = independent_review(context)
    review["reviewer"] = None
    with pytest.raises(scorer.QualityError, match="reviewer, method and reviewed_at"):
        scorer.summarize(context, review)
    review["reviewer"] = "Independent test reviewer"
    review["items"][0]["answer_points"].pop()
    with pytest.raises(scorer.QualityError, match="IDs differ from frozen rubric"):
        scorer.summarize(context, review)


def test_source_failure_blocks_complete_delivery_without_regrading_fact_support(tmp_path):
    context = scorer.load_run(fixture_run(tmp_path, ["q1"]))
    review = independent_review(context)
    item = review["items"][0]
    for row in item["answer_points"] + item["required_conditions"]:
        row.update(covered=True, rationale="All content independently verified")
    context["records"]["q1"]["deterministic_citation_check"]["status"] = "failed"
    report = scorer.summarize(context, review)
    assert report["metrics"]["fact_support"]["rate"] == 1
    assert report["metrics"]["citation_source_compliance"]["rate"] == 0
    assert report["metrics"]["complete_supported_answer"]["rate"] == 0


def test_cli_creates_templates_and_offline_summary_without_overwriting(tmp_path):
    run = fixture_run(tmp_path)
    review_path = tmp_path / "human-review.json"
    output = tmp_path / "summary.json"
    base = [sys.executable, str(Path(scorer.__file__))]
    first = subprocess.run(
        base
        + ["template", "--run", str(run), "--review-source", "human", "--output", str(review_path)],
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0, first.stderr
    second = subprocess.run(
        base
        + ["summarize", "--run", str(run), "--review", str(review_path), "--output", str(output)],
        capture_output=True,
        text=True,
    )
    assert second.returncode == 0, second.stderr
    report = json.loads(output.read_text())
    assert report["review_file_sha256"] == scorer.digest(review_path.read_bytes())
    assert len(report["scorer"]["code_sha"]) == 40
    assert report["bindings"]["run_code_sha"] == "a" * 40
    assert report["review_source"] == "human"
    with pytest.raises(FileExistsError):
        scorer.write_new(output, {"overwritten": True})
