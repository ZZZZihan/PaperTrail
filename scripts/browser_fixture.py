"""Disposable OFFLINE browser fixture. This never validates a real model or paper.

Run: uv run --locked python scripts/browser_fixture.py
SIGUSR1 to the printed supervisor PID restarts the application; SIGUSR2 restarts
both the application and its private PostgreSQL cluster. Ctrl+C cleans up both.

The printed introduction_pdfs map contains explicit synthetic scenarios. Upload success
to generate/cache a card, revision to exercise the one content revision, failure or
unsupported to exercise terminal errors, and slow to observe pending work. Upload
legacy-success or legacy-failure to seed a clearly marked old card; click its upgrade
button to test replacement or preservation on failure. These seeds and mock controls
exist only in this disposable fixture; no production route accepts them.
"""

import argparse
import asyncio
import hashlib
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
from uuid import uuid4

import httpx2 as httpx
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_MODEL = "OFFLINE-UI-FIXTURE-NOT-A-REAL-MODEL"
OMEGA = "Omega on physical page three. This is an OFFLINE synthetic UI fixture."
INTRODUCTION_SCENARIOS = (
    "success",
    "revision",
    "failure",
    "unsupported",
    "slow",
    "legacy-success",
    "legacy-failure",
)
SCENARIO_PREFIX = "OFFLINE INTRODUCTION SCENARIO: "


def synthetic_pdf(introduction_scenario: str | None = None) -> bytes:
    """Three physical pages with a preserved empty middle page; not academic evidence."""
    if introduction_scenario not in (None, *INTRODUCTION_SCENARIOS):
        raise ValueError("Unknown OFFLINE introduction scenario")
    writer = PdfWriter()
    writer.add_metadata({"/Title": "OFFLINE synthetic PaperTrail browser fixture"})
    for text in (
        "Alpha evidence on physical page one. Synthetic text, not a research paper."
        + (f" {SCENARIO_PREFIX}{introduction_scenario}." if introduction_scenario else ""),
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
        "PAPERTRAIL_MODEL_PROFILE": "compatible",
        "PAPERTRAIL_INTRODUCTION_REASONING_EFFORT": "none",
        "PAPERTRAIL_MODEL_BUDGET_MODE": "priced",
        "PAPERTRAIL_MODEL_BUDGET": "0",
        "PAPERTRAIL_MODEL_INPUT_PRICE_PER_MILLION": "0",
        "PAPERTRAIL_MODEL_OUTPUT_PRICE_PER_MILLION": "0",
        "PAPERTRAIL_MODEL_CURRENCY": "OFFLINE",
        "PAPERTRAIL_MODEL_BUDGET_SCOPE": "OFFLINE-UI-FIXTURE",
        "PAPERTRAIL_BROWSER_FIXTURE_ROOT": str(root),
    }


def introduction_scenario(passages: list[dict]) -> str:
    source = "\n".join(passage["text"] for passage in passages)
    return next(
        (
            scenario
            for scenario in INTRODUCTION_SCENARIOS
            if f"{SCENARIO_PREFIX}{scenario}." in source
        ),
        "success",
    )


def introduction_output(chunk_id: str, *, needs_revision: bool = False) -> dict:
    """Artificial content exercises response structure, never scholarly correctness."""
    from papertrail.introduction import FIELDS

    def claim(text, basis="paper_statement"):
        return {"text": text, "basis": basis, "citations": [{"chunk_id": chunk_id}]}

    introduction = {
        field: claim(f"OFFLINE 离线研读卡：{field} 的固定界面测试内容，原文测试标记位于第 3 页。")
        for field in FIELDS
    }
    introduction["mechanism"] = claim(
        "OFFLINE 作者解释示例：输入合成文字，过程为定位 Omega，输出为第 3 页测试片段。",
        "author_interpretation",
    )
    if needs_revision:
        introduction["summary"]["text"] += " OFFLINE-REVISION-DRAFT"
    introduction["terms"] = [
        {
            "term": term,
            "explanation": "OFFLINE 合成样例中的定位标记，仅用于验证术语展示。",
            "basis": "paper_statement",
            "citations": [{"chunk_id": chunk_id}],
        }
        for term in ("Omega", "物理页码")
    ]
    introduction["learning_aids"] = [
        claim("OFFLINE 教学示意：把物理页码想成合成 PDF 的页序号。", "teaching_example"),
        claim("OFFLINE 系统推断：此固定标记可用于检查引用按钮的跳转。", "system_inference"),
    ]
    return {"status": "answered", "introduction": introduction}


