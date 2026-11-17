"""Shared review material -> section drafts -> one complete authored report."""

from __future__ import annotations

import json

from spacebio_review.agents.diagnostics import digest
from spacebio_review.authored_markdown import compile_blocks, plain_text
from spacebio_review.persistence import write_json
from spacebio_review.review.instructions import ENGLISH_REPORT, GENERAL_REVIEW

VOICE = """
You are the lead reviewer writing a coherent, evidence-based scientific review for a
committee. The complete proposal, expert opinions, decisive original passages and the chief's
decision form the shared background for every writing step. Preserve the meaning and strength
of the adopted judgement. The chief has already written the overall assessment directly from
the proposal and expert evidence in decision.overall_judgment. Develop each criterion's own
argument. Explain a shared finding fully where it is most relevant and describe its distinct
implications in the other sections. Retain concrete findings, study objects, comparisons and
conditions. Organize expert analysis into professional judgements and supporting arguments.
Keep research logs and internal questions in the working records. Select the short markers
supplied in the original passages' citation fields and place them after the supported
statements. Preserve the markers when moving an argument. Code maintains permanent reference
identities, assigns final reference numbers and renders the bibliography, ratings and score
tables. Write ordinary Markdown prose.
"""

WRITE = """
Write only the body for current_criterion, directly in Markdown. Use the complete proposal and
expert evidence to explain the criterion judgement in decision. Previously completed sections
help coordinate detail and avoid repetition. Begin with a substantive judgement, then explain
the proposal arrangements, research findings or implementation precedents that support it and
why they matter. Aim for roughly 350-650 English words or 600-1200 Chinese characters,
depending on the required report language and the argument. Keep paragraphs focused and
readable. Use bold for a short opening judgement when helpful; keep institution and paper
names in ordinary text. Use a concise table when it clarifies a comparison, with prose
explaining its implication rather than repeating its rows. The overall assessment handles the
project introduction and overall trade-off. This section explains its own criterion. Return
the section body without a chapter title, score or bibliography.
"""

SYNTHESIZE = """
Use the shared background and criterion drafts to produce the complete, coherent report. Reuse
decision.overall_judgment as the overall assessment; it was formed directly from the primary
review inputs. Edit the remaining drafts into a unified report in this single pass:

1. Consolidate repetition. Develop a study precedent, specific finding or methodological
limitation fully in the most relevant section; retain its distinct implications elsewhere.
Keep the project introduction in the overall assessment and let the criteria proceed directly
to their judgements.

2. Improve paragraphs. Split long, multi-argument paragraphs into connected, focused
paragraphs. Use short subheadings or bold opening judgements where useful. Use tables when
they clarify a comparison, with complementary prose. Keep institution and paper names in
ordinary text.

3. Preserve the evidence. Retain concrete findings, objects, comparisons, conditions and
citations when moving or consolidating arguments. Use the shared proposal, expert opinions and
original passages to preserve the judgement's meaning and strength. Complete any relocation in
this output, without adding a new literature review, speculative mechanisms or experimental
redesign. Return the final report itself, with every chapter in report_outline in the given
order and with its exact title. Start each chapter with '## ' and its title; third-level
headings are optional. Begin with the first outlined chapter. Omit the document title, ratings
table, bibliography, code fences and editorial instructions, which are outside this authored
body.
"""


class ReportWriting:
    def __init__(self, system, store):
        self.system, self.store = system, store

    def load(self, state):
        if state.report_writing_ref:
            return json.loads(
                self.store._path("report-writing", state.report_writing_ref).read_text("utf-8")
            )
        return {"drafts": {}}

    def outline(self):
        return [
            {
                "section_id": "summary",
                "title": "Overall assessment" if self.system.language == "en" else "综合评价",
                "title_en": "Overall assessment",
            },
            *[
                {"section_id": item.id, "title": item.label, "title_en": item.label_en}
                for item in self.system.criteria
            ],
        ]

    def operations(self, work):
        return [
            *[
                "write_section:" + item.id
                for item in self.system.criteria
                if item.id not in work["drafts"]
            ],
            "synthesize_report",
        ]

    @staticmethod
    def output_tokens(operation, config):
        if operation == "synthesize_report":
            return config.max_tokens
        return min(config.max_tokens, 4096 + config.reasoning_reserve)

    def messages(self, work, operation, review_input, decision, *, estimate=False):
        # All calls receive the same original material and decision. Research-stage
        # delivery instructions no longer govern the report-writing task.
        common = {
            key: value for key, value in review_input.items() if key != "evaluation_instructions"
        }
        common["decision"] = decision
        drafts = [
            {
                "id": item.id,
                "title": item.label,
                "body": self.store.author_text(work["drafts"][item.id])["content"],
            }
            for item in self.system.criteria
            if item.id in work["drafts"]
        ]
        kind, _, criterion_id = operation.partition(":")
        if estimate:
            existing = {row["id"] for row in drafts}
            for item in self.system.criteria:
                if item.id == criterion_id:
                    break
                if item.id not in existing:
                    drafts.append(
                        {
                            "id": item.id,
                            "body": "Specific scientific arguments and their citations. " * 100,
                        }
                    )
        task = {"operation": operation, "report_outline": self.outline(), "drafts": drafts}
        if criterion_id:
            item = next(item for item in self.system.criteria if item.id == criterion_id)
            task["current_criterion"] = {
                "id": item.id,
                "title": item.label,
                "description": item.description,
            }
        return [
            {
                "role": "system",
                "content": VOICE
                + "\n"
                + self.system.language_instruction
                + ("\n" + GENERAL_REVIEW + "\n" + ENGLISH_REPORT
                   if self.system.language == "en" else ""),
            },
            {"role": "user", "content": json.dumps(common, ensure_ascii=False)},
            {"role": "user", "content": json.dumps(task, ensure_ascii=False)},
            {"role": "user", "content": SYNTHESIZE if kind == "synthesize_report" else WRITE},
        ]

    def accept(self, state, work, raw_ref, raw):
        kind, _, criterion_id = raw["operation"].partition(":")
        sections = None
        if kind == "synthesize_report":
            sections = self.sections({**work, "report": raw_ref})
            work["report"] = raw_ref
        else:
            work["drafts"][criterion_id] = raw_ref
        ref = "writing-" + digest(work)
        write_json(self.store._path("report-writing", ref), work)
        state.report_writing_ref = ref
        return sections

    def sections(self, work):
        """Read chapter boundaries from the one authored text; never merge draft prose."""
        ref = work["report"]
        raw = self.store.author_text(ref)
        blocks = compile_blocks(
            raw["content"],
            {"author_text_ref": ref},
            citation_refs=raw.get("citation_refs"),
        )
        sections = []
        for node in blocks:
            if node.kind == "heading" and node.attrs.get("level") == 2:
                sections.append(
                    {
                        "title": plain_text(node.children).strip(),
                        "body": {"blocks": []},
                    }
                )
            elif sections:
                sections[-1]["body"]["blocks"].append(node.model_dump(mode="json"))
            else:
                raise ValueError("report_must_start_with_chapter")
        outline = self.outline()
        if [section["title"] for section in sections] != [item["title"] for item in outline] or any(
            not section["body"]["blocks"] for section in sections
        ):
            raise ValueError("report_chapters_do_not_match_outline")
        return [{**item, "body": section["body"]} for item, section in zip(outline, sections)]
