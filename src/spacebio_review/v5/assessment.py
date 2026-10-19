"""One chief decision, deterministic scoring, then synthesis of that same decision."""

from __future__ import annotations

import json
from copy import deepcopy

from spacebio_review.agents.diagnostics import digest
from spacebio_review.evidence.reading_render import render_originals
from spacebio_review.persistence import write_json
from spacebio_review.prose import cited_ids, prose_inputs
from spacebio_review.review.evaluation import EvaluationSystem
from spacebio_review.review.scoring import finalize_assessment
from spacebio_review.tools import ToolResult

from .assessment_authoring import (
    compile_fields,
    decision_schema,
    field_sources,
)
from .report_composition import ReportComposition
from .research_context import context_references, expand_captured_units
from .review_context import review_skill_context
from .source_reading import SourceReading, visible_note_refs

DECISION_INSTRUCTION = """现在形成本轮唯一一次正式评价决定。直接写普通中文 Markdown 正文。
用 Markdown 的 ## 和 ### 小标题组织项目理解、审议范围、各项指标、否决或支持范围、尚未确定事项。
每个指标独立一个 ## 章节，标题中标明 criterion_id、指标名称和本次分值或等级。
项目理解、审议范围、否决依据或支持范围各自独立一个 ## 章节；否决值或建议也写在标题中。
尚未确定事项逐项使用简短小标题，再写对应的科学说明。
依据完整申请、当前项目理解、有序科学稿和关键原文，给出项目理解、实际审议范围、全部
逐项评价、本轮要求的决定和尚未确定事项。尚未确定事项为空时明确写无。
每项先说明可得知识、实际贡献及已有支持，再解释真正主导这一指标的限制及其作用。
相对已有工作具体比较对象、读出与发现，作出有依据的判断。研究计划允许合理的方法优化、
参数选择和样品适配；已有基础支持实施时予以承认。描述性比较与方法应用按知识增量判断，
机制性主张才需要相称的因果识别依据。材料不足仍评价已支持的程度，不机械选最低档。
申请实际安排和基础由申请原文确定，外部科学稿用于领域比较和实现参照。评审搜到的论文、
工具数量和作者自己的建议不是申请团队已有条件；未提供不等于不存在。
一项限制确实影响不同指标时分别解释其作用，不把局部问题扩大为所有方面均无法判断。
安全评价连起材料、操作、暴露或泄漏路径及控制依据；未确定条件保持未确定。未发现具体
否决事实可以不否决，但不等于已证实安全。技术性未完成与科学未知分开表达。
完整科学稿由代码按选定版本出版；这里写清其发现如何影响评价，交付现有方案的评价与范围。
引用紧跟具体论述，说明来源标题和表格、章节等原始位置，已有证据链接直接保留。
你写科学正文和明确决定；装配员选择原文块，代码绑定引用并计算制度结果。本阶段不调用工具。
"""

NATIONAL_DECISIONS = """本轮为国标评价。六项均给完整整数 score，同次写出各项理由，
先解释实际表现，对照完整分档再决定档内分值。另给 safety_veto 的 applies 与 reason；
低分或未知本身不等于否决。总分、档位及正式支持结论由代码在本次决定之后计算。
本步只交付逐项评价与否决决定，摘要和综合在看到实际计算结果后撰写。
"""

GENERAL_DECISIONS = """本轮为通用评价。各项明确写 grade/confidence 为 A—E，未形成等级则写 null。
confidence 表达证据强弱。给出 recommendation（support、conditional_support、revise、
not_support、insufficient_basis）和完整 Markdown support_scope，明确支持决定对应的科学范围。
这些决定本次形成，下一步综合沿用。摘要和综合在下一步撰写。
"""

SYNTHESIS_INSTRUCTION = """依据已经作出的同一份评价决定，直接写普通中文 Markdown 正文。
实际计算的总分、档位、建议及安全否决，或通用体系的建议与支持范围，已经确定。
解释这些决定，保留评价中真实成立的积极认识及主导判断的限制。
综合已有认识，不重新建立分子通路、补出实验方案或改变分数和选择。
需要引用时说明来源标题和原始位置；已有证据链接直接保留。本阶段不调用工具。
"""


