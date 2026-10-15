"""Publication tree and Markdown adapter. Text and reference targets are independent."""

from typing import Literal

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.review.evaluation import GENERAL, EvaluationSystem

from .assessment_input import MARKER

LABELS = {item.id: item.label for item in GENERAL.criteria}
EN_LABELS = {item.id: item.label_en for item in GENERAL.criteria}


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
    format_version: Literal["publication-2", "publication-3"] = "publication-3"
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


def _citation_rule(state, silent):
    match = MARKER.match(state.src, state.pos)
    if not match:
        return False
    if not silent:
        token = state.push("citation", "", 0)
        token.content = match[1]
    state.pos = match.end()
    return True


PARSER = MarkdownIt("commonmark").enable("table").enable("strikethrough")
PARSER.inline.ruler.before("link", "publication_citation", _citation_rule)


def parse_text(text):
    def convert(tokens):
        root, stack = [], []
        current = root
        for token in tokens:
            if token.nesting == -1:
                current = stack.pop()
            elif token.type == "inline":
                current.extend(convert(token.children or []))
            else:
                kind = token.type.removesuffix("_open")
                attrs = dict(token.attrs or {})
                if kind == "heading":
                    attrs["level"] = int(token.tag[1:])
                if kind in {"fence", "code_block"}:
                    attrs["info"] = token.info
                node = Node(
                    kind=kind,
                    text=token.content if kind != "citation" else "",
                    target=token.content if kind == "citation" else "",
                    attrs=attrs,
                    children=convert(token.children or []) if token.type == "image" else [],
                )
                current.append(node)
                if token.nesting == 1:
                    stack.append(current)
                    current = node.children
        return root

    return convert(PARSER.parse(text))


def walk_nodes(nodes):
    for node in nodes:
        yield node
        yield from walk_nodes(node.children)


def walk_sections(sections):
    for section in sections:
        yield section
        yield from walk_sections(section.sections)


def text_slots(section):
    """Local slots are rebound to the current tree, never persisted as citation identities."""
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
