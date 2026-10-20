"""Shared publication tree; scientific text and reference identities stay separate."""

from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.authored_markdown import Node
from spacebio_review.authored_markdown import plain_text as plain_text
from spacebio_review.authored_markdown import walk_nodes as walk_nodes
from spacebio_review.review.evaluation import GENERAL, EvaluationSystem


class PublicValue(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Section(PublicValue):
    section_id: str
    title: str
    title_en: str = ""
    blocks: list[Node] = Field(default_factory=list)


class Source(PublicValue):
    title: str
    kind: str
    url: str = ""
    authors: list[str] = Field(default_factory=list)
    publication_date: str = ""


class Location(PublicValue):
    source: str
    locator: str = ""
    reading_scope: str = ""


class PublicationDocument(PublicValue):
    main: list[Section]
    sources: dict[str, Source] = Field(default_factory=dict)
    locations: dict[str, Location] = Field(default_factory=dict)
    numbering: dict[str, int] = Field(default_factory=dict)
    recommendation: str
    evaluation_system: EvaluationSystem = GENERAL
    grades: dict[str, dict[str, str | None]] = Field(default_factory=dict)
    scores: dict[str, int] = Field(default_factory=dict)
    total_score: int | None = None
    score_grade: str | None = None
    safety_veto: bool = False
    admission: dict = Field(default_factory=dict)
