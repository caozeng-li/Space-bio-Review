"""Map one complete scientific manuscript, score it, and publish its original paragraphs."""

from __future__ import annotations

import json
from copy import deepcopy

from pydantic import Field, ValidationError

from spacebio_review.agents.diagnostics import digest
from spacebio_review.persistence import write_json
from spacebio_review.prose import cited_ids, prose_type
from spacebio_review.review.evaluation import EvaluationSystem
from spacebio_review.review.scoring import finalize_assessment
from spacebio_review.tools import ToolResult

from .assessment_authoring import (
    GRADE,
    RECOMMENDATION,
    assessment_mapping_type,
    assessment_values,
    parsing_instruction,
    record,
)

INSTRUCTION = """现在形成本项目的完整评价原稿。直接输出普通中文正文，代码负责保存与出版。
结合完整申请、当前项目理解、专家专业意见、相关论文完整说明及原文一次写出以下内容：
项目理解 project、实际审议范围 scope、全部 dimensions 逐项评价、综合科学判断 overall、
尚未确定事项 unknowns、安全否决或通用支持范围及建议、所选专题附录 appendix_topics。
按目录中的名称和 ID 区分各指标，章节标题、自然段与决定值用空行分隔。
每项先解释可得知识、实际贡献和已有支持，再说明真正决定判断的限制，单独成段明确
写分值，或 grade/confidence。有相称依据时明确肯定，有决定性问题时明确指出。
综合判断说明为何值得开展或为何当前方案不足，以及主导限制影响哪一步认识。
以研究计划的阶段评价：方法优化、参数选择和样品适配可以是合理计划工作；已有基础
支持其实施时予以承认。合理的专业推断说明来由；申请实际条件由原文确定。
在项目理解或综合判断中自然写出少量可独立阅读的核心判语：写完整项目对象、可获得的
知识、主导限制及判断范围。可用“核心判语”标题；无需另写一份重复的摘要。出版时将
直接选用这些原段落作为摘要，不另行概括。核心段落保留对象和阶段，如“计划返回后检测”
“申请未提供前期数据”；“未提供”不等于“不存在”，研究背景也不是本项目已完成的工作。
材料不足仍评价现有证据所支持的程度，不机械选择最低档或把缺项换算零分。同一限制
确实影响不同指标时才分别解释其作用，避免将局部问题扩展为所有方面均无法判断。
正文交付现有方案的评价和适用范围，不安排整改。
描述性比较与方法应用按知识增量判断；机制性主张才需要相应的因果识别依据。创新具体
比较已有工作的对象、读出和发现；有限先例只能支持具体差异，分档名称本身不是领先证据。
安全评价连起材料、实际操作、暴露或泄漏路径及已有控制依据；未确定条件保持未确定。
未发现具体否决事实可以不否决，但不等于已证实安全。技术性未完成与科学未知分开表达。
国标六项均给完整整数分数，先解释实际表现，对照本项分档再定档内分值。安全否决须单独
明确适用或不适用及理由；低分或未知本身不等于否决。代码计算总分、等级及最终支持结论，
作者无需预测这些计算值。通用体系各项 grade/confidence 为 A—E 或明确 null，confidence
表达证据强弱；明确 recommendation 与 support_scope，不写国标分数。
无未知事项写“未知事项：无”；不选附录写“专题附录：无”。附录仅选择值得展开的已提交
专题及其明确 topic_id、note_ref，代码直接装配所选版本。
引用紧跟具体论述，用 [@E1]、[@E1][@E2] 或 [@E1, @E2]；引号和科学符号照常写。
不生成段落 ID、JSON 或嵌套数组，不写保存或提交操作行。本阶段不调用研究工具。
"""


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


def assessment_type(system, choices):
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
        dimensions[criterion.id] = (dimension, ...)
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
        "overall": (prose, ...),
        "summary": (prose, ...),
    }
    if system.id == "gbt42048_2022":
        fields["safety_veto"] = (
            record("SafetyVeto", applies=(bool, ...), reason=(prose, ...)),
            ...,
        )
    else:
        fields.update(support_scope=(prose, ...), recommendation=(RECOMMENDATION, ...))
    return record("ChiefAssessment", **fields)


