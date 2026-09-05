"""Introduction persistence, idempotency and shared cost controls on an isolated DB."""

import json
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from test_import import upload
from test_introduction import introduction_output

from papertrail.budget import Budget, CallLedger
from papertrail.errors import ImportFailure
from papertrail.main import create_app
from papertrail.model import ModelClient, ModelConfig
from papertrail.repository import Repository


def configure_fake_model(monkeypatch, *, cap=10):
    for key, value in {
        "PAPERTRAIL_MODEL_BASE_URL": "https://test.invalid/v1",
        "PAPERTRAIL_MODEL_API_KEY": "test-only-key",
        "PAPERTRAIL_MODEL_NAME": "test-model",
        "PAPERTRAIL_MODEL_BUDGET_MODE": "provider_quota",
        "PAPERTRAIL_MODEL_BUDGET_SCOPE": "introduction-test",
        "PAPERTRAIL_MODEL_MAX_CALLS": str(cap),
        "PAPERTRAIL_MODEL_MAX_OUTPUT_TOKENS": "1800",
        "PAPERTRAIL_MODEL_TIMEOUT": "45",
    }.items():
        monkeypatch.setenv(key, value)
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        data = json.loads(payload["messages"][1]["content"])
        if "claims" in data:
            result = {
                "verdicts": [
                    {"claim_index": i, "supported": True, "reason": "测试固定判定。"}
                    for i in range(len(data["claims"]))
                ]
            }
        else:
            passage = data["passages"][0]
            result = introduction_output(passage["chunk_id"])
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 100},
            },
        )

    def fake_model(config, **kwargs):
        return ModelClient(config, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("papertrail.model.ModelClient", fake_model)
    return requests


def test_intro_persists_across_refresh_restart_and_never_enters_qa_history(
    client, settings, monkeypatch
):
    requests = configure_fake_model(monkeypatch)
    paper = upload(client).json()["paper"]
    path = f"/api/papers/{paper['id']}/introduction"
    assert client.get(path).json() is None
    body = {"request_id": str(uuid4())}
    submitted = client.post(path, json=body)
    assert submitted.status_code == 202 and submitted.json()["status"] == "pending"
    row = client.get(path).json()
    assert row["status"] == "answered" and row["kind"] == "introduction"
    assert row["introduction"]["terms"][0]["citations"][0]["paper_id"] == paper["id"]
    assert len(row["claims"]) == 7 and len(requests) == 2
    assert row["trace"]["model_config"]["timeout_seconds"] == 90
    # The introduction adjusts its own runtime config; ordinary QA still reads the
    # existing provider settings, and the shared pipeline deadline remains 180s.
    assert ModelConfig.from_env().timeout == 45
    assert ModelConfig.from_env().max_output_tokens == 1800
    assert all(payload["max_tokens"] == 5000 for payload in requests)
    assert all(
        call["details"]["max_output_tokens"] == 5000 for call in row["trace"]["ledger"]["calls"]
    )
    assert client.post(path, json=body).json()["id"] == row["id"]
    assert client.post(path, json={"request_id": str(uuid4())}).json()["id"] == row["id"]
    assert len(requests) == 2
    assert client.get(f"/api/papers/{paper['id']}/questions").json() == []
    assert client.get(f"/api/papers/{paper['id']}/questions/{row['id']}").status_code == 404
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(path).json() == row
        assert restarted.post(path, json={"request_id": str(uuid4())}).json()["id"] == row["id"]
    assert len(requests) == 2


def test_active_intro_reuses_requests_and_failure_needs_truly_new_request_id(client, settings):
    paper_id = UUID(upload(client).json()["paper"]["id"])
    repository = Repository(settings)
    original_id, alias_id = uuid4(), uuid4()
    original, created = repository.create_introduction(paper_id, original_id)
    assert created
    alias, created = repository.create_introduction(paper_id, alias_id)
    assert not created and alias["id"] == original["id"]
    with pytest.raises(ImportFailure, match="已有问题"):
        repository.create_question(paper_id, uuid4(), "简介处理时不能抢占调用槽")
    repository.finish_question(original["id"], {"status": "failed", "error_code": "model_failure"})
    for request_id in (original_id, alias_id):
        failed, created = repository.create_introduction(paper_id, request_id)
        assert not created and failed["status"] == "failed"
    retry, created = repository.create_introduction(paper_id, uuid4())
    assert created and retry["id"] != original["id"]
    with repository.connect() as conn:
        assert conn.execute("SELECT count(*) AS total FROM questions").fetchone()["total"] == 2
    assert repository.introduction(paper_id)["id"] == retry["id"]


def test_request_identifiers_cannot_cross_kind_or_paper_including_alias(client, settings):
    paper_id = UUID(upload(client).json()["paper"]["id"])
    repository = Repository(settings)
    original_id, alias_id = uuid4(), uuid4()
    row, _ = repository.create_introduction(paper_id, original_id)
    repository.create_introduction(paper_id, alias_id)
    repository.finish_question(row["id"], {"status": "failed"})
    for request_id in (original_id, alias_id):
        with pytest.raises(ImportFailure) as conflict:
            repository.create_introduction(uuid4(), request_id)
        assert conflict.value.code == "request_conflict"
        with pytest.raises(ImportFailure) as conflict:
            repository.create_question(paper_id, request_id, row["question"])
        assert conflict.value.code == "request_conflict"
    qa, _ = repository.create_question(paper_id, uuid4(), "普通问题")
    with pytest.raises(ImportFailure) as conflict:
        repository.create_introduction(paper_id, uuid4())
    assert conflict.value.code == "question_in_progress"
    assert repository.questions(paper_id)[0]["id"] == qa["id"]


def test_restart_recovers_pending_intro_and_preserves_old_request_id(client, settings):
    paper_id = UUID(upload(client).json()["paper"]["id"])
    repository = Repository(settings)
    request_id = uuid4()
    row, _ = repository.create_introduction(paper_id, request_id)
    repository.progress_question(row["id"], "generating")
    with TestClient(create_app(settings)) as restarted:
        recovered = restarted.get(f"/api/papers/{paper_id}/introduction").json()
        assert recovered["error_code"] == "interrupted" and recovered["introduction"] is None
        again, created = repository.create_introduction(paper_id, request_id)
        assert not created and again["status"] == "failed"
        assert repository.create_introduction(paper_id, uuid4())[1]


def test_intro_uses_existing_scope_call_count_and_reservations(client, settings, monkeypatch):
    requests = configure_fake_model(monkeypatch, cap=2)
    paper_id = UUID(upload(client).json()["paper"]["id"])
    repository = Repository(settings)
    qa, _ = repository.create_question(paper_id, uuid4(), "已有配额使用")
    budget = Budget.from_env()
    assert budget is not None and budget.max_calls == 2
    ledger = CallLedger(repository, qa["id"], budget)
    ledger.before_call({"stage": "query", "input_token_upper_bound": 50, "max_output_tokens": 1800})
    ledger.record_call({"usage": {"prompt_tokens": 10, "completion_tokens": 10}})
    repository.finish_question(qa["id"], {"status": "answered"})
    path = f"/api/papers/{paper_id}/introduction"
    assert client.post(path, json={"request_id": str(uuid4())}).status_code == 202
    row = client.get(path).json()
    assert row["status"] == "failed" and row["error_code"] == "call_limit_exceeded"
    assert row["introduction"] is None and row["claims"] == []
    assert len(requests) == len(row["trace"]["ledger"]["calls"]) == 1
    assert row["trace"]["ledger"]["estimated_cost"] is None
    # Even a new active retry cannot reset the accumulated calls in the same scope.
    client.post(path, json={"request_id": str(uuid4())})
    retry = client.get(path).json()
    assert retry["error_code"] == "call_limit_exceeded"
    assert retry["trace"]["ledger"]["calls"] == [] and len(requests) == 1
    assert len(repository.questions(paper_id)) == 1


def test_intro_unknown_paper_and_invalid_input_do_not_create_tasks(client):
    assert client.get(f"/api/papers/{uuid4()}/introduction").status_code == 404
    assert (
        client.post(
            f"/api/papers/{uuid4()}/introduction", json={"request_id": str(uuid4())}
        ).status_code
        == 404
    )
    paper = upload(client).json()["paper"]
    path = f"/api/papers/{paper['id']}/introduction"
    assert client.post(path, json={}).status_code == 422
    assert client.get(path).json() is None
