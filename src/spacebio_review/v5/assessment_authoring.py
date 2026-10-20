"""Decisions supplied directly by the chief, never extracted from report prose."""

from typing import Literal

from pydantic import ConfigDict, Field, create_model


def decision_type(system):
    def record(name, fields):
        return create_model(name, __config__=ConfigDict(extra="forbid"), **fields)

    text = (str, Field(min_length=1))
    dimensions = {}
    for criterion in system.criteria:
        fields = {"reason": text}
        if system.score_grades:
            fields["score"] = (int, Field(ge=0, le=criterion.max_score, strict=True))
        else:
            grade = (Literal["A", "B", "C", "D", "E"] | None, ...)
            fields.update(grade=grade, confidence=grade)
        dimensions[criterion.id] = (record(criterion.id, fields), ...)
    fields = {
        "overall_judgment": (str, Field(
            min_length=1,
            description="直接基于申请和专家意见形成、可用于报告开头的正式综合评价正文。",
        )),
        "dimensions": (record("Dimensions", dimensions), ...),
    }
    if system.score_grades:
        fields["safety_veto"] = (
            record("SafetyDecision", {"applies": (bool, Field(strict=True)), "reason": text}),
            ...,
        )
    else:
        fields.update(
            support_scope=text,
            recommendation=(
                Literal[
                    "support", "conditional_support", "revise", "not_support", "insufficient_basis"
                ],
                ...,
            ),
        )
    return record("ReviewDecision", fields)


def decision_schema(system):
    return decision_type(system).model_json_schema()
