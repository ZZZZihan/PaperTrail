"""Validate model coverage judgements separately from citation support.

These are diagnostic judgements, never a substitute for a human rubric. The
planner only sees the question; the checker also sees retrieved context so it
can identify important qualifications omitted by both planner and generator.
"""

from papertrail.model import ModelError


def requirements_from_query(value: object) -> list[str]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 12
        or any(not isinstance(item, str) or not 1 <= len(item.strip()) <= 250 for item in value)
        or len({item.strip() for item in value}) != len(value)
    ):
        raise ModelError("invalid_output", "问题要点整理未完成，请重新提问。")
    return [item.strip() for item in value]


def checked_coverage(raw: dict, requirements: list[str], verdicts: list[dict]) -> dict:
    """Separate requested facts from context; retain only supported claim links."""
    planned = raw.get("coverage")
    extra = raw.get("additional_requirements")
    if not isinstance(planned, list) or len(planned) != len(requirements):
        raise ModelError("verification_failed", "回答覆盖检查未完成，请重试或查看原文。")
    if not isinstance(extra, list) or len(extra) > 8:
        raise ModelError("verification_failed", "必要条件检查未完成，请重试或查看原文。")
    seen = set()
    items = []
    supported = {v["claim_index"] for v in verdicts if v["supported"]}
    retained_indices = {index: new for new, index in enumerate(sorted(supported))}
    for item in [*planned, *extra]:
        if not isinstance(item, dict):
            raise ModelError("verification_failed", "回答覆盖检查格式无效。")
        if len(items) < len(planned):
            index = item.get("requirement_index")
            if type(index) is not int or not 0 <= index < len(requirements) or index in seen:
                raise ModelError("verification_failed", "回答覆盖检查缺失或重复要点。")
            seen.add(index)
            requirement = requirements[index]
            origin = "question"
        else:
            requirement = item.get("requirement")
            if not isinstance(requirement, str) or not 1 <= len(requirement.strip()) <= 250:
                raise ModelError("verification_failed", "必要条件描述无效。")
            requirement = requirement.strip()
            origin = "retrieved_context"
        kind = item.get("kind")
        covered, indices, reason = (
            item.get("covered"),
            item.get("claim_indices"),
            item.get("reason"),
        )
        if (
            kind not in ("requested", "qualification", "background")
            or type(covered) is not bool
            or not isinstance(indices, list)
            or len(indices) > len(verdicts)
            or any(type(i) is not int or not 0 <= i < len(verdicts) for i in indices)
            or len(set(indices)) != len(indices)
            or (covered and not indices)
            or not isinstance(reason, str)
            or not 1 <= len(reason.strip()) <= 1500
        ):
            raise ModelError("verification_failed", "回答覆盖检查不完整或格式无效。")
        items.append(
            {
                "requirement": requirement,
                "origin": origin,
                "kind": kind,
                "covered": covered and all(i in supported for i in indices),
                "claim_indices": [retained_indices[i] for i in indices if i in supported],
                "reason": reason.strip(),
            }
        )
    requested = [item for item in items if item["kind"] == "requested"]
    if not requested:
        raise ModelError("verification_failed", "未识别用户实际请求的要点，请重试或查看原文。")
    # A linked claim can supply a real partial fact even when the whole item is
    # not covered. Background and qualifications alone cannot answer the request.
    has_answer = any(item["claim_indices"] for item in requested)
    complete = has_answer and all(item["covered"] for item in items if item["kind"] != "background")
    return {
        "status": "complete" if complete else "partial" if has_answer else "unanswered",
        "review_source": "ai",
        "items": items,
    }
