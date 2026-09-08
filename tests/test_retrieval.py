from copy import deepcopy

import pytest

from papertrail.retrieval import build_chunks, retrieve, retrieve_for_queries


def test_chunks_preserve_physical_pages_offsets_overlap_and_identity():
    pages = [
        {"page_index": 0, "text": "Alpha method. " * 300},
        {"page_index": 1, "text": ""},
        {"page_index": 2, "text": "Third physical page."},
    ]
    chunks = build_chunks("paper-1", "hash-1", pages)
    assert chunks == build_chunks("paper-1", "hash-1", pages)
    assert len({c["chunk_id"] for c in chunks}) == len(chunks)
    assert {c["page_index"] for c in chunks} == {0, 2}
    for chunk in chunks:
        page = pages[chunk["page_index"]]
        assert chunk["text"] == page["text"][chunk["start_char"] : chunk["end_char"]]
        assert len(chunk["text"]) <= 1400
    first_page = [c for c in chunks if c["page_index"] == 0]
    assert first_page[1]["start_char"] == first_page[0]["end_char"] - 200
    other_identity = build_chunks("paper-2", "hash-1", pages)
    other_hash = build_chunks("paper-1", "hash-2", pages)
    assert not ({c["chunk_id"] for c in chunks} & {c["chunk_id"] for c in other_identity})
    assert not ({c["chunk_id"] for c in chunks} & {c["chunk_id"] for c in other_hash})


def test_bm25_retrieves_expanded_english_terms_for_chinese_question():
    chunks = build_chunks(
        "p",
        "h",
        [
            {"page_index": 0, "text": "We combine reasoning and acting in language models."},
            {"page_index": 1, "text": "Experiments use HotpotQA and FEVER benchmarks."},
            {"page_index": 2, "text": "We discuss future directions and limitations."},
        ],
    )
    assert retrieve(chunks, "实验使用什么数据集") == []
    matches = retrieve(chunks, "实验使用什么数据集 experiments datasets benchmarks HotpotQA FEVER")
    assert matches[0]["page_index"] == 1
    assert len(matches) == 1


def test_chinese_text_budget_ties_and_no_matches_are_explicit():
    chunks = build_chunks(
        "p",
        "h",
        [
            {"page_index": 0, "text": "检索方法使用词频排序。"},
            {"page_index": 1, "text": "检索方法使用词频排序。"},
        ],
    )
    matches = retrieve(chunks, "检索方法", top_k=1)
    assert matches[0]["page_index"] == 0
    assert retrieve(chunks, "检索方法", max_chars=5) == []
    assert retrieve(chunks, "unrelated") == []
    assert retrieve([], "anything") == []


MAIN_TERMS = "alpha beta gamma delta epsilon"


def _chunks(texts):
    return build_chunks(
        "paper", "source-hash", [{"page_index": i, "text": text} for i, text in enumerate(texts)]
    )


def test_query_coverage_replaces_only_two_tail_items_and_preserves_scores_and_source():
    chunks = _chunks(
        [MAIN_TERMS] * 12
        + [f"{term} alpha " + "filler " * 100 for term in ("zeta", "eta", "theta")]
    )
    original = deepcopy(chunks)
    queries = ["zeta", "eta", "theta"]
    result = retrieve_for_queries(chunks, MAIN_TERMS, queries)
    assert result == retrieve_for_queries(chunks, MAIN_TERMS, queries)
    assert chunks == original
    assert [c["page_index"] for c in result["merged_selected"]] == list(range(12))
    assert [c["page_index"] for c in result["selected"]] == [*range(10), 12, 13]
    assert result["selected"][:10] == result["merged_selected"][:10]
    assert len({c["chunk_id"] for c in result["selected"]}) == 12
    assert sum(len(c["text"]) for c in result["selected"]) <= 20_000
    diagnostics = result["supplementation"]
    assert [d["trigger_terms"] for d in diagnostics["decisions"]] == [["zeta"], ["eta"]]
    assert [d["replaced_chunk_id"] for d in diagnostics["decisions"]] == [
        chunks[11]["chunk_id"],
        chunks[10]["chunk_id"],
    ]
    assert set(diagnostics["baseline_matched_terms"]) | {"zeta", "eta"} == set(
        diagnostics["selected_matched_terms"]
    )
    merged = retrieve(chunks, " ".join([MAIN_TERMS, *queries]), top_k=len(chunks))
    for selected in result["selected"]:
        assert selected == next(c for c in merged if c["chunk_id"] == selected["chunk_id"])
        assert {k: v for k, v in selected.items() if k != "score"} == chunks[selected["page_index"]]
    for decision in diagnostics["decisions"]:
        ranking = retrieve(chunks, queries[decision["query_index"]], top_k=12)
        assert ranking[decision["query_rank"] - 1]["chunk_id"] == decision["chunk_id"]
        assert decision["query_score"] > 0
        published = next(c for c in result["selected"] if c["chunk_id"] == decision["chunk_id"])
        assert published["score"] > decision["query_score"]


def test_query_coverage_repeated_queries_do_not_duplicate_supplements():
    chunks = _chunks([MAIN_TERMS] * 12 + ["zeta " + "filler " * 100])
    result = retrieve_for_queries(chunks, MAIN_TERMS, ["zeta", "zeta", "zeta"])
    assert len(result["supplementation"]["decisions"]) == 1
    assert len({c["chunk_id"] for c in result["selected"]}) == 12
    assert [c["page_index"] for c in result["selected"]] == [*range(11), 12]