async def mock_introduction(data: dict) -> dict | httpx.Response:
    from papertrail.introduction import COVERAGE_ASPECTS

    scenario = introduction_scenario(data["passages"])
    if "claims" in data:
        rejected = scenario == "unsupported" or any(
            "OFFLINE-REVISION-DRAFT" in claim["text"] for claim in data["claims"]
        )
        return {
            "verdicts": [
                {
                    "claim_index": index,
                    "supported": not rejected or index != 0,
                    "reason": "OFFLINE 固定支持判定，不是真实模型语义检查。",
                }
                for index, _ in enumerate(data["claims"])
            ],
            "coverage": [
                {"aspect": aspect, "covered": True, "reason": "OFFLINE 固定内容覆盖判定。"}
                for aspect in COVERAGE_ASPECTS
            ],
        }
    if scenario in {"failure", "legacy-failure"}:
        return httpx.Response(503, json={"error": "OFFLINE injected introduction failure"})
    if scenario == "slow":
        await asyncio.sleep(3)
    passage = next(passage for passage in data["passages"] if OMEGA in passage["text"])
    return introduction_output(
        passage["chunk_id"], needs_revision=scenario == "revision" and "draft" not in data
    )


def seed_legacy_introduction(repository, paper: dict) -> None:
    """Only exact known synthetic PDFs can receive a fixture-only historical card."""
    from papertrail.introduction import FIELDS, build_introduction_chunks

    legacy_hashes = {
        hashlib.sha256(synthetic_pdf(scenario)).hexdigest()
        for scenario in ("legacy-success", "legacy-failure")
    }
    if paper["sha256"] not in legacy_hashes or repository.introduction(paper["id"]) is not None:
        return
    chunks = build_introduction_chunks(
        str(paper["id"]), paper["sha256"], repository.pages(paper["id"])
    )
    chunk = next(chunk for chunk in chunks if OMEGA in chunk["text"])
    introduction = introduction_output(chunk["chunk_id"])["introduction"]
    citation = {
        "chunk_id": chunk["chunk_id"],
        "paper_id": str(paper["id"]),
        "page_index": chunk["page_index"],
        "quote": chunk["text"],
    }
    for item in [
        *(introduction[field] for field in FIELDS),
        *introduction["terms"],
        *introduction["learning_aids"],
    ]:
        item["citations"] = [citation]
    introduction["summary"]["text"] = "OFFLINE 旧版简介：升级中或失败后，这段已保存内容仍应可读。"
    row, created = repository.create_introduction(paper["id"], uuid4())
    if created:
        repository.finish_question(
            row["id"],
            {
                "status": "answered",
                "introduction": introduction,
                "message": "OFFLINE 人工播种的历史缓存，未执行真实模型或人工学术核对。",
                "trace": {"pipeline_version": "OFFLINE-legacy-card", "fixture": FIXTURE_MODEL},
            },
        )


async def mock_completion(request: httpx.Request) -> httpx.Response:
    """All outcomes are artificial, selected by explicit Chinese question keywords."""
    assert request.url.host == "offline-fixture.invalid"
    data = json.loads(json.loads(request.content)["messages"][1]["content"])
    question = data.get("question", "")
    if "claim_fields" in data or ("passages" in data and "question" not in data):
        result = await mock_introduction(data)
        if isinstance(result, httpx.Response):
            return result
    elif "claims" in data:
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
        original_import = app.state.ingestion.import_file

        def import_with_offline_legacy_seed(*args, **kwargs):
            paper, duplicate = original_import(*args, **kwargs)
            seed_legacy_introduction(app.state.repository, paper)
            return paper, duplicate

        app.state.ingestion.import_file = import_with_offline_legacy_seed

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
            introduction_pdfs = {}
            for scenario in INTRODUCTION_SCENARIOS:
                scenario_pdf = root / f"OFFLINE-introduction-{scenario}.pdf"
                scenario_pdf.write_bytes(synthetic_pdf(scenario))
                introduction_pdfs[scenario] = str(scenario_pdf)
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
                            "introduction_pdfs": introduction_pdfs,
                            "temporary_root": str(root),
                            "question_keywords": [
                                "正常",
                                "部分回答",
                                "超时",
                                "失败",
                                "无效引用",
                                "证据不足",
                            ],
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
