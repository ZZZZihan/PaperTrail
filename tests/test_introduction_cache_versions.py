"""Checking-version updates are explicit and never rewrite saved card evidence."""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_import import upload

from papertrail.introduction import FIELDS, INTRODUCTION_SCHEMA_VERSION, INTRODUCTION_VERSION
from papertrail.main import create_app


def saved_card():
    return {
        **{field: {"text": "仅用于缓存测试的保存内容。", "citations": []} for field in FIELDS},
        "terms": [],
        "schema_version": INTRODUCTION_SCHEMA_VERSION,
        "coverage": [{"aspect": "research_problem", "covered": True, "reason": "保存的旧判定。"}],
    }


def store_success(client, *, schema=INTRODUCTION_SCHEMA_VERSION, pipeline=INTRODUCTION_VERSION):
    repository = client.app.state.repository
    paper_id = UUID(upload(client).json()["paper"]["id"])
    request_id = uuid4()
    row, _ = repository.create_introduction(paper_id, request_id)
    introduction = {**saved_card(), "schema_version": schema}
    trace = {"pipeline_version": pipeline, "historical_evidence": "preserve exactly"}
    repository.finish_question(
        row["id"], {"status": "answered", "introduction": introduction, "trace": trace}
    )
    return paper_id, request_id, row["id"], introduction, trace


@pytest.mark.parametrize(
    ("schema", "pipeline", "outdated"),
    [
        (None, INTRODUCTION_VERSION, True),
        (INTRODUCTION_SCHEMA_VERSION, "historical-check", True),
        (INTRODUCTION_SCHEMA_VERSION, None, True),
        (INTRODUCTION_SCHEMA_VERSION, INTRODUCTION_VERSION, False),
    ],
)
def test_refresh_requires_explicit_action_and_keeps_request_and_active_idempotency(
    client, monkeypatch, schema, pipeline, outdated
):
    paper_id, original_request, old_id, introduction, trace = store_success(
        client, schema=schema, pipeline=pipeline
    )
    path = f"/api/papers/{paper_id}/introduction"
    started = []
    monkeypatch.setattr(
        client.app.state.questions, "run_introduction", lambda *a: started.append(a)
    )
    assert client.get(path).json()["introduction_outdated"] is outdated
    alias = str(uuid4())
    cached = client.post(path, json={"request_id": alias}).json()
    assert cached["id"] == str(old_id) and cached["introduction_outdated"] is outdated
    for request_id in (str(original_request), alias):
        same = client.post(
            path, json={"request_id": request_id, "refresh_if_outdated": True}
        ).json()
        assert same["id"] == str(old_id)
    assert started == []
    request = {"request_id": str(uuid4()), "refresh_if_outdated": True}
    updated = client.post(path, json=request).json()
    assert (updated["id"] != str(old_id)) is outdated
    assert len(started) == int(outdated)
    if outdated:
        assert updated["previous_introduction_id"] == str(old_id)
        assert updated["previous_introduction"]["coverage"] == introduction["coverage"]
        assert updated["introduction_outdated"] is True
    # Same request and a new alias both reuse the active task, even with refresh=true.
    for body in (request, {**request, "request_id": str(uuid4())}):
        assert client.post(path, json=body).json()["id"] == updated["id"]
    assert len(started) == int(outdated)
    with client.app.state.repository.connect() as conn:
        stored = conn.execute(
            "SELECT introduction, trace FROM questions WHERE id = %s", (old_id,)
        ).fetchone()
    assert stored == {"introduction": introduction, "trace": trace}


@pytest.mark.parametrize("failure", ["failed", "insufficient_evidence"])
def test_failed_recheck_keeps_current_schema_previous_card_and_refresh_flag_after_restart(
    client, settings, monkeypatch, failure
):
    paper_id, _, old_id, introduction, trace = store_success(client, pipeline="historical-check")
    path = f"/api/papers/{paper_id}/introduction"
    monkeypatch.setattr(client.app.state.questions, "run_introduction", lambda *a: None)
    request = {"request_id": str(uuid4()), "refresh_if_outdated": True}
    pending = client.post(path, json=request).json()
    repository = client.app.state.repository
    repository.finish_question(
        UUID(pending["id"]),
        {"status": failure, "trace": {"pipeline_version": INTRODUCTION_VERSION}},
    )
    failed = client.get(path).json()
    assert failed["status"] == failure and failed["introduction"] is None
    assert failed["introduction_outdated"] is True  # From the old card, not the new attempt.
    assert failed["previous_introduction_id"] == str(old_id)
    assert failed["previous_introduction"]["coverage"] == introduction["coverage"]
    assert client.post(path, json=request).json() == failed
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(path).json() == failed
        monkeypatch.setattr(restarted.app.state.questions, "run_introduction", lambda *a: None)
        retry = restarted.post(path, json={**request, "request_id": str(uuid4())}).json()
        assert retry["id"] not in {failed["id"], str(old_id)}
        assert retry["previous_introduction"] == failed["previous_introduction"]
        assert retry["introduction_outdated"] is True
    with repository.connect() as conn:
        stored = conn.execute(
            "SELECT introduction, trace FROM questions WHERE id = %s", (old_id,)
        ).fetchone()
    assert stored == {"introduction": introduction, "trace": trace}


def test_server_recomputes_check_version_without_backfilling_the_saved_card(client, monkeypatch):
    paper_id, _, old_id, introduction, trace = store_success(client)
    path = f"/api/papers/{paper_id}/introduction"
    assert client.get(path).json()["introduction_outdated"] is False
    monkeypatch.setattr("papertrail.introduction.INTRODUCTION_VERSION", "next-test-check")
    shown = client.get(path).json()
    assert shown["introduction_outdated"] is True and shown["id"] == str(old_id)
    assert shown["trace"] == trace and shown["introduction"]["coverage"] == introduction["coverage"]
    cached = client.post(path, json={"request_id": str(uuid4())}).json()
    assert cached == shown
