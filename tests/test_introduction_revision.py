"""One content revision is distinct from retrying failed provider requests."""

import json
import time
from types import SimpleNamespace

import httpx2 as httpx
import pytest
from test_introduction import PAGES, PAPER, introduction_client, introduction_output
from test_model import config, response

from papertrail.introduction import introduce_paper
from papertrail.model import ModelClient


def verdicts(data, *, supported):
    return {
        "verdicts": [
            {"claim_index": i, "supported": supported, "reason": "删除未被该段支持的额外断言。"}
            for i in range(len(data["claims"]))
        ]
    }


def test_citation_repair_has_all_source_and_specific_feedback_without_checking_bad_draft():
    generations = 0
    inputs = []

    def generate(result):
        nonlocal generations
        generations += 1
        if generations == 1:
            result["introduction"]["problem"]["citations"] = [{"chunk_id": "nonexistent-id"}]
        return result

    result = introduce_paper(
        PAPER, PAGES, client=introduction_client(generate=generate, observed=inputs)
    )
    assert result["status"] == "answered" and result["trace"]["call_count"] == 3
    assert [call["stage"] for call in result["trace"]["calls"]] == [
        "introduction_generate",
        "introduction_revise",
        "introduction_verify",
    ]
    first, second = result["trace"]["attempts"]
    assert first["citation_validation"] == "failed" and first["support_verdicts"] == []
    assert first["feedback"][0]["field"] == "problem"
    assert first["feedback"][0]["unknown_chunk_ids"] == ["nonexistent-id"]
    assert inputs[1]["passages"] == inputs[0]["passages"]
    assert inputs[1]["draft"] == first["draft"] and inputs[1]["feedback"] == first["feedback"]
    assert second["citation_validation"] == "passed" and second["feedback"] == []
    assert "nonexistent-id" not in json.dumps(result["introduction"])


def test_failed_support_gets_only_one_revision_and_all_claims_checked_again():
    checks = 0
    inputs, stages = [], []

    def verify(data):
        nonlocal checks
        checks += 1
        return verdicts(data, supported=checks == 2)

    result = introduce_paper(
        PAPER,
        PAGES,
        client=introduction_client(verify=verify, observed=inputs),
        progress=stages.append,
    )
    assert result["status"] == "answered" and result["trace"]["call_count"] == 4
    assert stages.count("revising") == 1 and stages.count("verifying") == 2
    assert len(inputs[1]["claims"]) == len(inputs[3]["claims"]) == 7
    first, second = result["trace"]["attempts"]
    assert len(first["feedback"]) == 7 and second["feedback"] == []
    assert inputs[2]["feedback"] == first["feedback"]
    assert all(item["code"] == "unsupported" and item["field"] for item in first["feedback"])


def test_revised_content_still_unsupported_never_publishes_and_never_revises_twice():
    result = introduce_paper(
        PAPER,
        PAGES,
        client=introduction_client(verify=lambda data: verdicts(data, supported=False)),
    )
    assert result["status"] == "insufficient_evidence"
    assert result["introduction"] is None and result["claims"] == []
    assert result["trace"]["call_count"] == 4 and len(result["trace"]["attempts"]) == 2
    assert all(attempt["feedback"] for attempt in result["trace"]["attempts"])


def test_structured_schema_error_is_repaired_with_explicit_feedback():
    generations = 0

    def generate(result):
        nonlocal generations
        generations += 1
        if generations == 1:
            result["introduction"]["terms"] = []
        return result

    result = introduce_paper(PAPER, PAGES, client=introduction_client(generate=generate))
    assert result["status"] == "answered" and result["trace"]["call_count"] == 3
    feedback = result["trace"]["attempts"][0]["feedback"][0]
    assert feedback["field"] == "introduction" and feedback["code"] == "invalid_output"


@pytest.mark.parametrize("failure", ["invalid_json", "transport", "incomplete_response"])
def test_provider_failure_is_not_a_content_revision(failure):
    requests = []

    def handler(request):
        requests.append(request)
        if failure == "transport":
            return httpx.Response(502, text="private-provider-error")
        if failure == "incomplete_response":
            return response(choices=[{"finish_reason": "length", "message": {}}])
        return response('{"introduction":')

    client = ModelClient(config(), transport=httpx.MockTransport(handler))
    result = introduce_paper(PAPER, PAGES, client=client)
    assert result["status"] == "failed"
    assert result["trace"]["call_count"] == len(requests) == 1
    assert len(result["trace"]["attempts"]) == 1 and result["introduction"] is None


def test_all_calls_share_original_deadline_and_timeout_stops_before_last_support(monkeypatch):
    start = time.monotonic()
    clock = {"now": start}
    monkeypatch.setattr(
        "papertrail.introduction.time", SimpleNamespace(monotonic=lambda: clock["now"])
    )
    requests, deadlines = [], []

    def handler(request):
        data = json.loads(json.loads(request.content)["messages"][1]["content"])
        requests.append(data)
        if "claims" in data:
            clock["now"] = start + 179
            result = verdicts(data, supported=False)
        else:
            if "draft" in data:
                clock["now"] = start + 181
            result = introduction_output(data["passages"][0]["chunk_id"])
        return response(json.dumps(result))

    client = ModelClient(config(), transport=httpx.MockTransport(handler))
    complete = client.complete_json

    def record_deadline(stage, messages, *, deadline=None):
        deadlines.append(deadline)
        return complete(stage, messages, deadline=deadline)

    monkeypatch.setattr(client, "complete_json", record_deadline)
    result = introduce_paper(PAPER, PAGES, client=client)
    assert result["error_code"] == "model_timeout" and result["introduction"] is None
    assert result["claims"] == [] and len(requests) == 3
    assert deadlines == [start + 180] * 3
    assert len(result["trace"]["attempts"]) == 2
