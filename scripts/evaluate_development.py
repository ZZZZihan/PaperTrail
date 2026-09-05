"""Freeze and diagnose the public development set through the local application only.

No provider client, credentials, expected answers, or hidden retry enters the application.
Every invocation creates a new write-once run; --prepare-only never contacts the app.
"""

import argparse
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2 as httpx
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"answered", "insufficient_evidence", "failed"}
REVIEW = {"status": "pending", "reviewer": None, "notes": None}


class DiagnosticError(Exception):
    """Controlled diagnostic message; never expose arbitrary HTTP response bodies."""


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize(text: str) -> str:
    return " ".join(text.split())


def write_once(path: Path, value: dict | list | bytes) -> None:
    """An artifact is never overwritten, even if a caller accidentally reuses its name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        value
        if isinstance(value, bytes)
        else (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    )
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o444)


def git_state() -> dict:
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
        )
        if result.returncode:
            raise DiagnosticError("Unable to record Git provenance")
        return result.stdout.strip()

    status = git("status", "--porcelain=v1", "--untracked-files=all")
    paths = git("ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    # Record hashes, never local configuration contents. Ignored data stays outside this list.
    hashes = {
        path: digest((ROOT / path).read_bytes())
        for path in paths
        if (ROOT / path).is_file() and not Path(path).name.startswith(".env")
    }
    return {
        "sha": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "dirty": bool(status),
        "status_porcelain": status.splitlines(),
        "working_file_sha256": hashes,
    }


def prepare(dataset: Path, run: Path) -> tuple[dict, list[dict], dict]:
    """Read each source once, validate the frozen contract, then snapshot those same bytes."""
    raw = {
        name: (dataset / name).read_bytes()
        for name in ("manifest.json", "questions.json", "checksums.sha256")
    }
    expected = {}
    for line in raw["checksums.sha256"].decode().splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})\s+\*?(manifest\.json|questions\.json)", line)
        if not match or match[2] in expected:
            raise DiagnosticError("Invalid or duplicated dataset checksum entry")
        expected[match[2]] = match[1]
    if set(expected) != {"manifest.json", "questions.json"}:
        raise DiagnosticError("Dataset checksum file must bind manifest.json and questions.json")
    for name, checksum in expected.items():
        if digest(raw[name]) != checksum:
            raise DiagnosticError(f"Dataset checksum mismatch: {name}")
    manifest = json.loads(raw["manifest.json"])
    questions = json.loads(raw["questions.json"])["questions"]
    if manifest["extractor"]["version"] != version("pypdf"):
        raise DiagnosticError("Installed pypdf version differs from the frozen source extractor")
    if len(manifest["papers"]) != 3 or len(questions) != 15:
        raise DiagnosticError("Development v0.1 requires exactly 3 papers and 15 questions")
    if len({q["id"] for q in questions}) != len(questions):
        raise DiagnosticError("Duplicate question IDs")
    sources = {}
    for paper in manifest["papers"]:
        paper_id = paper["id"]
        if not re.fullmatch(r"[a-z0-9_-]+", paper_id) or paper_id in sources:
            raise DiagnosticError("Invalid or duplicate paper ID")
        path = (ROOT / paper["local_path"]).resolve()
        if not path.is_relative_to(ROOT):
            raise DiagnosticError("Paper source must be within the project")
        pdf = path.read_bytes()
        if digest(pdf) != paper["sha256"]:
            raise DiagnosticError(f"Source PDF checksum mismatch: {paper_id}")
        pages = [
            (page.extract_text() or "").replace("\x00", "")
            for page in PdfReader(io.BytesIO(pdf)).pages
        ]
        hashes = [digest(page.encode()) for page in pages]
        if len(pages) != paper["page_count"] or hashes != paper["page_text_sha256"]:
            raise DiagnosticError(f"Source page extraction mismatch: {paper_id}")
        sources[paper_id] = {"paper": paper, "pdf": pdf, "pages": pages}
    for question in questions:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", question["id"]):
            raise DiagnosticError("Question ID cannot be used as an artifact filename")
        if question["paper_id"] not in sources:
            raise DiagnosticError(f"Unknown paper in question {question['id']}")
        pages = sources[question["paper_id"]]["pages"]
        for evidence in question.get("expected_evidence", []):
            index, quote = evidence["page_index"], evidence["quote"]
            if type(index) is not int or not 0 <= index < len(pages):
                raise DiagnosticError(f"Invalid expected evidence page: {question['id']}")
            if not normalize(quote) or normalize(quote) not in normalize(pages[index]):
                raise DiagnosticError(f"Expected quote absent from source: {question['id']}")
        if question["expected_status"] == "answered" and not question.get("expected_evidence"):
            raise DiagnosticError(f"Answerable question lacks source evidence: {question['id']}")
    counts = Counter(q["expected_status"] for q in questions)
    if counts != {"answered": 10, "insufficient_evidence": 5}:
        raise DiagnosticError("Expected development split is 10 answerable and 5 insufficient")
    for name, content in raw.items():
        write_once(run / "dataset" / name, content)
    for paper_id, source in sources.items():
        write_once(run / "sources" / f"{paper_id}.pdf", source["pdf"])
        write_once(run / "sources" / f"{paper_id}.pages.json", source["pages"])
    report = {
        "status": "passed",
        "completed_at": now(),
        "paper_count": len(sources),
        "question_count": len(questions),
        "expected_status_counts": dict(counts),
        "dataset_file_sha256": {name: digest(content) for name, content in raw.items()},
        "source_pdfs_and_pages_verified": True,
        "expected_quote_checks": sum(len(q.get("expected_evidence", [])) for q in questions),
        "ai_review": dict(REVIEW),
        "human_review": dict(REVIEW),
        "meaning": "Source/quote existence only; semantic and human review remain pending",
    }
    write_once(run / "preparation.json", report)
    return manifest, questions, sources


def retrieval_chunks(trace: dict, baseline: bool = False) -> list[dict] | None:
    """Versioned pipeline contract; absent trace is unknown, never inferred as zero recall."""
    retrieval = trace.get("retrieval", {})
    key = "baseline_selected" if baseline else "selected"
    chunks = retrieval.get(key)
    return chunks if isinstance(chunks, list) else None


def citation_check(result: dict, paper_id: str, source: dict) -> dict:
    """Independent checks use frozen source pages, not the pipeline's validation verdict."""
    chunks = retrieval_chunks(result.get("trace", {}))
    by_id = {chunk["chunk_id"]: chunk for chunk in chunks or []}
    errors = []
    checked = 0
    claims = result.get("claims", [])
    if result.get("paper_id") != paper_id:
        errors.append("response_paper_mismatch")
    if result.get("status") == "answered" and not claims:
        errors.append("answered_without_claims")
    for claim_index, claim in enumerate(claims):
        if not claim.get("citations"):
            errors.append(f"claim_{claim_index}:no_citations")
        for citation in claim.get("citations", []):
            checked += 1
            label = f"citation_{checked}"
            index, quote = citation.get("page_index"), citation.get("quote", "")
            if citation.get("paper_id") != paper_id:
                errors.append(f"{label}:wrong_paper")
            if type(index) is not int or not 0 <= index < len(source["pages"]):
                errors.append(f"{label}:invalid_page")
                continue
            if not normalize(quote) or normalize(quote) not in normalize(source["pages"][index]):
                errors.append(f"{label}:quote_not_in_source_page")
            chunk = by_id.get(citation.get("chunk_id"))
            if chunk is None:
                errors.append(f"{label}:chunk_not_in_retrieved_trace")
                continue
            start, end = chunk.get("start_char"), chunk.get("end_char")
            if (
                type(start) is not int
                or type(end) is not int
                or not (0 <= start < end <= len(source["pages"][index]))
            ):
                errors.append(f"{label}:invalid_chunk_offsets")
                continue
            text = source["pages"][index][start:end]
            identity = (
                f"{chunk.get('chunk_version')}|{paper_id}|{source['paper']['sha256']}|"
                f"{index}|{start}|{end}|{text}"
            )
            expected_id = "pt_" + digest(identity.encode())[:32]
            if (
                chunk.get("paper_id") != paper_id
                or chunk.get("page_index") != index
                or chunk.get("paper_sha256") != source["paper"]["sha256"]
                or chunk.get("text") != text
                or chunk.get("chunk_id") != expected_id
            ):
                errors.append(f"{label}:chunk_source_identity_mismatch")
            if normalize(quote) not in normalize(text):
                errors.append(f"{label}:quote_not_in_source_chunk")
    return {
        "status": "failed" if errors else "passed",
        "citations_checked": checked,
        "errors": errors,
        "semantic_support": "not_assessed_by_this_program",
    }


