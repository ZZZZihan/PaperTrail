"""PostgreSQL owns document identity and all-or-nothing page records."""

import os
from importlib.resources import files
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from papertrail.config import Settings


class Repository:
    def __init__(self, settings: Settings):
        self.settings = settings

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