class AssessmentSubmission:
    instruction = INSTRUCTION
    allow_research = True
    parsed_operations = {"submit_assessment"}

    @staticmethod
    def closing_instruction(state):
        return (
            "研究收束。直接写现有依据支持的完整评价原稿，包括全部逐项评价、综合科学判断和"
            "可直接用于摘要的核心判语。代码在同一原稿上完成解析、计分和报告装配。"
        )

    def __init__(self, chief, chief_id):
        self.chief, self.runtime, self.chief_id = chief, chief.runtime, chief_id
        self.store = self.runtime.store
        self.system = EvaluationSystem.model_validate(
            chief._manifest(chief_id)["review_context"]["evaluation"]
        )

    def _artifact(self, ref):
        return json.loads(self.store._path("assessments", ref).read_text("utf-8"))

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
        }
        return (
            assessment_mapping_type(self.system),
            parsing_instruction(),
            context,
        )

    def accept_authoring(self, state, raw_ref, raw, mapping):
        values = assessment_values(raw, mapping)
        try:
            draft = assessment_type(self.system, raw["aliases"]).model_validate(values)
        except ValidationError as exc:
            return ToolResult(
                ok=False,
                error="author_content_missing",
                content={
                    "author_text_ref": raw_ref,
                    "missing_fields": [".".join(map(str, row["loc"])) for row in exc.errors()],
                },
            )
        if state.assessment_ref:
            artifact = self._artifact(state.assessment_ref)
            if artifact.get("last_author_text_ref") == raw_ref:
                return self._publish(state, raw_ref, mapping)
        self._finalize(
            state,
            bind_citations(draft.model_dump(mode="json"), raw["aliases"]),
            raw_ref=raw_ref,
            mapping=mapping,
        )
        state.observations = [
            row for row in state.observations if row["index"] > raw["absorbed_through"]
        ]
        self.store.save(state)
        return self._publish(state, raw_ref, mapping)

    def completed(self, state):
        return state.report_ref is not None

    @staticmethod
    def completion_operations(state):
        return [] if state.report_ref else ["submit_assessment"]

    @staticmethod
    def completion_input(state, operation):
        # The sole author receives the existing research context. No second report prompt.
        return None

    def context(self):
        state = self.runtime.load(self.chief_id)
        value = (
            self._artifact(state.assessment_ref)
            if state.assessment_ref
            else {"assessment": None}
        )
        # The originals themselves already belong to the chief's reading scope.
        value = {
            key: item for key, item in value.items() if key not in {"evidence", "selected_topics"}
        }
        return bind_citations(value, {handle: alias for alias, handle in state.aliases.items()})

    def _finalize(self, state, draft, *, raw_ref, mapping):
        selected = []
        owned = self.chief._manifest(self.chief_id)["topic_ids"]
        for row in draft["appendix_topics"]:
            if row["topic_id"] not in owned:
                raise ValueError("appendix_topic_not_owned_by_chief")
            topic_state = self.runtime.load(row["topic_id"])
            if row["note_ref"] not in topic_state.submissions:
                raise ValueError("appendix_requires_submitted_topic_version")
            selected.append(
                self.chief.topics.result(row["topic_id"], note_ref=row["note_ref"]).model_dump(
                    mode="json"
                )
            )
        assessment = finalize_assessment(draft, self.system)
        origin = {
            "author_text_ref": raw_ref,
            "parse_ref": (state.pending_delivery or {}).get("parse_ref"),
        }
        mapped = mapping.model_dump(mode="json")
        sources = {
            key: {
                criterion: {**origin, "selection": mapped[key][criterion]}
                for criterion in draft[key]
            }
            if key == "dimensions"
            else {**origin, "selection": mapped[key]}
            for key in draft
        }
        artifact = {
            "chief_id": self.chief_id,
            "assessment": assessment,
            "evidence": {key: state.evidence[key] for key in sorted(set(cited_ids(assessment)))},
            "selected_topics": selected,
            "admission": self.chief._manifest(self.chief_id)["review_context"]["admission"],
            "author_sources": sources,
            "last_author_text_ref": raw_ref,
        }
        ref = "assessment-" + digest(artifact)
        write_json(self.store._path("assessments", ref), artifact)
        state.assessment_ref, state.report_ref = ref, None
        self.store.save(state)

    def _publish(self, state, raw_ref, mapping):
        """Build the report from the same accepted manuscript, with no further author call."""
        artifact = self._artifact(state.assessment_ref)
        artifact["assessment_ref"] = state.assessment_ref
        artifact["report_author_source"] = {
            "author_text_ref": raw_ref,
            "parse_ref": (state.pending_delivery or {}).get("parse_ref"),
            "overall_paragraph_ids": mapping.overall,
            "summary_paragraph_ids": mapping.summary,
        }
        ref = "report-" + digest(artifact)
        write_json(self.store._path("assessments", ref), artifact)
        state.report_ref = ref
        state.status, state.reason = "submitted", None
        self.store.save(state)
        return ToolResult(ok=True, content={"report_ref": ref, "status": "submitted"})

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