def retrieval_score(question: dict, chunks: list[dict] | None) -> dict:
    expected = question.get("expected_evidence", [])
    if chunks is None:
        return {"status": "unavailable", "page_recall": None, "evidence_recall": None}
    pages = sorted({chunk["page_index"] for chunk in chunks})
    expected_pages = sorted({item["page_index"] for item in expected})
    hits = [
        any(
            chunk["page_index"] == item["page_index"]
            and normalize(item["quote"]) in normalize(chunk.get("text", ""))
            for chunk in chunks
        )
        for item in expected
    ]
    return {
        "status": "measured" if expected else "not_applicable_no_reference_evidence",
        "retrieved_pages": pages,
        "expected_pages": expected_pages,
        "page_recall": len(set(pages) & set(expected_pages)) / len(expected_pages)
        if expected_pages
        else None,
        "evidence_recall": sum(hits) / len(hits) if hits else None,
        "evidence_hits": hits,
        "meaning": "Recall against AI-prepared reference anchors, not semantic correctness",
    }


def metrics(result: dict) -> dict:
    trace = result.get("trace", {})
    ledger = trace.get("ledger", {})
    ledger_calls = ledger.get("calls", [])
    calls = (
        [call.get("details", {}) for call in ledger_calls]
        if ledger_calls
        else (trace.get("calls", []))
    )
    totals = Counter()
    unknown_usage = 0
    cost_sources = sorted({call.get("cost_source") or "unknown" for call in calls}) or ["unknown"]
    for call in calls:
        usage = call.get("usage")
        if not isinstance(usage, dict) or not all(
            type(usage.get(key)) is int for key in ("prompt_tokens", "completion_tokens")
        ):
            unknown_usage += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = (usage or {}).get(key)
            if type(value) is int and value >= 0:
                totals[key] += value
    return {
        "calls": len(calls),
        "usage_known_subtotals": dict(totals),
        "unknown_usage_calls": unknown_usage,
        "elapsed_seconds": trace.get("elapsed_seconds"),
        "estimated_cost_known_subtotal": ledger.get(
            "known_cost_subtotal", ledger.get("estimated_cost")
        ),
        "estimated_cost_total": ledger.get("estimated_cost")
        if ledger.get("unknown_cost_calls", len(calls)) == 0
        else None,
        "unknown_cost_calls": ledger.get("unknown_cost_calls", len(calls)),
        "currency": ledger.get("currency"),
        "cost_source": cost_sources[0] if len(cost_sources) == 1 else "mixed",
        "cost_sources": cost_sources,
        "cost_note": "Known subtotals exclude unknown charges and are not a provider invoice",
    }


