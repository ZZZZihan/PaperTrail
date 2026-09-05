import asyncio
import json

import httpx2 as httpx
import pytest

from papertrail.model import ModelClient, ModelConfig, ModelError


def config(**kwargs):
    return ModelConfig(
        base_url="https://provider.test/v1",
        api_key="secret-test-key",
        model_name="test-model",
        **kwargs,
    )


MESSAGES = [{"role": "system", "content": "Return JSON."}, {"role": "user", "content": "中文问题"}]


def response(content='{"ok":true}', **kwargs):
    return httpx.Response(
        200,
        json={
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40},
            "model": "test-model-version",
            **kwargs,
        },
    )


def test_json_request_and_safe_usage_recording():
    reserved, recorded = [], []

    def handler(request):
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["temperature"] == 0
        assert "tools" not in payload
        assert request.headers["authorization"] == "Bearer secret-test-key"
        return response()

    client = ModelClient(
        config(thinking="disabled"),
        transport=httpx.MockTransport(handler),
        before_call=reserved.append,
        record_call=recorded.append,
    )
    assert client.complete_json("query", MESSAGES) == {"ok": True}
    assert len(reserved) == len(recorded) == len(client.calls) == 1
    assert reserved[0]["input_token_upper_bound"] > len("中文问题".encode())
    call = recorded[0]
    assert call["status"] == "succeeded"
    assert call["usage"]["total_tokens"] == 40
    assert call["returned_model"] == "test-model-version"
    assert call["cost"] is None and call["cost_status"] == "unknown"
    assert "secret-test-key" not in json.dumps(recorded) + repr(client.config)
    assert "provider.test" not in json.dumps(recorded)


def test_missing_or_invalid_configuration_never_calls_network(monkeypatch):
    requests = []
    client = ModelClient(ModelConfig(), transport=httpx.MockTransport(requests.append))
    with pytest.raises(ModelError) as error:
        client.complete_json("query", MESSAGES)
    assert error.value.code == "model_not_configured"
    assert requests == client.calls == []
    monkeypatch.setenv("PAPERTRAIL_MODEL_TIMEOUT", "invalid")
    assert not ModelConfig.from_env().configured
    monkeypatch.setenv("PAPERTRAIL_MODEL_TIMEOUT", "nan")
    assert not ModelConfig.from_env().configured


def test_budget_refusal_does_not_count_as_an_external_call():
    records = []

    def refuse(_):
        raise ModelError("budget_exceeded", "预算不足")

    client = ModelClient(config(), before_call=refuse, record_call=records.append)
    with pytest.raises(ModelError, match="预算不足"):
        client.complete_json("query", MESSAGES)
    assert records == client.calls == []


@pytest.mark.parametrize("bad_content", ["not JSON", "[]", "```json\n{}\n```"])
def test_invalid_json_keeps_usage_and_does_not_retry(bad_content):
    records = []
    client = ModelClient(
        config(),
        transport=httpx.MockTransport(lambda _: response(bad_content)),
        record_call=records.append,
    )
    with pytest.raises(ModelError) as error:
        client.complete_json("generate", MESSAGES)
    assert error.value.code == "invalid_output"
    assert len(records) == 1
    assert records[0]["usage"]["prompt_tokens"] == 30
    assert records[0]["status"] == "failed"


def test_timeout_is_total_and_recorded_without_retry():
    async def delayed(_):
        await asyncio.sleep(0.1)
        return response()

    client = ModelClient(config(timeout=0.01), transport=httpx.MockTransport(delayed))
    with pytest.raises(ModelError) as error:
        client.complete_json("query", MESSAGES)
    assert error.value.code == "model_timeout"
    assert len(client.calls) == 1
    assert client.calls[0]["error_code"] == "model_timeout"
    assert client.calls[0]["usage"] is None


def test_provider_error_does_not_expose_body_or_credentials():
    client = ModelClient(
        config(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(401, text="secret-test-key raw internal credentials")
        ),
    )
    with pytest.raises(ModelError) as error:
        client.complete_json("query", MESSAGES)
    assert error.value.code == "model_failure"
    assert "secret" not in str(error.value) + json.dumps(client.calls)


@pytest.mark.parametrize("choices", [[], [None], [{"finish_reason": "length", "message": {}}]])
def test_malformed_or_truncated_choices_are_invalid_output(choices):
    client = ModelClient(
        config(), transport=httpx.MockTransport(lambda _: response(choices=choices))
    )
    with pytest.raises(ModelError) as error:
        client.complete_json("query", MESSAGES)
    assert error.value.code == "invalid_output"
    assert client.calls[0]["usage"]["total_tokens"] == 40


