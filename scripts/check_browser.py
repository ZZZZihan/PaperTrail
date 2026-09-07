"""Run the GPU-request browser regression against a disposable OFFLINE app and PostgreSQL.

Run: make check-browser
Requires npm ci --prefix web, the local web build, PostgreSQL 17, and Playwright
Chromium (install once with: web/node_modules/.bin/playwright install chromium).
No existing application, library, provider credentials, or quota ledger is used.
"""

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from browser_fixture import FIXTURE_MODEL, GPU_QUESTION, GPU_REQUIREMENTS
from check_e2e import ROOT, ApiChecks, cleanup, fixture_root, require, wait_metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence", type=Path, default=ROOT / "output/playwright/quality/latest.json"
    )
    args = parser.parse_args()
    evidence_path = args.evidence.resolve()
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    evidence = {
        "suite": "PaperTrail OFFLINE GPU-request browser regression",
        "started_at": datetime.now(UTC).isoformat(),
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in (
                "scripts/check_browser.py",
                "scripts/browser_quality_regression.mjs",
                "scripts/browser_fixture.py",
                "web/src/QuestionPanel.tsx",
            )
        },
        "real_model_calls": 0,
        "quality_acceptance": False,
        "checks": [],
        "status": "RUNNING",
    }
    base_url = f"http://127.0.0.1:{port}"
    checks = ApiChecks(base_url, 30, evidence)
    process, metadata = None, None
    with tempfile.TemporaryDirectory(prefix="papertrail-browser-quality-") as temporary:
        log = Path(temporary) / "fixture.log"
        try:
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("PAPERTRAIL_") or key == "PAPERTRAIL_PG_BIN"
            }
            with log.open("w") as output:
                process = subprocess.Popen(
                    [sys.executable, str(ROOT / "scripts/browser_fixture.py"), "--port", str(port)],
                    cwd=ROOT,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=environment,
                )
            metadata = wait_metadata(process, log, 30)
            root = fixture_root(metadata)
            pdf_path = Path(metadata["synthetic_pdf"])
            require(pdf_path.parent == root, "Synthetic PDF is outside the private fixture root.")
            checks.wait_ready(process)
            result_path = Path(temporary) / "browser-result.json"
            result = subprocess.run(
                ["node", str(ROOT / "scripts/browser_quality_regression.mjs")],
                cwd=ROOT,
                env=environment,
                input=json.dumps(
                    {
                        "base_url": base_url,
                        "fixture_model": FIXTURE_MODEL,
                        "pdf": str(pdf_path),
                        "gpu_question": GPU_QUESTION,
                        "gpu_requirements": GPU_REQUIREMENTS[3:],
                        "output_dir": str(evidence_path.parent),
                        "result_path": str(result_path),
                    }
                ),
                text=True,
                timeout=90,
                check=False,
            )
            if result_path.exists():
                evidence["browser"] = json.loads(result_path.read_text())
            require(result.returncode == 0, "Browser regression failed; see browser evidence.")
            evidence["status"] = "PASS"
        except (Exception, KeyboardInterrupt) as error:
            evidence.update(status="FAIL", error=f"{type(error).__name__}: {error}")
            evidence["fixture_log_tail"] = (
                log.read_text().splitlines()[-25:] if log.exists() else []
            )
            print(f"BROWSER FAIL: {evidence['error']}", file=sys.stderr, flush=True)
        finally:
            checks.client.close()
            try:
                if process is not None:
                    cleanup(process, metadata)
                require(
                    not metadata or not Path(metadata["temporary_root"]).exists(),
                    "Private fixture files remain after cleanup.",
                )
                evidence["checks"].append({"name": "Disposable fixture cleanup", "status": "PASS"})
            except Exception as error:
                evidence.update(status="FAIL", cleanup_error=f"{type(error).__name__}: {error}")
            evidence["completed_at"] = datetime.now(UTC).isoformat()
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(f"BROWSER {evidence['status']}; real model calls: 0; evidence: {evidence_path}")
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":

    def interrupt(_number, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    raise SystemExit(main())
