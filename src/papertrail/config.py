"""Small, explicit local-development configuration."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    data_dir: Path
    max_upload_bytes: int = 20 * 1024 * 1024
    max_pages: int = 100
    parse_timeout: float = 20
    max_text_chars: int = 2_000_000

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            database_url=os.getenv(
                "PAPERTRAIL_DATABASE_URL",
                "postgresql://papertrail@127.0.0.1:55432/papertrail",
            ),
            data_dir=Path(os.getenv("PAPERTRAIL_DATA_DIR", "data/library")).resolve(),
            max_upload_bytes=int(os.getenv("PAPERTRAIL_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))),
            max_pages=int(os.getenv("PAPERTRAIL_MAX_PAGES", "100")),
            parse_timeout=float(os.getenv("PAPERTRAIL_PARSE_TIMEOUT", "20")),
        )
        if min(settings.max_upload_bytes, settings.max_pages, settings.parse_timeout) <= 0:
            raise ValueError("PaperTrail limits must be positive")
        return settings
