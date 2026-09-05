"""Durable task contracts; test doubles here never stand in for real model acceptance."""

from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_import import upload

from papertrail.budget import Budget, CallLedger
from papertrail.errors import ImportFailure
from papertrail.main import create_app
from papertrail.repository import Repository


def test_history_idempotency_and_paper_scope_survive_restart(client, settings, monkeypatch):
    paper = upload(client).json()["paper"]
    path = f"/api/papers/{paper['id']}/questions"
    repository = client.app.state.repository
    runs = []

    def run(question_id, paper_id, question):
        runs.append(question_id)
        repository.finish_question(
            question_id,
            {
                "status": "answered",
                "claims": [
                    {
                        "text": "测试原文事实",
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
                "message": "",
                "support_status": "ai_checked",
                "trace": {"fixture": True},
            },
        )

    monkeypatch.setattr(client.app.state.questions, "run", run)
    body = {"question": "  研究问题是什么？  ", "request_id": str(uuid4())}
    submitted = client.post(path, json=body)
    assert submitted.status_code == 202
    row = client.get(path).json()[0]
    assert row["question"] == "研究问题是什么？"
    assert row["status"] == "answered"
    assert client.post(path, json=body).json()["id"] == row["id"]
    assert len(runs) == 1
    assert client.post(path, json={**body, "question": "另一个问题"}).status_code == 409
    assert client.get(f"/api/papers/{uuid4()}/questions/{row['id']}").status_code == 404
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(path + "/" + row["id"]).json() == row


def test_pending_question_blocks_new_request_and_recovers(settings, client, monkeypatch):
    paper = upload(client).json()["paper"]
    repository = Repository(settings)
    row, created = repository.create_question(UUID(paper["id"]), uuid4(), "待处理问题")
    assert created
    with pytest.raises(ImportFailure, match="已有问题"):
        repository.create_question(UUID(paper["id"]), uuid4(), "第二题")
    repository.progress_question(row["id"], "generating")
    with TestClient(create_app(settings)) as restarted:
        found = restarted.get(f"/api/papers/{paper['id']}/questions/{row['id']}").json()
        assert found["status"] == "failed"
        assert found["error_code"] == "interrupted"
        assert "费用可能未知" in found["message"]
        assert repository.create_question(UUID(paper["id"]), uuid4(), "恢复后提问")[1]


def test_second_server_cannot_interrupt_first_servers_pending_work(settings):
    exclusive = replace(settings, exclusive_service=True)
    with TestClient(create_app(exclusive)) as first:
        paper = upload(first).json()["paper"]
        repository = first.app.state.repository
        row, _ = repository.create_question(UUID(paper["id"]), uuid4(), "真实服务仍在处理")
        with pytest.raises(RuntimeError, match="已有服务"):
            with TestClient(create_app(exclusive)):
                pass
        assert repository.question(UUID(paper["id"]), row["id"])["status"] == "pending"
    with TestClient(create_app(exclusive)) as restarted:
        recovered = restarted.app.state.repository.question(UUID(paper["id"]), row["id"])
        assert recovered["error_code"] == "interrupted"


def test_lost_guard_stops_model_calls_and_new_questions_but_keeps_history_readable(
    settings, monkeypatch
):
    import httpx2 as httpx

    from papertrail.model import ModelClient

    for key, value in {
        "PAPERTRAIL_MODEL_BASE_URL": "https://provider.test/v1",
        "PAPERTRAIL_MODEL_API_KEY": "test-guard-key",
        "PAPERTRAIL_MODEL_NAME": "test-guard-model",
        "PAPERTRAIL_MODEL_BUDGET": "1",
        "PAPERTRAIL_MODEL_INPUT_PRICE_PER_MILLION": "1",
        "PAPERTRAIL_MODEL_OUTPUT_PRICE_PER_MILLION": "1",
        "PAPERTRAIL_MODEL_CURRENCY": "USD",
        "PAPERTRAIL_MODEL_BUDGET_SCOPE": "guard-test",
    }.items():
        monkeypatch.setenv(key, value)
    exclusive = replace(settings, exclusive_service=True)
    with TestClient(create_app(exclusive)) as first:
        paper = upload(first).json()["paper"]
        repository = first.app.state.repository
        requests = []

        def handler(request):
            requests.append(request)
            # Drop only this temporary test database's guard session. A DB restart
            # likewise destroys it, while subsequent repository connections can recover.
            with repository.connect() as conn:
                row = conn.execute(
                    "SELECT pid FROM pg_locks WHERE locktype = 'advisory' "
                    "AND classid = 0 AND objid = 18091803 AND objsubid = 1 "
                    "AND database = (SELECT oid FROM pg_database "
                    "WHERE datname = current_database())"
                ).fetchone()
                assert row is not None
                assert conn.execute(
                    "SELECT pg_terminate_backend(%s) AS stopped", (row["pid"],)
                ).fetchone()["stopped"]
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": (
                                    '{"search_queries":["Alpha evidence physical page one"],'
                                    '"requirements":["What is Alpha evidence?"]}'
                                )
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 30, "completion_tokens": 10},
                },
            )

        def local_model(config, **kwargs):
            return ModelClient(config, transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr("papertrail.model.ModelClient", local_model)
        path = f"/api/papers/{paper['id']}/questions"
        assert (
            first.post(
                path, json={"question": "Alpha evidence 是什么？", "request_id": str(uuid4())}
            ).status_code
            == 202
        )
        history = first.get(path).json()
        assert history[0]["status"] == "failed"
        assert history[0]["error_code"] == "service_restart_required"
        assert len(requests) == len(history[0]["trace"]["ledger"]["calls"]) == 1
        assert first.get(f"/api/papers/{paper['id']}").status_code == 200
        assert first.get(f"/api/papers/{paper['id']}/pages/0").status_code == 200

        def assert_original_refuses_new_question():
            response = first.post(path, json={"question": "再次提问", "request_id": str(uuid4())})
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "service_restart_required"
            assert len(first.get(path).json()) == 1

        assert_original_refuses_new_question()
        # A fresh application may acquire the released lock. The original remains
        # unable to regain permission, even after the new application's shutdown.
        with TestClient(create_app(exclusive)) as restarted:
            restarted.app.state.repository.require_service_guard()
            assert_original_refuses_new_question()
        assert_original_refuses_new_question()


def test_invalid_question_and_unknown_paper_never_start(client):
    paper = upload(client).json()["paper"]
    path = f"/api/papers/{paper['id']}/questions"
    for question in (" ", "x" * 2001, None, "bad\x00text"):
        assert (
            client.post(path, json={"question": question, "request_id": str(uuid4())}).status_code
            == 422
        )
    assert (
        client.post(
            f"/api/papers/{uuid4()}/questions",
            json={
                "question": "test",
                "request_id": str(uuid4()),
            },
        ).status_code
        == 404
    )
    assert client.get(path).json() == []


def test_invalid_unicode_request_is_safe_json(client):
    paper = upload(client).json()["paper"]
    response = client.post(
        f"/api/papers/{paper['id']}/questions",
        content='{"question":"\\ud800","request_id":"' + str(uuid4()) + '"}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_lost_terminal_write_expires_without_restart(client, settings):
    paper = upload(client).json()["paper"]
    repository = Repository(settings)
    row, _ = repository.create_question(UUID(paper["id"]), uuid4(), "写入中断")
    with repository.connect() as conn:
        conn.execute(
            "UPDATE questions SET created_at = now() - interval '6 minutes' WHERE id = %s",
            (row["id"],),
        )
    assert repository.questions(UUID(paper["id"]))[0]["error_code"] == "interrupted"
    assert repository.create_question(UUID(paper["id"]), uuid4(), "可以继续")[1]


def test_ledger_snapshot_failure_still_saves_terminal_state(client, monkeypatch):
    for name in ("PAPERTRAIL_MODEL_API_KEY", "PAPERTRAIL_MODEL_NAME", "PAPERTRAIL_MODEL_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    def fail_snapshot(self):
        raise OSError("transient storage fault")

    monkeypatch.setattr(CallLedger, "snapshot", fail_snapshot)
    paper = upload(client).json()["paper"]
    path = f"/api/papers/{paper['id']}/questions"
    assert (
        client.post(path, json={"question": "问题", "request_id": str(uuid4())}).status_code == 202
    )
    row = client.get(path).json()[0]
    assert row["status"] == "failed"
    assert row["trace"]["ledger"]["status"] == "unavailable"


def test_nonfinite_model_config_never_leaves_running_task(client, monkeypatch):
    monkeypatch.setenv("PAPERTRAIL_MODEL_TIMEOUT", "nan")
    paper = upload(client).json()["paper"]
    path = f"/api/papers/{paper['id']}/questions"
    assert (
        client.post(path, json={"question": "问题", "request_id": str(uuid4())}).status_code == 202
    )
    row = client.get(path).json()[0]
    assert row["status"] == "failed"
    assert row["error_code"] == "model_not_configured"


def test_missing_model_saved_as_failure_and_config_safe(client, monkeypatch):
    for name in ("PAPERTRAIL_MODEL_API_KEY", "PAPERTRAIL_MODEL_NAME", "PAPERTRAIL_MODEL_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    paper = upload(client).json()["paper"]
    path = f"/api/papers/{paper['id']}/questions"
    result = client.post(path, json={"question": "问题是什么？", "request_id": str(uuid4())})
    assert result.status_code == 202
    row = client.get(path).json()[0]
    assert row["status"] == "failed"
    assert row["error_code"] == "model_not_configured"
    assert row["trace"]["ledger"]["calls"] == []
    config = client.get("/api/config").json()
    assert config["model"]["configured"] is False
    assert "api_key" not in str(config).lower()


def test_budget_reserves_before_call_and_persists_unknown_consumption(client, settings):
    from papertrail.model import ModelError

    paper = upload(client).json()["paper"]
    repository = Repository(settings)
    row, _ = repository.create_question(UUID(paper["id"]), uuid4(), "预算测试")
    budget = Budget(Decimal("0.003"), Decimal("1"), Decimal("2"), "USD", "test")
    ledger = CallLedger(repository, row["id"], budget)
    metadata = {"stage": "query", "input_token_upper_bound": 1000, "max_output_tokens": 1000}
    ledger.before_call(metadata)
    # A timeout without usage keeps the full reservation; restarting cannot erase it.
    ledger.record_call({"stage": "query", "error_code": "model_timeout", "usage": None})
    with pytest.raises(ModelError):
        CallLedger(repository, row["id"], budget).before_call(metadata)
    saved = ledger.snapshot()
    assert len(saved["calls"]) == 1
    assert saved["unknown_cost_calls"] == 1
    assert saved["calls"][0]["actual_cost"] is None


def test_budget_actual_usage_releases_only_known_unused_reservation(client, settings):
    paper = upload(client).json()["paper"]
    repository = Repository(settings)
    row, _ = repository.create_question(UUID(paper["id"]), uuid4(), "预算测试")
    budget = Budget(Decimal("0.006"), Decimal("1"), Decimal("2"), "USD", "test-actual")
    ledger = CallLedger(repository, row["id"], budget)
    metadata = {"stage": "query", "input_token_upper_bound": 1000, "max_output_tokens": 1000}
    ledger.before_call(metadata)
    ledger.record_call({"usage": {"prompt_tokens": 100, "completion_tokens": 50}})
    assert Decimal(ledger.snapshot()["estimated_cost"]) == Decimal("0.0002")
    ledger.before_call(metadata)
    assert len(ledger.snapshot()["calls"]) == 2


def test_missing_budget_refuses_call_before_reservation(client, settings):
    from papertrail.model import ModelError

    paper = upload(client).json()["paper"]
    repository = Repository(settings)
    row, _ = repository.create_question(UUID(paper["id"]), uuid4(), "预算未配置")
    ledger = CallLedger(repository, row["id"], None)
    with pytest.raises(ModelError):
        ledger.before_call({})
    assert ledger.snapshot()["calls"] == []


@pytest.mark.parametrize("cap", [None, "", "0", "-1", "1001", "1.5", "invalid", "1", "1000"])
def test_provider_quota_requires_explicit_bounded_call_cap(monkeypatch, cap):
    for key in (
        "PAPERTRAIL_MODEL_BUDGET",
        "PAPERTRAIL_MODEL_INPUT_PRICE_PER_MILLION",
        "PAPERTRAIL_MODEL_OUTPUT_PRICE_PER_MILLION",
        "PAPERTRAIL_MODEL_CURRENCY",
        "PAPERTRAIL_MODEL_MAX_CALLS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PAPERTRAIL_MODEL_BUDGET_MODE", "provider_quota")
    if cap is not None:
        monkeypatch.setenv("PAPERTRAIL_MODEL_MAX_CALLS", cap)
    budget = Budget.from_env()
    if cap in {"1", "1000"}:
        assert budget.mode == "provider_quota"
        assert budget.max_calls == int(cap)
        assert budget.currency == "USD"
    else:
        assert budget is None
    monkeypatch.delenv("PAPERTRAIL_MODEL_BUDGET_MODE", raising=False)
    assert Budget.from_env() is None  # A cap alone never implicitly enables quota mode.


def test_quota_counts_existing_priced_calls_and_keeps_known_usage_cost_unknown(client, settings):
    from papertrail.model import ModelError

    paper = upload(client).json()["paper"]
    repository = Repository(settings)
    metadata = {"stage": "query", "input_token_upper_bound": 1000, "max_output_tokens": 1000}
    first, _ = repository.create_question(UUID(paper["id"]), uuid4(), "原有按价调用")
    priced = Budget(Decimal("1"), Decimal("1"), Decimal("2"), "USD", "same-scope")
    ledger = CallLedger(repository, first["id"], priced)
    ledger.before_call(metadata)
    ledger.record_call({"usage": {"prompt_tokens": 30, "completion_tokens": 10}})
    ledger.before_call(metadata)  # An unfinished reservation counts as another call.
    repository.finish_question(first["id"], {"status": "failed"})

    second, _ = repository.create_question(UUID(paper["id"]), uuid4(), "使用既有中转额度")
    quota = Budget(Decimal(0), Decimal(0), Decimal(0), "USD", "same-scope", "provider_quota", 2)
    with pytest.raises(ModelError) as capped:
        CallLedger(repository, second["id"], quota).before_call(metadata)
    assert capped.value.code == "call_limit_exceeded"

    quota = replace(quota, max_calls=3)
    ledger = CallLedger(repository, second["id"], quota)
    ledger.before_call(metadata)
    ledger.record_call({"usage": {"prompt_tokens": 500, "completion_tokens": 200}})
    saved = ledger.snapshot()
    assert saved["estimated_cost"] is None
    assert saved["known_cost_subtotal"] == "0"
    assert saved["estimated_cost_scope"] == "known_calls_only"
    assert saved["unknown_cost_calls"] == 1
    call = saved["calls"][0]
    assert call["reserved_cost"] == "0" and call["actual_cost"] is None
    assert call["details"]["estimated_cost"] is None
    assert call["details"]["price_per_million"] is None
    assert call["details"]["cost_source"] == "unknown_provider_rates"
    assert call["details"]["reserved_cost_purpose"] == "call_slot_only_not_monetary"

    # A fresh repository and ledger cannot reset this scope's accumulated three calls.
    with pytest.raises(ModelError) as capped:
        CallLedger(Repository(settings), second["id"], quota).before_call(metadata)
    assert capped.value.code == "call_limit_exceeded"
    assert len(ledger.snapshot()["calls"]) == 1
    with pytest.raises(ModelError) as unpriced:
        CallLedger(repository, second["id"], priced).before_call(metadata)
    assert unpriced.value.code == "budget_mode_conflict"
