"""Reading-card attribution and coverage gates; mocked quality is never acceptance."""

from copy import deepcopy

import pytest
from test_introduction import PAGES, PAPER, complete_coverage, introduction_client
from test_introduction_revision import verdicts

from papertrail.introduction import COVERAGE_ASPECTS, INTRODUCTION_SCHEMA_VERSION, introduce_paper


def test_full_source_is_available_only_to_separate_coverage_judgment():
    inputs = []
    extra = {"page_index": 2, "text": "Another setting uses a trained model and five trials."}
    result = introduce_paper(PAPER, [*PAGES, extra], client=introduction_client(observed=inputs))
    assert result["status"] == "answered"
    verify = inputs[1]
    assert verify["passages"] == inputs[0]["passages"]
    assert len(verify["passages"]) == 2
    assert verify["claim_fields"] == [
        "summary",
        "problem",
        "contribution",
        "mechanism",
        "evidence_and_limits",
        "terms[0]",
        "terms[1]",
    ]
    # Unattached source is not copied into a claim's support evidence.
    assert all(len(claim["citations"]) == 1 for claim in verify["claims"])
    assert all(claim["citations"][0]["page_index"] == 1 for claim in verify["claims"])


def test_supported_claims_with_missing_conditions_get_one_revision_and_complete_recheck():
    checks, generations, inputs = 0, 0, []

    def generate(draft):
        nonlocal generations
        generations += 1
        draft["introduction"]["mechanism"]["text"] = (
            "输入问题，输出工具观察。"
            if generations == 1
            else "冻结模型参考人工编排轨迹，输入问题后生成工具动作，再读取观察。"
        )
        return draft

    def verify(data):
        nonlocal checks
        checks += 1
        response = verdicts(data, supported=True)
        if checks == 1:
            response["coverage"][2].update(
                covered=False, reason="正文未说明模型冻结和人工编排示例条件。"
            )
        return response

    result = introduce_paper(
        PAPER, PAGES, client=introduction_client(generate=generate, verify=verify, observed=inputs)
    )
    assert result["status"] == "answered" and result["trace"]["call_count"] == 4
    first, second = result["trace"]["attempts"]
    assert all(item["supported"] for item in first["support_verdicts"])
    assert first["feedback"] == [
        {
            "aspect": "method_flow_and_setup",
            "field": "method_flow_and_setup",
            "covered": False,
            "reason": "正文未说明模型冻结和人工编排示例条件。",
            "code": "missing_coverage",
        }
    ]
    assert inputs[2]["feedback"] == first["feedback"]
    assert first["draft"]["introduction"]["mechanism"]["text"] == "输入问题，输出工具观察。"
    assert second["feedback"] == [] and len(second["support_verdicts"]) == 7
    assert "人工编排" in result["introduction"]["mechanism"]["text"]
    assert all(item["covered"] for item in result["introduction"]["coverage"])


@pytest.mark.parametrize("aspect", COVERAGE_ASPECTS)
def test_persisting_coverage_omission_with_supported_facts_cannot_publish(aspect):
    def verify(data):
        response = verdicts(data, supported=True)
        next(entry for entry in response["coverage"] if entry["aspect"] == aspect).update(
            covered=False, reason="必要要点仍然没有进入正文。"
        )
        return response

    result = introduce_paper(PAPER, PAGES, client=introduction_client(verify=verify))
    assert result["status"] == "insufficient_evidence" and result["support_status"] == "ai_checked"
    assert result["introduction"] is None and result["claims"] == []
    assert result["trace"]["call_count"] == 4 and len(result["trace"]["attempts"]) == 2
    assert "必要要点" in result["message"]


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "unknown", "string_boolean", "blank_reason"]
)
def test_incomplete_or_malformed_coverage_cannot_be_inferred_from_support_success(mutation):
    def verify(data):
        response = verdicts(data, supported=True)
        if mutation == "missing":
            del response["coverage"]
        elif mutation == "duplicate":
            response["coverage"][1] = deepcopy(response["coverage"][0])
        elif mutation == "unknown":
            response["coverage"][0]["aspect"] = "all_good"
        elif mutation == "string_boolean":
            response["coverage"][0]["covered"] = "true"
        else:
            response["coverage"][0]["reason"] = "  "
        return response

    result = introduce_paper(PAPER, PAGES, client=introduction_client(verify=verify))
    assert result["status"] == "failed" and result["error_code"] == "verification_failed"
    assert result["introduction"] is None and result["trace"]["call_count"] == 2


def with_learning_aids(draft):
    citation = draft["introduction"]["mechanism"]["citations"]
    draft["introduction"]["problem"]["basis"] = "author_interpretation"
    draft["introduction"]["problem"]["text"] = "作者认为，人工示例能帮助冻结模型形成动作轨迹。"
    draft["introduction"]["learning_aids"] = [
        {
            "text": "教学示意：假设模型按照一个示例查询工具，再读取观察。",
            "basis": "teaching_example",
            "citations": citation,
        },
        {
            "text": "系统推断：若采用这里的冻结设置，调整示例可能比改变参数更直接。",
            "basis": "system_inference",
            "citations": citation,
        },
    ]
    return draft


