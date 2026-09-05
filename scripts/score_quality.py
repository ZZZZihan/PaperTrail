"""Create independent review forms and summarize frozen QA runs, entirely offline.

This tool never grades semantics itself and never reads application support verdicts.
It preserves selected-but-missing questions, unknown reviews, and unknown costs.
"""

import argparse
import hashlib
import json
import math
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "papertrail-independent-quality-v1"
ANSWER_STATUSES = {"answered", "partially_answered", "partial_answer"}
TERMINAL = ANSWER_STATUSES | {"insufficient_evidence", "failed"}


class QualityError(ValueError):
    """An inconsistent or changed artifact must not silently produce a score."""


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise QualityError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def decode(raw: bytes):
    return json.loads(raw, object_pairs_hook=json_object)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise QualityError(message)


def ids(values: list, key: str, label: str) -> dict:
    check(isinstance(values, list), f"{label} must be a list")
    result = {}
    for row in values:
        check(isinstance(row, dict), f"Invalid {label} row")
        identity = row.get(key)
        check(
            isinstance(identity, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]+", identity)),
            f"Invalid {label} ID",
        )
        check(identity not in result, f"Duplicate {label} ID: {identity}")
        result[identity] = row
    return result


def point_rows(values: list, prefix: str) -> list[dict]:
    check(isinstance(values, list), f"{prefix} must be a list")
    rows = []
    for index, value in enumerate(values):
        row = {"id": f"{prefix}-{index + 1}", "text": value} if isinstance(value, str) else value
        check(isinstance(row, dict), f"Invalid {prefix}")
        check(isinstance(row.get("text"), str) and bool(row["text"].strip()), f"Empty {prefix}")
        rows.append({"id": row.get("id"), "text": row["text"]})
    ids(rows, "id", prefix)
    return rows