def request_json(client: httpx.Client, method: str, path: str, **kwargs):
    try:
        response = client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise DiagnosticError(f"Application HTTP transport failed: {type(exc).__name__}") from exc
    if response.status_code >= 400 or response.is_redirect:
        raise DiagnosticError(f"Application HTTP {response.status_code} for {method} {path}")
    try:
        return response.json()
    except ValueError as exc:
        raise DiagnosticError("Application returned invalid JSON") from exc


def upload_source(client: httpx.Client, source: dict) -> dict:
    uploaded = request_json(
        client,
        "POST",
        "/api/papers",
        files={"file": (f"{source['paper']['id']}.pdf", source["pdf"], "application/pdf")},
    )
    paper = uploaded["paper"]
    if paper["sha256"] != source["paper"]["sha256"] or (
        paper["page_count"] != len(source["pages"])
    ):
        raise DiagnosticError("Uploaded paper identity differs from frozen source")
    for index, expected in enumerate(source["pages"]):
        page = request_json(client, "GET", f"/api/papers/{paper['id']}/pages/{index}")
        if (
            page["paper_id"] != paper["id"]
            or page["page_index"] != index
            or (page["text"] != expected)
        ):
            raise DiagnosticError("Stored application page differs from frozen extraction")
    return uploaded


