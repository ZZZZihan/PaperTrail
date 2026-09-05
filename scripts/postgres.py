"""Manage only this project's local PostgreSQL cluster; never use brew services."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def binary(name: str) -> str:
    paths = [
        Path(os.getenv("PAPERTRAIL_PG_BIN", "/nonexistent")),
        Path("/opt/homebrew/opt/postgresql@17/bin"),
        Path("/usr/local/opt/postgresql@17/bin"),
        Path("/usr/lib/postgresql/17/bin"),
    ]
    for path in paths:
        if (path / name).is_file():
            return str(path / name)
    if found := shutil.which(name):
        return found
    raise RuntimeError(
        "PostgreSQL 17 binaries missing. Install PostgreSQL or set PAPERTRAIL_PG_BIN."
    )


def run(*args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return result


def start(directory: Path, port: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / "PG_VERSION").exists():
        run(
            binary("initdb"),
            "-D",
            str(directory),
            "-U",
            "papertrail",
            "-A",
            "trust",
            "-E",
            "UTF8",
            "--locale=C",
        )
    status = subprocess.run([binary("pg_ctl"), "-D", str(directory), "status"], capture_output=True)
    if status.returncode != 0:
        run(
            binary("pg_ctl"),
            "-D",
            str(directory),
            "-l",
            str(directory / "server.log"),
            "-o",
            f"-h 127.0.0.1 -p {port} -k ''",
            "-w",
            "start",
        )


def stop(directory: Path) -> None:
    if (directory / "postmaster.pid").exists():
        run(binary("pg_ctl"), "-D", str(directory), "-m", "fast", "-w", "stop")


def main() -> None:
    import psycopg

    root = Path(__file__).resolve().parents[1]
    directory = root / "data" / "postgres"
    if sys.argv[1:] == ["stop"]:
        stop(directory)
        print("PaperTrail local PostgreSQL stopped.")
        return
    start(directory, 55432)
    with psycopg.connect(
        "postgresql://papertrail@127.0.0.1:55432/postgres", autocommit=True
    ) as connection:
        if not connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = 'papertrail'"
        ).fetchone():
            connection.execute("CREATE DATABASE papertrail")
    print("PaperTrail local PostgreSQL ready on 127.0.0.1:55432.")


if __name__ == "__main__":
    main()