class AssessmentSubmission:
    allow_research = True

    def __init__(self, chief, chief_id):
        self.chief, self.runtime, self.chief_id = chief, chief.runtime, chief_id
        self.store = self.runtime.store
        self.composition = ReportComposition(chief, chief_id)
        self.system = EvaluationSystem.model_validate(
            chief._manifest(chief_id)["review_context"]["evaluation"]
        )

    @staticmethod
    def closing_instruction(state):
        return "研究收束，先形成逐项评价决定；代码计算制度结果后，再解释同一决定。"

    def _artifact(self, ref):
        return json.loads(self.store._path("assessments", ref).read_text("utf-8"))

    def instruction(self, state, operation):
        if operation == "write_overall":
            return SYNTHESIS_INSTRUCTION + "只写综合科学判断，连起不同科学问题的价值与取舍。"
        if operation == "write_summary":
            return (
                SYNTHESIS_INSTRUCTION
                + "只写可独立阅读的摘要，说明项目对象、可得知识、正式决定及主要理由。"
            )
        if operation != "submit_assessment":
            raise ValueError("unknown_assessment_operation:" + operation)
        instruction = (
            DECISION_INSTRUCTION
            + "\n"
            + (NATIONAL_DECISIONS if self.system.score_grades else GENERAL_DECISIONS)
        )
        if state.report_selection is None:
            instruction += (
                "\n末尾另列选文安排：从 scientific_manuscripts 选出主文与附录，"
                "写出候选编号（如 S2、S1）和顺序；主文至少一篇，无附录则明确写无。"
            )
        return instruction

    def _messages(self, state, operation, view):
        from .session import encoded

        instruction = self.instruction(state, operation)
        messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": encoded(view)},
        ]
        conversation = self.runtime._conversations.get(state.session_id)
        if conversation and self.runtime.models[state.model_alias].config.vision:
            messages.extend(conversation.visual_messages(view.get("originals", [])))
        return messages

    async def accept_authoring(self, state, raw_ref, raw):
        operation = raw["operation"]
        schema = None
        if operation == "submit_assessment":
            schema = decision_schema(
                self.system,
                raw["paragraphs"],
                candidates=(
                    [row["candidate_id"] for row in state.report_candidates]
                    if state.report_selection is None
                    else ()
                ),
            )
        body, values = await self.runtime.assembly.assemble(
            state,
            raw,
            decision_schema=schema,
            purpose="completion",
        )
        if operation == "submit_assessment":
            self.composition.choose(state, values)
            result = self._save_decision(state, compile_fields(values, body), raw_ref)
        else:
            result = self._publish(state, {operation.removeprefix("write_"): body}, raw_ref)
        state.observations = [
            row for row in state.observations if row["index"] > raw["absorbed_through"]
        ]
        self.store.save(state)
        return result

    def completed(self, state):
        return state.report_ref is not None

    @staticmethod
    def completion_operations(state):
        if state.report_ref:
            return []
        if state.overall_ref:
            return ["write_summary"]
        if state.assessment_ref:
            return ["write_overall", "write_summary"]
        return ["submit_assessment", "write_overall", "write_summary"]

    def prepare(self, state, selection=None):
        self.composition.prepare(state, selection)
        for ref in visible_note_refs(
            {
                "core_contribution": self.chief.contribution_context(state),
                "scientific_manuscripts": self._scientific_manuscripts(state),
            }
        ):
            self.runtime._capture(state, self.store.note(ref)["evidence"])
        self.store.save(state)

    def _scientific_manuscripts(self, state):
        """Current candidate manuscripts retain their identified source explanations."""
        return [
            {
                **{
                    key: row[key]
                    for key in (
                        "candidate_id",
                        "inquiry_id",
                        "contribution_ref",
                        "note",
                        "focus",
                        "current_focus",
                        "author_session_id",
                    )
                    if key in row
                },
                "source_role": "review_interpretation",
                "source_readings": [
                    SourceReading.view(reading)
                    for reading in self.chief.topics.reader.results(row["inquiry_id"])
                ],
            }
            for row in self.composition.candidates(state)
        ]

    def _decision_view(self, state):
        """The same current scientific input is used for budget previews and the decision."""
        preview = state.model_copy(deep=True)
        inquiry = self.runtime._inquiry(preview)
        if inquiry:
            preview.note_ref = inquiry.current_contribution_ref
            if preview.note_ref:
                self.runtime._capture(preview, self.store.note(preview.note_ref)["evidence"])
        manuscripts = self._scientific_manuscripts(state)
        for ref in visible_note_refs(manuscripts):
            self.runtime._capture(preview, self.store.note(ref)["evidence"])
        seen = {row["contribution_ref"] for row in manuscripts}
        if preview.note_ref:
            seen.add(preview.note_ref)
        working = {
            "core_contribution": self.chief.contribution_context(state, seen=seen),
            "report_selection": state.report_selection.model_dump(mode="json")
            if state.report_selection
            else None,
            "scientific_manuscripts": manuscripts,
        }
        view = self.runtime._context(preview, lambda: working, purpose="composition")
        conversation = self.runtime._conversations.get(state.session_id)
        if conversation is not None:
            # The decision inherits the originals still readable in the chief's
            # current dialogue, whether or not a working manuscript cites them.
            # Keep the ordinary composition basis as well (including selected
            # manuscripts), without restoring the whole historical archive.
            originals = set(context_references(view["originals"])) | conversation.shown_handles
            view = self.runtime._context(
                preview,
                lambda: working,
                purpose="composition",
                originals=originals,
            )
        evaluation = self.chief._manifest(self.chief_id)["review_context"]
        view["task"]["evaluation"] = {
            key: item for key, item in evaluation.items() if key != "capabilities"
        }
        if self.runtime.skills:
            view["skills"][self.system.skill] = review_skill_context(
                self.runtime.skills.get(self.system.skill), decision=True
            )
        return view, preview

    @staticmethod
    def _manuscript_titles(artifact):
        locations = {
            (row["inquiry_id"], row["contribution_ref"]): field
            for field, rows in artifact["report_selection"].items()
            for row in rows
        }
        return [
            {
                "title": row["note"]["title"],
                "purpose": row.get("focus"),
                "placement": locations[(row["inquiry_id"], row["contribution_ref"])],
                "inquiry_id": row["inquiry_id"],
                "contribution_ref": row["contribution_ref"],
            }
            for row in artifact["contributions"]
        ]

    def _synthesis_view(self, state, artifact):
        return {
            "assessment_ref": state.assessment_ref,
            "assessment": prose_inputs(artifact["assessment"]),
            "selected_manuscripts": self._manuscript_titles(artifact),
            "originals": render_originals(
                {
                    handle: {**deepcopy(snapshot), "citation": f"[{handle}](evidence:{handle})"}
                    for handle, snapshot in expand_captured_units(
                        state.evidence, artifact["evidence"]
                    ).items()
                }
            ),
        }

    def authoring_input(self, state, operation):
        if operation == "submit_assessment":
            view, _ = self._decision_view(state)
        elif operation in {"write_overall", "write_summary"}:
            view = self._synthesis_view(
                state, self._artifact(state.overall_ref or state.assessment_ref)
            )
        else:
            raise ValueError("unknown_assessment_operation:" + operation)
        readings = set(context_references(view["originals"])) | set(context_references(view))
        return self._messages(state, operation, view), sorted(readings)

    def completion_input(self, state, operation):
        return self.authoring_input(state, operation)[0]

    def synthesis_estimate(self, state, expected_output_tokens, operation="write_overall"):
        """Budget-only projected decision size; actual synthesis always uses the saved decision."""
        if state.assessment_ref:
            return self.completion_input(state, operation)
        view, preview = self._decision_view(state)
        used = set(context_references(view))
        originals = expand_captured_units(preview.evidence, used)
        estimate = {
            "assessment": {
                "evaluation_system": self.system.snapshot(),
                "expected_decision_body": (
                    "科学评价正文与依据。" * max(1, expected_output_tokens // 10)
                ),
            },
            "selected_manuscripts": [
                {"title": row["note"]["title"], "purpose": row.get("focus")}
                for row in self.composition.candidates(state)
            ],
            "originals": render_originals(
                {
                    handle: {**item, "citation": f"[{handle}](evidence:{handle})"}
                    for handle, item in preview.evidence.items()
                    if handle in originals
                }
            ),
        }
        return self._messages(state, operation, estimate)

    def context(self):
        state = self.runtime.load(self.chief_id)
        return {
            "report_selection": state.report_selection.model_dump(mode="json")
            if state.report_selection
            else None,
            "scientific_manuscripts": self.composition.catalogue(),
        }

    def _save_decision(self, state, draft, raw_ref):
        assessment = finalize_assessment(draft, self.system)
        artifact = {
            "chief_id": self.chief_id,
            "assessment": assessment,
            "evidence": {key: state.evidence[key] for key in sorted(set(cited_ids(assessment)))},
            "report_selection": state.report_selection.model_dump(mode="json"),
            "contributions": self.composition.selected(state),
            "core_contribution": self.chief.contribution_context(state),
            "admission": self.chief._manifest(self.chief_id)["review_context"]["admission"],
            "author_sources": field_sources(draft, raw_ref),
            "last_author_text_ref": raw_ref,
        }
        ref = "assessment-" + digest(artifact)
        write_json(self.store._path("assessments", ref), artifact)
        state.assessment_ref, state.report_ref = ref, None
        self.store.save(state)
        return ToolResult(ok=True, content={"assessment_ref": ref, "status": "decided"})

    def _publish(self, state, synthesis, raw_ref):
        artifact = self._artifact(state.overall_ref or state.assessment_ref)
        artifact["assessment_ref"] = state.assessment_ref
        artifact["assessment"].update(synthesis)
        artifact["author_sources"].update(field_sources(synthesis, raw_ref))
        artifact["report_author_source"] = {"author_text_ref": raw_ref}
        artifact["last_author_text_ref"] = raw_ref
        artifact["evidence"] = {
            key: state.evidence[key] for key in sorted(set(cited_ids(artifact["assessment"])))
        }
        complete = "summary" in synthesis
        ref = ("report-" if complete else "overall-") + digest(artifact)
        write_json(self.store._path("assessments", ref), artifact)
        if complete:
            state.report_ref = ref
            state.status, state.reason = "submitted", None
        else:
            state.overall_ref = ref
        self.store.save(state)
        return ToolResult(
            ok=True,
            content={
                "report_ref" if complete else "overall_ref": ref,
                "status": "submitted" if complete else "composed",
            },
        )

    def result(self):
        state = self.runtime.load(self.chief_id)
        ref = state.report_ref or state.overall_ref or state.assessment_ref
        return {
            "assessment_ref": state.assessment_ref,
            "report_ref": state.report_ref,
            "scientific_status": "completed" if state.report_ref else "partial",
            **(
                deepcopy(self._artifact(ref))
                if ref
                else {
                    "assessment": None,
                    "evidence": {},
                    "report_selection": None,
                    "contributions": [],
                }
            ),
        }
