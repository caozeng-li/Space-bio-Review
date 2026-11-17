from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator


class ReferenceNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    cites: list[str]


class WorkingStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement_id: str
    kind: Literal["application", "external", "interpretation", "recommendation", "unknown"]
    parts: list[ReferenceNote]
    scope: list[ReferenceNote] = Field(default_factory=list)
    based_on: list[str] = Field(default_factory=list)
    premise_ids: list[str] = Field(default_factory=list)
    supersedes: list[str] = Field(default_factory=list)
    origin: str = "professional"
    support: str = ""
    quantity: dict | None = None

    @property
    def text(self):
        return "".join(p.text for p in self.parts)


class WorkingMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_claims: list[str | ReferenceNote | WorkingStatement] = Field(default_factory=list)
    counterevidence: list[str | ReferenceNote | WorkingStatement] = Field(default_factory=list)
    revisions: list[str | ReferenceNote | WorkingStatement] = Field(default_factory=list)
    unresolved: list[str | ReferenceNote | WorkingStatement] = Field(default_factory=list)
    next_steps: list[str | ReferenceNote | WorkingStatement] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    active_refs: list[str] = Field(
        default_factory=list, description=(
            'Current judgments, counter-evidence, and conditions requiring E/R references; '
            'remaining materials can still be re-read.'
        )
    )


class ClosingBudget(BaseModel):
    reserve_actions: int = Field(default=0, ge=0)
    reserve_model_turns: int = Field(default=0, ge=0)

    def check_limits(self, actions, turns):
        if self.reserve_actions >= actions or self.reserve_model_turns >= turns:
            raise ValueError("closing reserves must be smaller than total task budgets")
        if bool(self.reserve_actions) != bool(self.reserve_model_turns):
            raise ValueError("configure both closing reserves together")


class AgentTask(ClosingBudget):
    task_id: str
    objective: str
    phase: str
    expected_product: str
    max_actions: int = Field(default=20, ge=1)
    max_model_turns: int = Field(default=40, ge=1)
    max_truncation_recoveries: int = Field(default=0, ge=0, le=1)
    max_repairs: int = Field(default=2, ge=0)
    context_soft_tokens: int = Field(default=64000, ge=128)
    kind: str = "specialist"
    mode: Literal["research", "direct"] | None = None
    read_actions: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_budget(self):
        self.check_limits(self.max_actions, self.max_model_turns)
        return self


class RoleProfile(BaseModel):
    agent_id: str
    responsibility: str
    model: str = "primary"
    required_skills: list[str] = Field(default_factory=list)
    available_skills: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)


class MaterialPassage(BaseModel):
    passage_id: str
    section: str = ""
    text: str


class Material(BaseModel):
    source_id: str
    text: str = ""
    data: dict[str, Any] | list[Any] | None = None
    summary: str = ""
    conditions: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    essential: bool = False
    passages: list[MaterialPassage] = Field(default_factory=list)
    selected_passage_ids: list[str] | None = None
    kind: Literal["source", "quote", "interpretation", "assumption"] = "source"
    scope: Literal["public", "private"] = "public"
    owner: str | None = None
    phases: list[str] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)


class ContextView(BaseModel):
    agent_id: str
    phase: str
    materials: list[Material] = Field(default_factory=list)
    # Project-path/section selectors are supplied by the task, not inferred by truncation.
    sections: list[str] = Field(default_factory=list)
    passage_ids: list[str] = Field(default_factory=list)

    def visible_materials(self) -> list[Material]:
        return [
            m
            for m in self.materials
            if (not m.phases or self.phase in m.phases)
            and (m.scope == "public" or (m.scope == "private" and m.owner == self.agent_id))
        ]


class TaskResult(BaseModel):
    task_id: str
    agent_id: str
    status: Literal["completed", "limited", "failed"]
    product: dict[str, Any] | None = None
    memory: WorkingMemory = Field(default_factory=WorkingMemory)
    unfinished: list[str] = Field(default_factory=list)
    reason: str
    actions: int = 0
    model_turns: int = 0
    segments: int = 1
    model: str = ""
    partial_product: dict[str, Any] | None = None
    candidate_id: str | None = None
    candidate_path: str | None = None
    context_usage: list[dict[str, Any]] = Field(default_factory=list)
    submission_diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    citation_issues: list[dict[str, Any]] = Field(default_factory=list)
    reference_resolutions: list[dict[str, Any]] = Field(default_factory=list)
    loaded_skills: list[str] = Field(default_factory=list)
    statement_handoff: dict | None = None

    @model_serializer(mode="wrap")
    def compatible_handoff(self, handler):
        data = handler(self)
        if data["statement_handoff"] is None:
            data.pop("statement_handoff")
        return data
