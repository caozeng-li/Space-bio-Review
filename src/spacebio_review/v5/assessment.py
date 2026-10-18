"""Compose the chief's judgment alongside explicitly selected scientific manuscripts."""

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
from .report_composition import ReportComposition
from .research_context import context_references

INSTRUCTION = """现在撰写主审自己的综合评价原稿。直接输出普通中文正文，代码负责保存与出版。
科学作者的选定稿件将以完整正文进入报告主文，技术论证由这些稿件承担。
依据完整申请、当前项目理解、有序科学稿和关键原文，一次写出以下主审内容：
项目理解 project、实际审议范围 scope、全部 dimensions 逐项评价、综合科学判断 overall、
尚未确定事项 unknowns，以及本轮评价体系要求的决定。
综合判断连接不同科学稿说明哪些认识值得获取、为何值得开展以及真正主导判断的条件。
每项指标用一到两段简明理由，解释科学稿的贡献或困难如何影响该项评价。
正文按章节标题提及科学稿，候选短标识只用于选文行。
科学稿已展开的方法事实直接保留在原章节；主审可以作不同判断并说明取舍。
按目录中的名称和 ID 区分各指标，章节标题、自然段与决定值用空行分隔。
每项先解释可得知识、实际贡献和已有支持，再说明真正决定判断的限制，将本轮要求的
评分或等级单独成段明确写出。有相称依据时明确肯定，有决定性问题时明确指出。
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
无未知事项写“未知事项：无”。已确定的 report_selection 由代码直接装配。
若 report_selection 为 null，则从 scientific_manuscripts 在本稿中明确写出“主文选文：S2、S1”
这样的短标识顺序及“附录选文：无”或相应短标识。主文至少选一篇，按各稿实际价值取舍。
引用紧跟具体论述，用 [@E1]、[@E1][@E2] 或 [@E1, @E2]；引号和科学符号照常写。
不生成段落 ID、JSON 或嵌套数组，不写保存或提交操作行。本阶段不调用研究工具。
"""

NATIONAL_DECISIONS = """本轮为国标评价。六项均给完整整数 score，先解释实际表现，
对照本项分档再定档内分值。另给 safety_veto，明确适用或不适用及理由；低分或未知本身
不等于否决。本轮作者交付止于这些指标与否决决定，总分、等级及最终支持结论由代码计算。
"""

