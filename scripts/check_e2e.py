"""Run repeatable OFFLINE API E2E checks in a disposable application and database.

Run: uv run --locked python scripts/check_e2e.py [--evidence /absolute/output.json]
Requires the existing local web build and PostgreSQL 17 binaries. This exercises
real HTTP, PDF parsing, persistence, and restart behavior with an injected model.
It does not test a browser, a real model, or scholarly answer quality.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx2 as httpx
from browser_fixture import FIXTURE_MODEL, OMEGA

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"answered", "partial_answer", "insufficient_evidence", "failed"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


class ApiChecks:
    def __init__(self, base_url: str, timeout: float, evidence: dict):
        self.client = httpx.Client(base_url=base_url, timeout=timeout, trust_env=False)
        self.timeout = timeout
        self.evidence = evidence

    def passed(self, name: str, **details) -> None:
        self.evidence["checks"].append({"name": name, "status": "PASS", **details})
        print(f"API E2E PASS: {name}", flush=True)

    def request(self, method: str, path: str, expected: int = 200, **kwargs) -> httpx.Response:
        # A loopback URL alone cannot distinguish this fixture from the user's app.
        # Recheck the marker before EVERY write, including after a service restart.
        if method not in {"GET", "HEAD"}:
            health = self.client.get("/health")
            require(
                health.status_code == 200
                and health.headers.get("X-PaperTrail-Fixture") == FIXTURE_MODEL,
                "Refusing an API write: the OFFLINE fixture marker is absent.",
            )
        response = self.client.request(method, path, **kwargs)
        require(
            response.headers.get("X-PaperTrail-Fixture") == FIXTURE_MODEL,
            f"Fixture marker absent from {method} {path}.",
        )
        require(
            response.status_code == expected,
            f"{method} {path}: expected HTTP {expected}, received {response.status_code}.",
        )
        return response

    def error(self, method: str, path: str, expected: int, code: str, **kwargs) -> None:
        result = self.request(method, path, expected, **kwargs).json()
        require(result.get("error", {}).get("code") == code, f"Expected error code {code}.")

    def wait_ready(self, supervisor: subprocess.Popen) -> None:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            require(supervisor.poll() is None, "Fixture supervisor exited before API readiness.")
            try:
                response = self.client.get("/health")
                if response.status_code == 200:
                    require(
                        response.headers.get("X-PaperTrail-Fixture") == FIXTURE_MODEL,
                        "Refusing to use an API without the OFFLINE fixture marker.",
                    )
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise RuntimeError("Fixture API did not become ready before the deadline.")

    def wait_question(self, path: str, question_id: str) -> dict:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            row = self.request("GET", f"{path}/{question_id}").json()
            if row["status"] in TERMINAL:
                return row
            time.sleep(0.05)
        raise RuntimeError(f"Question {question_id} did not reach a terminal state.")

    def snapshot(self, paper_id: str, question_ids: list[str]) -> dict:
        path = f"/api/papers/{paper_id}"
        file = self.request("GET", path + "/file")
        return {
            "papers": self.request("GET", "/api/papers").json(),
            "paper": self.request("GET", path).json(),
            "pages": [self.request("GET", f"{path}/pages/{index}").json() for index in range(3)],
            "history": self.request("GET", path + "/questions").json(),
            "questions": {
                question_id: self.request("GET", f"{path}/questions/{question_id}").json()
                for question_id in question_ids
            },
            "file_sha256": hashlib.sha256(file.content).hexdigest(),
            "file_size": len(file.content),
        }

    def run_cases(self, pdf: bytes) -> tuple[str, list[str]]:
        config = self.request("GET", "/api/config").json()
        require(config["model"]["model"] == FIXTURE_MODEL, "Fixture model identity mismatch.")
        require(self.request("GET", "/api/papers").json() == [], "Fixture library is not empty.")
        self.passed("OFFLINE marker, model identity, and empty isolated library")

        result = self.request(
            "POST",
            "/api/papers",
            201,
            files={"file": ("OFFLINE-api-e2e.pdf", pdf, "application/pdf")},
        ).json()
        paper = result["paper"]
        paper_id = paper["id"]
        path = f"/api/papers/{paper_id}"
        require(not result["duplicate"] and paper["page_count"] == 3, "Upload metadata mismatch.")
        require(paper["sha256"] == hashlib.sha256(pdf).hexdigest(), "Upload hash mismatch.")
        detail = self.request("GET", path).json()
        pages = [self.request("GET", f"{path}/pages/{index}").json() for index in range(3)]
        require(detail["empty_pages"] == [1], "Empty physical page index was not preserved.")
        require(
            "Alpha" in pages[0]["text"] and pages[1]["text"] == "" and OMEGA in pages[2]["text"],
            "Extracted text no longer maps to the original physical pages.",
        )
        self.passed(
            "HTTP PDF upload and three physical pages including empty page", paper_id=paper_id
        )

        duplicate = self.request(
            "POST", "/api/papers", files={"file": ("renamed.pdf", pdf, "application/pdf")}
        ).json()
        require(
            duplicate["duplicate"] and duplicate["paper"] == paper, "Duplicate upload changed ID."
        )
        self.passed("Identical PDF bytes reuse the original paper and ID")

        self.error(
            "POST",
            "/api/papers",
            422,
            "invalid_pdf",
            files={"file": ("broken.pdf", b"not a PDF", "application/pdf")},
        )
        self.error("POST", "/api/papers", 422, "invalid_request", json={})
        self.error("GET", "/api/papers?offset=-1", 422, "invalid_request")
        self.error("GET", "/api/papers/not-a-uuid", 422, "invalid_request")
        self.error("GET", path + "/pages/not-an-index", 422, "invalid_request")
        self.error("GET", path + "/pages/3", 404, "page_not_found")
        self.error("GET", path + "/pages/-1", 404, "page_not_found")
        questions_path = path + "/questions"
        for question in (" ", "x" * 2001, "invalid\0text"):
            self.error(
                "POST",
                questions_path,
                422,
                "invalid_request",
                json={"question": question, "request_id": str(uuid4())},
            )
        self.error(
            "POST",
            questions_path,
            422,
            "invalid_request",
            json={"question": "正常", "request_id": "not-a-uuid"},
        )
        self.error(
            "POST",
            f"/api/papers/{uuid4()}/questions",
            404,
            "not_found",
            json={"question": "正常", "request_id": str(uuid4())},
        )
        require(self.request("GET", questions_path).json() == [], "Invalid input created a task.")
        require(len(self.request("GET", "/api/papers").json()) == 1, "Invalid PDF created a paper.")
        self.passed("Invalid PDF, malformed requests, missing resources, and no spurious records")

        rows, bodies = [], []
        scenarios = (
            ("正常", "answered", None, 3),
            ("部分回答", "partial_answer", None, 3),
            ("证据不足", "insufficient_evidence", None, 3),
            ("无效引用", "failed", "invalid_citation", 2),
            ("超时", "failed", "model_timeout", 1),
            ("失败", "failed", "model_failure", 1),
        )
        for keyword, status, code, call_count in scenarios:
            body = {"question": f"{keyword}：Omega 位于哪一页？", "request_id": str(uuid4())}
            submitted = self.request("POST", questions_path, 202, json=body).json()
            row = self.wait_question(questions_path, submitted["id"])
            require(
                row["status"] == status and row["error_code"] == code, f"Wrong {keyword} result."
            )
            calls = row["trace"]["calls"]
            require(
                len(calls) == row["trace"]["call_count"] == call_count
                and all(call["model"] == FIXTURE_MODEL for call in calls),
                f"Wrong {keyword} call count or model identity.",
            )
            if status in {"answered", "partial_answer"}:
                coverage = "complete" if status == "answered" else "partial"
                require(row["coverage"]["status"] == coverage, "Coverage status mismatch.")
                if status == "partial_answer":
                    require(
                        any(not item["covered"] for item in row["coverage"]["items"]),
                        "Partial answer lost its missing requirement.",
                    )
                for claim in row["claims"]:
                    for citation in claim["citations"]:
                        require(
                            citation["paper_id"] == paper_id
                            and citation["page_index"] == 2
                            and citation["quote"] in pages[2]["text"],
                            "Citation does not match the current paper's physical page three.",
                        )
            else:
                require(row["claims"] == [], "Failed/insufficient task published claims.")
            rows.append(row)
            bodies.append(body)
            self.passed(
                f"Question outcome: {keyword} -> {status}", error_code=code, calls=call_count
            )

        reused = self.request("POST", questions_path, 202, json=bodies[0]).json()
        require(reused == rows[0], "Same request ID changed its completed result or trace.")
        self.error(
            "POST",
            questions_path,
            409,
            "request_conflict",
            json={**bodies[0], "question": "different question with an existing request ID"},
        )
        history = self.request("GET", questions_path).json()
        require(
            {row["id"] for row in history} == {row["id"] for row in rows}, "History IDs changed."
        )
        require(len(history) == len(rows), "Idempotent/conflicting request created extra work.")
        require(
            {row["id"]: row for row in history} == {row["id"]: row for row in rows},
            "History differs from terminal task objects.",
        )
        self.error(
            "GET", f"/api/papers/{uuid4()}/questions/{rows[0]['id']}", 404, "question_not_found"
        )
        self.passed("Request ID reuse, conflict, history equality, and question paper scope")

        original = self.request("GET", path + "/file")
        require(original.content == pdf, "Downloaded PDF differs from uploaded bytes.")
        require(original.headers["content-type"] == "application/pdf", "PDF content type changed.")
        require(original.headers.get("x-content-type-options") == "nosniff", "Missing nosniff.")
        ranged = self.request("GET", path + "/file", 206, headers={"Range": "bytes=0-31"})
        require(ranged.content == pdf[:32], "PDF byte-range response differs from original.")
        self.passed("Original PDF byte equality and HTTP byte-range reading")
        return paper_id, [row["id"] for row in rows]


def wait_metadata(process: subprocess.Popen, log: Path, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for line in log.read_text().splitlines():
            if line.startswith("{"):
                value = json.loads(line)
                if value.get("mode") == FIXTURE_MODEL:
                    require(value["supervisor_pid"] == process.pid, "Supervisor identity mismatch.")
                    return value
        require(process.poll() is None, "Fixture exited before emitting startup metadata.")
        time.sleep(0.1)
    raise RuntimeError("Fixture did not emit startup metadata before the deadline.")


def fixture_root(metadata: dict) -> Path:
    root = Path(metadata["temporary_root"])
    require(root.name.startswith("papertrail-OFFLINE-browser-"), "Unexpected fixture root.")
    require(
        (root / "OFFLINE-FIXTURE-MARKER").read_text() == FIXTURE_MODEL,
        "Private fixture directory marker is missing.",
    )
    return root


def restart(checks: ApiChecks, process: subprocess.Popen, log: Path, metadata: dict) -> None:
    root = fixture_root(metadata)
    pid_file = root / "postgres" / "postmaster.pid"
    old_database_pid = int(pid_file.read_text().splitlines()[0])
    process.send_signal(signal.SIGUSR2)
    deadline = time.monotonic() + checks.timeout
    new_server_pid = None
    while time.monotonic() < deadline:
        require(process.poll() is None, "Fixture exited during application/database restart.")
        matches = re.findall(r"OFFLINE fixture restarted; server_pid=(\d+)", log.read_text())
        if matches and int(matches[-1]) != metadata["server_pid"]:
            new_server_pid = int(matches[-1])
            break
        time.sleep(0.1)
    require(new_server_pid is not None, "Application restart was not observed.")
    checks.wait_ready(process)
    new_database_pid = int(pid_file.read_text().splitlines()[0])
    require(new_database_pid != old_database_pid, "Private PostgreSQL did not restart.")
    checks.passed(
        "SIGUSR2 restarts both application and private PostgreSQL",
        old_server_pid=metadata["server_pid"],
        new_server_pid=new_server_pid,
        old_database_pid=old_database_pid,
        new_database_pid=new_database_pid,
    )


def cleanup(process: subprocess.Popen, metadata: dict | None) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            # The process group belongs exclusively to this disposable supervisor.
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    # An externally killed supervisor cannot run its finally block. Its Uvicorn
    # child can still own this session's process group after the leader has exited.
    # Clean that group independently of the supervisor's return code before
    # removing any database or library files.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    else:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if metadata and Path(metadata["temporary_root"]).exists():
        from postgres import stop

        root = fixture_root(metadata)
        stop(root / "postgres")
        shutil.rmtree(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence", type=Path, help="Optional JSON evidence file, including failures"
    )
    parser.add_argument(
        "--port", type=int, default=0, help="Local port; default 0 selects a free port"
    )
    parser.add_argument("--timeout", type=float, default=30, help="Per-stage deadline in seconds")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535 or not 0 < args.timeout <= 120:
        parser.error("Use a port in 0..65535 and a timeout in (0, 120].")
    with socket.socket() as available:
        available.bind(("127.0.0.1", args.port))
        port = available.getsockname()[1]
    evidence = {
        "suite": "PaperTrail OFFLINE API E2E",
        "started_at": datetime.now(UTC).isoformat(),
        "real_model_calls": 0,
        "browser_test": False,
        "quality_acceptance": False,
        "checks": [],
        "status": "RUNNING",
        "api_base_url": f"http://127.0.0.1:{port}",
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_script_sha256": hashlib.sha256(
            (ROOT / "scripts/browser_fixture.py").read_bytes()
        ).hexdigest(),
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
    }
    checks = ApiChecks(f"http://127.0.0.1:{port}", args.timeout, evidence)
    process, metadata = None, None
    with tempfile.TemporaryDirectory(prefix="papertrail-api-e2e-") as temporary:
        log = Path(temporary) / "fixture.log"
        try:
            with log.open("w") as output:
                process = subprocess.Popen(
                    [sys.executable, str(ROOT / "scripts/browser_fixture.py"), "--port", str(port)],
                    cwd=ROOT,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env={
                        key: value
                        for key, value in os.environ.items()
                        if not key.startswith("PAPERTRAIL_") or key == "PAPERTRAIL_PG_BIN"
                    },
                )
            metadata = wait_metadata(process, log, args.timeout)
            root = fixture_root(metadata)
            pdf_path = Path(metadata["synthetic_pdf"])
            require(pdf_path.parent == root, "Synthetic PDF is outside the private fixture root.")
            checks.wait_ready(process)
            paper_id, question_ids = checks.run_cases(pdf_path.read_bytes())
            before = checks.snapshot(paper_id, question_ids)
            restart(checks, process, log, metadata)
            after = checks.snapshot(paper_id, question_ids)
            require(
                after == before, "Restart changed persisted objects, PDF, or model-call traces."
            )
            call_count = sum(len(row["trace"]["calls"]) for row in after["questions"].values())
            checks.passed(
                "All paper/page/history/question objects, PDF, and call traces survive restart",
                questions=len(question_ids),
                offline_calls=call_count,
                snapshot_sha256=digest(after),
            )
            evidence.update(status="PASS", snapshot=after)
        except (Exception, KeyboardInterrupt) as error:
            evidence.update(status="FAIL", error=f"{type(error).__name__}: {error}")
            evidence["fixture_log_tail"] = (
                log.read_text().splitlines()[-25:] if log.exists() else []
            )
            print(f"API E2E FAIL: {evidence['error']}", file=sys.stderr, flush=True)
        finally:
            checks.client.close()
            try:
                if process is not None:
                    cleanup(process, metadata)
                require(
                    not metadata or not Path(metadata["temporary_root"]).exists(),
                    "Private fixture files remain after cleanup.",
                )
                checks.passed("Disposable application, PostgreSQL, and library cleaned up")
            except Exception as error:
                evidence.update(status="FAIL", cleanup_error=f"{type(error).__name__}: {error}")
                print(f"API E2E FAIL: cleanup: {error}", file=sys.stderr, flush=True)
            evidence["completed_at"] = datetime.now(UTC).isoformat()
            if args.evidence:
                args.evidence.parent.mkdir(parents=True, exist_ok=True)
                args.evidence.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(f"API E2E {evidence['status']}: {len(evidence['checks'])} checks; real model calls: 0.")
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":

    def interrupt(_number, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    raise SystemExit(main())
