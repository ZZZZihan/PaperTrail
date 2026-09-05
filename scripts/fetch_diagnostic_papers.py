"""Fetch only the three frozen development PDFs, or verify their existing bytes.

No model calls, application imports, or library/database mutations are performed.
Existing source files are never overwritten. All paths are relative to repository root.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pypdf

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals" / "development-v0.1"
MAX_PDF_BYTES = 20 * 1024 * 1024


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_dataset_files() -> None:
    expected_names = {"manifest.json", "questions.json"}
    verified_names: set[str] = set()
    for line in (DATASET / "checksums.sha256").read_text().splitlines():
        expected, name = line.split("  ", 1)
        if name not in expected_names or name in verified_names:
            raise ValueError(f"Unexpected or duplicate dataset checksum entry: {name}")
        if sha256((DATASET / name).read_bytes()) != expected:
            raise ValueError(f"Frozen dataset checksum mismatch: {name}")
        verified_names.add(name)
    if verified_names != expected_names:
        raise ValueError("Missing frozen dataset checksum entries")


def validate_pdf(paper: dict, data: bytes) -> list[str]:
    if len(data) != paper["size_bytes"] or sha256(data) != paper["sha256"]:
        raise ValueError(
            f"Source bytes changed for {paper['id']}; do not replace the frozen source"
        )
    if not data.startswith(b"%PDF-"):
        raise ValueError(f"Source is not a PDF: {paper['id']}")
    pages = [
        (page.extract_text() or "").replace("\x00", "")
        for page in pypdf.PdfReader(io.BytesIO(data)).pages
    ]
    if len(pages) != paper["page_count"]:
        raise ValueError(f"Page count changed for {paper['id']}")
    if [sha256(page.encode()) for page in pages] != paper["page_text_sha256"]:
        raise ValueError(f"Page extraction changed for {paper['id']}; check locked pypdf version")
    if paper["version_id"] not in pages[0]:
        raise ValueError(f"Version marker absent from first page: {paper['id']}")
    return pages


def fetch_source(paper: dict, verify_only: bool) -> list[str]:
    target = (ROOT / paper["local_path"]).resolve()
    source_root = (ROOT / "data" / "diagnostics" / "sources").resolve()
    if target.parent != source_root or target.suffix != ".pdf":
        raise ValueError(f"Source destination outside expected directory: {paper['id']}")
    if target.exists():
        return validate_pdf(paper, target.read_bytes())
    if verify_only:
        raise FileNotFoundError(f"Missing frozen PDF: {target}; run without --verify-only")
    url = paper["download_url"]
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "arxiv.org":
        raise ValueError("Only HTTPS arxiv.org frozen sources are permitted")
    if parsed.path != f"/pdf/{paper['version_id']}" or parsed.query or parsed.fragment:
        raise ValueError("The download URL must identify the exact frozen arXiv version")
    request = Request(url, headers={"User-Agent": "PaperTrail-development-diagnostics/0.1"})
    with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed HTTPS origin above
        data = response.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("Source exceeds diagnostic PDF download limit")
    pages = validate_pdf(paper, data)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        temporary.chmod(0o444)
        # A hard link publishes complete bytes atomically and fails if a file appeared meanwhile.
        os.link(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return pages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="Do not use the network")
    args = parser.parse_args()
    verify_dataset_files()
    manifest = json.loads((DATASET / "manifest.json").read_text())
    questions = json.loads((DATASET / "questions.json").read_text())["questions"]
    if pypdf.__version__ != manifest["extractor"]["version"]:
        raise ValueError("pypdf version differs from the frozen extractor; run uv sync --locked")
    pages_by_id = {}
    for paper in manifest["papers"]:
        pages_by_id[paper["id"]] = fetch_source(paper, args.verify_only)
        print(f"{paper['id']}: {paper['page_count']} pages, SHA-256 and page extraction verified")
    evidence_count = 0
    question_ids: set[str] = set()
    for question in questions:
        if question["id"] in question_ids:
            raise ValueError(f"Duplicate question ID: {question['id']}")
        question_ids.add(question["id"])
        pages = pages_by_id[question["paper_id"]]
        if question["human_review"]["status"] != "pending":
            raise ValueError("This frozen AI diagnostic set must not imply human acceptance")
        for evidence in question["expected_evidence"]:
            page_index = evidence["page_index"]
            if not 0 <= page_index < len(pages):
                raise ValueError(f"Invalid PDF page index: {question['id']}")
            if evidence["pdf_page_number"] != page_index + 1:
                raise ValueError(f"Display page number mismatch: {question['id']}")
            if (
                pages[page_index][evidence["char_start"] : evidence["char_end"]]
                != evidence["quote"]
            ):
                raise ValueError(f"Exact quote offsets changed: {question['id']}")
            evidence_count += 1
    answered = sum(question["expected_status"] == "answered" for question in questions)
    insufficient = sum(
        question["expected_status"] == "insufficient_evidence" for question in questions
    )
    if len(questions) != 15 or answered != 10 or insufficient != 5:
        raise ValueError("Expected exactly 15 diagnostic questions: 10 answered, 5 insufficient")
    print(f"{len(questions)} questions, {evidence_count} exact evidence locators verified")
    print("No model calls performed; AI source checks do not equal human semantic acceptance.")


if __name__ == "__main__":
    main()