def load_run(run: Path, rubric: Path | None = None) -> dict:
    """Read bytes once; bind expected IDs before inspecting outputs or scoring reviews."""
    files = {}

    def read(name: str, optional: bool = False):
        path = run / name
        if optional and not path.exists():
            files[name] = None
            return None
        raw = path.read_bytes()
        files[name] = digest(raw)
        return decode(raw)

    manifest = read("dataset/manifest.json")
    dataset = read("dataset/questions.json")
    invocation = read("invocation.json")
    selection = read("selection.json")["question_ids"]
    check(invocation.get("mode") == "run", "Only explicitly selected run mode can be scored")
    code_sha = invocation.get("git", {}).get("sha")
    check(
        isinstance(code_sha, str) and bool(re.fullmatch(r"[a-f0-9]{40}", code_sha)),
        "Missing run code SHA",
    )
    checksum_raw = (run / "dataset/checksums.sha256").read_bytes()
    files["dataset/checksums.sha256"] = digest(checksum_raw)
    bound = {}
    for line in checksum_raw.decode().splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})\s+\*?([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        check(match is not None, "Invalid dataset checksum entry")
        check(match[2] not in bound, "Duplicate dataset checksum entry")
        bound[match[2]] = match[1]
    check({"manifest.json", "questions.json"} <= set(bound), "Missing dataset checksums")
    for name, expected in bound.items():
        if f"dataset/{name}" not in files:
            files[f"dataset/{name}"] = digest((run / "dataset" / name).read_bytes())
        check(files[f"dataset/{name}"] == expected, f"Dataset checksum mismatch: {name}")
    questions = ids(dataset["questions"], "id", "question")
    papers = ids(manifest["papers"], "id", "paper")
    check(isinstance(selection, list) and bool(selection), "Selection must be a nonempty list")
    check(all(isinstance(key, str) for key in selection), "Invalid selection ID")
    check(len(selection) == len(set(selection)), "Duplicate selected question ID")
    check(set(selection) <= set(questions), "Unknown selected question ID")
    for question in questions.values():
        check(question.get("paper_id") in papers, "Question paper ID absent from manifest")
        check(
            question.get("expected_status") in {"answered", "insufficient_evidence"},
            "Invalid expected status",
        )
    rubric_data = None
    rubric_hash = None
    if rubric is not None:
        raw = rubric.read_bytes()
        rubric_hash = digest(raw)
        rubric_data = decode(raw)
        check(rubric_data.get("schema_version") == 1, "Unsupported rubric version")
        check(isinstance(rubric_data.get("questions"), dict), "Invalid rubric questions")
        check(set(rubric_data["questions"]) <= set(questions), "Unknown rubric question ID")
    conditions = {}
    points = {}
    for key in selection:
        question = questions[key]
        points[key] = point_rows(
            question.get("answer_points", question.get("expected_answer_points", [])), "point"
        )
        if "answer_points" in question:
            check(
                [point["text"] for point in points[key]] == question.get("expected_answer_points"),
                "Answer point representations disagree",
            )
        if question["expected_status"] == "answered":
            check(bool(points[key]), f"Answerable question lacks frozen answer points: {key}")
        condition_values = question.get("required_conditions")
        if rubric_data and key in rubric_data["questions"]:
            supplement = rubric_data["questions"][key]
            check(
                isinstance(supplement, dict) and set(supplement) == {"required_conditions"},
                "Rubric may only annotate conditions",
            )
            if condition_values is not None:
                check(
                    condition_values == supplement["required_conditions"],
                    "Rubric cannot override frozen conditions",
                )
            condition_values = supplement["required_conditions"]
        conditions[key] = (
            None if condition_values is None else point_rows(condition_values, "condition")
        )
    # Every frozen source is bound. This proves source identity, not semantic support.
    for paper_id, paper in papers.items():
        pdf_name = f"sources/{paper_id}.pdf"
        pdf = (run / pdf_name).read_bytes()
        files[pdf_name] = digest(pdf)
        check(files[pdf_name] == paper.get("sha256"), f"Source PDF checksum mismatch: {paper_id}")
        pages = read(f"sources/{paper_id}.pages.json")
        check(
            isinstance(pages, list) and all(isinstance(page, str) for page in pages),
            "Invalid frozen page text",
        )
        check(
            [digest(page.encode()) for page in pages] == paper.get("page_text_sha256"),
            f"Source page checksum mismatch: {paper_id}",
        )
    records = {}
    actual = {
        path.name.removesuffix(".result.json") for path in (run / "questions").glob("*.result.json")
    }
    check(actual <= set(selection), "Result question ID outside frozen selection")
    for key in selection:
        record = read(f"questions/{key}.result.json", optional=True)
        if record is not None:
            check(record.get("question_id") == key, "Result question ID mismatch")
            check(
                record.get("input") == questions[key],
                f"Result input differs from frozen question: {key}",
            )
            check(record.get("runner_status") in {"completed", "failed"}, "Invalid runner status")
            result = record.get("result", {})
            check(
                record["runner_status"] != "completed" or result.get("status") in TERMINAL,
                "Completed runner lacks terminal result",
            )
        records[key] = record
    return {
        "questions": {key: questions[key] for key in selection},
        "records": records,
        "points": points,
        "conditions": conditions,
        "bindings": {
            "dataset_id": dataset.get("dataset_id"),
            "run_code_sha": code_sha,
            "run_code_dirty": invocation.get("git", {}).get("dirty"),
            "question_ids": selection,
            "file_sha256": files,
            "condition_rubric_sha256": rubric_hash,
        },
    }


def template(context: dict, review_source: str) -> dict:
    check(review_source in {"ai", "human"}, "review_source must be ai or human")
    return {
        "schema_version": SCHEMA,
        "review_source": review_source,
        "reviewer": None,
        "method": None,
        "reviewed_at": None,
        "bindings": context["bindings"],
        "items": [
            {
                "question_id": key,
                "fact_inventory_complete": None,
                "atomic_facts": [],
                "answer_points": [
                    {**point, "covered": None, "rationale": None}
                    for point in context["points"][key]
                ],
                "required_conditions": None
                if context["conditions"][key] is None
                else [
                    {**point, "covered": None, "rationale": None}
                    for point in context["conditions"][key]
                ],
                "retrieval_sufficient": None,
                "unjustified_answer": None,
                "notes": None,
            }
            for key in context["questions"]
        ],
    }


def nullable_bool(value, label: str) -> None:
    check(value is None or type(value) is bool, f"{label} must be true, false or null")


def validate_review(context: dict, review: dict) -> dict:
    check(review.get("schema_version") == SCHEMA, "Unsupported review version")
    check(review.get("review_source") in {"ai", "human"}, "review_source must be ai or human")
    check(
        review.get("bindings") == context["bindings"],
        "Review artifact bindings drifted; use the original data/run/rubric",
    )
    items = ids(review.get("items"), "question_id", "review question")
    check(
        set(items) == set(context["questions"]), "Review question IDs must exactly match selection"
    )
    any_judgment = False
    for key, item in items.items():
        for field in ("fact_inventory_complete", "retrieval_sufficient", "unjustified_answer"):
            check(field in item, f"Missing {field}: {key}")
            nullable_bool(item[field], field)
            any_judgment |= item[field] is not None
        facts = ids(item.get("atomic_facts"), "id", "atomic fact")
        for fact in facts.values():
            check(
                isinstance(fact.get("text"), str) and bool(fact["text"].strip()),
                "Atomic fact text required",
            )
            nullable_bool(fact.get("supported"), "supported")
            any_judgment |= fact.get("supported") is not None
            if fact.get("supported") is not None:
                check(bool(fact.get("rationale")), "Independent fact judgment requires rationale")
        for field, frozen in (
            ("answer_points", context["points"][key]),
            ("required_conditions", context["conditions"][key]),
        ):
            rows = item.get(field)
            if frozen is None:
                check(rows is None, "Unfrozen conditions cannot be added during review")
                continue
            indexed = ids(rows, "id", field)
            check(
                set(indexed) == {point["id"] for point in frozen},
                f"{field} IDs differ from frozen rubric",
            )
            for point in frozen:
                row = indexed[point["id"]]
                check(row.get("text") == point["text"], f"{field} text differs from frozen rubric")
                nullable_bool(row.get("covered"), "covered")
                any_judgment |= row.get("covered") is not None
                if row.get("covered") is not None:
                    check(
                        bool(row.get("rationale")),
                        "Independent coverage judgment requires rationale",
                    )
        record = context["records"][key]
        status = (record or {}).get("result", {}).get("status")
        if status in ANSWER_STATUSES and item["fact_inventory_complete"] is True:
            check(bool(facts), "Answered output cannot have a complete empty fact inventory")
        if record is None:
            check(
                not facts
                and all(
                    item[field] is None
                    for field in (
                        "fact_inventory_complete",
                        "retrieval_sufficient",
                        "unjustified_answer",
                    )
                ),
                "Missing output cannot have semantic judgments",
            )
            check(
                all(
                    row.get("covered") is None
                    for row in item["answer_points"] + (item["required_conditions"] or [])
                ),
                "Missing output cannot have coverage judgments",
            )
        if item["retrieval_sufficient"] is not None or item["unjustified_answer"] is not None:
            check(bool(item.get("notes")), "Independent retrieval/answer judgment requires notes")
    if any_judgment:
        check(
            all(
                isinstance(review.get(key), str) and bool(review[key].strip())
                for key in ("reviewer", "method", "reviewed_at")
            ),
            "Judgments require reviewer, method and reviewed_at",
        )
    return items


def tally(values: list[bool | None]) -> dict:
    yes = sum(value is True for value in values)
    no = sum(value is False for value in values)
    pending = len(values) - yes - no
    return {
        "numerator": yes,
        "denominator": len(values),
        "negative": no,
        "pending": pending,
        "rate": yes / len(values) if values and not pending else None,
        "confirmed_fraction_of_all": yes / len(values) if values else None,
    }


def conjunction(values: list[bool | None]) -> bool | None:
    if any(value is False for value in values):
        return False
    return None if not values or any(value is None for value in values) else True


def finite_number(value) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def resource_totals(records: list[dict | None]) -> dict:
    calls = 0
    unknown_calls = 0
    cost_unknown_calls = 0
    unmeasured_cost = 0
    costs = {}
    elapsed = []
    tokens = Counter()
    unknown_usage_calls = 0
    for record in records:
        record = record or {}
        wall = record.get("wall_elapsed_seconds")
        if finite_number(wall):
            elapsed.append(wall)
        measured = record.get("metrics") or {}
        count = measured.get("calls")
        if type(count) is not int or count < 0:
            unknown_calls += 1
            unmeasured_cost += 1
            continue
        calls += count
        unknown_usage = measured.get("unknown_usage_calls")
        unknown_usage_calls += (
            unknown_usage if type(unknown_usage) is int and unknown_usage >= 0 else count
        )
        for key, value in measured.get("usage_known_subtotals", {}).items():
            if type(value) is int and value >= 0:
                tokens[key] += value
        unknown = measured.get("unknown_cost_calls")
        unknown = unknown if type(unknown) is int and 0 <= unknown <= count else count
        cost_unknown_calls += unknown
        currency = measured.get("currency")
        subtotal = measured.get("estimated_cost_known_subtotal")
        try:
            amount = Decimal(str(subtotal))
            valid = (
                amount.is_finite() and amount >= 0 and isinstance(currency, str) and bool(currency)
            )
        except InvalidOperation:
            valid = False
        if valid:
            costs[currency] = costs.get(currency, Decimal(0)) + amount
        total = measured.get("estimated_cost_total")
        if count and (unknown or total is None or not valid):
            unmeasured_cost += 1
    known_costs = {currency: str(value) for currency, value in sorted(costs.items())}
    return {
        "calls_known_subtotal": calls,
        "calls_total": calls if not unknown_calls else None,
        "questions_with_unknown_call_count": unknown_calls,
        "unknown_usage_calls": unknown_usage_calls,
        "token_known_subtotals": dict(tokens),
        "elapsed_seconds_known_subtotal": sum(elapsed),
        "elapsed_seconds_mean_observed": sum(elapsed) / len(elapsed) if elapsed else None,
        "elapsed_questions_observed": len(elapsed),
        "elapsed_questions_missing": len(records) - len(elapsed),
        "unknown_cost_calls": cost_unknown_calls,
        "questions_with_unknown_total_cost": unmeasured_cost,
        "estimated_cost_known_subtotals": known_costs,
        "estimated_cost_totals": known_costs if not unmeasured_cost else None,
        "cost_note": "Recorded estimates, not provider invoices. Unknown rates remain unknown.",
    }


def summarize(context: dict, review: dict) -> dict:
    items = validate_review(context, review)
    per_question = []
    complete_values, false_refusals, unsupported_answers = [], [], []
    point_values, condition_values, retrieval_values, engineering_values = [], [], [], []
    facts, citation_values = [], []
    missing_inventories, missing_condition_rubrics = [], []
    citation_count = 0
    for key, question in context["questions"].items():
        record = context["records"][key]
        item = items[key]
        result = (record or {}).get("result", {})
        status = result.get("status")
        failure = (
            None if record is None else record["runner_status"] == "failed" or status == "failed"
        )
        engineering_values.append(failure)
        answerable = question["expected_status"] == "answered"
        answer = status in ANSWER_STATUSES
        citation = (record or {}).get("deterministic_citation_check", {})
        # Source checks remain separate from semantics, but invalid sources cannot pass
        # the combined complete-and-supported delivery metric.
        check_count = citation.get("citations_checked")
        citation_verdict = None
        if answer:
            if type(check_count) is int and check_count >= 0:
                citation_count += check_count
                citation_verdict = {"passed": True, "failed": False}.get(citation.get("status"))
            if check_count == 0 and citation_verdict is True:
                citation_verdict = None
            citation_values.append(citation_verdict)
        point_checks = [row.get("covered") for row in item["answer_points"]]
        condition_checks = [row.get("covered") for row in item["required_conditions"] or []]
        if answerable:
            if item["required_conditions"] is None:
                missing_condition_rubrics.append(key)
            if status == "insufficient_evidence" or failure:
                point_checks = [False] * len(point_checks)
                condition_checks = [False] * len(condition_checks)
            point_values.extend(point_checks)
            condition_values.extend(condition_checks)
        atom_checks = [fact.get("supported") for fact in item["atomic_facts"]]
        if answer or record is None:
            facts.extend(atom_checks)
            if item["fact_inventory_complete"] is not True:
                missing_inventories.append(key)
        complete = None
        if answerable:
            if (
                failure
                or status == "insufficient_evidence"
                or status in ANSWER_STATUSES - {"answered"}
            ):
                complete = False
            elif status == "answered":
                complete = conjunction(
                    [
                        *point_checks,
                        *condition_checks,
                        *atom_checks,
                        citation_verdict,
                        True if item["fact_inventory_complete"] is True and atom_checks else None,
                        True if item["required_conditions"] is not None else None,
                    ]
                )
            complete_values.append(complete)
            false_refusals.append(None if record is None else status == "insufficient_evidence")
            retrieval_values.append(item["retrieval_sufficient"])
        unjustified = item["unjustified_answer"]
        if not answerable:
            if failure and not result.get("claims") and not result.get("answer"):
                unjustified = False
            unsupported_answers.append(unjustified)
        per_question.append(
            {
                "question_id": key,
                "expected_status": question["expected_status"],
                "application_status": status,
                "record_missing": record is None,
                "engineering_failure": failure,
                "complete_supported_answer": complete,
                "answer_point_coverage": tally(point_checks) if answerable else None,
                "condition_coverage": tally(condition_checks) if answerable else None,
                "condition_rubric_missing": item["required_conditions"] is None,
                "fact_support": tally(atom_checks) if answer else None,
                "fact_inventory_complete": item["fact_inventory_complete"],
                "retrieval_sufficient": item["retrieval_sufficient"],
                "false_refusal": None
                if not answerable or record is None
                else status == "insufficient_evidence",
                "unjustified_answer": unjustified if not answerable else None,
                "citation_source_check": citation_verdict,
            }
        )
    fact_metric = tally(facts)
    fact_metric["denominator_is_complete"] = not missing_inventories
    if missing_inventories:
        fact_metric["rate"] = None
        fact_metric["confirmed_fraction_of_all"] = None
    fact_metric["questions_with_unfinished_fact_inventory"] = missing_inventories
    condition_metric = tally(condition_values)
    condition_metric["denominator_is_complete"] = not missing_condition_rubrics
    if missing_condition_rubrics:
        condition_metric["rate"] = None
        condition_metric["confirmed_fraction_of_all"] = None
    condition_metric["questions_without_frozen_condition_rubric"] = missing_condition_rubrics
    return {
        "schema_version": SCHEMA,
        "review_source": review["review_source"],
        "reviewer": review.get("reviewer"),
        "reviewed_at": review.get("reviewed_at"),
        "bindings": context["bindings"],
        "selected_questions": len(items),
        "missing_result_question_ids": [
            key for key, record in context["records"].items() if record is None
        ],
        "metrics": {
            "complete_supported_answer": tally(complete_values),
            "fact_support": fact_metric,
            "answer_point_coverage": tally(point_values),
            "necessary_condition_coverage": condition_metric,
            "retrieval_sufficiency_on_answerable": tally(retrieval_values),
            "false_refusal_on_answerable": tally(false_refusals),
            "unjustified_answer_on_unanswerable": tally(unsupported_answers),
            "engineering_failure": tally(engineering_values),
            "citation_source_compliance": {
                **tally(citation_values),
                "citations_checked": citation_count,
            },
        },
        "resources": resource_totals(list(context["records"].values())),
        "per_question": per_question,
        "interpretation": [
            "AI and human reviews are separate reports, never merged into human acceptance.",
            "Pending is unknown; rate is null until all defined units are assessed.",
            "Confirmed fractions are lower bounds on selected units, not completed review scores.",
            "Fact support alone is not completeness; inspect answer and condition coverage.",
            "Error-premise questions with a source-supported rebuttal are answerable.",
            "Source/ownership compliance is independent from semantic support.",
        ],
    }


def write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("template", "summarize"))
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument(
        "--rubric", type=Path, help="Frozen optional condition annotation supplement"
    )
    parser.add_argument("--review-source", choices=("ai", "human"))
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        context = load_run(args.run, args.rubric)
        if args.command == "template":
            check(
                args.review_source is not None and args.review is None,
                "template requires --review-source and no --review",
            )
            output = template(context, args.review_source)
        else:
            check(
                args.review is not None and args.review_source is None,
                "summarize requires --review and no --review-source",
            )
            raw = args.review.read_bytes()
            output = summarize(context, decode(raw))
            output["review_file_sha256"] = digest(raw)
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True
        )
        output["scorer"] = {
            "code_sha": result.stdout.strip(),
            "script_sha256": digest(Path(__file__).read_bytes()),
            "created_at": datetime.now(UTC).isoformat(),
        }
        write_new(args.output, output)
    except (QualityError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Quality scoring stopped: {exc}\n")
    print(f"Wrote {args.output}; no network or model calls. Human acceptance remains separate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