def execute_question(client, question, source, paper_id, directory, poll_timeout) -> dict:
    started = time.monotonic()
    request_id = str(uuid4())
    record = {
        "question_id": question["id"],
        "input": question,
        "source": source["paper"],
        "application_paper_id": paper_id,
        "request_id": request_id,
        "started_at": now(),
        "ai_review": dict(REVIEW),
        "human_review": dict(REVIEW),
    }
    write_once(directory / f"{question['id']}.started.json", record)
    try:
        # Deliberately send exactly these two fields. No answer keys, IDs, or reference evidence.
        result = request_json(
            client,
            "POST",
            f"/api/papers/{paper_id}/questions",
            json={
                "question": question["question"],
                "request_id": request_id,
            },
        )
        write_once(directory / f"{question['id']}.submitted.json", result)
        record["application_question_id"] = result["id"]
        deadline = time.monotonic() + poll_timeout
        while result["status"] not in TERMINAL:
            if time.monotonic() >= deadline:
                raise DiagnosticError(
                    "Polling deadline exceeded; inspect saved application question ID"
                )
            time.sleep(0.5)
            result = request_json(client, "GET", f"/api/papers/{paper_id}/questions/{result['id']}")
        record["result"] = result
        record["metrics"] = metrics(result)
        write_once(directory / f"{question['id']}.terminal.json", result)
        history = request_json(client, "GET", f"/api/papers/{paper_id}/questions")
        history_row = next((row for row in history if row["id"] == result["id"]), None)
        record["history_check"] = {
            "present": history_row is not None,
            "identical": history_row == result,
        }
        record["deterministic_citation_check"] = citation_check(result, paper_id, source)
        record["retrieval_comparison"] = {
            "raw_question_bm25": retrieval_score(question, retrieval_chunks(result["trace"], True)),
            "expanded_query_bm25": retrieval_score(question, retrieval_chunks(result["trace"])),
        }
        record["metrics"] = metrics(result)
        record["expected_status_match"] = result["status"] == question["expected_status"]
        record["pipeline_support_check"] = {
            "status": result.get("support_status"),
            "meaning": "Product guard, not independent quality grading or human acceptance",
        }
        record["runner_status"] = "completed"
    except Exception as exc:
        record["runner_status"] = "failed"
        record["error"] = str(exc) if isinstance(exc, DiagnosticError) else type(exc).__name__
        record["calls_may_have_occurred"] = True
        record["retry_policy"] = "No automatic retry; use --ids in a new run after inspection"
    record["completed_at"] = now()
    record["wall_elapsed_seconds"] = round(time.monotonic() - started, 3)
    write_once(directory / f"{question['id']}.result.json", record)
    return record


def aggregate(records: list[dict]) -> dict:
    currencies = {}
    tokens = Counter()
    calls = 0
    unknown_cost = 0
    unknown_usage = 0
    unmeasured = 0
    cost_sources = set()
    for record in records:
        measured = record.get("metrics")
        if measured is None:
            unmeasured += 1
            continue
        cost_sources.update(
            measured.get("cost_sources") or [measured.get("cost_source", "unknown")]
        )
        calls += measured["calls"]
        tokens.update(measured["usage_known_subtotals"])
        unknown_cost += measured["unknown_cost_calls"]
        unknown_usage += measured["unknown_usage_calls"]
        if measured["currency"] and measured["estimated_cost_known_subtotal"] is not None:
            try:
                value = Decimal(measured["estimated_cost_known_subtotal"])
                if value.is_finite() and value >= 0:
                    currency = measured["currency"]
                    currencies[currency] = currencies.get(currency, Decimal(0)) + value
            except InvalidOperation:
                unknown_cost += measured["calls"]
    return {
        "questions_recorded": len(records),
        "runner_statuses": dict(Counter(r["runner_status"] for r in records)),
        "application_statuses": dict(
            Counter(r.get("result", {}).get("status", "unknown") for r in records)
        ),
        "status_matches": sum(r.get("expected_status_match", False) for r in records),
        "recorded_calls": calls,
        "usage_known_subtotals": dict(tokens),
        "unknown_usage_calls": unknown_usage,
        "unknown_cost_calls": unknown_cost,
        "unmeasured_questions": unmeasured,
        "estimated_cost_known_subtotals": {key: str(value) for key, value in currencies.items()},
        "cost_source": next(iter(cost_sources))
        if len(cost_sources) == 1
        else ("mixed" if cost_sources else "unknown"),
        "cost_sources": sorted(cost_sources) or ["unknown"],
        "cost_note": "Known subtotals exclude unknown calls and unmeasured questions",
        "estimated_cost_totals": {key: str(value) for key, value in currencies.items()}
        if not unknown_cost and not unmeasured
        else None,
        "ai_semantic_quality_review": "pending",
        "human_review": "pending",
        "warning": "Status agreement and valid citations do not establish answer correctness",
    }