@pytest.mark.parametrize("unsafe", ["\\u0000", "\\ud800", "\\udfff"])
def test_model_json_rejects_postgresql_unsafe_unicode_and_preserves_usage(unsafe):
    content = '{"nested":[{"text":"' + unsafe + '"}]}'
    client = ModelClient(config(), transport=httpx.MockTransport(lambda _: response(content)))
    with pytest.raises(ModelError) as error:
        client.complete_json("generate", MESSAGES)
    assert error.value.code == "invalid_output"
    assert client.calls[0]["usage"]["total_tokens"] == 40
    assert "\\ud800" not in json.dumps(client.calls)


def test_unsafe_returned_model_and_nonfinite_config_never_enter_trace():
    client = ModelClient(
        config(), transport=httpx.MockTransport(lambda _: response(model="provider\0model"))
    )
    with pytest.raises(ModelError) as error:
        client.complete_json("query", MESSAGES)
    assert error.value.code == "invalid_output"
    assert "returned_model" not in client.calls[0]
    assert client.calls[0]["usage"]["total_tokens"] == 40
    assert config(timeout=float("nan")).public()["timeout_seconds"] is None
    assert config(timeout=float("inf")).public()["timeout_seconds"] is None


@pytest.mark.parametrize(
    "endpoint",
    ["https://provider.test:bad/v1", "https://provider.test:0/v1", "https://[invalid/v1"],
)
def test_invalid_endpoint_is_safe_to_show_in_configuration_status(endpoint):
    client_config = ModelConfig(base_url=endpoint, api_key="secret", model_name="model")
    assert not client_config.configured
    assert client_config.public()["provider_origin"] is None


def test_provider_provenance_excludes_private_endpoint_path():
    client_config = ModelConfig(
        base_url="https://provider.test:443/private-route/v1",
        api_key="secret",
        model_name="model",
    )
    public = client_config.public()
    assert public["provider_origin"] == "https://provider.test:443"
    assert len(public["endpoint_sha256"]) == 64
    assert "secret" not in json.dumps(public)
    assert "private-route" not in json.dumps(public)


@pytest.mark.parametrize(
    ("base_url", "allowed_origin", "expected"),
    [
        ("http://192.168.1.2:8080/private/v1", "", False),
        ("http://192.168.1.2:8080/private/v1", "http://192.168.1.2:8080", True),
        ("http://192.168.1.2:8080/v1", "http://192.168.1.2:8080/", True),
        ("http://relay.test/v1", "http://RELAY.test:80", True),
        ("http://relay.test:80/v1", "http://relay.test", True),
        ("http://192.168.1.2:8081/v1", "http://192.168.1.2:8080", False),
        ("http://192.168.1.3:8080/v1", "http://192.168.1.2:8080", False),
        ("http://relay.test.evil/v1", "http://relay.test", False),
        ("http://relay.test/v1", "https://relay.test", False),
        ("http://relay.test/v1", "http://*.test", False),
        ("http://*.test/v1", "http://*.test", False),
        ("http://relay.test/v1", "http://relay.test,http://other.test", False),
        ("http://relay.test/v1", "http://relay.test/v1", False),
        ("http://relay.test/v1", "http://relay.test//", False),
        ("http://relay.test/v1", "http://user:password@relay.test", False),
        ("http://relay.test/v1", "http://@relay.test", False),
        ("http://relay.test/v1", "http://relay.test?", False),
        ("http://relay.test/v1", "http://relay.test#", False),
        ("http://relay.test/v1", "http://relay.test?key=secret", False),
        ("http://relay.test/v1", "http://relay.test#fragment", False),
        ("http://relay.test/v1", "http://relay.test:bad", False),
        ("http://relay.test/v1", "http://relay.test:0", False),
        ("http://relay.test/v1", "http://relay.test:65536", False),
        ("http://relay.test/v1", "http://re\nlay.test", False),
        ("http://relay.test/v1", "http://[invalid", False),
        ("http://@relay.test/v1", "http://relay.test", False),
        ("http://relay.test/v1?", "http://relay.test", False),
        ("http://relay.test/v1#", "http://relay.test", False),
        ("https://provider.test/v1", "", True),
        ("https://provider.test/v1", "invalid optional origin", True),
        ("http://127.0.0.1:8080/v1", "", True),
        ("http://localhost:8080/v1", "", True),
        ("http://localhost:8080/v1", "invalid optional origin", True),
        ("http://[2001:db8::1]:8080/v1", "http://[2001:db8::1]:8080", True),
        ("http://[2001:db8::2]:8080/v1", "http://[2001:db8::1]:8080", False),
    ],
)
def test_http_requires_exact_explicit_origin_without_changing_existing_support(
    base_url, allowed_origin, expected
):
    client_config = ModelConfig(
        base_url=base_url,
        api_key="local-test-secret",
        model_name="model",
        allow_http_origin=allowed_origin,
    )
    assert client_config.configured is expected
    assert client_config.public()["configured"] is expected


