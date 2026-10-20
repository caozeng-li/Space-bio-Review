"""Chief evaluation and summary -> section drafts -> one authored report."""

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

from .assessment_authoring import decision_schema, decision_type
from .report_writing import ReportWriting
from .research_context import application_originals, context_references, expand_captured_units
from .review_context import review_skill_context

DECISION_INSTRUCTION = """你是科学项目评审系统的主评审者。专家已经阅读申请和资料，
提交了各自负责问题的评审意见。研究阶段已经结束，现在由你综合这些意见，形成评价决定。
依据完整申请、专家意见、主审已有判断和相关原文，决定各指标的评价；对照评价制度的
完整分档给出分值或等级。每项用一小段说明主要判断依据。
overall_judgment 直接写成放在正式报告开头的“综合评价”正文：三至四个自然段，约450—700中文字，
交代项目对象与安排、最值得获得的认识、主要支持与限制，以及整体取舍。不逐项复述评分，
不输出标题或评分表；可以用简短加粗判断句突出重点。它直接基于本轮申请、专家意见和原文形成，
后续分节写作与统稿沿用这份综合评价，不再根据中间草稿重新总结。
专家意见是有依据的专业判断，由你综合取舍；申请安排与团队基础以申请原文为准。
按申请实际目标与研究阶段评价，肯定已有支持和价值，说明真正影响判断的限制。
描述性比较、探索和方法应用也有独立价值；对机制解释的限制按它实际影响的目标评价。
申请中合理的参数优化与适配属于计划工作。材料不足时评价已有依据支持的程度，
申请未提供不等于条件不存在，未发现具体安全否决事实可以不否决。各指标按自身依据判断：
科学意义看问题价值，创新性比较已有工作，方案评价方法和实施路径；某一目标的解释力度
不足，按它对相应指标的实际影响取值，而不替代其他指标各自的专业判断。
本步使用给定 JSON 接口提交评价。overall_judgment 是正式综合评价，其余字段供计算及各项论证使用。
总分、档位及制度结论由程序计算；随后逐节展开论据，再一次统稿形成完整报告。
"""

