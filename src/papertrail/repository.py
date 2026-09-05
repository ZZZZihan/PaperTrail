"""PostgreSQL owns document identity and all-or-nothing page records."""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from papertrail.config import Settings
from papertrail.errors import ImportFailure


class Repository:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._service_guard = None
        self._service_guard_lost = False

    def bind_service_guard(self, guard) -> None:
        """Bind only the session whose exclusive lock was acquired at application startup."""
        self._service_guard = guard
        self._service_guard_lost = False

    def require_service_guard(self) -> None:
        """A lost PostgreSQL session cannot regain permission without an application restart."""
        if not self.settings.exclusive_service:
            return
        if not self._service_guard_lost and self._service_guard is not None:
            try:
                self._service_guard.execute("SELECT 1").fetchone()
                return
            except psycopg.Error:
                self._service_guard_lost = True
        raise ImportFailure(
            "service_restart_required",
            "应用的数据库会话已中断。请先停止并重启 PaperTrail，再提交或重试问题。",
            503,
        )

    def connect(self):
        return psycopg.connect(
            self.settings.database_url,
            row_factory=dict_row,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c lock_timeout=5000",
        )

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(16091601)")
            conn.execute(files("papertrail").joinpath("schema.sql").read_text())

    def list(self, offset: int = 0) -> list[dict]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM papers ORDER BY created_at DESC, id DESC LIMIT 50 OFFSET %s",
                (offset,),
            ).fetchall()

    def get(self, paper_id: UUID) -> dict | None:
        with self.connect() as conn:
            paper = conn.execute("SELECT * FROM papers WHERE id = %s", (paper_id,)).fetchone()
            if paper:
                paper["empty_pages"] = [
                    row["page_index"]
                    for row in conn.execute(
                        "SELECT page_index, text FROM pages "
                        "WHERE paper_id = %s ORDER BY page_index",
                        (paper_id,),
                    )
                    if not row["text"].strip()
                ]
            return paper

    def by_hash(self, sha256: str) -> dict | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM papers WHERE sha256 = %s", (sha256,)).fetchone()

    def page(self, paper_id: UUID, page_index: int) -> dict | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT paper_id, page_index, text FROM pages "
                "WHERE paper_id = %s AND page_index = %s",
                (paper_id, page_index),
            ).fetchone()

    def pages(self, paper_id: UUID) -> list[dict]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT paper_id, page_index, text FROM pages "
                "WHERE paper_id = %s ORDER BY page_index",
                (paper_id,),
            ).fetchall()

    def create_question(self, paper_id: UUID, request_id: UUID, question: str):
        return self._create_task(paper_id, request_id, question, "qa")

    def create_introduction(self, paper_id: UUID, request_id: UUID):
        from papertrail.introduction import INTRODUCTION_QUESTION

        return self._create_task(paper_id, request_id, INTRODUCTION_QUESTION, "introduction")

    def _create_task(self, paper_id: UUID, request_id: UUID, question: str, kind: str):
        self.require_service_guard()
        self.expire_questions()
        with self.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(18091801)")
            existing = conn.execute(
                "SELECT * FROM questions WHERE request_id = %s OR id IN "
                "(SELECT question_id FROM question_request_aliases WHERE request_id = %s)",
                (request_id, request_id),
            ).fetchone()
            if existing:
                if (
                    existing["paper_id"] != paper_id
                    or existing["question"] != question
                    or existing["kind"] != kind
                ):
                    raise ImportFailure(
                        "request_conflict", "提交标识已用于其他任务，请重新提交。", 409
                    )
                return existing, False
            if not conn.execute("SELECT id FROM papers WHERE id = %s", (paper_id,)).fetchone():
                raise ImportFailure("not_found", "找不到这篇论文，请返回论文库。", 404)
            if kind == "introduction":
                existing = conn.execute(
                    "SELECT * FROM questions WHERE paper_id = %s AND kind = 'introduction' "
                    "AND status IN ('pending', 'running', 'answered') "
                    "ORDER BY created_at DESC, id DESC LIMIT 1",
                    (paper_id,),
                ).fetchone()
                if existing:
                    conn.execute(
                        "INSERT INTO question_request_aliases(request_id, question_id) "
                        "VALUES (%s, %s)",
                        (request_id, existing["id"]),
                    )
                    return existing, False
            if conn.execute(
                "SELECT id FROM questions WHERE status IN ('pending', 'running') LIMIT 1"
            ).fetchone():
                raise ImportFailure(
                    "question_in_progress", "已有问题或简介正在处理，请稍候再试。", 409
                )
            row = conn.execute(
                "INSERT INTO questions(id, paper_id, request_id, question, kind, status) "
                "VALUES (%s, %s, %s, %s, %s, 'pending') RETURNING *",
                (uuid4(), paper_id, request_id, question, kind),
            ).fetchone()
            return row, True

    def questions(self, paper_id: UUID, offset: int = 0) -> list[dict]:
        self.expire_questions()
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM questions WHERE paper_id = %s AND kind = 'qa' "
                "ORDER BY created_at DESC, id DESC LIMIT 100 OFFSET %s",
                (paper_id, offset),
            ).fetchall()

    def question(self, paper_id: UUID, question_id: UUID) -> dict | None:
        self.expire_questions()
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM questions WHERE id = %s AND paper_id = %s AND kind = 'qa'",
                (question_id, paper_id),
            ).fetchone()

    def introduction(self, paper_id: UUID) -> dict | None:
        self.expire_questions()
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM questions WHERE paper_id = %s AND kind = 'introduction' "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (paper_id,),
            ).fetchone()

    def progress_question(self, question_id: UUID, stage: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE questions SET status = 'running', stage = %s "
                "WHERE id = %s AND status IN ('pending', 'running')",
                (stage, question_id),
            )

    def finish_question(self, question_id: UUID, result: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE questions SET status = %s, stage = 'complete', claims = %s, "
                "message = %s, error_code = %s, support_status = %s, human_review = %s, "
                "trace = %s, introduction = %s, completed_at = now() "
                "WHERE id = %s AND status IN ('pending', 'running')",
                (
                    result["status"],
                    Jsonb(result.get("claims", [])),
                    result.get("message", ""),
                    result.get("error_code"),
                    result.get("support_status"),
                    Jsonb(result.get("human_review")),
                    Jsonb(result.get("trace", {})),
                    Jsonb(result.get("introduction") if result["status"] == "answered" else None),
                    question_id,
                ),
            )

    def recover_questions(self) -> int:
        with self.connect() as conn:
            return conn.execute(
                "UPDATE questions SET status = 'failed', stage = 'complete', "
                "error_code = 'interrupted', message = %s, completed_at = now() "
                "WHERE status IN ('pending', 'running')",
                (
                    "上次处理因应用停止而中断。模型调用可能已发生，费用可能未知。"
                    "原问题已保存，可确认后主动重试。",
                ),
            ).rowcount

    def expire_questions(self) -> None:
        # The fixed pipeline has a 180s deadline. Leave a generous persistence margin.
        # This also releases tasks whose terminal write failed during a database outage.
        with self.connect() as conn:
            conn.execute(
                "UPDATE questions SET status = 'failed', stage = 'complete', "
                "error_code = 'interrupted', message = %s, completed_at = now() "
                "WHERE status IN ('pending', 'running') "
                "AND created_at < now() - interval '5 minutes'",
                (
                    "处理记录超过时间上限，可能在服务异常时中断。模型调用可能已发生，"
                    "请核对后主动重试。",
                ),
            )

    def file_path(self, paper_id: UUID) -> Path:
        return self.settings.data_dir / "papers" / f"{paper_id}.pdf"

    def save(self, temporary: Path, filename: str, sha256: str, size: int, parsed: dict):
        with self.connect() as conn:
            # Serialize only publication of identical bytes, including concurrent uploads.
            lock_key = int.from_bytes(bytes.fromhex(sha256[:16]), signed=True)
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
            existing = conn.execute("SELECT * FROM papers WHERE sha256 = %s", (sha256,)).fetchone()
            if existing:
                return existing, True
            paper_id = uuid4()
            target = self.file_path(paper_id)
            paper = conn.execute(
                """INSERT INTO papers (id, filename, sha256, size_bytes, page_count, parser_version)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
                (paper_id, filename, sha256, size, len(parsed["pages"]), parsed["parser_version"]),
            ).fetchone()
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO pages (paper_id, page_index, text) VALUES (%s, %s, %s)",
                    [(paper_id, index, text) for index, text in enumerate(parsed["pages"])],
                )
            os.replace(temporary, target)
            # Keep the published file even if COMMIT's outcome is ambiguous.
            # An orphan is safer than deleting a file a committed row may reference.
        return paper, False
