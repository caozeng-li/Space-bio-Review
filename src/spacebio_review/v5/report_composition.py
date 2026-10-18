"""Freeze authored manuscripts once; selection and publication share those exact inputs."""

from __future__ import annotations

from copy import deepcopy

from spacebio_review.models.provider import ModelError

from .records import ReportSelection


class ReportComposition:
    def __init__(self, chief, chief_id):
        self.chief, self.chief_id = chief, chief_id
        self.runtime, self.store = chief.runtime, chief.runtime.store

    def _owned(self):
        return self.chief._manifest(self.chief_id)["topic_ids"]

    def _resolve(self, inquiry_id, contribution_ref):
        if inquiry_id not in self._owned():
            raise ValueError("report_inquiry_not_owned_by_chief")
        if contribution_ref not in self.chief.topics.contribution_refs(inquiry_id):
            raise ValueError("report_contribution_not_available")
        artifact = self.store.contribution(inquiry_id, contribution_ref)
        raw_ref = artifact.get("author_text_ref")
        return {
            "inquiry_id": inquiry_id,
            "contribution_ref": contribution_ref,
            "note": artifact["note"],
            "evidence": artifact["evidence"],
            "focus": artifact.get("focus"),
            "current_focus": self.store.inquiry(inquiry_id).current_focus,
            "author_session_id": artifact["author_session_id"],
            "author_text_ref": raw_ref,
            "paragraph_ids": artifact["paragraph_ids"],
            "author_paragraph_ids": [
                row["id"] for row in self.store.author_text(raw_ref)["paragraphs"]
            ] if raw_ref else [],
        }

    def catalogue(self):
        return [
            {
                "inquiry_id": inquiry_id,
                "contribution_ref": ref,
                "title": note["note"]["title"],
                "focus": note.get("focus"),
                "current_focus": self.store.inquiry(inquiry_id).current_focus,
            }
            for inquiry_id in self._owned()
            for ref in self.chief.topics.contribution_refs(inquiry_id)
            for note in [self.store.contribution(inquiry_id, ref)]
        ]

    def candidates(self, state):
        """Budget previews read current versions without freezing research prematurely."""
        if state.report_candidates:
            return deepcopy(state.report_candidates)
        rows = [
            self._resolve(inquiry_id, refs[0])
            for inquiry_id in self._owned()
            if (refs := self.chief.topics.contribution_refs(inquiry_id))
        ]
        return [dict(row, candidate_id=f"S{index + 1}") for index, row in enumerate(rows)]

    def prepare(self, state, selection=None):
        if selection is not None:
            rows = [
                self._resolve(row.inquiry_id, row.contribution_ref)
                for row in [*selection.main_sections, *selection.appendix_sections]
            ]
            state.report_selection = selection
        else:
            rows = self.candidates(state)
        if not rows:
            raise ModelError("scientific_contribution_required", category="exploration",
                             request_sent=False)
        state.report_candidates = deepcopy(rows)
        for row in rows:
            self.runtime._capture(state, row["evidence"])
        self.store.save(state)

    def choose(self, state, mapping):
        """Resolve only the chief's explicit short IDs against the frozen manuscripts."""
        if state.report_selection is not None:
            return
        candidates = {row["candidate_id"]: row for row in state.report_candidates}
        selected = {}
        for field in ("main_sections", "appendix_sections"):
            selected[field] = []
            for candidate_id in getattr(mapping, field):
                row = candidates[candidate_id]
                selected[field].append({key: row[key] for key in (
                    "inquiry_id", "contribution_ref"
                )})
        state.report_selection = ReportSelection.model_validate(selected)
        self.store.save(state)

    def selected(self, state):
        rows = {
            (row["inquiry_id"], row["contribution_ref"]): row
            for row in state.report_candidates
        }
        return [
            {
                key: deepcopy(value)
                for key, value in rows[(item.inquiry_id, item.contribution_ref)].items()
                if key != "candidate_id"
            }
            for item in [*state.report_selection.main_sections,
                         *state.report_selection.appendix_sections]
        ]