class AssessmentSubmission:
    allow_research = True

    def __init__(self, chief, chief_id):
        self.chief, self.runtime, self.chief_id = chief, chief.runtime, chief_id
        self.store = self.runtime.store
        self.system = EvaluationSystem.model_validate(
            chief._manifest(chief_id)["review_context"]["evaluation"]
        )
        self.writing = ReportWriting(self.system, self.store)

    @staticmethod
    def closing_instruction(state):
        return "研究收束，综合专家评审意见形成评价决定；程序计算结果后，撰写完整报告。"

    def _artifact(self, ref):
        return json.loads(self.store._path("assessments", ref).read_text("utf-8"))

    def response_schema(self, state, operation):
        return (
            decision_schema(self.system) if operation == "submit_assessment"
            else None
        )

    def output_tokens(self, operation, config):
        return (
            config.max_tokens if operation == "submit_assessment"
            else self.writing.output_tokens(operation, config)
        )

    def _messages(self, state, operation, view):
        instruction = DECISION_INSTRUCTION
        if schema := self.response_schema(state, operation):
            # JSON-object providers need the contract in the actual model input too.
            instruction += "\n提交的 JSON 结构：\n" + json.dumps(schema, ensure_ascii=False)
        messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(view, ensure_ascii=False)},
        ]
        conversation = self.runtime._conversations.get(state.session_id)
        if conversation and self.runtime.models[state.model_alias].config.vision:
            messages.extend(conversation.visual_messages(view.get("originals", [])))
        return messages

    def _review_input(self, state):
        """Only delivered opinions and their original context enter final writing."""
        manifest = self.chief._manifest(self.chief_id)
        opinions, used = [], set()
        for inquiry_id in manifest["topic_ids"]:
            refs = self.chief.topics.contribution_refs(inquiry_id)
            if not refs:
                continue
            artifact = self.store.contribution(inquiry_id, refs[0])
            self.runtime._capture(state, artifact["evidence"])
            opinions.append(prose_inputs(artifact["note"]))
            used.update(cited_ids(artifact["note"]))
        inquiry = self.runtime._inquiry(state)
        chief_ref = inquiry.current_contribution_ref if inquiry else state.note_ref
        chief_opinion = None
        if chief_ref:
            artifact = self.store.note(chief_ref)
            self.runtime._capture(state, artifact["evidence"])
            chief_opinion = prose_inputs(artifact["note"])
            used.update(cited_ids(artifact["note"]))
        application = deepcopy(state.task.application)
        present = application_originals(application, state.evidence)
        for handle, passage in present.items():
            passage["citation"] = f"[{handle}](evidence:{handle})"
        originals = expand_captured_units(state.evidence, used)
        view = {
            "application": application,
            "expert_opinions": opinions,
            "chief_opinion": chief_opinion,
            "evaluation": deepcopy(manifest["review_context"]["evaluation"]),
            "originals": render_originals(
                {
                    handle: {**item, "citation": f"[{handle}](evidence:{handle})"}
                    for handle, item in originals.items()
                    if handle not in present
                }
            ),
        }
        if self.runtime.skills:
            view["evaluation_instructions"] = review_skill_context(
                self.runtime.skills.get(self.system.skill), decision=True
            )
        return view

    def prepare(self, state):
        self._review_input(state)
        self.store.save(state)

    def authoring_input(self, state, operation):
        if operation == "submit_assessment":
            view = self._review_input(state)
            messages = self._messages(state, operation, view)
        else:
            artifact = self._artifact(state.assessment_ref)
            messages = self.writing.messages(
                self.writing.load(state), operation,
                artifact["review_input"], artifact["assessment"],
            )
        return messages, sorted(set(context_references([
            message for message in messages if message["role"] == "user"
        ])))

    def completion_input(self, state, operation):
        return self.authoring_input(state, operation)[0]

    def estimate_input(self, state, operation):
        if operation == "submit_assessment":
            return self.completion_input(state, operation)
        artifact = self._artifact(state.assessment_ref) if state.assessment_ref else {
            "review_input": self._review_input(state),
            "assessment": {"overall_judgment": "评价决定及主要理由。" * 200},
        }
        return self.writing.messages(
            self.writing.load(state), operation, artifact["review_input"], artifact["assessment"],
            estimate=True,
        )

    def completion_operations(self, state):
        if state.report_ref:
            return []
        return [
            *([] if state.assessment_ref else ["submit_assessment"]),
            *self.writing.operations(self.writing.load(state)),
        ]

    @staticmethod
    def completed(state):
        return state.report_ref is not None

    def context(self):
        return {"instruction": "综合专家评审意见形成判断，研究结束后由主审撰写完整报告。"}

    async def accept_authoring(self, state, raw_ref, raw):
        if raw["operation"] == "submit_assessment":
            decision = decision_type(self.system).model_validate_json(raw["content"]).model_dump()
            artifact = {
                "chief_id": self.chief_id,
                "assessment": finalize_assessment(decision, self.system),
                "review_input": self._review_input(state),
                "admission": self.chief._manifest(self.chief_id)["review_context"]["admission"],
                "author_sources": {"decision": {"author_text_ref": raw_ref}},
                "evidence": {},
            }
            ref = "assessment-" + digest(artifact)
            write_json(self.store._path("assessments", ref), artifact)
            state.assessment_ref, state.report_ref = ref, None
            state.report_writing_ref = None
            result = {"assessment_ref": ref, "status": "decided"}
        else:
            work = self.writing.load(state)
            sections = self.writing.accept(state, work, raw_ref, raw)
            if sections is None:
                self.store.save(state)
                return ToolResult(ok=True, content={
                    "report_writing_ref": state.report_writing_ref, "status": "writing",
                })
            artifact = self._artifact(state.assessment_ref)
            artifact["assessment_ref"] = state.assessment_ref
            artifact["assessment"]["sections"] = sections
            artifact["author_sources"]["report"] = {
                "report_writing_ref": state.report_writing_ref,
                "author_text_ref": work["report"],
                "drafts": work["drafts"],
            }
            artifact["evidence"] = {
                key: state.evidence[key] for key in sorted(set(cited_ids(sections)))
            }
            ref = "report-" + digest(artifact)
            write_json(self.store._path("assessments", ref), artifact)
            state.report_ref = ref
            state.status, state.reason = "submitted", None
            result = {"report_ref": ref, "status": "submitted"}
        self.store.save(state)
        return ToolResult(ok=True, content=result)

    def result(self):
        state = self.runtime.load(self.chief_id)
        ref = state.report_ref or state.assessment_ref
        return {
            "assessment_ref": state.assessment_ref,
            "report_ref": state.report_ref,
            "scientific_status": "completed" if state.report_ref else "partial",
            **(deepcopy(self._artifact(ref)) if ref else {"assessment": None, "evidence": {}}),
        }
