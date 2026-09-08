"""Freeze offline comparisons on explicitly named historical QA runs; no inference.

The candidate prototype is kept here independently of the application so the
pre-implementation report can be replayed after the runtime implementation changes.
No candidate selector can access expected answers, evidence anchors, or question IDs.
"""

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from papertrail.retrieval import build_chunks, retrieve, tokenize

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def query_gap_candidate(chunks, question, queries, *, top_k=12, max_chars=20_000):
    """Prototype: supplement lexical gaps without sacrificing covered query terms."""
    merged = " ".join([question, *queries])
    baseline = retrieve(chunks, merged, top_k=top_k, max_chars=max_chars)
    words = {chunk["chunk_id"]: set(tokenize(chunk["text"])) for chunk in chunks}
    corpus = set().union(*words.values())
    query_terms = set(tokenize(merged))

    def matched(selected):
        return query_terms & set().union(*(words[c["chunk_id"]] for c in selected))

    original_terms = matched(baseline)
    covered = original_terms
    seen = {chunk["chunk_id"] for chunk in baseline}
    supplements, decisions, rejected = [], [], []
    for query_index, query in enumerate(queries):
        if len(supplements) >= min(2, len(baseline)):
            break
        gap = (set(tokenize(query)) & corpus) - covered
        if not gap:
            continue
        for rank, chunk in enumerate(retrieve(chunks, query, top_k=12, max_chars=max_chars), 1):
            added = gap & words[chunk["chunk_id"]]
            if chunk["chunk_id"] in seen or not added:
                continue
            trial = baseline[: len(baseline) - len(supplements) - 1] + [*supplements, chunk]
            if sum(len(c["text"]) for c in trial) > max_chars:
                rejected.append(
                    {"query_index": query_index, "chunk_id": chunk["chunk_id"], "reason": "chars"}
                )
                continue
            lost_terms = original_terms - matched(trial)
            if lost_terms:
                rejected.append(
                    {
                        "query_index": query_index,
                        "chunk_id": chunk["chunk_id"],
                        "reason": "would_drop_query_terms",
                        "terms": sorted(lost_terms),
                    }
                )
                continue
            decisions.append(
                {
                    "query_index": query_index,
                    "query": query,
                    "query_rank": rank,
                    "query_score": chunk["score"],
                    "trigger_terms": sorted(added),
                    "chunk_id": chunk["chunk_id"],
                    "replaced_chunk_id": baseline[len(baseline) - len(supplements) - 1]["chunk_id"],
                }
            )
            supplements.append(chunk)
            seen.add(chunk["chunk_id"])
            covered = matched(trial)
            break
    # Published scores stay on the merged-query BM25 scale; query scores are diagnostic.
    merged_scores = {
        c["chunk_id"]: c["score"]
        for c in retrieve(
            chunks, merged, top_k=len(chunks), max_chars=sum(len(c["text"]) for c in chunks)
        )
    }
    selected = baseline[: len(baseline) - len(supplements)] + [
        {**c, "score": merged_scores[c["chunk_id"]]} for c in supplements
    ]
    return {
        "selected": selected,
        "merged_selected": baseline,
        "supplementation": {
            "method": "query-term-coverage-v1",
            "meaning": "Lexical query-term coverage only, not semantic evidence sufficiency.",
            "candidate_top_k": 12,
            "max_replacements": 2,
            "decisions": decisions,
            "rejected_candidates": rejected,
            "baseline_matched_terms": sorted(original_terms),
            "selected_matched_terms": sorted(matched(selected)),
        },
    }


def round_robin(rankings):
    selected, seen = [], set()
    cursors = [0] * len(rankings)
    while len(selected) < 12:
        advanced = False
        for index, ranking in enumerate(rankings):
            while cursors[index] < len(ranking):
                chunk = ranking[cursors[index]]
                cursors[index] += 1
                if chunk["chunk_id"] in seen:
                    continue
                if sum(len(c["text"]) for c in selected) + len(chunk["text"]) > 20_000:
                    continue
                selected.append(chunk)
                seen.add(chunk["chunk_id"])
                advanced = True
                break
            if len(selected) == 12:
                break
        if not advanced:
            break
    return selected


def reserve(rankings, merged, count):
    selected, seen = [], set()
    for chunk in [*[c for ranking in rankings for c in ranking[:count]], *merged]:
        if chunk["chunk_id"] in seen:
            continue
        if sum(len(c["text"]) for c in selected) + len(chunk["text"]) > 20_000:
            continue
        selected.append(chunk)
        seen.add(chunk["chunk_id"])
        if len(selected) == 12:
            break
    return selected


