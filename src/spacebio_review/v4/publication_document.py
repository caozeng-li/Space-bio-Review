"""Publication tree and Markdown adapter. Text and reference targets are independent."""

from typing import Literal

from markdown_it import MarkdownIt
from pydantic import Field

from .publication_input import Coverage, PublicValue
from .report_content import MARKER


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
    kind: Literal["main", "topic", "analysis", "supplement"] = "main"
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


class Target(PublicValue):
    document: Literal["main", "appendix"]
    section: str
    anchor: str
    paragraph: int = 0


class PublicationDocument(PublicValue):
    format_version: str = "publication-1"
    main: list[Section]
    appendix: list[Section]
    sources: dict[str, Source] = Field(default_factory=dict)
    locations: dict[str, Location] = Field(default_factory=dict)
    targets: dict[str, Target] = Field(default_factory=dict)
    numbering: dict[str, int] = Field(default_factory=dict)
    recommendation: str
    coverage: dict[str, Coverage]


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


def summary_input(document):
    limits = {}
    for aspect, item in document.coverage.items():
        for text in item.analysis_limits:
            limits.setdefault(text, []).append(aspect)
    return {
        "recommendation": document.recommendation,
        "actual_coverage": {
            key: value.model_dump(exclude={"analysis_limits"})
            for key, value in document.coverage.items()
        },
        "analysis_limits": [{"aspects": aspects, "text": text} for text, aspects in limits.items()],
        "sections": [
            {"title": section.title, "text": plain_text(section.blocks).strip()}
            for section in document.main
            if section.section_id in {"overall", "conditions", "gaps"}
        ],
    }
