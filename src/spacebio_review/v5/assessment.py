"""The chief submits criteria, receives scores, then writes the overall judgment and summary."""

from __future__ import annotations

import json
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from spacebio_review.agents.diagnostics import digest
from spacebio_review.persistence import write_json
from spacebio_review.prose import assemble_prose, cited_ids, prose_input_type, prose_type
from spacebio_review.review.evaluation import EvaluationSystem
from spacebio_review.review.scoring import finalize_assessment
from spacebio_review.tools import Tool, ToolRegistry, ToolResult

from .assessment_authoring import (
    GRADE,
    RECOMMENDATION,
    assessment_mapping_type,
    assessment_values,
    parsing_instruction,
    record,
)

INSTRUCTION = """本次由同一主审完成研究、评价和直接交付；评价工具用于研究后的收束。
结合完整申请、已经读到的原文和实际研究结果形成评价。project 说明项目的知识路径，
scope 说明本次实际审议范围；dimensions 各项 body 给出完整评价和依据。
unknowns 说明仍未知的事实及其影响，技术性未完成与科学未知分开。
材料不足仍评价实际能支持的认识。交付现有方案的评价和适用范围，不安排申请方整改任务。
评价正文按当前证据解释可获得的知识、影响结论的限制及其程度，不罗列通用缺项。
描述性比较和方法应用的价值按它们带来的知识增量判断；只有机制性主张才需要相应的
因果识别依据。创新评价具体比较已有工作的对象、读出和发现；有限先例支持具体差异，
国际或国内领先的定位需要相应代表性比较，分档名称本身不能作为领先的证据。
安全评价连起材料、实际操作、暴露或泄漏路径及已提供的控制依据。常规材料只是风险
分析的起点；未说明密封、浓度或废物处置时保持该条件未确定，并评价现有材料能支持的
安全程度。未发现具体否决事实可以判不否决，它不等于已证实安全。
评价直接输出普通正文，不调用 save_assessment 或 submit_assessment 工具。
第一行写「交付操作：save_assessment」分批保存，每批写完整的若干部分；完整交付时
第一行写「交付操作：submit_assessment」。交付正文的响应不能同时调用工具。
操作行后用空行分隔章节标题、自然段和决定值。各部分用 project、scope、dimensions、
unknowns、appendix_topics 及当前体系的安全否决或支持建议标识，指标用目录中的名称和 ID。
每项写清主要正面依据与决定分档的限制，分值或 grade/confidence 单独成段明确写出。
save_assessment 替换本稿提供的部分，dimensions 只替换所写指标，未写部分保持原稿。
未知事项可写带标题的说明或简明列表；无未知事项写「未知事项：无」，不选附录写「专题附录：无」。
保存齐各部分之后仍可用无参数 finalize_assessment 提交完整评价。正文引用紧跟具体论述，
用 [@E1] 形式，连续来源写 [@E1][@E2]；不生成段落 ID、JSON 正文或嵌套数组。
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
公开文字使用中文。评价正文原样写引号和科学符号；仅 submit_report 保留本轮工具 schema。
"""


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
    completion_turns = 3
    local_tools = {"finalize_assessment", "submit_report"}

    @staticmethod
    def closing_instruction(state):
        if state.assessment_ref:
            return (
                "逐项评价已提交并完成代码计算。依据当前 assessment_ref 的确定结果，"
                "现在调用 submit_report，按现有工具 schema 提交 overall 综合评价与"
                "summary 中文摘要，完成交付。"
            )
        return (
            "研究进入评价收束。请以「交付操作：submit_assessment」为首行，"
            "直接写现有依据支持的完整普通评价正文，不同时调用工具；"
            "收到代码计算结果后，在下一次响应用 submit_report 提交综合评价与摘要。"
        )

    def __init__(self, chief, chief_id):
        self.chief, self.runtime, self.chief_id = chief, chief.runtime, chief_id
        self.store = self.runtime.store
        self.system = EvaluationSystem.model_validate(
            chief._manifest(chief_id)["review_context"]["evaluation"]
        )
        self.path = self.store._path("assessment-drafts", chief_id)
        self.sources_path = self.store._path("assessment-draft-sources", chief_id)

    def _draft(self):
        return json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}

    def _artifact(self, ref):
        return json.loads(self.store._path("assessments", ref).read_text("utf-8"))

    def _sources(self):
        return (
            json.loads(self.sources_path.read_text("utf-8")) if self.sources_path.exists() else {}
        )

    def author_operations(self, state, *, completing):
        if completing and state.assessment_ref:
            return set()
        return (
            {"save_assessment"}
            if state.assessment_ref
            else {"save_assessment", "submit_assessment"}
        )

    def authoring_contract(self, raw):
        topics = [
            {"topic_id": topic_id, "note_refs": self.runtime.load(topic_id).submissions}
            for topic_id in self.chief._manifest(self.chief_id)["topic_ids"]
        ]
        context = {
            "evaluation_system": self.system.id,
            "criteria": [
                {"id": item.id, "label": item.label, "max_score": item.max_score}
                for item in self.system.criteria
            ],
            "selected_topic_options": topics,
            "complete_submission": raw["operation"] == "submit_assessment",
        }
        return assessment_mapping_type(self.system), parsing_instruction(raw["operation"]), context

    def accept_authoring(self, state, raw_ref, raw, mapping):
        partial = raw["operation"] == "save_assessment"
        values = assessment_values(raw, mapping)
        if missing := [key for key, value in values.items() if value is None]:
            return ToolResult(
                ok=False,
                error="author_content_missing",
                content={"author_text_ref": raw_ref, "missing_fields": missing},
            )
        try:
            # Reuse the canonical contract, including integer scores and explicit null grades.
            assessment_type(self.system, raw["aliases"], partial=partial).model_validate(values)
        except ValidationError as exc:
            return ToolResult(
                ok=False,
                error="author_content_missing",
                content={
                    "author_text_ref": raw_ref,
                    "missing_fields": [".".join(map(str, row["loc"])) for row in exc.errors()],
                },
            )
        if not partial and state.assessment_ref:
            artifact = self._artifact(state.assessment_ref)
            if artifact.get("last_author_text_ref") == raw_ref:
                return self._finalized_result(state.assessment_ref, artifact["assessment"])
        self._save(state, values, aliases=raw["aliases"], author_text_ref=raw_ref)
        # A saved draft absorbs only observations available when the author wrote it.
        state.observations = [
            row for row in state.observations if row["index"] > raw["absorbed_through"]
        ]
        self.store.save(state)
        if not partial:
            return self._finalize(state)
        return ToolResult(ok=True, content={"saved_parts": list(values)})

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

    def _save(self, state, values, *, aliases=None, author_text_ref=None):
        draft = self._draft()
        bound = bind_citations(values, state.aliases if aliases is None else aliases)
        if "dimensions" in bound:
            draft.setdefault("dimensions", {}).update(bound.pop("dimensions"))
        draft.update(bound)
        write_json(self.path, draft)
        if author_text_ref:
            sources = self._sources()
            origin = {
                "author_text_ref": author_text_ref,
                "parse_ref": (state.pending_delivery or {}).get("parse_ref"),
            }
            for key in values:
                if key == "dimensions":
                    sources.setdefault(key, {}).update(
                        {criterion: origin for criterion in values[key]}
                    )
                else:
                    sources[key] = origin
            sources["last_author_text_ref"] = author_text_ref
            write_json(self.sources_path, sources)
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
        if sources := self._sources():
            artifact["author_sources"] = {
                key: value for key, value in sources.items() if key != "last_author_text_ref"
            }
            artifact["last_author_text_ref"] = sources["last_author_text_ref"]
        ref = "assessment-" + digest(artifact)
        write_json(self.store._path("assessments", ref), artifact)
        state.assessment_ref, state.report_ref = ref, None
        self.store.save(state)
        return self._finalized_result(ref, assessment)

    @staticmethod
    def _finalized_result(ref, assessment):
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

        async def finalize(args):
            return self._finalize(state)

        async def report(args):
            if not state.assessment_ref:
                return ToolResult(ok=False, error="submit_assessment_before_report")
            artifact = self._artifact(state.assessment_ref)
            closing = bind_citations(
                {key: assemble_prose(value) for key, value in args.model_dump(mode="json").items()},
                state.aliases,
            )
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
                        overall=(prose_input_type(state.aliases), ...),
                        summary=(prose_input_type(state.aliases), ...),
                    ),
                    report,
                    read_only=False,
                )
            )
        else:
            registry.add(
                Tool(
                    "finalize_assessment",
                    "将已保存的全部评价部分提交计分；要求六项及主文完整。",
                    NoArguments,
                    finalize,
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
