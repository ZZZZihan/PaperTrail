"""Bounded PDF extraction in a disposable subprocess; stdout is a JSON protocol."""

import json
import math
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from pypdf import PdfReader

from papertrail.config import Settings
from papertrail.errors import ImportFailure


def extract(path: Path, max_pages: int, max_chars: int) -> dict:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ImportFailure("encrypted_pdf", "此 PDF 已加密，请上传无需密码的版本。")
        if len(reader.pages) > max_pages:
            raise ImportFailure("too_many_pages", f"页数超过 {max_pages} 页，请缩小文件范围。", 413)
        pages = []
        total = 0
        for page in reader.pages:
            text = (page.extract_text() or "").replace("\x00", "")
            total += len(text)
            if total > max_chars:
                raise ImportFailure("text_limit", "提取文本过大，请拆分文件后重试。", 413)
            pages.append(text)
        if not any(text.strip() for text in pages):
            raise ImportFailure(
                "no_text", "未提取到可用文本。请上传可选中文字的 PDF；当前未启用扫描件识别。"
            )
        return {"pages": pages, "parser_version": f"pypdf/{version('pypdf')};plain-v1"}
    except ImportFailure:
        raise
    except Exception as exc:
        raise ImportFailure("invalid_pdf", "无法解析此 PDF，文件可能损坏或不受支持。") from exc


def parse_pdf(path: Path, settings: Settings) -> dict:
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "papertrail.parser",
                str(path),
                str(settings.max_pages),
                str(settings.max_text_chars),
                str(math.ceil(settings.parse_timeout) + 1),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=settings.parse_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ImportFailure(
            "parse_timeout", "解析超时，请换用较小的文本 PDF 后重试。", 408
        ) from exc
    try:
        payload = json.loads(result.stdout) if result.returncode == 0 else None
        if payload and "error" in payload:
            raise ImportFailure(**payload["error"])
        if not payload or not isinstance(payload.get("pages"), list):
            raise ValueError("Parser process failed")
        return payload
    except (ValueError, TypeError) as exc:
        raise ImportFailure("parser_failed", "解析进程未能完成，请换用较小的文本 PDF。") from exc


def main() -> None:
    # macOS does not reliably enforce RLIMIT_AS; CPU/wall time apply on both platforms.
    import resource

    cpu_seconds = int(sys.argv[4])
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    try:
        payload = extract(Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]))
    except ImportFailure as exc:
        payload = {"error": {"code": exc.code, "message": exc.message, "status": exc.status}}
    print(json.dumps(payload, ensure_ascii=True))


if __name__ == "__main__":
    main()