def test_all_four_attribution_categories_and_learning_aids_are_preserved_and_checked():
    inputs = []
    result = introduce_paper(
        PAPER, PAGES, client=introduction_client(generate=with_learning_aids, observed=inputs)
    )
    assert result["status"] == "answered"
    intro = result["introduction"]
    assert intro["schema_version"] == INTRODUCTION_SCHEMA_VERSION
    assert set(claim["basis"] for claim in result["claims"]) == {
        "paper_statement",
        "author_interpretation",
        "teaching_example",
        "system_inference",
    }
    assert len(inputs[1]["claims"]) == 9
    assert inputs[1]["claim_fields"][-2:] == ["learning_aids[0]", "learning_aids[1]"]
    assert intro["learning_aids"] == result["claims"][-2:]
    for aid in intro["learning_aids"]:
        assert aid["citations"][0]["quote"] == PAGES[1]["text"]
        assert aid["citations"][0]["paper_id"] == PAPER["id"]


def test_unfounded_learning_aid_cannot_bypass_support_gate_by_its_label():
    def verify(data):
        response = verdicts(data, supported=True)
        response["verdicts"][-1].update(supported=False, reason="推断增加了原文没有支持的效果。")
        return response

    result = introduce_paper(
        PAPER, PAGES, client=introduction_client(generate=with_learning_aids, verify=verify)
    )
    assert result["introduction"] is None and result["status"] == "insufficient_evidence"
    assert result["trace"]["call_count"] == 4
    assert result["trace"]["attempts"][0]["feedback"][0]["field"] == "learning_aids[1]"


@pytest.mark.parametrize(
    "mutation",
    [
        "old_format",
        "unknown_basis",
        "aid_as_mechanism",
        "fact_as_aid",
        "three_aids",
        "foreign_citation",
    ],
)
def test_unclassified_content_or_learning_aids_cannot_replace_sourced_main_card(mutation):
    def generate(draft):
        draft = with_learning_aids(draft)
        intro = draft["introduction"]
        if mutation == "old_format":
            del intro["mechanism"]["basis"]
        elif mutation == "unknown_basis":
            intro["terms"][0]["basis"] = "trusted"
        elif mutation == "aid_as_mechanism":
            intro["mechanism"]["basis"] = "teaching_example"
        elif mutation == "fact_as_aid":
            intro["learning_aids"][0]["basis"] = "paper_statement"
        elif mutation == "three_aids":
            intro["learning_aids"].append(deepcopy(intro["learning_aids"][0]))
        else:
            intro["learning_aids"][0]["citations"] = [{"chunk_id": "other-paper"}]
        return draft

    result = introduce_paper(PAPER, PAGES, client=introduction_client(generate=generate))
    assert result["status"] == "failed" and result["introduction"] is None
    assert result["trace"]["call_count"] == 2


def test_model_cannot_supply_its_own_version_or_fake_a_coverage_verdict():
    def generate(draft):
        draft["introduction"].update(
            schema_version="all-human-approved", coverage=[{"covered": True}]
        )
        return draft

    result = introduce_paper(PAPER, PAGES, client=introduction_client(generate=generate))
    assert result["introduction"]["schema_version"] == INTRODUCTION_SCHEMA_VERSION
    assert result["introduction"]["coverage"] == complete_coverage()["coverage"]
    assert result["human_review"] is None


@pytest.mark.parametrize("location", ["mechanism", "term", "learning_aid"])
@pytest.mark.parametrize("count", [8, 9])
def test_intro_can_attach_eight_distinct_source_spans_but_never_nine(location, count):
    from papertrail.introduction import build_introduction_chunks

    pages = [{"page_index": i, "text": f"Evidence {i}: {PAGES[1]['text']}"} for i in range(9)]
    chunks = build_introduction_chunks(PAPER["id"], PAPER["sha256"], pages)
    citations = [{"chunk_id": c["chunk_id"]} for c in chunks[:count]]
    inputs = []

    def generate(draft):
        intro = draft["introduction"]
        if location == "term":
            intro["terms"][0]["citations"] = citations
        elif location == "learning_aid":
            intro["learning_aids"] = [
                {
                    "text": "教学示意：假设模型参考轨迹生成工具动作，再读取观察。",
                    "basis": "teaching_example",
                    "citations": citations,
                }
            ]
        else:
            intro[location]["citations"] = citations
        return draft

    result = introduce_paper(
        PAPER, pages, client=introduction_client(generate=generate, observed=inputs)
    )
    if count == 9:
        assert result["status"] == "failed" and result["error_code"] == "invalid_citation"
        assert result["introduction"] is None and result["trace"]["call_count"] == 2
        assert all("claims" not in data for data in inputs)
        return
    assert result["status"] == "answered" and result["trace"]["call_count"] == 2
    index = 5 if location == "term" else 7 if location == "learning_aid" else 3
    published = result["claims"][index]["citations"]
    assert len(published) == len(inputs[1]["claims"][index]["citations"]) == 8
    assert [c["quote"] for c in published] == [c["text"] for c in chunks[:8]]
    assert [c["page_index"] for c in published] == list(range(8))
    assert result["trace"]["max_citations_per_claim"] == 8


def test_ordinary_qa_still_rejects_five_citations_without_explicit_intro_limit():
    from papertrail.introduction import build_introduction_chunks
    from papertrail.model import ModelError
    from papertrail.qa import validate_claims

    pages = [{"page_index": i, "text": f"Evidence {i}: {PAGES[1]['text']}"} for i in range(5)]
    chunks = build_introduction_chunks(PAPER["id"], PAPER["sha256"], pages)
    citations = [{"chunk_id": c["chunk_id"], "quote": c["text"]} for c in chunks]
    claim = {"text": "冻结模型参考人工轨迹。", "citations": citations}
    with pytest.raises(ModelError) as error:
        validate_claims([claim], chunks, PAPER)
    assert error.value.code == "invalid_citation"
    assert (
        len(validate_claims([{**claim, "citations": citations[:4]}], chunks, PAPER)[0]["citations"])
        == 4
    )
