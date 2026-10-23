"""Decisions supplied directly by the chief, never extracted from report prose."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model


class OverallJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_and_value: str = Field(
        min_length=1,
        description="项目对象与研究安排，以及本项目最值得获得的认识。写成一个自然段。",
    )
    support_and_limits: str = Field(
        min_length=1,
        description="主要支持依据与真正影响判断的限制。写成一个自然段。",
    )
    overall_tradeoff: str = Field(
        min_length=1,
        description="依据前述价值和限制形成整体取舍，直接评价现有计划。写成一个自然段。",
    )

    def as_text(self):
        # Provider key order is not paragraph order; preserve each authored string.
        return "\n\n".join((
            self.project_and_value, self.support_and_limits, self.overall_tradeoff,
        ))


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
        "overall_judgment": (OverallJudgment, ...),
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
