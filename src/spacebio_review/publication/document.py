"""Shared publication tree; scientific text and reference identities stay separate."""

from typing import Literal

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
    kind: Literal["main", "scientific"] = "main"
    blocks: list[Node] = Field(default_factory=list)
    sections: list["Section"] = Field(default_factory=list)


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
    appendix: list[Section]
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


def walk_sections(sections):
    for section in sections:
        yield section
        yield from walk_sections(section.sections)


def text_slots(section):
    """Local translation slots never replace persisted reference identities."""
    slots = {"title": section.title}
    nodes = {}
    for index, node in enumerate(walk_nodes(section.blocks)):
        if node.kind == "text" and node.text.strip():
            key = f"T{index}"
            slots[key], nodes[key] = node.text, node
    return slots, nodes