GENERAL_DECISIONS = """本轮为通用评价。各项 grade/confidence 为 A—E 或明确 null，
confidence 表达证据强弱。给出 recommendation（support、conditional_support、revise、
not_support、insufficient_basis）和 support_scope，解释支持决定及对应的科学范围。
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
    unknown = record("ScientificUnknown", title=(str, ...), text=(prose, ...))
    fields = {
        "project": (prose, ...),
        "scope": (prose, ...),
        "dimensions": (record("Dimensions", **dimensions), ...),
        "unknowns": (list[unknown], ...),
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
    allow_research = True
    parsed_operations = {"submit_assessment"}

    @staticmethod
    def closing_instruction(state):
        return (
            "研究收束。直接写现有依据支持的主审评价原稿，包括全部逐项评价、综合科学判断和"
            "可直接用于摘要的核心判语。代码在同一原稿上完成解析、计分和报告装配。"
        )

    def __init__(self, chief, chief_id):
        self.chief, self.runtime, self.chief_id = chief, chief.runtime, chief_id
        self.store = self.runtime.store
        self.composition = ReportComposition(chief, chief_id)
        self.system = EvaluationSystem.model_validate(
            chief._manifest(chief_id)["review_context"]["evaluation"]
        )
        self.instruction = INSTRUCTION + "\n" + (
            NATIONAL_DECISIONS if self.system.score_grades else GENERAL_DECISIONS
        )

    def _artifact(self, ref):
        return json.loads(self.store._path("assessments", ref).read_text("utf-8"))

    def authoring_contract(self, raw):
        state = self.runtime.load(self.chief_id)
        select = state.report_selection is None
        context = {
            "evaluation_system": self.system.id,
            "criteria": [
                {"id": item.id, "label": item.label, "max_score": item.max_score}
                for item in self.system.criteria
            ],
        }
        if select:
            context["candidate_options"] = [
                {key: row[key] for key in ("candidate_id", "inquiry_id", "contribution_ref")}
                for row in self.composition.candidates(state)
            ]
        return (
            assessment_mapping_type(self.system, selection_from_manuscript=select),
            parsing_instruction(selection_from_manuscript=select),
            context,
        )

    def accept_authoring(self, state, raw_ref, raw, mapping, *, parse_ref):
        values = assessment_values(raw, mapping)
        try:
            draft = assessment_type(self.system, raw["aliases"]).model_validate(values)
            self.composition.choose(state, mapping)
        except (ValidationError, KeyError) as exc:
            return ToolResult(
                ok=False,
                error="author_content_missing",
                content={
                    "author_text_ref": raw_ref,
                    "missing_fields": [".".join(map(str, row["loc"])) for row in exc.errors()]
                    if isinstance(exc, ValidationError) else ["report_selection"],
                },
            )
        self._finalize(
            state,
            bind_citations(draft.model_dump(mode="json"), raw["aliases"]),
            raw_ref=raw_ref,
            mapping=mapping,
            parse_ref=parse_ref,
        )
        state.observations = [
            row for row in state.observations if row["index"] > raw["absorbed_through"]
        ]
        self.store.save(state)
        return self._publish(state, raw_ref, mapping, parse_ref=parse_ref)

    def completed(self, state):
        return state.report_ref is not None

    @staticmethod
    def completion_operations(state):
        return [] if state.report_ref else ["submit_assessment"]

    def prepare(self, state, selection=None):
        self.composition.prepare(state, selection)

    def completion_input(self, state, operation):
        """Price and write the same composition input, without freezing during a preview."""
        from .session import SCIENTIFIC_INSTRUCTION, encoded

        preview = state.model_copy(deep=True)
        inquiry = self.runtime._inquiry(preview)
        if inquiry:
            preview.note_ref = inquiry.current_contribution_ref
            if preview.note_ref:
                self.runtime._capture(preview, self.store.note(preview.note_ref)["evidence"])
        manuscripts = self.composition.candidates(state)
        for row in manuscripts:
            self.runtime._capture(preview, row["evidence"])
        working = {
            "report_selection": state.report_selection.model_dump(mode="json")
            if state.report_selection else None,
            "scientific_manuscripts": [
                {key: row[key] for key in (
                    "candidate_id", "inquiry_id", "contribution_ref", "note", "focus",
                    "current_focus",
                ) if key in row}
                for row in manuscripts
            ],
        }
        view = self.runtime._context(preview, lambda: working, purpose="composition")
        evaluation = json.loads(view["task"]["evaluation"])
        view["task"]["evaluation"] = {
            key: item for key, item in evaluation.items()
            if key not in {"capabilities", "scientific_role"}
        }
        application = view["task"]["application"]
        if isinstance(application, dict):
            positions = {
                (source["source_id"], passage["passage_id"], passage["text"]): passage
                for source in application.get("sources", [])
                for passage in source.get("passages", [])
            }
            for alias, handle in preview.aliases.items():
                original = preview.evidence[handle]
                ref = original["reference"]
                passage = positions.get((
                    ref["source_id"], ref.get("passage_id"), original.get("quote"),
                ))
                if passage is not None:
                    passage["evidence_handle"] = alias
        # Only originals actually referenced by this writing input accompany it.
        # Unabsorbed scientific observations and the complete application remain intact.
        used = set(context_references({
            key: item for key, item in view.items()
            if key not in {"originals", "available_readings"}
        }))
        view["originals"] = {key: item for key, item in view["originals"].items() if key in used}
        view["available_readings"] = {
            key: item for key, item in view["available_readings"].items() if key in used
        }
        return [
            {"role": "system", "content": SCIENTIFIC_INSTRUCTION + "\n" + self.instruction
             + "\n" + self.closing_instruction(state)},
            {"role": "user", "content": encoded(view)},
        ]

    def context(self):
        state = self.runtime.load(self.chief_id)
        return {
            "report_selection": state.report_selection.model_dump(mode="json")
            if state.report_selection else None,
            "scientific_manuscripts": self.composition.catalogue(),
        }

    def _finalize(self, state, draft, *, raw_ref, mapping, parse_ref):
        selected = self.composition.selected(state)
        assessment = finalize_assessment(draft, self.system)
        origin = {
            "author_text_ref": raw_ref,
            "parse_ref": parse_ref,
        }
        mapped = mapping.model_dump(mode="json")
        original_order = [row["id"] for row in self.store.author_text(raw_ref)["paragraphs"]]

        def paragraph_order(value):
            if isinstance(value, dict):
                return {key: paragraph_order(item) for key, item in value.items()}
            if isinstance(value, list):
                if value and all(
                    isinstance(item, str) and item in original_order for item in value
                ):
                    return [item for item in original_order if item in value]
                return [paragraph_order(item) for item in value]
            return value

        mapped = paragraph_order(mapped)
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
            "report_selection": state.report_selection.model_dump(mode="json"),
            "contributions": selected,
            "admission": self.chief._manifest(self.chief_id)["review_context"]["admission"],
            "author_sources": sources,
            "last_author_text_ref": raw_ref,
        }
        ref = "assessment-" + digest(artifact)
        write_json(self.store._path("assessments", ref), artifact)
        state.assessment_ref, state.report_ref = ref, None
        self.store.save(state)

    def _publish(self, state, raw_ref, mapping, *, parse_ref):
        """Build the report from the same accepted manuscript, with no further author call."""
        artifact = self._artifact(state.assessment_ref)
        artifact["assessment_ref"] = state.assessment_ref
        artifact["report_author_source"] = {
            "author_text_ref": raw_ref,
            "parse_ref": parse_ref,
            "overall_paragraph_ids": artifact["author_sources"]["overall"]["selection"],
            "summary_paragraph_ids": artifact["author_sources"]["summary"]["selection"],
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
                else {"assessment": None, "evidence": {}, "report_selection": None,
                      "contributions": []}
            ),
        }
