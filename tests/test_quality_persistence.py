"""Coverage and explicit cache upgrades are durable, bounded user actions."""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_import import upload

from papertrail.main import create_app


@pytest.mark.parametrize(
    ("kind", "age_minutes", "expired"),
    [("qa", 6, True), ("introduction", 6, False), ("introduction", 8, True)],
)
def test_expiry_preserves_live_reading_card_window(client, kind, age_minutes, expired):
    paper_id = UUID(upload(client).json()["paper"]["id"])
    repository = client.app.state.repository
    if kind == "qa":
        row, _ = repository.create_question(paper_id, uuid4(), "测试超时回收")
    else:
        row, _ = repository.create_introduction(paper_id, uuid4())
    repository.progress_question(row["id"], "verifying")
    with repository.connect() as connection:
        connection.execute(
            "UPDATE questions SET created_at = now() - %s * interval '1 minute' WHERE id = %s",
            (age_minutes, row["id"]),
        )
    repository.expire_questions()
    with repository.connect() as connection:
        result = connection.execute(
            "SELECT status, error_code FROM questions WHERE id = %s", (row["id"],)
        ).fetchone()
    assert result["status"] == ("failed" if expired else "running")
    assert result["error_code"] == ("interrupted" if expired else None)


def test_partial_answer_and_coverage_survive_history_reload_and_restart(
    client, settings, monkeypatch
):
    paper = upload(client).json()["paper"]
    repository = client.app.state.repository
    coverage = {
        "status": "partial",
        "review_source": "ai",
        "items": [
            {
                "requirement": "模型是否更新参数？",
                "covered": False,
                "claim_indices": [],
                "reason": "正文未说明。",
                "origin": "question",
            }
        ],
    }

    def run(question_id, paper_id, question):
        repository.finish_question(
            question_id,
            {
                "status": "partial_answer",
                "claims": [
                    {
                        "text": "测试事实",
                        "citations": [
                            {
                                "chunk_id": "test-only",
                                "paper_id": str(paper_id),
                                "page_index": 0,
                                "quote": "Alpha evidence on physical page one",
                            }
                        ],
                    }
                ],
                "coverage": coverage,
            },
        )

    monkeypatch.setattr(client.app.state.questions, "run", run)
    path = f"/api/papers/{paper['id']}/questions"
    request = {"question": "实验设置？", "request_id": str(uuid4())}
    created = client.post(path, json=request).json()
    result = client.get(path + "/" + created["id"]).json()
    assert result["status"] == "partial_answer" and result["coverage"] == coverage
    assert client.post(path, json=request).json() == result
    assert client.get(path).json()[0] == result
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(path + "/" + created["id"]).json() == result


def test_only_explicit_outdated_cache_upgrade_creates_new_task(client, monkeypatch):
    from papertrail.introduction import INTRODUCTION_SCHEMA_VERSION

    paper = upload(client).json()["paper"]
    repository = client.app.state.repository
    paper_id = UUID(paper["id"])
    row, _ = repository.create_introduction(paper_id, uuid4())
    repository.finish_question(row["id"], {"status": "answered", "introduction": {"legacy": True}})
    cached, created = repository.create_introduction(paper_id, uuid4())
    assert not created and cached["id"] == row["id"]
    request_id = uuid4()
    upgraded, created = repository.create_introduction(
        paper_id, request_id, refresh_if_outdated=True
    )
    assert created and upgraded["id"] != row["id"]
    assert (
        repository.create_introduction(paper_id, request_id, refresh_if_outdated=True)[0]["id"]
        == upgraded["id"]
    )

    repository.finish_question(
        upgraded["id"],
        {"status": "answered", "introduction": {"schema_version": INTRODUCTION_SCHEMA_VERSION}},
    )
    cached, created = repository.create_introduction(paper_id, uuid4(), refresh_if_outdated=True)
    assert not created and cached["id"] == upgraded["id"]
    with repository.connect() as connection:
        assert connection.execute(
            "SELECT introduction FROM questions WHERE id=%s", (row["id"],)
        ).fetchone()["introduction"] == {"legacy": True}


@pytest.mark.parametrize("failure", ["failed", "insufficient_evidence", "interrupted"])
def test_failed_upgrade_preserves_readable_previous_success_and_can_retry(
    client, settings, failure
):
    repository = client.app.state.repository
    paper_id = UUID(upload(client).json()["paper"]["id"])
    old, _ = repository.create_introduction(paper_id, uuid4())
    previous = {"legacy": True}
    repository.finish_question(old["id"], {"status": "answered", "introduction": previous})
    pending, created = repository.create_introduction(paper_id, uuid4(), refresh_if_outdated=True)
    assert created
    shown = repository.introduction(paper_id)
    assert shown["previous_introduction"] == previous
    assert shown["previous_introduction_id"] == old["id"]
    if failure == "interrupted":
        with TestClient(create_app(settings)):
            pass
    else:
        repository.finish_question(
            pending["id"],
            {
                "status": failure,
                "error_code": "call_limit_exceeded" if failure == "failed" else None,
            },
        )
    shown = repository.introduction(paper_id)
    assert shown["status"] in {"failed", "insufficient_evidence"}
    assert shown["previous_introduction"] == previous
    retried, created = repository.create_introduction(paper_id, uuid4())
    assert created and retried["id"] != old["id"]
