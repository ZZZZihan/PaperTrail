"""Verify the frozen candidate holdout, fetching missing exact PDFs only when requested.

The default is read-only/offline. --fetch-missing permits only the four frozen URLs;
existing bytes, labels and checksums are never overwritten or regenerated. No model,
application, database or library calls occur. Full contexts stay in source PDFs;
--context-report prints their exact text for a separate evaluator, never for tuning.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pypdf

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals" / "holdout-v0.2"
SOURCE_DIRECTORY = Path("data/diagnostics/holdout-v0.2/sources")
FROZEN_FILES = {"manifest.json", "questions.json", "evidence.json", "rubric.json"}
CATEGORIES = {
    "fact",
    "cross_section_conditions",
    "false_premise",
    "insufficient_evidence",
    "table_footnote_probe",
}
MAX_PDF_BYTES = 20 * 1024 * 1024


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_frozen_dataset(dataset: Path = DATASET) -> dict:
    """Fail on missing/extra checksum entries before parsing any frozen labels."""
    seen = set()
    for line in (dataset / "checksums.sha256").read_text().splitlines():
        expected, name = line.split("  ", 1)
        if name not in FROZEN_FILES or name in seen or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Unexpected, duplicate or malformed frozen checksum entry")
        if sha256((dataset / name).read_bytes()) != expected:
            raise ValueError(f"Frozen dataset checksum mismatch: {name}")
        seen.add(name)
    if seen != FROZEN_FILES:
        raise ValueError("Missing frozen checksum entries")
    return {
        name.removesuffix(".json"): json.loads((dataset / name).read_text())
        for name in sorted(FROZEN_FILES)
    }


def validate_pdf(paper: dict, data: bytes) -> list[str]:
    if len(data) != paper["size_bytes"] or sha256(data) != paper["sha256"]:
        raise ValueError(f"Frozen source bytes changed: {paper['id']}")
    if not data.startswith(b"%PDF-"):
        raise ValueError(f"Invalid PDF header: {paper['id']}")
    pages = [
        (page.extract_text() or "").replace("\x00", "")
        for page in pypdf.PdfReader(io.BytesIO(data)).pages
    ]
    if len(pages) != paper["page_count"]:
        raise ValueError(f"PDF page count changed: {paper['id']}")
    if [sha256(page.encode()) for page in pages] != paper["page_text_sha256"]:
        raise ValueError(f"PDF page extraction changed: {paper['id']}")
    if paper["first_page_marker"] not in pages[0]:
        raise ValueError(f"Source identity marker absent: {paper['id']}")
    return pages


def validate_download_url(paper: dict) -> str:
    url = paper["download_url"]
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.query or parsed.fragment:
        raise ValueError("Frozen source requires an exact HTTPS URL")
    if paper["source_kind"] == "arxiv":
        expected_host, expected_path = "arxiv.org", f"/pdf/{paper['version_id']}"
        if not re.fullmatch(r"\d{4}\.\d{4,5}v\d+", paper["version_id"]):
            raise ValueError("arXiv source must include a version")
    elif paper["source_kind"] == "acl_anthology":
        expected_host = "aclanthology.org"
        expected_path = f"/{paper['version_id']}.pdf"
        if paper["version_id"] != "2020.emnlp-main.550":
            raise ValueError("Unknown frozen ACL edition")
    else:
        raise ValueError("Unknown frozen source provider")
    if parsed.netloc != expected_host or parsed.path != expected_path:
        raise ValueError("URL differs from the frozen publication identity")
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise HTTPError(
            request.full_url, code, "Frozen source redirects are not permitted", headers, fp
        )


def read_or_fetch_source(
    paper: dict, *, fetch_missing: bool = False, root: Path = ROOT
) -> list[str]:
    target = root / paper["local_path"]
    source_root = (root / SOURCE_DIRECTORY).resolve()
    if target.is_symlink() or target.resolve().parent != source_root or target.suffix != ".pdf":
        raise ValueError("Source path is outside the frozen directory or is a symlink")
    url = validate_download_url(paper)
    if target.exists():
        return validate_pdf(paper, target.read_bytes())
    if not fetch_missing:
        raise FileNotFoundError(f"Missing frozen source: {target}; use --fetch-missing to fetch")
    request = Request(url, headers={"User-Agent": "PaperTrail-candidate-holdout/0.2"})
    with build_opener(NoRedirect).open(request, timeout=45) as response:
        data = response.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("Source exceeds the frozen PDF size limit")
    pages = validate_pdf(paper, data)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o444)
        os.link(temporary, target)  # Atomic and exclusive; an existing file wins any race.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return pages


def verify_annotations(bundle: dict, pages_by_id: dict[str, list[str]]) -> dict:
    manifest, question_set = bundle["manifest"], bundle["questions"]
    if (
        manifest["dataset_id"] != question_set["dataset_id"]
        or manifest["dataset_status"] != "candidate_holdout"
        or manifest["tuning_status_at_freeze"] != "never_used_for_tuning"
        or manifest["model_runs_at_freeze"] != 0
    ):
        raise ValueError("Candidate holdout identity/status mismatch")
    paper_ids = {paper["id"] for paper in manifest["papers"]}
    if len(manifest["papers"]) != 4 or len(paper_ids) != 4 or set(pages_by_id) != paper_ids:
        raise ValueError("Expected exactly four unique frozen papers")
    if paper_ids & {"react", "reflexion", "toolformer"}:
        raise ValueError("Development paper leaked into holdout")
    contexts = bundle["evidence"]["contexts"]
    evidence_by_id = {evidence["id"]: evidence for evidence in contexts}
    if len(evidence_by_id) != len(contexts):
        raise ValueError("Duplicate evidence ID")
    for evidence in contexts:
        pages = pages_by_id[evidence["paper_id"]]
        i, start, end = evidence["page_index"], evidence["context_start"], evidence["context_end"]
        if not 0 <= i < len(pages) or evidence["pdf_page_number"] != i + 1:
            raise ValueError("Evidence PDF page mismatch")
        page = pages[i]
        if not 0 <= start < end <= len(page):
            raise ValueError("Invalid evidence context bounds")
        if sha256(page[start:end].encode()) != evidence["context_sha256"]:
            raise ValueError("Evidence context checksum mismatch")
        a, b = evidence["char_start"], evidence["char_end"]
        if not start <= a < b <= end or page[a:b] != evidence["quote"]:
            raise ValueError("Exact evidence locator mismatch")
    seen_questions, seen_atoms, referenced = set(), set(), set()
    counts: dict[str, Counter] = {paper_id: Counter() for paper_id in paper_ids}
    condition_count = 0
    answer_point_count = 0
    for q in question_set["questions"]:
        if q["id"] in seen_questions or q["paper_id"] not in paper_ids:
            raise ValueError("Duplicate question or unknown paper")
        seen_questions.add(q["id"])
        counts[q["paper_id"]][q["category"]] += 1
        if q["human_review"]["status"] != "pending":
            raise ValueError("Frozen AI preparation must not imply human acceptance")
        refs = set(q["evidence_ids"])
        if not refs or len(refs) != len(q["evidence_ids"]):
            raise ValueError("Missing or duplicate question evidence references")
        if any(
            ref not in evidence_by_id or evidence_by_id[ref]["paper_id"] != q["paper_id"]
            for ref in refs
        ):
            raise ValueError("Evidence belongs to a different paper or is absent")
        referenced.update(refs)
        if [atom["text"] for atom in q["answer_points"]] != q["expected_answer_points"]:
            raise ValueError("Atomic and compatibility answer points disagree")
        for atom in q["answer_points"] + q["required_conditions"]:
            if atom["id"] in seen_atoms or not atom["text"].strip():
                raise ValueError("Duplicate or empty scoring atom")
            seen_atoms.add(atom["id"])
            if not atom["evidence_ids"] or not set(atom["evidence_ids"]) <= refs:
                raise ValueError("Scoring atom lacks in-scope evidence")
        condition_count += len(q["required_conditions"])
        answer_point_count += len(q["answer_points"])
        expected_evidence = [
            {
                key: evidence_by_id[ref][key]
                for key in (
                    "id",
                    "page_index",
                    "pdf_page_number",
                    "quote",
                    "char_start",
                    "char_end",
                    "section",
                )
            }
            for ref in q["evidence_ids"]
        ]
        if q["expected_evidence"] != expected_evidence:
            raise ValueError("Compatibility evidence differs from frozen contexts")
        if q["category"] == "insufficient_evidence":
            if (
                q["expected_status"] != "insufficient_evidence"
                or q["answer_points"]
                or q["required_conditions"]
                or not q["insufficient_reason"]
                or not q["insufficiency_audit"]["boundary"]
            ):
                raise ValueError("Insufficiency question lacks a scoped non-answer audit")
            audit = q["insufficiency_audit"]
            for pattern, expected_hits in audit["keyword_page_indices"].items():
                actual = [
                    i
                    for i in audit["search_page_indices"]
                    if re.search(pattern, pages_by_id[q["paper_id"]][i], re.I)
                ]
                if actual != expected_hits:
                    raise ValueError("Insufficiency keyword scan differs from frozen audit")
        elif q["expected_status"] != "answered" or not q["answer_points"]:
            raise ValueError("Answerable question is missing expected points")
        if q["category"] == "table_footnote_probe":
            probe = q["table_probe"]
            if (
                not probe["row_labels"]
                or not probe["column_labels"]
                or not probe["note_evidence_ids"]
                or not set(probe["note_evidence_ids"]) <= refs
                or not probe["visual_review_page_indices"]
            ):
                raise ValueError("Table probe lacks row/column/note/visual provenance")
    if any(count != Counter(dict.fromkeys(CATEGORIES, 1)) for count in counts.values()):
        raise ValueError("Expected five different categories for each of four papers")
    if referenced != set(evidence_by_id):
        raise ValueError("Unreferenced frozen context")
    totals = {
        "papers": 4,
        "questions": len(seen_questions),
        "answerable": 16,
        "insufficient": 4,
        "answer_points": answer_point_count,
        "required_conditions": condition_count,
        "contexts": len(contexts),
    }
    if totals != bundle["rubric"]["frozen_denominators"]:
        raise ValueError("Frozen scoring denominators disagree with annotations")
    return totals


def verify(*, dataset: Path = DATASET, root: Path = ROOT, fetch_missing: bool = False) -> tuple:
    bundle = read_frozen_dataset(dataset)
    if pypdf.__version__ != bundle["manifest"]["extractor"]["version"]:
        raise ValueError("pypdf differs from frozen extractor; use uv sync --locked")
    development = json.loads((root / "evals/development-v0.1/manifest.json").read_text())
    dev_hashes = {paper["sha256"] for paper in development["papers"]}
    if any(paper["sha256"] in dev_hashes for paper in bundle["manifest"]["papers"]):
        raise ValueError("Source PDF overlaps the development corpus")
    pages = {
        paper["id"]: read_or_fetch_source(paper, fetch_missing=fetch_missing, root=root)
        for paper in bundle["manifest"]["papers"]
    }
    return bundle, pages, verify_annotations(bundle, pages)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify-only", action="store_true", help="Read-only/offline (the default)")
    mode.add_argument("--fetch-missing", action="store_true", help="Fetch only missing frozen PDFs")
    parser.add_argument(
        "--context-report",
        action="store_true",
        help="Print full contexts for independent source review; do not tune on them",
    )
    args = parser.parse_args()
    bundle, pages, totals = verify(fetch_missing=args.fetch_missing)
    if args.context_report:
        print(
            json.dumps(
                [
                    {
                        "id": e["id"],
                        "paper_id": e["paper_id"],
                        "page_index": e["page_index"],
                        "text": pages[e["paper_id"]][e["page_index"]][
                            e["context_start"] : e["context_end"]
                        ],
                    }
                    for e in bundle["evidence"]["contexts"]
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(
            json.dumps(
                {
                    "verified": totals,
                    "model_calls": 0,
                    "status": "candidate_holdout",
                    "human_review": "pending",
                    "note": "Byte/offset checks do not prove semantic correctness.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
