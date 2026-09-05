"""Disposable OFFLINE browser fixture. This never validates a real model or paper.

Run: uv run --locked python scripts/browser_fixture.py
SIGUSR1 to the printed supervisor PID restarts the application; SIGUSR2 restarts
both the application and its private PostgreSQL cluster. Ctrl+C cleans up both.
"""

import argparse
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path

import httpx2 as httpx
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_MODEL = "OFFLINE-UI-FIXTURE-NOT-A-REAL-MODEL"
OMEGA = "Omega on physical page three. This is an OFFLINE synthetic UI fixture."


def synthetic_pdf() -> bytes:
    """Three physical pages with a preserved empty middle page; not academic evidence."""
    writer = PdfWriter()
    writer.add_metadata({"/Title": "OFFLINE synthetic PaperTrail browser fixture"})
    for text in (
        "Alpha evidence on physical page one. Synthetic text, not a research paper.",
        "",
        OMEGA,
    ):
        page = writer.add_blank_page(width=595, height=842)
        if text:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/F1"): writer._add_object(font)}
                    )
                }
            )
            content = StreamObject()
            content.set_data(f"BT /F1 11 Tf 35 760 Td ({text}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def fixture_environment(root: Path, database_port: int) -> dict[str, str]:
    """Override every service/config path; never reuse the user's library or allowance."""
    return {
        "PAPERTRAIL_DATABASE_URL": (
            f"postgresql://papertrail@127.0.0.1:{database_port}/papertrail_browser_fixture"
        ),
        "PAPERTRAIL_DATA_DIR": str(root / "library"),
        "PAPERTRAIL_MODEL_BASE_URL": "https://offline-fixture.invalid/v1",
        "PAPERTRAIL_MODEL_API_KEY": "OFFLINE-NO-CREDENTIAL",
        "PAPERTRAIL_MODEL_NAME": FIXTURE_MODEL,
        "PAPERTRAIL_MODEL_TIMEOUT": "0.25",
        "PAPERTRAIL_MODEL_MAX_OUTPUT_TOKENS": "1800",
        "PAPERTRAIL_MODEL_THINKING": "",
        "PAPERTRAIL_MODEL_BUDGET": "0",
        "PAPERTRAIL_MODEL_INPUT_PRICE_PER_MILLION": "0",
        "PAPERTRAIL_MODEL_OUTPUT_PRICE_PER_MILLION": "0",
        "PAPERTRAIL_MODEL_CURRENCY": "OFFLINE",
        "PAPERTRAIL_MODEL_BUDGET_SCOPE": "OFFLINE-UI-FIXTURE",
        "PAPERTRAIL_BROWSER_FIXTURE_ROOT": str(root),
    }


async def mock_completion(request: httpx.Request) -> httpx.Response:
    """All outcomes are artificial, selected by explicit Chinese question keywords."""
    assert request.url.host == "offline-fixture.invalid"
    data = json.loads(json.loads(request.content)["messages"][1]["content"])
    question = data["question"]
    if "claims" in data:
        result = {
            "verdicts": [
                {
                    "claim_index": index,
                    "supported": True,
                    "reason": "离线界面测试固定判定，不是真实模型语义检查。",
                }
                for index, _ in enumerate(data["claims"])
            ],
            "coverage": [
                {
                    "requirement_index": index,
                    "covered": bool(data["claims"]) and index == 0,
                    "claim_indices": [0] if data["claims"] and index == 0 else [],
                    "reason": "离线界面测试固定覆盖判定。",
                }
                for index, _ in enumerate(data["requirements"])
            ],
            "additional_requirements": [],
        }
    elif "passages" in data:
        if "证据不足" in question:
            result = {"status": "insufficient_evidence", "claims": [], "message": ""}
        else:
            passage = next((p for p in data["passages"] if OMEGA in p["text"]), None)
            if passage is None:
                result = {"status": "insufficient_evidence", "claims": [], "message": ""}
            else:
                result = {
                    "status": "answered",
                    "message": "",
                    "claims": [
                        {
                            "text": (
                                "离线界面测试片段：PDF 第 3 页包含 Omega 测试文字。"
                                "这不是论文问答结果。"
                            ),
                            "citations": [
                                {
                                    "chunk_id": "OFFLINE-invalid-chunk"
                                    if "无效引用" in question
                                    else (passage["chunk_id"]),
                                    "quote": OMEGA,
                                }
                            ],
                        }
                    ],
                }
    else:
        if "超时" in question:
            await asyncio.sleep(2)  # The real client cancels this at its 0.25-second deadline.
        if "失败" in question:
            return httpx.Response(503, json={"error": "OFFLINE injected service failure"})
        result = {
            "search_queries": ["Omega physical page three OFFLINE synthetic UI fixture"],
            "requirements": ["Omega 位于哪一页？"]
            + (["试验轮数是多少？"] if "部分回答" in question else []),
        }
    return httpx.Response(
        200,
        json={
            "model": FIXTURE_MODEL,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(result, ensure_ascii=False)},
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        },
    )


