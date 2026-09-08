"""Protect frozen-source evidence from drift and accidental overwrite; no network/model use."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import shutil
from pathlib import Path

import pypdf
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_holdout_papers.py"
spec = importlib.util.spec_from_file_location("holdout_sources", SCRIPT)
assert spec is not None and spec.loader is not None
holdout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(holdout)


@pytest.fixture
def frozen_copy(tmp_path):
    target = tmp_path / "dataset"
    shutil.copytree(holdout.DATASET, target)
    return target


@pytest.fixture
def sample_pdf():
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    data = buffer.getvalue()
    paper = {
        "id": "example",
        "source_kind": "arxiv",
        "version_id": "2201.11903v6",
        "download_url": "https://arxiv.org/pdf/2201.11903v6",
        "local_path": str(holdout.SOURCE_DIRECTORY / "example.pdf"),
        "first_page_marker": "",
        "size_bytes": len(data),
        "sha256": holdout.sha256(data),
        "page_count": 1,
        "page_text_sha256": [holdout.sha256(b"")],
    }
    return paper, data


def no_network(*args, **kwargs):
    raise AssertionError("This path must never perform a network operation")


def test_tracked_freeze_has_four_independent_papers_and_explicit_unrun_status():
    bundle = holdout.read_frozen_dataset()
    manifest = bundle["manifest"]
    assert manifest["model_runs_at_freeze"] == 0
    assert manifest["dataset_status"] == "candidate_holdout"
    assert len(manifest["papers"]) == 4
    assert {p["id"] for p in manifest["papers"]}.isdisjoint({"react", "reflexion", "toolformer"})
    assert len(bundle["questions"]["questions"]) == 20
    assert all(q["human_review"]["status"] == "pending" for q in bundle["questions"]["questions"])


def test_changed_label_bytes_are_rejected_without_rewriting_checksums(frozen_copy):
    original = (frozen_copy / "checksums.sha256").read_bytes()
    with (frozen_copy / "questions.json").open("ab") as out:
        out.write(b" ")
    with pytest.raises(ValueError, match="checksum mismatch"):
        holdout.read_frozen_dataset(frozen_copy)
    assert (frozen_copy / "checksums.sha256").read_bytes() == original


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "escape"])
def test_incomplete_or_unsafe_checksum_catalog_is_rejected(frozen_copy, mutation):
    path = frozen_copy / "checksums.sha256"
    lines = path.read_text().splitlines()
    if mutation == "missing":
        lines.pop()
    elif mutation == "duplicate":
        lines.append(lines[0])
    else:
        lines.append("0" * 64 + "  ../questions.json")
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError):
        holdout.read_frozen_dataset(frozen_copy)


def test_verify_only_missing_source_never_downloads_or_creates_directories(
    tmp_path, sample_pdf, monkeypatch
):
    paper, _ = sample_pdf
    monkeypatch.setattr(holdout, "build_opener", no_network)
    with pytest.raises(FileNotFoundError, match="Missing frozen source"):
        holdout.read_or_fetch_source(paper, root=tmp_path)
    assert not (tmp_path / "data").exists()


def test_existing_wrong_bytes_never_overwritten_even_with_fetch_enabled(
    tmp_path, sample_pdf, monkeypatch
):
    paper, _ = sample_pdf
    target = tmp_path / paper["local_path"]
    target.parent.mkdir(parents=True)
    target.write_bytes(b"source changed")
    monkeypatch.setattr(holdout, "build_opener", no_network)
    with pytest.raises(ValueError, match="source bytes changed"):
        holdout.read_or_fetch_source(paper, root=tmp_path, fetch_missing=True)
    assert target.read_bytes() == b"source changed"


def test_existing_valid_source_does_not_use_network(tmp_path, sample_pdf, monkeypatch):
    paper, data = sample_pdf
    target = tmp_path / paper["local_path"]
    target.parent.mkdir(parents=True)
    target.write_bytes(data)
    monkeypatch.setattr(holdout, "build_opener", no_network)
    assert holdout.read_or_fetch_source(paper, root=tmp_path, fetch_missing=True) == [""]
    assert target.read_bytes() == data


def test_extraction_drift_fails_even_when_pdf_bytes_match(sample_pdf):
    paper, data = sample_pdf
    changed = copy.deepcopy(paper)
    changed["page_text_sha256"] = [holdout.sha256(b"different extraction")]
    with pytest.raises(ValueError, match="extraction changed"):
        holdout.validate_pdf(changed, data)


@pytest.mark.parametrize(
    "url",
    [
        "https://arxiv.org/pdf/2201.11903",
        "http://arxiv.org/pdf/2201.11903v6",
        "https://example.com/pdf/2201.11903v6",
        "https://arxiv.org/pdf/2201.11903v6?version=latest",
    ],
)
def test_source_url_must_match_exact_allowed_version(sample_pdf, url):
    paper, _ = sample_pdf
    paper["download_url"] = url
    with pytest.raises(ValueError):
        holdout.validate_download_url(paper)


def test_source_symlink_is_rejected_without_modifying_target(tmp_path, sample_pdf):
    paper, data = sample_pdf
    other = tmp_path / "other.pdf"
    other.write_bytes(data)
    target = tmp_path / paper["local_path"]
    target.parent.mkdir(parents=True)
    target.symlink_to(other)
    with pytest.raises(ValueError, match="symlink"):
        holdout.read_or_fetch_source(paper, root=tmp_path, fetch_missing=True)
    assert other.read_bytes() == data


def test_new_download_must_validate_before_publish(tmp_path, sample_pdf, monkeypatch):
    paper, _ = sample_pdf

    class Response:
        def open(self, request, timeout):
            return io.BytesIO(b"unexpected upstream edition")

    monkeypatch.setattr(holdout, "build_opener", lambda *args: Response())
    with pytest.raises(ValueError, match="source bytes changed"):
        holdout.read_or_fetch_source(paper, root=tmp_path, fetch_missing=True)
    assert not (tmp_path / "data").exists()


def test_publication_race_preserves_winning_file_and_cleans_temporary(
    tmp_path, sample_pdf, monkeypatch
):
    paper, data = sample_pdf
    target = tmp_path / paper["local_path"]

    class Response:
        def open(self, request, timeout):
            return io.BytesIO(data)

    def raced_link(temporary, destination):
        Path(destination).write_bytes(b"concurrent writer")
        raise FileExistsError(destination)

    monkeypatch.setattr(holdout, "build_opener", lambda *args: Response())
    monkeypatch.setattr(holdout.os, "link", raced_link)
    with pytest.raises(FileExistsError):
        holdout.read_or_fetch_source(paper, root=tmp_path, fetch_missing=True)
    assert target.read_bytes() == b"concurrent writer"
    assert list(target.parent.iterdir()) == [target]


def test_human_acceptance_and_label_rewrite_cannot_hide_behind_valid_json(frozen_copy):
    # The frozen checksum binds provenance as well as questions and answer keys.
    path = frozen_copy / "questions.json"
    questions = json.loads(path.read_text())
    questions["questions"][0]["human_review"]["status"] = "passed"
    path.write_text(json.dumps(questions))
    with pytest.raises(ValueError, match="checksum mismatch"):
        holdout.read_frozen_dataset(frozen_copy)
