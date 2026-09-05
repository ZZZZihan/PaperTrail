"""Upload -> immutable bytes -> extraction -> transactional publication."""

import hashlib
import os
import tempfile
import threading
from pathlib import Path
from typing import BinaryIO

from papertrail.config import Settings
from papertrail.errors import ImportFailure
from papertrail.parser import parse_pdf
from papertrail.repository import Repository


class Ingestion:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repository = repository
        self.slots = threading.BoundedSemaphore(2)

    def import_file(self, stream: BinaryIO, filename: str) -> tuple[dict, bool]:
        if not self.slots.acquire(blocking=False):
            raise ImportFailure("busy", "正在处理其他论文，请稍后重试。", 503)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.settings.data_dir / "staging", suffix=".pdf", delete=False
            ) as file:
                temporary = Path(file.name)
                size = 0
                digest = hashlib.sha256()
                header = b""
                while chunk := stream.read(64 * 1024):
                    size += len(chunk)
                    if size > self.settings.max_upload_bytes:
                        raise ImportFailure(
                            "file_too_large", "文件超过大小上限，请缩小后重试。", 413
                        )
                    if not header:
                        header = chunk[:1024]
                    digest.update(chunk)
                    file.write(chunk)
                file.flush()
                os.fsync(file.fileno())
            if not size or b"%PDF-" not in header:
                raise ImportFailure("invalid_pdf", "请上传有效的 PDF 文件。")
            sha256 = digest.hexdigest()
            existing = self.repository.by_hash(sha256)
            if existing:
                if not self.repository.file_path(existing["id"]).is_file():
                    raise ImportFailure(
                        "file_missing", "已保存记录的原文件丢失，请检查本地数据目录。", 409
                    )
                return existing, True
            parsed = parse_pdf(temporary, self.settings)
            # Never use the client-supplied filename as a filesystem path.
            name = filename.replace("\\", "/").split("/")[-1]
            name = "".join(character for character in name if character.isprintable())[:200]
            return self.repository.save(temporary, name or "未命名论文.pdf", sha256, size, parsed)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
            self.slots.release()
