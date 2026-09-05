"""Reproducible, page-bound chunks and a small single-paper BM25 index."""

import hashlib
import math
import re
from collections import Counter

CHUNK_VERSION = "page-char-v1-1400-200"
RETRIEVAL_VERSION = "bm25-v2-query-term-coverage"
_STOPWORDS = set(
    "a an the and or of to in on for with from by is are was were be been being "
    "as at that this these those it its we our their they what which how does do did "
    "can could would should paper study research please explain describe about".split()
)


def tokenize(text: str) -> list[str]:
    """English words/numbers and Chinese bigrams; no language-specific dependency."""
    tokens = re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", text.lower())
    tokens = [token for token in tokens if token not in _STOPWORDS]
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        tokens.extend(run[i : i + 2] for i in range(max(1, len(run) - 1)))
    return tokens


def build_chunks(paper_id: str, sha256: str, pages: list[dict]) -> list[dict]:
    """Offsets index the stored page text exactly; IDs bind identity and content."""
    chunks = []
    for page in sorted(pages, key=lambda item: item["page_index"]):
        text = page["text"]
        start = 0
        while start < len(text):
            end = min(start + 1400, len(text))
            if end < len(text):
                boundary = max(
                    text.rfind("\n", start + 1000, end), text.rfind(" ", start + 1000, end)
                )
                if boundary > start:
                    end = boundary + 1
            passage = text[start:end]
            if passage.strip():
                identity = (
                    f"{CHUNK_VERSION}|{paper_id}|{sha256}|{page['page_index']}|"
                    f"{start}|{end}|{passage}"
                )
                chunks.append(
                    {
                        "chunk_id": "pt_" + hashlib.sha256(identity.encode()).hexdigest()[:32],
                        "paper_id": str(paper_id),
                        "paper_sha256": sha256,
                        "page_index": page["page_index"],
                        "start_char": start,
                        "end_char": end,
                        "text": passage,
                        "chunk_version": CHUNK_VERSION,
                    }
                )
            if end == len(text):
                break
            start = end - 200
    return chunks


def retrieve(
    chunks: list[dict], query: str, *, top_k: int = 12, max_chars: int = 20_000
) -> list[dict]:
    """Return only positive matches, stable ties, and an explicit context ceiling."""
    if not chunks or top_k <= 0 or max_chars <= 0:
        return []
    terms = set(tokenize(query))
    documents = [Counter(tokenize(chunk["text"])) for chunk in chunks]
    lengths = [sum(document.values()) for document in documents]
    average = sum(lengths) / len(lengths) or 1
    frequency = Counter(term for document in documents for term in document if term in terms)
    ranked = []
    for index, (chunk, document, length) in enumerate(zip(chunks, documents, lengths, strict=True)):
        score = 0.0
        for term in sorted(terms):
            count = document[term]
            if count:
                idf = math.log(1 + (len(chunks) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += idf * count * 2.5 / (count + 1.5 * (0.25 + 0.75 * length / average))
        if score > 0:
            ranked.append((score, index, chunk))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = []
    used = 0
    for score, _, chunk in ranked:
        if used + len(chunk["text"]) > max_chars:
            continue
        selected.append({**chunk, "score": round(score, 8)})
        used += len(chunk["text"])
        if len(selected) >= top_k:
            break
    return selected


def retrieve_for_queries(
    chunks: list[dict],
    question: str,
    queries: list[str],
    *,
    top_k: int = 12,
    max_chars: int = 20_000,
) -> dict:
    """Keep merged BM25 order and replace at most two tail items for lexical gaps.

    Candidates must come from a generated query's own top 12 positive matches.
    The final selection must retain every query term covered by the merged
    baseline. This preserves words, not their semantic relationships or evidence
    sufficiency; the answer's support and coverage checks still run separately.
    """
    merged = " ".join([question, *queries])
    baseline = retrieve(chunks, merged, top_k=top_k, max_chars=max_chars)
    words = {chunk["chunk_id"]: set(tokenize(chunk["text"])) for chunk in chunks}
    corpus = set().union(*words.values())
    query_terms = set(tokenize(merged))

    def matched(selected: list[dict]) -> set[str]:
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