def test_http_origin_configuration_from_environment_is_safe_in_public_trace(monkeypatch):
    monkeypatch.setenv("PAPERTRAIL_MODEL_BASE_URL", "http://192.168.1.2:8080/private-route/v1")
    monkeypatch.setenv("PAPERTRAIL_MODEL_ALLOW_HTTP_ORIGIN", "http://192.168.1.2:8080")
    monkeypatch.setenv("PAPERTRAIL_MODEL_API_KEY", "local-test-secret")
    monkeypatch.setenv("PAPERTRAIL_MODEL_NAME", "relay-model")
    client_config = ModelConfig.from_env()
    assert client_config.configured
    assert client_config.public()["provider_origin"] == "http://192.168.1.2:8080"
    serialized = json.dumps(client_config.public()) + repr(client_config)
    assert "private-route" not in serialized
    assert "local-test-secret" not in serialized
    assert "allow_http_origin" not in client_config.public()


def test_unapproved_http_request_fails_before_transport_or_budget_reservation():
    calls, reservations = [], []
    client = ModelClient(
        ModelConfig(
            base_url="http://192.168.1.2:8080/v1",
            api_key="secret",
            model_name="model",
            allow_http_origin="http://192.168.1.3:8080",
        ),
        transport=httpx.MockTransport(calls.append),
        before_call=reservations.append,
    )
    with pytest.raises(ModelError) as error:
        client.complete_json("query", MESSAGES)
    assert error.value.code == "model_not_configured"
    assert calls == reservations == client.calls == []


def test_allowed_ipv6_origin_remains_bracketed_without_endpoint_path():
    client_config = ModelConfig(
        base_url="http://[2001:db8::1]:8080/private-route/v1",
        api_key="secret",
        model_name="model",
        allow_http_origin="http://[2001:db8::1]:8080",
    )
    assert client_config.public()["provider_origin"] == "http://[2001:db8::1]:8080"
    assert "private-route" not in json.dumps(client_config.public())


@pytest.mark.parametrize("profile", ["compatible", "openai"])
def test_explicit_profile_controls_payload_and_preserves_budget_upper_bound(profile):
    reservations = []

    def handler(request):
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["stream"] is False
        if profile == "openai":
            assert payload["max_completion_tokens"] == 1234
            assert payload["reasoning_effort"] == "none"
            assert "max_tokens" not in payload
            assert "temperature" not in payload
            assert "thinking" not in payload
        else:
            assert payload["max_tokens"] == 1234
            assert payload["temperature"] == 0
            assert "max_completion_tokens" not in payload
            assert "reasoning_effort" not in payload
        return response()

    client_config = config(profile=profile, max_output_tokens=1234)
    client = ModelClient(
        client_config, transport=httpx.MockTransport(handler), before_call=reservations.append
    )
    assert client.complete_json("query", MESSAGES) == {"ok": True}
    assert reservations[0]["max_output_tokens"] == 1234
    assert client.calls[0]["profile"] == profile
    public = client_config.public()
    assert public["profile"] == profile
    assert public["output_token_parameter"] == (
        "max_completion_tokens" if profile == "openai" else "max_tokens"
    )
    assert public["temperature"] == (None if profile == "openai" else 0)
    assert public["reasoning_effort"] == ("none" if profile == "openai" else None)
    assert public["thinking"] == ("not_sent" if profile == "openai" else "provider_default")


@pytest.mark.parametrize(
    ("profile", "thinking"),
    [
        ("openai", "enabled"),
        ("openai", "disabled"),
        ("unknown", ""),
        ("", ""),
    ],
)
def test_conflicting_or_unknown_profile_fails_before_network(profile, thinking):
    requests = []
    client = ModelClient(
        config(profile=profile, thinking=thinking), transport=httpx.MockTransport(requests.append)
    )
    assert client.config.configured is False
    with pytest.raises(ModelError) as error:
        client.complete_json("query", MESSAGES)
    assert error.value.code == "model_not_configured"
    assert requests == client.calls == []


def test_profile_from_environment_and_default_do_not_guess_model_name(monkeypatch):
    monkeypatch.setenv("PAPERTRAIL_MODEL_BASE_URL", "https://provider.test/v1")
    monkeypatch.setenv("PAPERTRAIL_MODEL_API_KEY", "unit-test-secret")
    monkeypatch.setenv("PAPERTRAIL_MODEL_NAME", "gpt-5.4-mini")
    monkeypatch.setenv("PAPERTRAIL_MODEL_THINKING", "")
    monkeypatch.delenv("PAPERTRAIL_MODEL_PROFILE", raising=False)
    assert ModelConfig.from_env().profile == "compatible"
    assert ModelConfig.from_env().public()["output_token_parameter"] == "max_tokens"
    monkeypatch.setenv("PAPERTRAIL_MODEL_PROFILE", "openai")
    selected = ModelConfig.from_env()
    assert selected.configured
    assert selected.profile == "openai"
    assert selected.public()["output_token_parameter"] == "max_completion_tokens"
