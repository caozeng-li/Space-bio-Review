"""Shared publication tree; scientific text and reference identities stay separate."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.review.evaluation import GENERAL, EvaluationSystem


class PublicValue(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Node(PublicValue):
    kind: str
    text: str = ""
    target: str = ""
    node_id: str = ""
    attrs: dict[str, str | int] = Field(default_factory=dict)
    children: list["Node"] = Field(default_factory=list)


class Section(PublicValue):
    section_id: str
    title: str
    title_en: str = ""
    kind: Literal["main", "topic"] = "main"
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
    format_version: Literal["v5-publication-1"] = "v5-publication-1"
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


def walk_nodes(nodes):
    for node in nodes:
        yield node
        yield from walk_nodes(node.children)


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


def plain_text(nodes):
    parts = []
    for node in nodes:
        if node.kind in {"citation", "crossref"}:
            continue
        if node.kind in {"softbreak", "hardbreak"}:
            parts.append("\n")
        else:
            parts.append(node.text + plain_text(node.children))
            if node.kind in {"paragraph", "heading", "block", "scope", "list_item"}:
                parts.append("\n\n")
    return "".join(parts)
