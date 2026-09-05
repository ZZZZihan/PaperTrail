"""Explicit introduction reasoning stays isolated from QA, accounting and cached cards."""

import json
from uuid import uuid4

import httpx2 as httpx
import pytest
from test_introduction import PAGES, PAPER, complete_coverage, introduction_output
from test_introduction_cache_versions import store_success

from papertrail.budget import Budget
from papertrail.introduction import introduce_paper, introduction_model_config
from papertrail.model import ModelClient, ModelConfig


def configure(monkeypatch, *, effort=None, profile="openai"):
    for key, value in {
        "PAPERTRAIL_MODEL_BASE_URL": "https://test.invalid/v1",
        "PAPERTRAIL_MODEL_API_KEY": "test-only-key",
        "PAPERTRAIL_MODEL_NAME": "test-model",
        "PAPERTRAIL_MODEL_PROFILE": profile,
        "PAPERTRAIL_MODEL_THINKING": "",
        "PAPERTRAIL_MODEL_TIMEOUT": "45",
        "PAPERTRAIL_MODEL_MAX_OUTPUT_TOKENS": "1800",
        "PAPERTRAIL_MODEL_BUDGET_MODE": "provider_quota",
        "PAPERTRAIL_MODEL_BUDGET_SCOPE": "introduction-reasoning-test",
        "PAPERTRAIL_MODEL_MAX_CALLS": "20",
    }.items():
        monkeypatch.setenv(key, value)
    if effort is None:
        monkeypatch.delenv("PAPERTRAIL_INTRODUCTION_REASONING_EFFORT", raising=False)
    else:
        monkeypatch.setenv("PAPERTRAIL_INTRODUCTION_REASONING_EFFORT", effort)


def transport(requests, *, supported=True):
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        data = json.loads(payload["messages"][1]["content"])
        if "claims" in data:
            output = {
                **complete_coverage(),
                "verdicts": [
                    {"claim_index": i, "supported": supported, "reason": "测试固定判定。"}
                    for i in range(len(data["claims"]))
                ],
            }
        elif "passages" in data:
            output = introduction_output(data["passages"][0]["chunk_id"])
        else:
            output = {
                "search_queries": ["unmatched-test-query"],
                "requirements": ["测试问题要点"],
            }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 100,
                    "completion_tokens_details": {"reasoning_tokens": 23},
                },
            },
        )

    return httpx.MockTransport(handler)


def install_client(monkeypatch, requests, *, supported=True):
    def factory(config, **kwargs):
        return ModelClient(config, transport=transport(requests, supported=supported), **kwargs)

    monkeypatch.setattr("papertrail.model.ModelClient", factory)
    monkeypatch.setattr("papertrail.introduction.ModelClient", factory)


@pytest.mark.parametrize("effort", [None, "none", "low"])
def test_default_and_direct_introduction_share_the_explicit_configuration(monkeypatch, effort):
    configure(monkeypatch, effort=effort)
    baseline = ModelConfig.from_env()
    adjusted = introduction_model_config(baseline)
    assert baseline.reasoning_effort == "none" and baseline.timeout == 45
    assert adjusted.reasoning_effort == (effort or "none")
    assert adjusted.timeout == 120 and adjusted.max_output_tokens == 5000
    assert adjusted.model_name == baseline.model_name
    assert introduction_model_config(None) == adjusted
    requests = []
    install_client(monkeypatch, requests)
    result = introduce_paper(PAPER, PAGES)
    assert result["status"] == "answered"
    assert result["trace"]["model_config"] == adjusted.public()
    assert len(requests) == 2
    assert all(p["reasoning_effort"] == (effort or "none") for p in requests)
    assert all(p["max_completion_tokens"] == 5000 for p in requests)
    assert all("temperature" not in p and "thinking" not in p for p in requests)


def test_explicit_client_keeps_its_config_transport_and_callbacks_despite_intro_env(monkeypatch):
    configure(monkeypatch, effort="invalid")
    requests, reserved, recorded = [], [], []
    supplied = ModelClient(
        ModelConfig.from_env(),
        transport=transport(requests),
        before_call=reserved.append,
        record_call=recorded.append,
    )
    original = supplied.config
    result = introduce_paper(PAPER, PAGES, client=supplied)
    assert result["status"] == "answered" and supplied.config is original
    assert len(requests) == len(reserved) == len(recorded) == len(supplied.calls) == 2
    assert result["trace"]["model_config"] == original.public()
    assert all(p["reasoning_effort"] == "none" for p in requests)
    assert all(p["max_completion_tokens"] == 1800 for p in requests)


