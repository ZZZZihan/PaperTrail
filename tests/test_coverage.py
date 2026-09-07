"""A supported sentence must not hide omitted requirements or qualifications."""

from copy import deepcopy

import pytest
from test_qa import PAGES, PAPER, pipeline_client

from papertrail.coverage import checked_coverage, requirements_from_query
from papertrail.model import ModelError
from papertrail.qa import answer_question


def review():
    return {
        "verdicts": [{"claim_index": 0, "supported": True, "reason": "数据集有据。"}],
        "coverage": [
            {
                "requirement_index": 0,
                "kind": "requested",
                "covered": True,
                "claim_indices": [0],
                "reason": "已回答。",
            },
            {
                "requirement_index": 1,
                "kind": "requested",
                "covered": False,
                "claim_indices": [],
                "reason": "正文未说明。",
            },
        ],
        "additional_requirements": [],
    }


def test_supported_facts_with_omitted_subquestion_are_partial():
    result = answer_question(
        PAPER,
        PAGES,
        "数据集和试验轮数？",
        client=pipeline_client(requirements=["数据集？", "试验轮数？"], verify=review()),
    )
    assert result["status"] == "partial_answer"
    assert result["claims"] and result["coverage"]["status"] == "partial"
    assert result["coverage"]["items"][1]["covered"] is False
    assert result["trace"]["call_count"] == 3


def test_checker_can_find_qualification_omitted_by_planner():
    raw = review()
    raw["coverage"] = raw["coverage"][:1]
    raw["additional_requirements"] = [
        {
            "requirement": "实验适用范围？",
            "kind": "qualification",
            "covered": False,
            "claim_indices": [],
            "reason": "引用包含限定，正文遗漏。",
        }
    ]
    result = answer_question(PAPER, PAGES, "实验数据集？", client=pipeline_client(verify=raw))
    assert result["status"] == "partial_answer"
    assert result["coverage"]["items"][-1]["origin"] == "retrieved_context"


def test_coverage_cannot_rely_on_rejected_claim_and_remaps_retained_indices():
    raw = review()
    raw["verdicts"] = [
        {"claim_index": 0, "supported": False, "reason": "无据。"},
        {"claim_index": 1, "supported": True, "reason": "有据。"},
    ]
    raw["coverage"][1].update(covered=True, claim_indices=[1])
    result = checked_coverage(raw, ["一", "二"], raw["verdicts"])
    assert result["status"] == "partial"
    assert not result["items"][0]["covered"]
    assert result["items"][1]["claim_indices"] == [0]


@pytest.mark.parametrize(
    "change",
    ["missing", "duplicate", "bool_index", "dangling", "empty_link", "string_bool", "no_extras"],
)
def test_malformed_coverage_never_publishes_complete_answer(change):
    raw = review()
    if change == "missing":
        raw["coverage"].pop()
    elif change == "duplicate":
        raw["coverage"][1]["requirement_index"] = 0
    elif change == "bool_index":
        raw["coverage"][0]["requirement_index"] = False
    elif change == "dangling":
        raw["coverage"][0]["claim_indices"] = [8]
    elif change == "empty_link":
        raw["coverage"][0]["claim_indices"] = []
    elif change == "string_bool":
        raw["coverage"][0]["covered"] = "true"
    else:
        del raw["additional_requirements"]
    result = answer_question(
        PAPER,
        PAGES,
        "实验？",
        client=pipeline_client(requirements=["数据集？", "试验轮数？"], verify=raw),
    )
    assert result["status"] == "failed" and not result["claims"]
    assert result["error_code"] == "verification_failed"


@pytest.mark.parametrize("value", [None, [], [""], ["a", "a"], ["a", 1], ["x"] * 13])
def test_missing_or_ambiguous_planner_requirements_fail_closed(value):
    with pytest.raises(ModelError):
        requirements_from_query(value)


def test_rejected_fact_removed_but_supported_partial_answer_retained():
    raw = review()
    raw["verdicts"].append({"claim_index": 1, "supported": False, "reason": "第二项无据。"})

    def generate(value):
        other = deepcopy(value["claims"][0])
        other["text"] = "该方法在所有任务中均有效。"
        value["claims"].append(other)
        return value

    result = answer_question(
        PAPER,
        PAGES,
        "数据集和适用范围？",
        client=pipeline_client(
            requirements=["数据集？", "适用范围？"], verify=raw, generate=generate
        ),
    )
    assert result["status"] == "partial_answer" and len(result["claims"]) == 1
    assert "所有任务" not in result["claims"][0]["text"]
    assert len(result["trace"]["candidate_claims"]) == 2


