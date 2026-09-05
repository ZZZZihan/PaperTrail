"""Run backend checks in an isolated database, then remove only that test database."""

import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import psycopg
from postgres import start, stop
from psycopg import sql
from psycopg.conninfo import make_conninfo


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="papertrail-check-") as temporary:
        root = Path(temporary)
        directory = root / "postgres"
        admin = os.getenv("PAPERTRAIL_TEST_ADMIN_URL")
        local = not admin
        name = "papertrail_test_" + uuid4().hex
        created = False
        try:
            if local:
                with socket.socket() as socket_:
                    socket_.bind(("127.0.0.1", 0))
                    port = socket_.getsockname()[1]
                start(directory, port)
                admin = f"postgresql://papertrail@127.0.0.1:{port}/postgres"
            with psycopg.connect(admin, autocommit=True) as connection:
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            created = True
            database = make_conninfo(admin, dbname=name)
            environment = {
                **os.environ,
                "PAPERTRAIL_TEST_DATABASE_URL": database,
                "PAPERTRAIL_DATABASE_URL": database,
                "PAPERTRAIL_DATA_DIR": str(root / "library"),
            }
            subprocess.run([sys.executable, "-m", "pytest", "-q"], env=environment, check=True)
            subprocess.run([sys.executable, "scripts/smoke.py"], env=environment, check=True)
        finally:
            if created:
                with psycopg.connect(admin, autocommit=True) as connection:
                    connection.execute(
                        sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
                    )
            if local:
                stop(directory)


if __name__ == "__main__":
    main()
