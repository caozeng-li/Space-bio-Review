"""Expert opinions -> chief decision -> one complete chief-authored report."""

from __future__ import annotations

import json
from copy import deepcopy

from spacebio_review.agents.diagnostics import digest
from spacebio_review.evidence.reading_render import render_originals
from spacebio_review.persistence import write_json
from spacebio_review.prose import cited_ids, compile_authored_body, prose_inputs
from spacebio_review.review.evaluation import EvaluationSystem
from spacebio_review.review.scoring import finalize_assessment
from spacebio_review.tools import ToolResult

from .assessment_authoring import decision_schema, decision_type
from .research_context import application_originals, context_references, expand_captured_units
from .review_context import review_skill_context

DECISION_INSTRUCTION = """你是科学项目评审系统的主评审者。专家已经阅读申请和资料，
提交了各自负责问题的评审意见。研究阶段已经结束，现在由你综合这些意见，形成评价决定。
依据完整申请、专家意见、主审已有判断和必要原文，决定各指标的评价；对照评价制度的
完整分档给出分值或等级。每项用一小段说明主要判断依据，同时给出简要整体判断。
专家意见是有依据的专业判断，由你综合取舍；申请安排与团队基础以申请原文为准。
按申请实际目标与研究阶段评价，肯定已有支持和价值，说明真正影响判断的限制。
描述性比较、探索和方法应用也有独立价值；对机制解释的限制按它实际影响的目标评价。
申请中合理的参数优化与适配属于计划工作。材料不足时评价已有依据支持的程度，
申请未提供不等于条件不存在，未发现具体安全否决事实可以不否决。各指标按自身依据判断。
本步使用给定 JSON 接口提交决定，字段供程序计算和下一步写作使用，不是报告正文。
总分、档位及制度结论由程序计算；你将在下一步看到结果并写完整报告。
"""

REPORT_INSTRUCTION = """你是科学项目评审系统的主评审者，现在撰写供评审委员会阅读的
完整中文项目评审报告。专家已经提交评审意见，你已形成逐项评价，程序也已计算正式结果。
依据完整申请、专家意见、关键原文及已确定的评价决定，一次写成连贯的普通 Markdown 正文。

正文从“## 综合评价”开始：用两至三个自然段简要交代项目拟开展什么研究，综合说明
其价值、已有支持和影响本次结论的主要因素。随后以评价体系各指标的中文名称为二级标题，
逐项展开判断及依据。综合评价负责整体取舍，各指标负责具体论证，项目概况只交代一次。
整篇正文通常约2000—3500个中文字，按项目复杂程度适当调整；每项以连贯段落写清
判断及必要依据。由你融合专家意见，相关专业知识用于解释对本项目的判断。
这是对申请现有方案的评审意见，不向申请人提供改造实验的方案咨询，也不写文献综述、
研究过程、内部专题问答或各种尚无依据的机制推演。用正式、自然、明确的评审语言。
评价的限定就近融入相关论证。保留专家结论的依据与适用条件，综合重复意见，解释有实质
影响的分歧。总分、等级和正式支持结论已经确定，正文的整体判断与该结果一致。

程序会添加文档总标题、评分表、制度结论和参考文献；你只写上述完整正文，不重复这些内容。
引用直接沿用输入中的 evidence: Markdown 链接，放在所支持的具体论述后；同一来源无需
堆叠引用。内部记录与调度信息不是文献来源。报告面向委员会，不展示 JSON 字段或任务指令。
"""


class AssessmentSubmission:
    allow_research = True

    def __init__(self, chief, chief_id):
        self.chief, self.runtime, self.chief_id = chief, chief.runtime, chief_id
        self.store = self.runtime.store
        self.system = EvaluationSystem.model_validate(
            chief._manifest(chief_id)["review_context"]["evaluation"]
        )

    @staticmethod
    def closing_instruction(state):
        return "研究收束，综合专家评审意见形成评价决定；程序计算结果后，撰写完整报告。"

    def _artifact(self, ref):
        return json.loads(self.store._path("assessments", ref).read_text("utf-8"))

    def response_schema(self, operation):
        return decision_schema(self.system) if operation == "submit_assessment" else None

    def instruction(self, state, operation):
        return {
            "submit_assessment": DECISION_INSTRUCTION,
            "write_report": REPORT_INSTRUCTION,
        }[operation]

    def _messages(self, state, operation, view):
        instruction = self.instruction(state, operation)
        if schema := self.response_schema(operation):
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
        elif operation == "write_report":
            artifact = self._artifact(state.assessment_ref)
            view = {**artifact["review_input"], "decision": artifact["assessment"]}
        else:
            raise ValueError("unknown_assessment_operation:" + operation)
        readings = set(context_references(view))
        readings.update(context_references(view.get("originals", [])))
        return self._messages(state, operation, view), sorted(readings)

    def completion_input(self, state, operation):
        return self.authoring_input(state, operation)[0]

    def report_estimate(self, state, expected_output_tokens):
        if state.assessment_ref:
            return self.completion_input(state, "write_report")
        view = self._review_input(state)
        view["decision"] = "评价决定及主要理由。" * max(1, expected_output_tokens // 10)
        return self._messages(state, "write_report", view)

    @staticmethod
    def completion_operations(state):
        if state.report_ref:
            return []
        return ["write_report"] if state.assessment_ref else ["submit_assessment", "write_report"]

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
            result = {"assessment_ref": ref, "status": "decided"}
        else:
            body = compile_authored_body(raw["content"], {"author_text_ref": raw_ref})
            artifact = self._artifact(state.assessment_ref)
            artifact["assessment_ref"] = state.assessment_ref
            artifact["assessment"]["body"] = body
            artifact["author_sources"]["body"] = {"author_text_ref": raw_ref}
            artifact["evidence"] = {
                key: state.evidence[key] for key in sorted(set(cited_ids(body)))
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
