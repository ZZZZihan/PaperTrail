"""Isolated test database supplied exclusively by scripts/check_backend.py."""

import os

import pytest
from fastapi.testclient import TestClient

from papertrail.config import Settings
from papertrail.main import create_app
from papertrail.repository import Repository


@pytest.fixture
def settings(tmp_path):
    settings = Settings(
        database_url=os.environ["PAPERTRAIL_TEST_DATABASE_URL"],
        data_dir=tmp_path / "library",
    )
    repository = Repository(settings)
    repository.migrate()
    with repository.connect() as connection:
        connection.execute("TRUNCATE papers CASCADE")
    return settings


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client
