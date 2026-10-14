"""Program-owned, typed professional artifacts; model drafts contain no execution state."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_serializer

from spacebio_review.review.project import SourceRef

from .contracts import Block, Followup


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    reference: SourceRef
    title: str
    kind: str
    locator: str
    quote: str
    material_version: str
    reading_scope: str


class BoundBlock(Block):
    block_id: str


class ResearchArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    scope_note: str
    scope_links: list[str] = Field(default_factory=list)
    blocks: list[BoundBlock] = Field(min_length=1)
    evidence: dict[str, SourceRecord]
    followups: list[Followup]
    author: str
    task_id: str
    input_version: str
    reading: dict[str, JsonValue]
    input_refs: list[str] = Field(default_factory=list)
    citation_content: dict[str, list[dict]] | None = None
    statement_content: dict[str, dict] | None = None

    @model_serializer(mode="wrap")
    def compatible_archive(self, handler):
        data = handler(self)
        if data["citation_content"] is None:
            data.pop("citation_content")
        if data["statement_content"] is None:
            data.pop("statement_content")
        return data


class ScopeOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member_id: str
    status: Literal["analyzed", "partial", "blocked"]
    explanation: str
    block_ids: list[str]


class ProfessionalArtifact(ResearchArtifact):
    scope_outcomes: list[ScopeOutcome]
