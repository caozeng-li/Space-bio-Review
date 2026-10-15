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
from spacebio_review.prose import Prose, bind_prose, cited_ids


class SessionTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["chief", "researcher"]
    objective: str = Field(min_length=1)
    application: str | dict[str, Any]
    evaluation: str = ""
    skills: list[str] = Field(default_factory=list)


class SessionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_turns: int = Field(default=24, ge=2)
    context_tokens: int = Field(default=64000, ge=256)
    completion_turns: int = Field(default=1, ge=1)


class ResearchNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    body: Prose
    unknowns: list[str] = Field(default_factory=list)


class SessionState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["v5-session-1"] = "v5-session-1"
    session_id: str
    task: SessionTask
    model_alias: str
    model_snapshot: dict
    settings: SessionSettings
    skill_bodies: dict[str, str] = Field(default_factory=dict)
    status: Literal["ready", "running", "submitted", "partial", "failed"] = "ready"
    reason: str | None = None
    turns_used: int = 0
    revision: int = 0
    note_ref: str | None = None
    submitted_ref: str | None = None
    assessment_ref: str | None = None
    report_ref: str | None = None
    submissions: list[str] = Field(default_factory=list)
    evidence: dict[str, dict] = Field(default_factory=dict)
    aliases: dict[str, str] = Field(default_factory=dict)
    observations: list[dict] = Field(default_factory=list)
    observation_index: int = 0
    followup: str | None = None
    context_compactions: int = 0
    pending_delivery: dict[str, Any] | None = None


class SessionOutcome(BaseModel):
    session_id: str
    status: Literal["submitted", "partial", "failed"]
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

    def save_author_text(self, state, content, operation, *, absorbed_through):
        from .authoring import numbered_text

        artifact = {
            "schema_version": "v5-author-text-1",
            "session_id": state.session_id,
            "model_alias": state.model_alias,
            "model": state.model_snapshot,
            "operation": operation,
            "content": content,
            "paragraphs": numbered_text(content),
            "aliases": dict(state.aliases),
            "absorbed_through": absorbed_through,
            "note_revision": state.revision + 1,
        }
        ref = "author-" + digest(artifact)
        write_json(self._path("author-texts", ref), artifact)
        return ref

    def author_text(self, ref):
        return json.loads(self._path("author-texts", ref).read_text(encoding="utf-8"))

    def save_delivery_parse(self, raw_ref, model_snapshot, mapping):
        artifact = {
            "schema_version": "v5-delivery-parse-1",
            "author_text_ref": raw_ref,
            "model": model_snapshot,
            "mapping": mapping.model_dump(mode="json")
            if isinstance(mapping, BaseModel)
            else mapping,
        }
        ref = "parse-" + digest(artifact)
        write_json(self._path("delivery-parses", ref), artifact)
        return ref

    def delivery_parse(self, ref):
        return json.loads(self._path("delivery-parses", ref).read_text(encoding="utf-8"))

    def save_note(self, state, note, *, absorbed_through, author_text_ref=None, aliases=None):
        raw = self.author_text(author_text_ref) if author_text_ref else None
        if raw and state.note_ref:
            current = self.note(state.note_ref)
            if current.get("author_text_ref") == author_text_ref:
                return state.note_ref
        value = note.model_dump(mode="json")
        # Only citation identity is bound here; scientific meaning belongs to the author.
        identities = aliases if aliases is not None else raw["aliases"] if raw else state.aliases
        value["body"] = bind_prose(value["body"], identities)
        refs = set(cited_ids(value))
        revision = raw["note_revision"] if raw else state.revision + 1
        artifact = {
            "schema_version": "v5-note-1",
            "session_id": state.session_id,
            "revision": revision,
            "model": raw["model"] if raw else state.model_snapshot,
            "note": value,
            "paragraph_ids": [
                f"{state.session_id}-v{revision}-p{i + 1}"
                for i in range(len(value["body"]["paragraphs"]))
            ],
            "evidence": {key: state.evidence[key] for key in sorted(refs)},
        }
        if author_text_ref:
            artifact["author_text_ref"] = author_text_ref
        ref = "note-" + digest(artifact)
        write_json(self._path("notes", ref), artifact)
        state.revision, state.note_ref = revision, ref
        # A tool result created in this same response has not yet been read by the model.
        state.observations = [row for row in state.observations if row["index"] > absorbed_through]
        self.save(state)
        return ref