@pytest.mark.parametrize("kind", ["background", "qualification"])
def test_supported_context_alone_cannot_answer_an_unavailable_request(kind):
    raw = review()
    raw["coverage"][0]["kind"] = kind
    result = answer_question(
        PAPER,
        PAGES,
        "试验轮数？",
        client=pipeline_client(requirements=["数据集？", "试验轮数？"], verify=raw),
    )
    assert result["status"] == "insufficient_evidence"
    assert result["coverage"]["status"] == "unanswered"
    assert result["claims"][0]["role"] == "background"
    assert "试验轮数？：在当前已检索证据中未找到足够支持。" in result["message"]
    assert "数据集？" not in result["message"]
    assert result["message"].endswith("结论：证据不足。")


def test_real_partial_detail_is_retained_even_when_whole_requested_item_is_uncovered():
    raw = review()
    raw["coverage"] = [raw["coverage"][0]]
    raw["coverage"][0].update(covered=False, reason="列出了数据集，但遗漏所求试验轮数。")
    result = answer_question(
        PAPER,
        PAGES,
        "数据集和试验轮数？",
        client=pipeline_client(requirements=["数据集和试验轮数？"], verify=raw),
    )
    assert result["status"] == "partial_answer"
    assert result["claims"][0]["role"] == "answer"
    assert not result["coverage"]["items"][0]["covered"]


def test_uncovered_optional_background_does_not_demote_a_complete_requested_answer():
    raw = review()
    raw["coverage"][1]["kind"] = "background"
    result = answer_question(
        PAPER,
        PAGES,
        "数据集？",
        client=pipeline_client(requirements=["数据集？", "试验轮数？"], verify=raw),
    )
    assert result["status"] == "answered"
    assert result["coverage"]["status"] == "complete"


def test_checker_can_recover_requested_item_omitted_by_planner():
    raw = review()
    missing = raw["coverage"].pop()
    del missing["requirement_index"]
    missing["requirement"] = "试验轮数？"
    raw["additional_requirements"] = [missing]
    raw["coverage"][0]["kind"] = "background"
    result = answer_question(PAPER, PAGES, "试验轮数？", client=pipeline_client(verify=raw))
    assert result["status"] == "insufficient_evidence"
    assert result["coverage"]["items"][-1]["origin"] == "retrieved_context"
    assert "试验轮数？" in result["message"]


@pytest.mark.parametrize("kind", [None, True, [], {}, "answer", "", 1])
@pytest.mark.parametrize("additional", [False, True])
def test_missing_or_invalid_scope_classification_fails_closed(kind, additional):
    raw = review()
    if additional:
        raw["additional_requirements"] = [
            {"requirement": "限定？", "covered": False, "claim_indices": [], "reason": "缺项。"}
        ]
        item = raw["additional_requirements"][0]
    else:
        item = raw["coverage"][0]
        del item["kind"]
    if kind is not None:
        item["kind"] = kind
    result = answer_question(
        PAPER,
        PAGES,
        "数据集和试验轮数？",
        client=pipeline_client(requirements=["数据集？", "试验轮数？"], verify=raw),
    )
    assert result["status"] == "failed" and result["error_code"] == "verification_failed"
    assert result["claims"] == []


def test_checker_cannot_classify_away_all_requested_items():
    raw = review()
    for item in raw["coverage"]:
        item["kind"] = "background"
    result = answer_question(
        PAPER,
        PAGES,
        "数据集和试验轮数？",
        client=pipeline_client(requirements=["数据集？", "试验轮数？"], verify=raw),
    )
    assert result["status"] == "failed" and result["error_code"] == "verification_failed"
    assert result["claims"] == []


def test_rejected_requested_fact_cannot_be_rescued_by_retained_background():
    raw = review()
    raw["verdicts"].append({"claim_index": 1, "supported": False, "reason": "请求事实无据。"})
    raw["coverage"][0]["kind"] = "background"
    raw["coverage"][1].update(covered=True, claim_indices=[1])

    def generate(value):
        other = deepcopy(value["claims"][0])
        other["text"] = "试验执行了一百轮。"
        value["claims"].append(other)
        return value

    result = answer_question(
        PAPER,
        PAGES,
        "试验轮数？",
        client=pipeline_client(
            requirements=["数据集？", "试验轮数？"], generate=generate, verify=raw
        ),
    )
    assert result["status"] == "insufficient_evidence"
    assert len(result["claims"]) == 1 and result["claims"][0]["role"] == "background"
    assert not result["coverage"]["items"][1]["covered"]
    assert result["coverage"]["items"][1]["claim_indices"] == []
    assert "试验轮数？" in result["message"]


def test_no_retrieval_lists_every_unavailable_requested_item_without_checking_a_model():
    requirements = ["GPU 型号？", "GPU 数量？", "总训练电费？"]
    result = answer_question(
        PAPER,
        PAGES,
        "训练硬件与成本？",
        client=pipeline_client(requirements=requirements, search=["xyzzy"]),
    )
    assert result["status"] == "insufficient_evidence" and result["claims"] == []
    for item in requirements:
        assert f"{item}：在当前已检索证据中未找到足够支持。" in result["message"]
    assert result["message"].endswith("证据不足。")
    assert result["coverage"]["review_source"] == "not_checked"
    assert result["trace"]["call_count"] == 1
