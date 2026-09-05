from papertrail.retrieval import build_chunks, retrieve


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
