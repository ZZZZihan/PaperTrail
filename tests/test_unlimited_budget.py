"""Explicit unlimited quota retains serialized, durable per-call accounting."""

import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from test_import import upload

from papertrail.budget import Budget, CallLedger
from papertrail.model import ModelError
from papertrail.repository import Repository

METADATA = {"stage": "query", "input_token_upper_bound": 1000, "max_output_tokens": 1000}


def quota(monkeypatch, limit):
    monkeypatch.setenv("PAPERTRAIL_MODEL_BUDGET_MODE", "provider_quota")
    monkeypatch.setenv("PAPERTRAIL_MODEL_BUDGET_SCOPE", "unlimited-budget-test")
    monkeypatch.setenv("PAPERTRAIL_MODEL_MAX_CALLS", str(limit))
    monkeypatch.setenv("PAPERTRAIL_MODEL_CURRENCY", "USD")
    return Budget.from_env()


def test_unlimited_is_valid_model_status_without_changing_default_priced_mode(client, monkeypatch):
    unlimited = quota(monkeypatch, "unlimited")
    assert unlimited is not None and unlimited.max_calls is None
    for key, value in {
        "PAPERTRAIL_MODEL_BASE_URL": "https://quota.test/v1",
        "PAPERTRAIL_MODEL_NAME": "test-model",
        "PAPERTRAIL_MODEL_API_KEY": "test-only-key",
    }.items():
        monkeypatch.setenv(key, value)
    assert client.get("/api/config").json()["model"]["configured"] is True
    monkeypatch.setenv("PAPERTRAIL_MODEL_MAX_CALLS", "unlimted")
    assert client.get("/api/config").json()["model"]["configured"] is False
    monkeypatch.setenv("PAPERTRAIL_MODEL_MAX_CALLS", "unlimited")
    monkeypatch.delenv("PAPERTRAIL_MODEL_BUDGET_MODE")
    for key, value in {
        "PAPERTRAIL_MODEL_BUDGET": "0.05",
        "PAPERTRAIL_MODEL_INPUT_PRICE_PER_MILLION": "1",
        "PAPERTRAIL_MODEL_OUTPUT_PRICE_PER_MILLION": "2",
    }.items():
        monkeypatch.setenv(key, value)
    priced = Budget.from_env()
    assert priced.mode == "priced" and priced.limit == Decimal("0.05")
    assert priced.cost(1000, 1000) == Decimal("0.003")
    assert priced.scope == unlimited.scope


def test_unlimited_preserves_scope_old_calls_unknown_cost_and_numeric_restoration(
    client, settings, monkeypatch
):
    repository = client.app.state.repository
    paper_id = UUID(upload(client).json()["paper"]["id"])
    task, _ = repository.create_question(paper_id, uuid4(), "不限量账本测试")
    bounded = quota(monkeypatch, 1)
    ledger = CallLedger(repository, task["id"], bounded)
    ledger.before_call(METADATA)
    ledger.record_call({"usage": {"prompt_tokens": 20, "completion_tokens": 10}})
    with pytest.raises(ModelError) as exceeded:
        ledger.before_call(METADATA)
    assert exceeded.value.code == "call_limit_exceeded"

    unlimited = quota(monkeypatch, "unlimited")
    assert unlimited.scope == bounded.scope
    ledger = CallLedger(Repository(settings), task["id"], unlimited)
    ledger.before_call(METADATA)
    ledger.record_call({"usage": {"prompt_tokens": 50, "completion_tokens": 30}})
    ledger.before_call(METADATA)  # An unfinished reservation is retained too.
    snapshot = ledger.snapshot()
    assert snapshot["budget_mode"] == "provider_quota" and snapshot["max_calls"] is None
    assert len(snapshot["calls"]) == snapshot["unknown_cost_calls"] == 3
    assert snapshot["estimated_cost"] is None and snapshot["known_cost_subtotal"] == "0"
    assert [call["details"]["max_calls"] for call in snapshot["calls"]] == [1, None, None]
    assert all(call["actual_cost"] is None for call in snapshot["calls"])
    assert snapshot["calls"][-1]["completed_at"] is None

    restored = quota(monkeypatch, 3)
    assert restored.scope == unlimited.scope
    with pytest.raises(ModelError) as exceeded:
        CallLedger(Repository(settings), task["id"], restored).before_call(METADATA)
    assert exceeded.value.code == "call_limit_exceeded"
    repository.finish_question(task["id"], {"status": "failed"})
    with pytest.raises(ModelError) as interrupted:
        CallLedger(repository, task["id"], unlimited).before_call(METADATA)
    assert interrupted.value.code == "interrupted"
    assert len(ledger.snapshot()["calls"]) == 3


@pytest.mark.parametrize("limit", ["1", "unlimited"])
def test_unlimited_and_numeric_reservations_both_wait_for_same_database_lock(
    client, settings, monkeypatch, limit
):
    repository = client.app.state.repository
    paper_id = UUID(upload(client).json()["paper"]["id"])
    task, _ = repository.create_question(paper_id, uuid4(), "并发账本测试")
    budget = quota(monkeypatch, limit)

    def reserve():
        ledger = CallLedger(Repository(settings), task["id"], budget)
        try:
            ledger.before_call(METADATA)
        except ModelError as error:
            return error.code
        ledger.record_call({"usage": None})
        return "recorded"

    with ThreadPoolExecutor(max_workers=2) as workers:
        with repository.connect() as gate:
            gate.execute("SELECT pg_advisory_xact_lock(18091802)")
            futures = [workers.submit(reserve) for _ in range(2)]
            deadline = time.monotonic() + 3
            waiting = 0
            while time.monotonic() < deadline:
                waiting = gate.execute(
                    "SELECT count(*) AS total FROM pg_locks WHERE locktype = 'advisory' "
                    "AND classid = 0 AND objid = 18091802 AND objsubid = 1 AND NOT granted "
                    "AND database = (SELECT oid FROM pg_database "
                    "WHERE datname = current_database())"
                ).fetchone()["total"]
                if waiting == 2:
                    break
                time.sleep(0.01)
            assert waiting == 2 and all(not future.done() for future in futures)
            reserved = gate.execute("SELECT count(*) AS total FROM model_calls").fetchone()["total"]
            assert reserved == 0
        results = [future.result(timeout=5) for future in futures]
    expected = 2 if limit == "unlimited" else 1
    assert results.count("recorded") == expected
    assert results.count("call_limit_exceeded") == 2 - expected
    snapshot = CallLedger(repository, task["id"], budget).snapshot()
    assert len(snapshot["calls"]) == snapshot["unknown_cost_calls"] == expected
    assert all(call["completed_at"] is not None for call in snapshot["calls"])


def test_unlimited_quota_keeps_introduction_revision_and_task_time_limits(client, monkeypatch):
    from test_introduction_tasks import configure_fake_model

    requests = configure_fake_model(monkeypatch, cap="unlimited", support_passed=False)
    paper_id = upload(client).json()["paper"]["id"]
    path = f"/api/papers/{paper_id}/introduction"
    assert client.post(path, json={"request_id": str(uuid4())}).status_code == 202
    result = client.get(path).json()
    assert result["status"] == "insufficient_evidence"
    assert result["trace"]["max_content_revisions"] == 1
    assert result["trace"]["pipeline_timeout_seconds"] == 300
    assert result["trace"]["model_config"]["timeout_seconds"] == 120
    assert len(requests) == len(result["trace"]["ledger"]["calls"]) == 4
    assert result["trace"]["ledger"]["max_calls"] is None