def local_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        )
    ):
        raise argparse.ArgumentTypeError(
            "Use the local application HTTP origin without credentials"
        )
    return value.rstrip("/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--prepare-only", action="store_true", help="Verify/snapshot sources; no app/model calls"
    )
    mode.add_argument(
        "--run",
        action="store_true",
        help="Explicitly submit selected questions through app budget guard",
    )
    parser.add_argument("--base-url", type=local_url, default="http://127.0.0.1:8000")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals/development-v0.1")
    parser.add_argument(
        "--ids", help="Comma-separated question IDs; every retry is a new immutable run"
    )
    parser.add_argument("--poll-timeout", type=float, default=360)
    args = parser.parse_args()
    if not 1 <= args.poll_timeout <= 1800:
        parser.error("--poll-timeout must be 1 to 1800 seconds")
    run = (
        ROOT
        / "data/diagnostics/runs"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid4().hex)
    )
    run.mkdir(parents=True, exist_ok=False)
    print(f"Run artifacts: {run}", flush=True)
    write_once(
        run / "invocation.json",
        {
            "schema_version": 1,
            "started_at": now(),
            "mode": "run" if args.run else "prepare_only",
            "command": shlex.join([sys.executable, *sys.argv]),
            "cwd": str(Path.cwd()),
            "base_url": args.base_url,
            "git": git_state(),
            "python": sys.version,
            "packages": {name: version(name) for name in ("pypdf", "httpx2", "papertrail")},
            "retry_policy": "No automatic POST/provider retry",
            "ai_review": dict(REVIEW),
            "human_review": dict(REVIEW),
        },
    )
    records = []
    failed = False
    try:
        _, questions, sources = prepare(args.dataset, run)
        if args.ids:
            selected = set(args.ids.split(","))
            if selected - {q["id"] for q in questions}:
                raise DiagnosticError("--ids contains an unknown question ID")
            questions = [q for q in questions if q["id"] in selected]
        write_once(run / "selection.json", {"question_ids": [q["id"] for q in questions]})
        if args.run:
            with httpx.Client(
                base_url=args.base_url, timeout=190, follow_redirects=False
            ) as client:
                try:
                    config = request_json(client, "GET", "/api/config")
                    write_once(run / "application_config.json", config)
                except DiagnosticError as exc:
                    write_once(run / "application_config_failure.json", {"error": str(exc)})
                uploaded = {}
                upload_errors = {}
                for question in questions:
                    source_id = question["paper_id"]
                    if source_id not in uploaded and source_id not in upload_errors:
                        try:
                            uploaded[source_id] = upload_source(client, sources[source_id])
                            write_once(run / "uploads" / f"{source_id}.json", uploaded[source_id])
                        except Exception as exc:
                            upload_errors[source_id] = (
                                str(exc) if isinstance(exc, DiagnosticError) else type(exc).__name__
                            )
                    if source_id in upload_errors:
                        record = {
                            "question_id": question["id"],
                            "input": question,
                            "runner_status": "failed",
                            "error": upload_errors[source_id],
                            "stage": "upload_or_source_verification",
                            "calls_may_have_occurred": False,
                            "ai_review": dict(REVIEW),
                            "human_review": dict(REVIEW),
                        }
                        write_once(run / "questions" / f"{question['id']}.result.json", record)
                    else:
                        record = execute_question(
                            client,
                            question,
                            sources[source_id],
                            uploaded[source_id]["paper"]["id"],
                            run / "questions",
                            args.poll_timeout,
                        )
                    records.append(record)
                    print(
                        f"{question['id']}: "
                        f"{record.get('result', {}).get('status', record['runner_status'])}",
                        flush=True,
                    )
        failed = any(
            r["runner_status"] == "failed"
            or r.get("result", {}).get("status") == "failed"
            or r.get("deterministic_citation_check", {}).get("status") == "failed"
            or not r.get("history_check", {}).get("identical", True)
            for r in records
        )
    except Exception as exc:
        failed = True
        write_once(
            run / "failure.json",
            {
                "error": str(exc) if isinstance(exc, DiagnosticError) else type(exc).__name__,
                "at": now(),
                "no_automatic_retry": True,
            },
        )
    finally:
        write_once(
            run / "summary.json",
            {
                **aggregate(records),
                "completed_at": now(),
                "mode": "run" if args.run else "prepare_only",
                "runner_completed_without_error": not failed,
            },
        )
        artifacts = sorted(path for path in run.rglob("*") if path.is_file())
        checksums = "".join(
            f"{digest(path.read_bytes())}  {path.relative_to(run)}\n" for path in artifacts
        )
        write_once(run / "artifacts.sha256", checksums.encode())
    print(
        "Finished; review summary.json and each question result. Human review remains pending.",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