def describe(selected, question, citations):
    selected_ids = {c["chunk_id"] for c in selected}
    return {
        "selected": [
            {k: c[k] for k in ("chunk_id", "page_index", "start_char", "end_char", "score")}
            for c in selected
        ],
        "chars": sum(len(c["text"]) for c in selected),
        "anchor_hits": [
            any(
                c["page_index"] == e["page_index"]
                and c["start_char"] <= e["char_start"]
                and c["end_char"] >= e["char_end"]
                for c in selected
            )
            for e in question["expected_evidence"]
        ],
        "historically_cited_chunk_ids_lost": sorted(citations - selected_ids),
    }


def compare(run):
    manifest = json.loads((run / "dataset/manifest.json").read_text())
    questions = json.loads((run / "dataset/questions.json").read_text())["questions"]
    by_id = {q["id"]: q for q in questions}
    selection = json.loads((run / "selection.json").read_text())["question_ids"]
    pages, source_hashes = {}, {}
    for paper in manifest["papers"]:
        key = paper["id"]
        source_hashes[key] = digest(run / f"sources/{key}.pdf")
        assert source_hashes[key] == paper["sha256"]
        pages[key] = json.loads((run / f"sources/{key}.pages.json").read_text())
        assert [hashlib.sha256(p.encode()).hexdigest() for p in pages[key]] == paper[
            "page_text_sha256"
        ]
    rows, totals = [], {}
    for key in selection:
        path = run / f"questions/{key}.result.json"
        record = json.loads(path.read_text())
        question = record["input"]
        assert question == by_id[key]
        trace = record["result"]["trace"]
        chunks = build_chunks(
            trace["paper_id"],
            trace["paper_sha256"],
            [{"page_index": i, "text": p} for i, p in enumerate(pages[question["paper_id"]])],
        )
        queries = trace["retrieval"]["queries"]
        text = " ".join([question["question"], *queries])
        candidate = query_gap_candidate(chunks, question["question"], queries)
        assert candidate["merged_selected"] == trace["retrieval"]["selected"]
        rankings = [retrieve(chunks, q, top_k=len(chunks), max_chars=1_000_000) for q in queries]
        merged = retrieve(chunks, text, top_k=len(chunks), max_chars=1_000_000)
        variants = {
            "merged12": candidate["merged_selected"],
            "query_gap2": candidate["selected"],
            "round_robin3": round_robin(rankings),
            "reserve2": reserve(rankings, merged, 2),
            "reserve4": reserve(rankings, merged, 4),
            "reserve6": reserve(rankings, merged, 6),
            "merged16_same20k": retrieve(chunks, text, top_k=16),
        }
        citations = {
            c["chunk_id"] for claim in record["result"]["claims"] for c in claim["citations"]
        }
        details = {name: describe(cs, question, citations) for name, cs in variants.items()}
        for name, detail in details.items():
            totals.setdefault(name, Counter()).update(
                {
                    "anchor_hits": sum(detail["anchor_hits"]),
                    "anchor_count": len(detail["anchor_hits"]),
                    "historically_cited_chunks_lost": len(
                        detail["historically_cited_chunk_ids_lost"]
                    ),
                    "chars": detail["chars"],
                }
            )
        rows.append(
            {
                "question_id": key,
                "result_sha256": digest(path),
                "historical_pipeline_version": trace["pipeline_version"],
                "queries": queries,
                "baseline_replay_exact": True,
                "candidate_diagnostics": candidate["supplementation"],
                "variants": details,
            }
        )
    return {
        "run": str(run.relative_to(ROOT)),
        "source_pdf_sha256": source_hashes,
        "dataset_manifest_sha256": digest(run / "dataset/manifest.json"),
        "dataset_questions_sha256": digest(run / "dataset/questions.json"),
        "selection_sha256": digest(run / "selection.json"),
        "selected_questions": selection,
        "totals": totals,
        "questions": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {
        "schema_version": 1,
        "prepared_at": datetime.now(UTC).isoformat(),
        "scope": "Historical development replay, not new inference or held-out evaluation.",
        "limits": "Anchor coverage and retained old citations do not prove semantic quality.",
        "code_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "comparison_script_sha256": digest(Path(__file__)),
        "baseline_retrieval_source_sha256": digest(ROOT / "src/papertrail/retrieval.py"),
        "candidate_frozen_before_application_implementation": True,
        "runs": [compare(run.resolve()) for run in args.run],
    }
    with args.output.open("x") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps({"output": str(args.output), "sha256": digest(args.output)}))


if __name__ == "__main__":
    main()
