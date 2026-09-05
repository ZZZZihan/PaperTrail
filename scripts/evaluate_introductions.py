"""Generate introduction demos via the local app, preserving each run and source.

Uses the existing frozen public development sources and application call ledger.
There is no direct provider call and no automatic retry. Successful cached results
are read as-is; their historical model calls are not counted as new requests.
"""

import argparse
import json
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx2 as httpx
from evaluate_development import (
    ROOT,
    TERMINAL,
    DiagnosticError,
    digest,
    git_state,
    local_url,
    metrics,
    now,
    prepare,
    request_json,
    upload_source,
    write_once,
)

from papertrail.introduction import build_introduction_chunks
from papertrail.qa import normalize_quote


def check_citations(result: dict, paper: dict, source: dict) -> dict:
    """Rebuild authoritative chunks independently of model/result trace contents."""
    intro = result.get("introduction")
    if result.get("status") != "answered" or not isinstance(intro, dict):
        return {"status": "not_applicable", "citations_checked": 0, "errors": []}
    chunks = build_introduction_chunks(
        paper["id"],
        paper["sha256"],
        [{"page_index": i, "text": page} for i, page in enumerate(source["pages"])],
    )
    allowed = {chunk["chunk_id"]: chunk for chunk in chunks}
    errors = []
    count = 0
    claims = [
        intro[key]
        for key in ("summary", "problem", "contribution", "mechanism", "evidence_and_limits")
    ]
    claims += [
        {"text": f"{t['term']}：{t['explanation']}", "citations": t["citations"]}
        for t in intro["terms"]
    ]
    claims += intro.get("learning_aids", [])
    for index, claim in enumerate(claims):
        if not claim.get("citations"):
            errors.append(f"claim_{index}:missing_citations")
        for citation in claim.get("citations", []):
            count += 1
            chunk = allowed.get(citation.get("chunk_id"))
            quote = citation.get("quote", "")
            if (
                chunk is None
                or citation.get("paper_id") != paper["id"]
                or citation.get("page_index") != chunk["page_index"]
                or quote != chunk["text"]
                or not normalize_quote(quote)
                or normalize_quote(quote) not in normalize_quote(chunk["text"])
            ):
                errors.append(f"citation_{count}:source_mismatch")
    return {
        "status": "failed" if errors else "passed",
        "citations_checked": count,
        "errors": errors,
        "semantic_support": "not_assessed_by_this_program",
    }


def execute(client, source: dict, run, *, refresh_if_outdated=False) -> dict:
    source_id = source["paper"]["id"]
    directory = run / source_id
    paper = upload_source(client, source)["paper"]
    write_once(directory / "paper.json", paper)
    path = f"/api/papers/{paper['id']}/introduction"
    previous = request_json(client, "GET", path)
    request_id = str(uuid4())
    write_once(
        directory / "request.json",
        {
            "request_id": request_id,
            "paper_id": paper["id"],
            "started_at": now(),
            "previous_id": previous["id"] if previous else None,
            "rubric": "docs/paper-introduction-demo.md",
            "refresh_if_outdated": refresh_if_outdated,
        },
    )
    result = request_json(
        client,
        "POST",
        path,
        json={
            "request_id": request_id,
            "refresh_if_outdated": refresh_if_outdated,
        },
    )
    write_once(directory / "submitted.json", result)
    reused = bool(previous and previous["id"] == result["id"])
    deadline = time.monotonic() + 240
    while result["status"] not in TERMINAL:
        if time.monotonic() >= deadline:
            raise DiagnosticError("Introduction polling timeout; inspect saved task before retry")
        time.sleep(1)
        result = request_json(client, "GET", path)
    write_once(directory / "terminal.json", result)
    persisted = request_json(client, "GET", path)
    repeated = request_json(client, "POST", path, json={"request_id": request_id})
    historical = request_json(client, "GET", f"/api/papers/{paper['id']}/questions")
    verification = {
        "source_id": source_id,
        "paper_id": paper["id"],
        "task_id": result["id"],
        "application_status": result["status"],
        "error_code": result.get("error_code"),
        "reused_existing_task": reused,
        "persisted_identical": persisted == result,
        "same_request_identical": repeated == result,
        "excluded_from_qa_history": all(row["id"] != result["id"] for row in historical),
        "citation_check": check_citations(result, paper, source),
        "task_historical_metrics": metrics(result),
        "ai_quality_review": "pending",
        "human_review": "pending",
        "completed_at": now(),
    }
    write_once(directory / "verification.json", verification)
    print(
        json.dumps(
            {
                k: verification[k]
                for k in ("source_id", "application_status", "error_code", "reused_existing_task")
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return verification


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--ids", default="reflexion,react,toolformer")
    parser.add_argument("--refresh-if-outdated", action="store_true")
    parser.add_argument("--base-url", type=local_url, default="http://127.0.0.1:8000")
    args = parser.parse_args()
    run = (
        ROOT
        / "data/diagnostics/introduction-runs"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid4().hex)
    )
    run.mkdir(parents=True, exist_ok=False)
    print(f"Run artifacts: {run}", flush=True)
    write_once(
        run / "invocation.json",
        {
            "started_at": now(),
            "git": git_state(),
            "mode": "run" if args.run else "prepare_only",
            "ids": args.ids.split(","),
            "refresh_if_outdated": args.refresh_if_outdated,
            "base_url": args.base_url,
            "retry_policy": "No automatic retry; every invocation preserves new artifacts",
        },
    )
    records = []
    failed = False
    try:
        _, _, sources = prepare(ROOT / "evals/development-v0.1", run)
        selected = args.ids.split(",")
        if len(set(selected)) != len(selected) or any(i not in sources for i in selected):
            raise DiagnosticError("Unknown or duplicate source ID")
        if args.run:
            with httpx.Client(
                base_url=args.base_url, timeout=190, follow_redirects=False
            ) as client:
                for source_id in selected:
                    record = execute(
                        client,
                        sources[source_id],
                        run,
                        refresh_if_outdated=args.refresh_if_outdated,
                    )
                    records.append(record)
                    if (
                        record["application_status"] != "answered"
                        or record["citation_check"]["status"] != "passed"
                        or not all(
                            record[key]
                            for key in (
                                "persisted_identical",
                                "same_request_identical",
                                "excluded_from_qa_history",
                            )
                        )
                    ):
                        failed = True
    except Exception as exc:
        failed = True
        write_once(
            run / "failure.json",
            {
                "error": str(exc) if isinstance(exc, DiagnosticError) else type(exc).__name__,
                "calls_may_have_occurred": bool(args.run),
                "no_automatic_retry": True,
            },
        )
    finally:
        write_once(
            run / "summary.json",
            {
                "completed_at": now(),
                "records": records,
                "run_passed": not failed,
                "human_review": "pending",
                "ai_quality_review": "pending",
            },
        )
        write_once(
            run / "artifacts.sha256",
            "".join(
                f"{digest(path.read_bytes())}  {path.relative_to(run)}\n"
                for path in sorted(run.rglob("*"))
                if path.is_file()
            ).encode(),
        )
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
