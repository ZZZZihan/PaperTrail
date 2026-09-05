"""Provider-output failure metadata diagnoses structure without storing raw content."""

import json

import httpx2 as httpx
import pytest
from test_model import MESSAGES, config, response

from papertrail.model import ModelClient, ModelError


@pytest.mark.parametrize(
    ("content", "issue", "first", "last"),
    [
        (' {"private":"do-not-log-secret",}\n', "invalid_json", ord("{"), ord("}")),
        ('```json\n{"private":"do-not-log-secret"}\n```', "invalid_json", ord("`"), ord("`")),
        ('["do-not-log-secret"]', "non_object", ord("["), ord("]")),
        ('{"private":"do-not-log-secret","bad":NaN}', "unsafe_json_value", ord("{"), ord("}")),
        (" \t\n", "invalid_json", None, None),
    ],
)
def test_structured_output_diagnostics_are_safe_and_failure_does_not_retry(
    content, issue, first, last, caplog
):
    records = []
    client = ModelClient(
        config(),
        transport=httpx.MockTransport(lambda _: response(content)),
        record_call=records.append,
    )
    with pytest.raises(ModelError) as error:
        client.complete_json("introduction_generate", MESSAGES)
    assert error.value.code == "invalid_output"
    assert len(client.calls) == len(records) == 1
    call = records[0]
    assert call["output_issue"] == issue
    assert call["output_shape"] == {
        "content_chars": len(content),
        "first_nonspace_codepoint": first,
        "last_nonspace_codepoint": last,
    }
    assert call["usage"]["total_tokens"] == 40 and call["status"] == "failed"
    assert len(call["response_sha256"]) == 64
    if issue == "invalid_json":
        with pytest.raises(json.JSONDecodeError) as malformed:
            json.loads(content)
        expected = malformed.value
        assert call["json_error"] == {
            "lineno": expected.lineno,
            "colno": expected.colno,
            "pos": expected.pos,
            "msg": expected.msg,
        }
    else:
        assert "json_error" not in call
    serialized = json.dumps(records) + str(error.value) + caplog.text
    assert "do-not-log-secret" not in serialized
    assert "secret-test-key" not in serialized
    assert "private" not in serialized


@pytest.mark.parametrize(
    ("choices", "issue"),
    [
        ([], "invalid_response_shape"),
        ([None], "invalid_choice"),
        ([{"finish_reason": "length", "message": {}}], "incomplete_response"),
        ([{"finish_reason": "stop", "message": {}}], "missing_content"),
        ([{"finish_reason": "stop", "message": {"content": None}}], "non_string_content"),
        (
            [{"finish_reason": "stop", "message": {"content": ["do-not-log-secret"]}}],
            "non_string_content",
        ),
    ],
)
def test_response_shape_errors_have_only_enumerated_diagnostics(choices, issue):
    client = ModelClient(
        config(), transport=httpx.MockTransport(lambda _: response(choices=choices))
    )
    with pytest.raises(ModelError):
        client.complete_json("introduction_generate", MESSAGES)
    call = client.calls[0]
    assert call["output_issue"] == issue
    assert "json_error" not in call and "output_shape" not in call
    assert "do-not-log-secret" not in json.dumps(call)


def test_success_does_not_gain_failure_metadata():
    client = ModelClient(config(), transport=httpx.MockTransport(lambda _: response()))
    assert client.complete_json("generate", MESSAGES) == {"ok": True}
    assert not {"output_issue", "output_shape", "json_error"}.intersection(client.calls[0])