def test_query_coverage_candidate_must_be_in_its_query_top_twelve():
    chunks = _chunks([MAIN_TERMS] * 12 + ["zeta " + "filler " * 100])
    query = MAIN_TERMS + " zeta"
    ranking = retrieve(chunks, query, top_k=13)
    assert ranking[12]["page_index"] == 12
    assert ranking[12]["score"] > 0
    result = retrieve_for_queries(chunks, "", [query])
    assert result["selected"] == result["merged_selected"]
    assert result["supplementation"]["decisions"] == []


def test_query_coverage_rejects_loss_of_an_originally_matched_term():
    chunks = _chunks(
        [MAIN_TERMS, MAIN_TERMS + " omega " + "filler " * 10, "zeta " + "filler " * 20]
    )
    result = retrieve_for_queries(chunks, MAIN_TERMS + " omega", ["zeta"], top_k=2)
    assert [c["page_index"] for c in result["merged_selected"]] == [0, 1]
    assert result["selected"] == result["merged_selected"]
    assert result["supplementation"]["rejected_candidates"] == [
        {
            "query_index": 0,
            "chunk_id": chunks[2]["chunk_id"],
            "reason": "would_drop_query_terms",
            "terms": ["omega"],
        }
    ]


def test_query_coverage_uses_exact_final_character_budget():
    chunks = _chunks([MAIN_TERMS] * 2 + ["zeta " + "filler " * 10])
    boundary = len(chunks[0]["text"]) + len(chunks[2]["text"])
    accepted = retrieve_for_queries(chunks, MAIN_TERMS, ["zeta"], top_k=2, max_chars=boundary)
    assert [c["page_index"] for c in accepted["selected"]] == [0, 2]
    assert sum(len(c["text"]) for c in accepted["selected"]) == boundary
    rejected = retrieve_for_queries(chunks, MAIN_TERMS, ["zeta"], top_k=2, max_chars=boundary - 1)
    assert rejected["selected"] == rejected["merged_selected"]
    assert rejected["supplementation"]["rejected_candidates"] == [
        {"query_index": 0, "chunk_id": chunks[2]["chunk_id"], "reason": "chars"}
    ]


def test_query_coverage_continues_to_a_fitting_candidate_after_rejection():
    chunks = _chunks(
        [(MAIN_TERMS + " ") * 2, MAIN_TERMS, "zeta " * 8 + "fill " * 4, "zeta " + "filler " * 4]
    )
    budget = len(chunks[0]["text"]) + len(chunks[3]["text"])
    result = retrieve_for_queries(chunks, MAIN_TERMS, ["zeta"], top_k=2, max_chars=budget)
    assert [c["page_index"] for c in result["merged_selected"]] == [0, 1]
    assert [c["page_index"] for c in result["selected"]] == [0, 3]
    assert result["supplementation"]["rejected_candidates"] == [
        {"query_index": 0, "chunk_id": chunks[2]["chunk_id"], "reason": "chars"}
    ]
    assert result["supplementation"]["decisions"][0]["query_rank"] == 2


def test_query_coverage_checks_the_second_replacement_against_original_terms():
    chunks = _chunks(
        [
            MAIN_TERMS,
            MAIN_TERMS + " omega " + "filler " * 17,
            MAIN_TERMS + " " + "filler " * 22,
            "zeta " + "filler " * 30,
            "eta " + "filler " * 30,
        ]
    )
    result = retrieve_for_queries(chunks, MAIN_TERMS + " omega", ["zeta", "eta"], top_k=3)
    assert [c["page_index"] for c in result["merged_selected"]] == [0, 1, 2]
    assert [c["page_index"] for c in result["selected"]] == [0, 1, 3]
    diagnostics = result["supplementation"]
    assert len(diagnostics["decisions"]) == 1
    assert diagnostics["rejected_candidates"] == [
        {
            "query_index": 1,
            "chunk_id": chunks[4]["chunk_id"],
            "reason": "would_drop_query_terms",
            "terms": ["omega"],
        }
    ]
    assert set(diagnostics["baseline_matched_terms"]) | {"zeta"} == set(
        diagnostics["selected_matched_terms"]
    )


@pytest.mark.parametrize(
    ("texts", "question", "queries", "options"),
    [
        ([], "alpha", ["zeta"], {}),
        ([MAIN_TERMS], "unmatched", ["missing"], {}),
        ([MAIN_TERMS], MAIN_TERMS, ["missing"], {}),
        ([MAIN_TERMS], MAIN_TERMS, ["the and of"], {}),
        ([MAIN_TERMS], MAIN_TERMS, [], {}),
        ([MAIN_TERMS], MAIN_TERMS, ["alpha"], {"top_k": 0}),
        ([MAIN_TERMS], MAIN_TERMS, ["alpha"], {"max_chars": 0}),
    ],
)
def test_query_coverage_has_no_fallback_without_a_matching_gap(texts, question, queries, options):
    result = retrieve_for_queries(_chunks(texts), question, queries, **options)
    assert result["selected"] == result["merged_selected"]
    assert result["supplementation"]["decisions"] == []
