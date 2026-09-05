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
            {"requirement_index": 0, "covered": True, "claim_indices": [0], "reason": "已回答。"},
            {
                "requirement_index": 1,
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
