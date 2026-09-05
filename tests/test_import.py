import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pdf_fixtures import pdf_bytes

from papertrail.main import create_app
from papertrail.repository import Repository


def upload(client, data=None, name="paper.pdf"):
    return client.post(
        "/api/papers",
        files={"file": (name, data if data is not None else pdf_bytes(), "application/pdf")},
    )


def test_pages_original_identity_and_restart(client, settings):
    original = pdf_bytes()
    response = upload(client, original)
    assert response.status_code == 201
    body = response.json()
    assert body["duplicate"] is False
    paper = body["paper"]
    assert paper["sha256"] == hashlib.sha256(original).hexdigest()
    assert paper["size_bytes"] == len(original)
    assert paper["page_count"] == 3
    assert paper["parser_version"].startswith("pypdf/")
    path = f"/api/papers/{paper['id']}"
    assert client.get(path).json()["empty_pages"] == [1]
    assert "Alpha" in client.get(path + "/pages/0").json()["text"]
    assert client.get(path + "/pages/1").json()["text"] == ""
    assert "Omega" in client.get(path + "/pages/2").json()["text"]
    assert client.get(path + "/pages/3").status_code == 404
    assert client.get(path + "/pages/-1").status_code == 404
    assert client.get(path + "/file").content == original
    assert list((settings.data_dir / "staging").iterdir()) == []
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/api/papers").json()[0]["id"] == paper["id"]
        assert restarted.get(path + "/file").content == original
        assert "Omega" in restarted.get(path + "/pages/2").json()["text"]


def test_duplicate_and_same_name_different_content(client, settings):
    first = upload(client).json()["paper"]
    repeated = upload(client)
    assert repeated.status_code == 200
    assert repeated.json()["duplicate"] is True
    assert repeated.json()["paper"]["id"] == first["id"]
    changed = upload(client, pdf_bytes(("Changed",)))
    assert changed.status_code == 201
    assert changed.json()["paper"]["id"] != first["id"]
    assert len(client.get("/api/papers").json()) == 2
    assert len(list((settings.data_dir / "papers").iterdir())) == 2


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "invalid_pdf"),
        (b"not a pdf", "invalid_pdf"),
        (b"%PDF-1.7\nmalformed", "invalid_pdf"),
    ],
)
def test_invalid_upload_is_not_saved(client, settings, data, code):
    response = upload(client, data)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert client.get("/api/papers").json() == []
    assert list((settings.data_dir / "staging").iterdir()) == []
    assert list((settings.data_dir / "papers").iterdir()) == []


def test_whole_document_empty_and_encrypted(client):
    assert upload(client, pdf_bytes(("", " "))).json()["error"]["code"] == "no_text"
    assert upload(client, pdf_bytes(encrypted=True)).json()["error"]["code"] == "encrypted_pdf"
    assert client.get("/api/papers").json() == []


@pytest.mark.parametrize(
    ("changes", "data", "code", "status"),
    [
        ({"max_upload_bytes": 64}, None, "file_too_large", 413),
        ({"max_pages": 2}, None, "too_many_pages", 413),
        ({"max_text_chars": 2}, None, "text_limit", 413),
        ({"parse_timeout": 0.001}, None, "parse_timeout", 408),
        ({"max_upload_bytes": 64}, b"%PDF-" + b"x" * (2 * 1024 * 1024), "file_too_large", 413),
    ],
)
def test_resource_limits(settings, changes, data, code, status):
    with TestClient(create_app(replace(settings, **changes))) as client:
        response = upload(client, data)
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert client.get("/api/papers").json() == []
        assert list((settings.data_dir / "staging").iterdir()) == []


def test_concurrent_duplicates_publish_one_document(client, settings):
    data = pdf_bytes()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: upload(client, data), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 201]
    assert len({response.json()["paper"]["id"] for response in responses}) == 1
    assert len(client.get("/api/papers").json()) == 1
    assert len(list((settings.data_dir / "papers").iterdir())) == 1


def test_filename_never_controls_storage_path(client, settings):
    result = upload(client, name="../../论文.pdf").json()["paper"]
    assert result["filename"] == "论文.pdf"
    assert list((settings.data_dir / "papers").iterdir()) == [
        settings.data_dir / "papers" / f"{result['id']}.pdf"
    ]


def test_file_publication_failure_rolls_back_pages(client, settings, monkeypatch):
    def fail(*args):
        raise OSError("injected write failure")

    monkeypatch.setattr("papertrail.repository.os.replace", fail)
    response = upload(client)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "storage_failure"
    assert client.get("/api/papers").json() == []
    with Repository(settings).connect() as connection:
        assert connection.execute("SELECT count(*) AS n FROM pages").fetchone()["n"] == 0
    assert list((settings.data_dir / "staging").iterdir()) == []


def test_mid_transaction_database_failure_leaves_no_success(client, settings, monkeypatch):
    monkeypatch.setattr(
        "papertrail.ingestion.parse_pdf",
        lambda *args: {
            "pages": ["first page", "bad\x00page"],
            "parser_version": "injected-failure",
        },
    )
    assert upload(client).status_code == 503
    assert client.get("/api/papers").json() == []
    with Repository(settings).connect() as connection:
        assert connection.execute("SELECT count(*) AS n FROM pages").fetchone()["n"] == 0
    assert list((settings.data_dir / "papers").iterdir()) == []


def test_missing_paper_and_missing_original(client, settings):
    assert client.get(f"/api/papers/{uuid4()}").status_code == 404
    paper = upload(client).json()["paper"]
    (settings.data_dir / "papers" / f"{paper['id']}.pdf").unlink()
    assert client.get(f"/api/papers/{paper['id']}/file").json()["error"]["code"] == "file_missing"
    assert upload(client).status_code == 409


def test_busy_response_releases_no_extra_slots(client):
    slots = client.app.state.ingestion.slots
    slots.acquire()
    slots.acquire()
    try:
        response = upload(client)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "busy"
    finally:
        slots.release()
        slots.release()
    assert upload(client).status_code == 201


def test_database_unavailable_returns_safe_message(client, monkeypatch):
    import psycopg

    def fail(*args):
        raise psycopg.OperationalError("injected connection details must not be returned")

    monkeypatch.setattr(client.app.state.repository, "connect", fail)
    response = client.get("/api/papers")
    assert response.status_code == 503
    assert "injected" not in response.text
