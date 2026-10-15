"""The chief submits criteria, receives scores, then writes the overall judgment and summary."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from spacebio_review.agents.diagnostics import digest
from spacebio_review.persistence import write_json
from spacebio_review.prose import cited_ids, prose_type
from spacebio_review.review.evaluation import EvaluationSystem
from spacebio_review.review.scoring import finalize_assessment
from spacebio_review.tools import Tool, ToolRegistry, ToolResult

GRADE = Literal["A", "B", "C", "D", "E"]
RECOMMENDATION = Literal[
    "support", "conditional_support", "revise", "not_support", "insufficient_basis"
]

INSTRUCTION = """当前工作是同一主审的最终评价与直接交付，取代之前仅提交研究稿的停止点。
结合完整申请、已经读到的原文和实际研究结果，完成评价正文。project 说明项目的知识路径，
scope 说明本次实际审议范围；dimensions 各项 body 给出完整评价和依据。
unknowns 说明仍未知的事实及其影响，技术性未完成与科学未知分开。
材料不足仍评价实际能支持的认识。交付现有方案的评价和适用范围，不安排申请方整改任务。
可用 save_assessment 分次保存已完成部分，也可直接用 submit_assessment 提交完整评价。
save_assessment 替换传入的部分，dimensions 只替换所传指标，未传部分保持原稿。
保存各部分之后用 finalize_assessment 提交完整评价。正文的 cites 只用当前 E 编号。
appendix_topics 选择值得展开的已提交专题及明确 note_ref，附录直接使用该版本的研究说明。
国标六项完整整数评分，先解释实际表现，对照本项分档再给档内分值。依据薄弱与已经证实的
缺陷分别判断；领域的重要性与本项目前沿贡献分别判断。安全否决须有影响航天员或平台的
具体事实及路径，安全低分或未知本身不等同否决。代码计算总分、等级及最终支持结论。
通用体系各项 grade/confidence 为 A—E 或 null，confidence 是证据支持强弱；保留
recommendation 与 support_scope，不写国标分数。两套体系均按实际研究类型评价贡献。
提交评价后，先阅读工具返回的确定结果，再调用 submit_report 一起撰写 overall 综合评价
和 summary 中文摘要。overall 解释主导整体结论的科学理由、适用范围及未知；摘要概括
这些判断。两者依据已经确定的分数和结论，自此形成整体判断，不自行计算国标总分。
这一步由当前同一主审完成，结束本次科学交付。
公开文字使用中文，工具参数须符合本轮 schema；正文引述使用中文引号。
"""


def record(name, **fields):
    return create_model(name, __config__=ConfigDict(extra="forbid"), **fields)


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


def bind_citations(value, aliases):
    """Change reference identities only, preserving every authored character and paragraph."""
    if isinstance(value, dict):
        return {
            key: list(dict.fromkeys(aliases[ref] for ref in item))
            if key == "cites"
            else bind_citations(item, aliases)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [bind_citations(item, aliases) for item in value]
    return value


def assessment_type(system, choices, *, partial=False):
    prose = prose_type(choices)
    dimensions = {}
    for criterion in system.criteria:
        fields = {"body": (prose, ...)}
        if system.id == "gbt42048_2022":
            fields["score"] = (
                int,
                Field(
                    ge=0,
                    le=criterion.max_score,
                    strict=True,
                    description="；".join(
                        f"{b.minimum}—{b.maximum}分：{b.description}" for b in criterion.bands
                    ),
                ),
            )
        else:
            fields.update(grade=(GRADE | None, ...), confidence=(GRADE | None, ...))
        dimension = record(criterion.id + "Assessment", **fields)
        dimensions[criterion.id] = (dimension | None, None) if partial else (dimension, ...)
    topic = record(
        "SelectedTopic", topic_id=(str, Field(min_length=1)), note_ref=(str, Field(min_length=1))
    )
    unknown = record("ScientificUnknown", title=(str, ...), text=(prose, ...))
    fields = {
        "project": (prose, ...),
        "scope": (prose, ...),
        "dimensions": (record("Dimensions", **dimensions), ...),
        "unknowns": (list[unknown], ...),
        "appendix_topics": (list[topic], ...),
    }
    if system.id == "gbt42048_2022":
        fields["safety_veto"] = (
            record("SafetyVeto", applies=(bool, ...), reason=(prose, ...)),
            ...,
        )
    else:
        fields.update(support_scope=(prose, ...), recommendation=(RECOMMENDATION, ...))
    if partial:
        fields = {key: (kind | None, None) for key, (kind, _) in fields.items()}
    return record("AssessmentParts" if partial else "ChiefAssessment", **fields)


class AssessmentSubmission:
    instruction = INSTRUCTION
    allow_research = True
    closing_instruction = (
        "研究进入评价收束。请直接提交现有依据支持的完整评价，"
        "收到代码计算结果后，在下一次响应用 submit_report 提交综合评价与摘要。"
    )
    completion_turns = 3
    local_tools = {"save_assessment", "submit_assessment", "finalize_assessment", "submit_report"}

    def __init__(self, chief, chief_id):
        self.chief, self.runtime, self.chief_id = chief, chief.runtime, chief_id
        self.store = self.runtime.store
        self.system = EvaluationSystem.model_validate(
            chief._manifest(chief_id)["review_context"]["evaluation"]
        )
        self.path = self.store._path("assessment-drafts", chief_id)

    def _draft(self):
        return json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}

    def _artifact(self, ref):
        return json.loads(self.store._path("assessments", ref).read_text("utf-8"))

    def completed(self, state):
        return state.report_ref is not None

    def context(self):
        state = self.runtime.load(self.chief_id)
        value = (
            self._artifact(state.assessment_ref)
            if state.assessment_ref
            else {"draft": self._draft()}
        )
        # The originals themselves already belong to the chief's reading scope.
        value = {
            key: item for key, item in value.items() if key not in {"evidence", "selected_topics"}
        }
        return bind_citations(value, {handle: alias for alias, handle in state.aliases.items()})

    def _save(self, state, values):
        draft = self._draft()
        bound = bind_citations(values, state.aliases)
        if "dimensions" in bound:
            draft.setdefault("dimensions", {}).update(bound.pop("dimensions"))
        draft.update(bound)
        write_json(self.path, draft)
        state.assessment_ref = state.report_ref = None
        self.store.save(state)
        return draft

    def _finalize(self, state):
        try:
            draft = assessment_type(self.system, state.evidence).model_validate(self._draft())
        except ValidationError as exc:
            return ToolResult(
                ok=False,
                error="assessment_incomplete",
                content=[{"location": e["loc"], "type": e["type"]} for e in exc.errors()],
            )
        selected = []
        owned = self.chief._manifest(self.chief_id)["topic_ids"]
        for row in draft.appendix_topics:
            if row.topic_id not in owned:
                raise ValueError("appendix_topic_not_owned_by_chief")
            topic_state = self.runtime.load(row.topic_id)
            if row.note_ref not in topic_state.submissions:
                raise ValueError("appendix_requires_submitted_topic_version")
            selected.append(
                self.chief.topics.result(row.topic_id, note_ref=row.note_ref).model_dump(
                    mode="json"
                )
            )
        assessment = finalize_assessment(draft.model_dump(mode="json"), self.system)
        artifact = {
            "schema_version": "v5-assessment-artifact-1",
            "chief_id": self.chief_id,
            "assessment": assessment,
            "evidence": {key: state.evidence[key] for key in sorted(set(cited_ids(assessment)))},
            "selected_topics": selected,
            "admission": self.chief._manifest(self.chief_id)["review_context"]["admission"],
        }
        ref = "assessment-" + digest(artifact)
        write_json(self.store._path("assessments", ref), artifact)
        state.assessment_ref, state.report_ref = ref, None
        self.store.save(state)
        return ToolResult(
            ok=True,
            content={
                "assessment_ref": ref,
                "assessment": assessment,
                "next": (
                    "依据此确定结果，使用 submit_report 提交综合评价 overall 与中文摘要 summary。"
                ),
            },
        )

    def tools(self, state, absorbed_through, *, completing):
        registry = ToolRegistry()

        async def save(args):
            values = {
                key: value
                for key, value in args.model_dump(mode="json", exclude_unset=True).items()
                if value is not None
            }
            if "dimensions" in values:
                values["dimensions"] = {
                    k: v for k, v in values["dimensions"].items() if v is not None
                }
            self._save(state, values)
            # The current author has absorbed earlier tools into a saved public draft.
            state.observations = [o for o in state.observations if o["index"] > absorbed_through]
            self.store.save(state)
            return ToolResult(ok=True, content={"saved_parts": list(self._draft())})

        async def submit(args):
            self._save(state, args.model_dump(mode="json"))
            return self._finalize(state)

        async def finalize(args):
            return self._finalize(state)

        async def report(args):
            if not state.assessment_ref:
                return ToolResult(ok=False, error="submit_assessment_before_report")
            artifact = self._artifact(state.assessment_ref)
            closing = bind_citations(args.model_dump(mode="json"), state.aliases)
            artifact["assessment"].update(closing)
            for key in cited_ids(closing):
                artifact["evidence"][key] = state.evidence[key]
            artifact["assessment_ref"] = state.assessment_ref
            ref = "report-" + digest(artifact)
            write_json(self.store._path("assessments", ref), artifact)
            state.report_ref = ref
            state.status, state.reason = "submitted", None
            self.store.save(state)
            return ToolResult(ok=True, content={"report_ref": ref, "status": "submitted"})

        if state.assessment_ref:
            registry.add(
                Tool(
                    "submit_report",
                    "依据代码返回的确定结果，提交综合评价与摘要，完成同主审科学交付。",
                    record(
                        "ReportConclusion",
                        overall=(prose_type(state.aliases), ...),
                        summary=(prose_type(state.aliases), ...),
                    ),
                    report,
                    read_only=False,
                )
            )
        else:
            registry.add(
                Tool(
                    "submit_assessment",
                    "提交逐项评价；代码计算结果并反馈，供下一轮写综合评价与摘要。",
                    assessment_type(self.system, state.aliases),
                    submit,
                    read_only=False,
                )
            )
            registry.add(
                Tool(
                    "finalize_assessment",
                    "将已保存的全部评价部分提交计分；要求六项及主文完整。",
                    NoArguments,
                    finalize,
                    read_only=False,
                )
            )
        if not completing or not state.assessment_ref:
            registry.add(
                Tool(
                    "save_assessment",
                    "保存本次完成的评价部分；未传部分保留，dimensions按指标更新。",
                    assessment_type(self.system, state.aliases, partial=True),
                    save,
                    read_only=False,
                )
            )
        return registry

    def result(self):
        state = self.runtime.load(self.chief_id)
        ref = state.report_ref or state.assessment_ref
        return {
            "assessment_ref": state.assessment_ref,
            "report_ref": state.report_ref,
            "scientific_status": "completed" if state.report_ref else "partial",
            **(
                deepcopy(self._artifact(ref))
                if ref
                else {"assessment": None, "evidence": {}, "selected_topics": []}
            ),
        }
