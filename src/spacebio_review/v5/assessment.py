"""Chief evaluation and summary -> section drafts -> one authored report."""

from __future__ import annotations

import json
from copy import deepcopy

from spacebio_review.agents.diagnostics import digest
from spacebio_review.evidence.reading_render import render_originals
from spacebio_review.persistence import write_json
from spacebio_review.prose import cited_ids, prose_inputs
from spacebio_review.review.evaluation import EvaluationSystem
from spacebio_review.review.instructions import GENERAL_DECISION, review_instructions
from spacebio_review.review.scoring import finalize_assessment
from spacebio_review.tools import ToolResult

from .assessment_authoring import decision_schema, decision_type
from .report_writing import ReportWriting
from .research_context import (
    application_originals,
    context_references,
    expand_captured_units,
)
from .review_context import review_skill_context

DECISION_INSTRUCTION = """You are the chief reviewer. Form the evaluation decision from the complete
proposal, original evidence and the experts' delivered analyses. Decide each criterion against the
provided evaluation rules and give its main reasoning in a short paragraph. Use the specified
ratings or score bands; code calculates any total, grade and rule-based outcome.
Submit overall_judgment as three prose fields in the same response. They become the opening
Overall assessment of the report in this order: project_and_value explains the research object,
planned work and most valuable expected knowledge; support_and_limits explains the main supporting
evidence and limitations that materially affect the judgement; overall_tradeoff weighs them to
judge the current plan. Together aim for about 300-450 English words or 450-700 Chinese characters,
depending on the required language. Write three connected paragraphs without headings or a score
table; short bold judgements are optional. Subsequent drafting reuses these paragraphs rather
than deriving another overall judgement from intermediate section drafts.
Expert opinions contain professional analyses and judgements. Interpret them with the proposal
and original sources, adopt supported analysis, and make your own overall judgement. Facts follow
their original objects, stages and conditions. Where an opinion differs from the source, use the
judgement supported by the source. Judge unspecified conditions by their actual implications.
Use the supplied JSON interface. overall_judgment is publication-ready prose; the other fields
support the calculations and criterion arguments. Subsequent steps develop the criterion sections
and consolidate the report.
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

    def _artifact(self, ref):
        return json.loads(self.store._path("assessments", ref).read_text("utf-8"))

    def response_schema(self, state, operation):
        return decision_schema(self.system) if operation == "submit_assessment" else None

    def output_tokens(self, operation, config):
        return (
            config.max_tokens
            if operation == "submit_assessment"
            else self.writing.output_tokens(operation, config)
        )

    def _messages(self, state, operation, view):
        instruction = (
            DECISION_INSTRUCTION + "\n" + review_instructions(self.system)
            + ("\n" + GENERAL_DECISION if self.system.id == "general" else "")
        )
        if schema := self.response_schema(state, operation):
            # Keep field meanings visible in the writing instructions as well as the API schema.
            instruction += "\nJSON submission schema:\n" + json.dumps(schema, ensure_ascii=False)
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
        if chief_ref:
            artifact = self.store.note(chief_ref)
            self.runtime._capture(state, artifact["evidence"])
            # Keep the chief's research sources without another layer of conclusions.
            used.update(cited_ids(artifact["note"]))
        application = deepcopy(state.task.application)
        present = application_originals(application, state.evidence)
        for handle, passage in present.items():
            passage["citation"] = f"[{handle}](evidence:{handle})"
        originals = expand_captured_units(state.evidence, used)
        view = {
            "application": application,
            "expert_opinions": opinions,
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
                self.writing.load(state),
                operation,
                artifact["review_input"],
                artifact["assessment"],
            )
        inputs = []
        for message in messages:
            if message["role"] != "user":
                continue
            try:
                inputs.append(json.loads(message["content"]))
            except json.JSONDecodeError:
                inputs.append(message["content"])
        # The author may select any original actually displayed, including adjacent
        # native units that previous experts did not cite.
        readings = set(context_references(inputs, state.evidence, include_originals=True))
        return messages, sorted(readings)

    def completion_input(self, state, operation):
        return self.authoring_input(state, operation)[0]

    def estimate_input(self, state, operation):
        if operation == "submit_assessment":
            return self.completion_input(state, operation)
        artifact = (
            self._artifact(state.assessment_ref)
            if state.assessment_ref
            else {
                "review_input": self._review_input(state),
                "assessment": {
                    "overall_judgment": "Evaluation decision and principal reasons. " * 200
                },
            }
        )
        return self.writing.messages(
            self.writing.load(state),
            operation,
            artifact["review_input"],
            artifact["assessment"],
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
        return {
            "instruction": (
                "Synthesize expert opinions into a judgement; the chief writes the full report "
                "after research."
            )
        }

    async def accept_authoring(self, state, raw_ref, raw):
        if raw["operation"] == "submit_assessment":
            authored = decision_type(self.system).model_validate_json(raw["content"])
            decision = authored.model_dump()
            decision["overall_judgment"] = authored.overall_judgment.as_text()
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
                return ToolResult(
                    ok=True,
                    content={
                        "report_writing_ref": state.report_writing_ref,
                        "status": "writing",
                    },
                )
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