def serve(port: int) -> None:
    from unittest.mock import patch

    import uvicorn

    from papertrail.model import ModelClient

    root = Path(os.environ["PAPERTRAIL_BROWSER_FIXTURE_ROOT"])
    if not (root / "OFFLINE-FIXTURE-MARKER").is_file():
        raise RuntimeError("Fixture child must be launched by its disposable supervisor.")
    if os.environ.get("PAPERTRAIL_MODEL_NAME") != FIXTURE_MODEL:
        raise RuntimeError("Refusing to start an offline fixture with a real model configuration.")

    class OfflineModelClient(ModelClient):
        def __init__(self, config, **kwargs):
            kwargs["transport"] = httpx.MockTransport(mock_completion)
            super().__init__(config, **kwargs)

    # Patch before importing the application so both questions.py and qa.py use only the mock.
    with patch("papertrail.model.ModelClient", OfflineModelClient):
        from papertrail.main import create_app

        app = create_app()

        @app.middleware("http")
        async def mark_offline_fixture(request, call_next):
            response = await call_next(request)
            response.headers["X-PaperTrail-Fixture"] = FIXTURE_MODEL
            return response

        uvicorn.run(app, host="127.0.0.1", port=port, timeout_graceful_shutdown=5)


def supervise(port: int) -> None:
    import psycopg
    from postgres import start, stop

    if not (ROOT / "web" / "dist" / "index.html").is_file():
        raise RuntimeError("Build the local browser first: make web-build")
    with socket.socket() as available:
        available.bind(("127.0.0.1", port))
    with socket.socket() as free_database_port:
        free_database_port.bind(("127.0.0.1", 0))
        database_port = free_database_port.getsockname()[1]

    state = {"stop": False, "restart": False, "restart_database": False}

    def handle_signal(number, _frame):
        if number in {signal.SIGINT, signal.SIGTERM}:
            state["stop"] = True
        else:
            state["restart"] = True
            state["restart_database"] = number == signal.SIGUSR2

    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1, signal.SIGUSR2)
    previous = {number: signal.signal(number, handle_signal) for number in signals}
    child = None

    def stop_child():
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)

    try:
        with tempfile.TemporaryDirectory(prefix="papertrail-OFFLINE-browser-") as temporary:
            root = Path(temporary)
            cluster = root / "postgres"
            pdf = root / "OFFLINE-synthetic-three-pages.pdf"
            pdf.write_bytes(synthetic_pdf())
            (root / "OFFLINE-FIXTURE-MARKER").write_text(FIXTURE_MODEL)
            environment = {
                **{
                    key: value
                    for key, value in os.environ.items()
                    if not key.startswith("PAPERTRAIL_")
                },
                **fixture_environment(root, database_port),
            }
            try:
                start(cluster, database_port)
                with psycopg.connect(
                    f"postgresql://papertrail@127.0.0.1:{database_port}/postgres", autocommit=True
                ) as connection:
                    connection.execute("CREATE DATABASE papertrail_browser_fixture")

                def start_child():
                    return subprocess.Popen(
                        [
                            sys.executable,
                            str(Path(__file__).resolve()),
                            "--serve",
                            "--port",
                            str(port),
                        ],
                        cwd=ROOT,
                        env=environment,
                    )

                child = start_child()
                print(
                    json.dumps(
                        {
                            "mode": FIXTURE_MODEL,
                            "url": f"http://127.0.0.1:{port}",
                            "supervisor_pid": os.getpid(),
                            "server_pid": child.pid,
                            "synthetic_pdf": str(pdf),
                            "temporary_root": str(root),
                            "question_keywords": ["正常", "超时", "失败", "无效引用", "证据不足"],
                            "restart_app": f"kill -USR1 {os.getpid()}",
                            "restart_app_and_database": f"kill -USR2 {os.getpid()}",
                            "warning": (
                                "OFFLINE technical fixture only; no real model or paper validation."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                while not state["stop"]:
                    if state["restart"]:
                        stop_child()
                        if state["restart_database"]:
                            stop(cluster)
                            start(cluster, database_port)
                        child = start_child()
                        print(f"OFFLINE fixture restarted; server_pid={child.pid}", flush=True)
                        state["restart"] = state["restart_database"] = False
                    elif child.poll() is not None:
                        raise RuntimeError(f"OFFLINE fixture child exited with {child.returncode}")
                    time.sleep(0.2)
            finally:
                stop_child()
                stop(cluster)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        serve(args.port)
    else:
        supervise(args.port)


if __name__ == "__main__":
    main()