@pytest.mark.parametrize("supported", [True, False])
def test_low_intro_upgrade_keeps_old_card_qa_and_ledger_boundaries(client, monkeypatch, supported):
    configure(monkeypatch, effort="low")
    original_scope = Budget.from_env().scope
    requests = []
    install_client(monkeypatch, requests, supported=supported)
    paper_id, _, old_id, old_intro, old_trace = store_success(
        client, pipeline="paper-introduction-v9"
    )
    path = f"/api/papers/{paper_id}/introduction"
    assert client.get(path).json()["introduction_outdated"] is True
    assert client.post(path, json={"request_id": str(uuid4())}).json()["id"] == str(old_id)
    assert requests == []
    body = {"request_id": str(uuid4()), "refresh_if_outdated": True}
    client.post(path, json=body)
    row = client.get(path).json()
    assert row["id"] != str(old_id)
    assert row["status"] == ("answered" if supported else "insufficient_evidence")
    trace = row["trace"]
    assert trace["pipeline_version"] == "paper-introduction-v10"
    assert trace["model_config"]["reasoning_effort"] == "low"
    assert trace["pipeline_timeout_seconds"] == 300
    assert trace["max_content_revisions"] == 1
    assert trace["max_citations_per_claim"] == 8
    assert trace["model_config"]["timeout_seconds"] == 120
    assert len(requests) == trace["call_count"] == (2 if supported else 4)
    assert all(p["model"] == "test-model" and p["reasoning_effort"] == "low" for p in requests)
    assert all(p["max_completion_tokens"] == 5000 for p in requests)
    for call in trace["ledger"]["calls"]:
        details = call["details"]
        assert details["reasoning_effort"] == "low" and details["reasoning_tokens"] == 23
        assert details["max_output_tokens"] == 5000
        assert details["usage"] == {"prompt_tokens": 100, "completion_tokens": 100}
        assert details["estimated_cost"] is None
    if not supported:
        assert row["introduction"] is None
        assert row["previous_introduction_id"] == str(old_id)
        assert row["previous_introduction"]["coverage"] == old_intro["coverage"]
    assert client.post(path, json=body).json()["id"] == row["id"]
    with client.app.state.repository.connect() as connection:
        stored = connection.execute(
            "SELECT introduction, trace FROM questions WHERE id = %s", (old_id,)
        ).fetchone()
    assert stored == {"introduction": old_intro, "trace": old_trace}
    qa_path = f"/api/papers/{paper_id}/questions"
    client.post(qa_path, json={"request_id": str(uuid4()), "question": "测试普通问题"})
    qa = client.get(qa_path).json()[0]
    assert qa["status"] == "insufficient_evidence" and qa["trace"]["call_count"] == 1
    assert qa["trace"]["model_config"]["reasoning_effort"] == "none"
    assert qa["trace"]["model_config"]["timeout_seconds"] == 45
    assert requests[-1]["reasoning_effort"] == "none"
    assert requests[-1]["max_completion_tokens"] == 1800
    with client.app.state.repository.connect() as connection:
        scopes = connection.execute("SELECT DISTINCT budget_scope FROM model_calls").fetchall()
    assert scopes == [{"budget_scope": original_scope}]


@pytest.mark.parametrize(
    ("profile", "effort"), [("openai", ""), ("openai", "invalid"), ("compatible", "low")]
)
def test_invalid_intro_configuration_fails_before_network_and_does_not_disable_qa(
    client, monkeypatch, profile, effort
):
    configure(monkeypatch, effort=effort, profile=profile)
    requests = []
    install_client(monkeypatch, requests)
    paper_id, _, _, _, _ = store_success(client, pipeline="paper-introduction-v9")
    path = f"/api/papers/{paper_id}/introduction"
    client.post(path, json={"request_id": str(uuid4()), "refresh_if_outdated": True})
    row = client.get(path).json()
    assert row["status"] == "failed" and row["error_code"] == "model_not_configured"
    assert row["trace"]["call_count"] == 0 and row["trace"]["ledger"]["calls"] == []
    assert requests == []
    qa_path = f"/api/papers/{paper_id}/questions"
    client.post(qa_path, json={"request_id": str(uuid4()), "question": "测试普通问题"})
    qa = client.get(qa_path).json()[0]
    assert qa["status"] == "insufficient_evidence" and len(requests) == 1
    assert requests[0].get("reasoning_effort") == ("none" if profile == "openai" else None)
