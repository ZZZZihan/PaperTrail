import importlib.util
from io import BytesIO
from pathlib import Path

import httpx2 as httpx
import pytest
from pypdf import PdfReader

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


@pytest.mark.parametrize(
    ("question", "status", "code"),
    [
        ("正常", "answered", None),
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
    if status == "answered":
        assert result["claims"][0]["citations"][0]["page_index"] == 2
        assert "离线界面测试片段" in result["claims"][0]["text"]
        assert fixture.FIXTURE_MODEL == result["trace"]["calls"][0]["returned_model"]
