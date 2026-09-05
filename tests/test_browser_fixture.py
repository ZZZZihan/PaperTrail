import importlib.util
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from pypdf import PdfReader

from papertrail.introduction import INTRODUCTION_SCHEMA_VERSION, introduce_paper
from papertrail.model import ModelClient, ModelConfig
from papertrail.qa import answer_question

spec = importlib.util.spec_from_file_location(
    "papertrail_browser_fixture",
    Path(__file__).resolve().parents[1] / "scripts" / "browser_fixture.py",
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def test_fixture_pdf_preserves_physical_page_three_and_environment_is_isolated(tmp_path):
    reader = PdfReader(BytesIO(fixture.synthetic_pdf()))
    assert len(reader.pages) == 3
    assert "Alpha" in reader.pages[0].extract_text()
    assert reader.pages[1].extract_text() == ""
    assert fixture.OMEGA in reader.pages[2].extract_text()
    environment = fixture.fixture_environment(tmp_path, 12345)
    assert environment["PAPERTRAIL_DATA_DIR"] == str(tmp_path / "library")
    assert "12345/papertrail_browser_fixture" in environment["PAPERTRAIL_DATABASE_URL"]
    assert environment["PAPERTRAIL_MODEL_NAME"] == fixture.FIXTURE_MODEL
    assert environment["PAPERTRAIL_MODEL_BUDGET"] == "0"
    assert environment["PAPERTRAIL_MODEL_BUDGET_MODE"] == "priced"
    assert environment["PAPERTRAIL_MODEL_PROFILE"] == "compatible"
    assert environment["PAPERTRAIL_INTRODUCTION_REASONING_EFFORT"] == "none"


@pytest.mark.parametrize(
    ("question", "status", "code"),
    [
        ("正常", "answered", None),
        ("部分回答", "partial_answer", None),
        ("超时", "failed", "model_timeout"),
        ("失败", "failed", "model_failure"),
        ("无效引用", "failed", "invalid_citation"),
        ("证据不足", "insufficient_evidence", None),
    ],
)
def test_fixture_exercises_production_pipeline_without_real_network(question, status, code):
    reader = PdfReader(BytesIO(fixture.synthetic_pdf()))
    pages = [
        {"page_index": index, "text": page.extract_text()}
        for index, page in enumerate(reader.pages)
    ]
    client = ModelClient(
        ModelConfig(
            base_url="https://offline-fixture.invalid/v1",
            api_key="OFFLINE-NO-CREDENTIAL",
            model_name=fixture.FIXTURE_MODEL,
            timeout=0.01,
        ),
        transport=httpx.MockTransport(fixture.mock_completion),
    )
    result = answer_question(
        {"id": "offline-paper", "sha256": "offline-hash"}, pages, question, client=client
    )
    assert result["status"] == status
    assert result["error_code"] == code
    if status in {"answered", "partial_answer"}:
        assert result["claims"][0]["citations"][0]["page_index"] == 2
        assert "离线界面测试片段" in result["claims"][0]["text"]
        assert fixture.FIXTURE_MODEL == result["trace"]["calls"][0]["returned_model"]
        assert result["coverage"]["status"] == ("complete" if status == "answered" else "partial")


@pytest.mark.parametrize(
    ("scenario", "status", "error_code", "stages"),
    [
        ("success", "answered", None, ["introduction_generate", "introduction_verify"]),
        (
            "revision",
            "answered",
            None,
            [
                "introduction_generate",
                "introduction_verify",
                "introduction_revise",
                "introduction_verify",
            ],
        ),
        ("failure", "failed", "model_failure", ["introduction_generate"]),
        (
            "unsupported",
            "insufficient_evidence",
            None,
            [
                "introduction_generate",
                "introduction_verify",
                "introduction_revise",
                "introduction_verify",
            ],
        ),
        ("slow", "failed", "model_timeout", ["introduction_generate"]),
    ],
)
def test_intro_fixture_exercises_real_generation_revision_and_checking_contracts(
    scenario, status, error_code, stages
):
    reader = PdfReader(BytesIO(fixture.synthetic_pdf(scenario)))
    pages = [{"page_index": i, "text": page.extract_text()} for i, page in enumerate(reader.pages)]
    model = ModelClient(
        ModelConfig(
            base_url="https://offline-fixture.invalid/v1",
            api_key="OFFLINE-NO-CREDENTIAL",
            model_name=fixture.FIXTURE_MODEL,
            timeout=0.01,
        ),
        transport=httpx.MockTransport(fixture.mock_completion),
    )
    result = introduce_paper({"id": "offline-paper", "sha256": "offline-hash"}, pages, client=model)
    assert result["status"] == status and result["error_code"] == error_code
    assert [call["stage"] for call in model.calls] == stages
    if status == "answered":
        introduction = result["introduction"]
        assert introduction["schema_version"] == INTRODUCTION_SCHEMA_VERSION
        assert all(item["covered"] for item in introduction["coverage"])
        assert {claim["basis"] for claim in result["claims"]} == {
            "paper_statement",
            "author_interpretation",
            "teaching_example",
            "system_inference",
        }
        assert all(claim["citations"][0]["page_index"] == 2 for claim in result["claims"])
        assert all("OFFLINE" in claim["text"] for claim in result["claims"])
        assert "OFFLINE-REVISION-DRAFT" not in introduction["summary"]["text"]
    else:
        assert result["introduction"] is None and result["claims"] == []


@pytest.mark.parametrize("scenario", ["legacy-success", "legacy-failure"])
def test_legacy_fixture_preserves_real_cache_upgrade_and_request_idempotency(
    client, monkeypatch, tmp_path, scenario
):
    # The fixture's model and zero-priced ledger remain separate from real provider config.
    for key, value in fixture.fixture_environment(tmp_path, 12345).items():
        if key.startswith("PAPERTRAIL_MODEL_") or key == "PAPERTRAIL_INTRODUCTION_REASONING_EFFORT":
            monkeypatch.setenv(key, value)
    calls = []

    async def observed(request):
        calls.append(request)
        return await fixture.mock_completion(request)

    monkeypatch.setattr(
        "papertrail.model.ModelClient",
        lambda config, **kwargs: ModelClient(
            config, transport=httpx.MockTransport(observed), **kwargs
        ),
    )
    paper = client.post(
        "/api/papers",
        files={
            "file": (f"OFFLINE-{scenario}.pdf", fixture.synthetic_pdf(scenario), "application/pdf")
        },
    ).json()["paper"]
    paper["id"] = UUID(paper["id"])
    repository = client.app.state.repository
    fixture.seed_legacy_introduction(repository, paper)
    path = f"/api/papers/{paper['id']}/introduction"
    original = client.get(path).json()
    assert original["introduction_outdated"] is True
    assert original["trace"]["fixture"] == fixture.FIXTURE_MODEL
    assert "OFFLINE 旧版简介" in original["introduction"]["summary"]["text"]
    fixture.seed_legacy_introduction(repository, paper)  # Re-upload must not rewrite history.
    assert client.get(path).json() == original
    assert client.post(path, json={"request_id": str(uuid4())}).json()["id"] == original["id"]
    assert calls == []
    body = {"request_id": str(uuid4()), "refresh_if_outdated": True}
    pending = client.post(path, json=body).json()
    assert pending["status"] == "pending"
    assert pending["previous_introduction"] == original["introduction"]
    upgraded = client.get(path).json()
    assert upgraded["id"] != original["id"]
    if scenario == "legacy-success":
        assert upgraded["status"] == "answered" and not upgraded["introduction_outdated"]
        assert upgraded["introduction"]["schema_version"] == INTRODUCTION_SCHEMA_VERSION
        assert len(calls) == 2
    else:
        assert upgraded["status"] == "failed" and upgraded["error_code"] == "model_failure"
        assert upgraded["previous_introduction"] == original["introduction"]
        assert upgraded["introduction_outdated"] is True
        assert len(calls) == 1
    assert client.post(path, json=body).json() == upgraded
    assert len(calls) == (2 if scenario == "legacy-success" else 1)
    assert client.get(f"/api/papers/{paper['id']}/questions").json() == []


def test_legacy_seeding_requires_exact_synthetic_pdf_bytes(client):
    ordinary = client.post(
        "/api/papers",
        files={"file": ("OFFLINE-legacy-success.pdf", fixture.synthetic_pdf(), "application/pdf")},
    ).json()["paper"]
    ordinary["id"] = UUID(ordinary["id"])
    fixture.seed_legacy_introduction(client.app.state.repository, ordinary)
    assert client.get(f"/api/papers/{ordinary['id']}/introduction").json() is None
    with pytest.raises(ValueError, match="Unknown OFFLINE"):
        fixture.synthetic_pdf("unexpected")
