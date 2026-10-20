"""Public working state and immutable research notes, without private model reasoning."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.agents.diagnostics import digest
from spacebio_review.persistence import write_json
from spacebio_review.prose import Prose, cited_ids


class InquiryState(BaseModel):
    """One continuing scientific question; its manuscripts remain ordinary notes."""

    model_config = ConfigDict(extra="forbid")
    inquiry_id: str
    assignment: dict[str, Any]
    responsibility: str = Field(min_length=1)
    application_anchors: list[dict[str, Any]] = Field(default_factory=list)
    current_focus: str | None = None
    focus_reason: str | None = None
    current_owner_session_id: str
    current_contribution_ref: str | None = None
    budget_account_id: str


class SessionTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["chief", "researcher", "source_reader"]
    objective: str = Field(min_length=1)
    application: str | dict[str, Any]
    evaluation: str | dict = ""
    skills: list[str] = Field(default_factory=list)


class SessionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_turns: int = Field(default=24, ge=2)
    context_tokens: int = Field(default=64000, ge=256)
    completion_turns: int = Field(default=1, ge=1)


class SessionOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_turns: int | None = Field(default=None, ge=2)
    context_tokens: int | None = Field(default=None, ge=256)
    completion_turns: int | None = Field(default=None, ge=1)


class ResearchNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    body: Prose


class SessionState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    task: SessionTask
    model_alias: str
    model_snapshot: dict
    inquiry_id: str | None = None
    contribution_inquiry_id: str | None = None
    budget_owner: str | None = None
    settings: SessionSettings
    skill_bodies: dict[str, str] = Field(default_factory=dict)
    status: Literal["ready", "running", "waiting", "submitted", "partial", "failed"] = "ready"
    reason: str | None = None
    turns_used: int = 0
    revision: int = 0
    note_ref: str | None = None
    coordination_phase: Literal["planning", "synthesis"] | None = None
    authoring_phase: str | None = None
    phase_reason: str | None = None
    research_finished: bool = False
    submitted_ref: str | None = None
    assessment_ref: str | None = None
    report_writing_ref: str | None = None
    report_ref: str | None = None
    submissions: list[str] = Field(default_factory=list)
    evidence: dict[str, dict] = Field(default_factory=dict)
    observations: list[dict] = Field(default_factory=list)
    observation_index: int = 0
    followup: str | None = None
    context_compactions: int = 0


class SessionOutcome(BaseModel):
    session_id: str
    status: Literal["waiting", "submitted", "partial", "failed"]
    note_ref: str | None
    reason: str | None
    turns_used: int


class SessionStore:
    def __init__(self, root):
        self.root = Path(root) / "v5"

    def _path(self, directory, key):
        if not re.fullmatch(r"[a-z0-9-]+", key):
            raise ValueError("invalid_v5_record_id")
        return self.root / directory / (key + ".json")

    def create(self, **values):
        state = SessionState(session_id="session-" + uuid4().hex, **values)
        self.save(state)
        return state

    def save(self, state):
        write_json(self._path("sessions", state.session_id), state)

    def load(self, session_id):
        return SessionState.model_validate_json(
            self._path("sessions", session_id).read_text(encoding="utf-8")
        )

    def note(self, ref):
        return json.loads(self._path("notes", ref).read_text(encoding="utf-8"))

    def create_inquiry(
        self, assignment, *, responsibility, owner_session_id, budget_account_id,
        application_anchors=(),
    ):
        state = self.load(owner_session_id)
        inquiry = InquiryState(
            inquiry_id="inquiry-" + uuid4().hex,
            assignment=assignment,
            responsibility=responsibility,
            application_anchors=list(application_anchors),
            current_owner_session_id=owner_session_id,
            budget_account_id=budget_account_id,
        )
        self.save_inquiry(inquiry)
        state.inquiry_id = inquiry.inquiry_id
        self.save(state)
        return inquiry

    def inquiry(self, inquiry_id):
        return InquiryState.model_validate_json(
            self._path("inquiries", inquiry_id).read_text(encoding="utf-8")
        )

    def save_inquiry(self, inquiry):
        write_json(self._path("inquiries", inquiry.inquiry_id), inquiry)

    def set_focus(self, inquiry_id, focus, *, reason=None, responsibility=None):
        inquiry = self.inquiry(inquiry_id)
        inquiry.current_focus, inquiry.focus_reason = focus, reason
        if responsibility is not None:
            inquiry.responsibility = responsibility
        self.save_inquiry(inquiry)
        return inquiry

    def assign_inquiry(self, inquiry_id, session_id):
        """Change execution ownership; preserve the budget and manuscript chain."""
        inquiry, state = self.inquiry(inquiry_id), self.load(session_id)
        state.inquiry_id = inquiry_id
        self.save(state)
        inquiry.current_owner_session_id = session_id
        self.save_inquiry(inquiry)
        return inquiry

    def contribution(self, inquiry_id, contribution_ref):
        artifact = self.note(contribution_ref)
        if artifact.get("inquiry_id") != inquiry_id:
            raise ValueError("contribution_inquiry_mismatch")
        return artifact

    def save_author_text(
        self,
        state,
        content,
        operation,
        *,
        absorbed_through,
        phase=None,
        title=None,
        replaces=(),
        focus=None,
        reason=None,
        finish_reason=None,
        request_id=None,
        readings=None,
    ):
        from spacebio_review.authored_markdown import numbered_blocks

        artifact = {
            "session_id": state.session_id,
            "model_alias": state.model_alias,
            "model": state.model_snapshot,
            "operation": operation,
            "content": content,
            "readings": list(state.evidence if readings is None else readings),
            "absorbed_through": absorbed_through,
            "note_revision": state.revision + 1,
            "focus": focus,
        }
        if request_id is not None:
            artifact["request_id"] = request_id
        if finish_reason is not None:
            artifact["finish_reason"] = finish_reason
        artifact["paragraphs"] = numbered_blocks(content)
        if state.inquiry_id:
            inquiry = self.inquiry(state.inquiry_id)
            previous = inquiry.current_contribution_ref
            artifact.update(
                inquiry_id=inquiry.inquiry_id,
                focus=focus if focus is not None else inquiry.current_focus,
                focus_reason=reason if reason is not None else inquiry.focus_reason,
                previous_ref=previous,
                note_revision=self.note(previous)["revision"] + 1 if previous else 1,
            )
        if phase:
            artifact["phase"] = phase
        if title is not None:
            artifact["title"] = title
            artifact["replaces"] = list(replaces)
        ref = "author-" + digest(artifact)
        write_json(self._path("author-texts", ref), artifact)
        return ref

    def author_text(self, ref):
        return {
            **json.loads(self._path("author-texts", ref).read_text(encoding="utf-8")),
            "author_text_ref": ref,
        }

    def save_note(self, state, note, *, absorbed_through, author_text_ref=None):
        raw = self.author_text(author_text_ref) if author_text_ref else None
        value = note.model_dump(mode="json")
        refs = set(cited_ids(value))
        inquiry = self.inquiry(state.inquiry_id) if state.inquiry_id else None
        previous = None
        if inquiry:
            previous = raw["previous_ref"] if raw else inquiry.current_contribution_ref
        revision = (
            raw["note_revision"]
            if raw
            else self.note(previous)["revision"] + 1
            if previous
            else state.revision + 1
        )
        artifact = {
            "session_id": state.session_id,
            "revision": revision,
            "model": raw["model"] if raw else state.model_snapshot,
            "note": value,
            "paragraph_ids": [
                f"{state.session_id}-v{revision}-p{i + 1}"
                for i in range(len(value["body"]["blocks"]))
            ],
            "evidence": {key: state.evidence[key] for key in sorted(refs)},
        }
        if author_text_ref:
            artifact["author_text_ref"] = author_text_ref
        if inquiry:
            artifact.update(
                inquiry_id=inquiry.inquiry_id,
                focus=raw["focus"] if raw else inquiry.current_focus,
                focus_reason=raw["focus_reason"] if raw else inquiry.focus_reason,
                author_session_id=raw["session_id"] if raw else state.session_id,
                previous_ref=previous,
            )
        ref = "note-" + digest(artifact)
        write_json(self._path("notes", ref), artifact)
        if inquiry:
            inquiry.current_focus = artifact["focus"]
            inquiry.focus_reason = artifact["focus_reason"]
            inquiry.current_contribution_ref = ref
            self.save_inquiry(inquiry)
        state.revision, state.note_ref = revision, ref
        # A tool result created in this same response has not yet been read by the model.
        state.observations = [row for row in state.observations if row["index"] > absorbed_through]
        self.save(state)
        return ref

    def save_reply(self, state, raw, note):
        value = note.model_dump(mode="json")
        artifact = {"request_id": raw["request_id"], "session_id": state.session_id,
                    "inquiry_id": state.inquiry_id, "reply": value,
                    "author_text_ref": raw["author_text_ref"],
                    "evidence": {key: state.evidence[key] for key in set(cited_ids(value))}}
        ref = "reply-" + digest(artifact)
        write_json(self._path("replies", ref), artifact)
        state.observations = [row for row in state.observations
                              if row["index"] > raw["absorbed_through"]]
        state.authoring_phase, state.phase_reason = None, None
        self.save(state)
        return ref

    def reply(self, ref):
        return json.loads(self._path("replies", ref).read_text("utf-8"))
