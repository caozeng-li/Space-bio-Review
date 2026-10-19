"""One compiled Markdown body for research, collaboration and publication."""

from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field

from .authored_markdown import MarkdownRenderer, Node, compile_blocks


class Prose(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocks: list[Node] = Field(min_length=1)


def compile_authored_body(markdown, origin=None):
    """Compile Markdown once and bind evidence-link targets to stable identities."""
    return Prose(blocks=compile_blocks(markdown, origin)).model_dump(mode="json")


def select_authored_body(body, paragraph_ids):
    """Select complete original structures without reparsing or changing their IDs."""
    value = body.model_dump(mode="json") if isinstance(body, Prose) else body
    selected = set(paragraph_ids)
    existing = {node["origin"]["paragraph_id"] for node in value["blocks"]}
    if unknown := selected - existing:
        raise ValueError("unknown_author_paragraph:" + ",".join(sorted(unknown)))
    return {
        "blocks": [
            deepcopy(node) for node in value["blocks"] if node["origin"]["paragraph_id"] in selected
        ]
    }


def render_authored_body(prose):
    """Render a manuscript with its permanent evidence identities."""
    value = prose.model_dump(mode="json") if isinstance(prose, Prose) else prose
    return MarkdownRenderer().blocks([Node.model_validate(row) for row in value["blocks"]])


def prose_inputs(value):
    """Render body leaves while retaining the surrounding business structure."""
    if isinstance(value, Prose):
        return render_authored_body(value)
    if isinstance(value, dict):
        if set(value) == {"blocks"}:
            return render_authored_body(value)
        return {key: prose_inputs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [prose_inputs(item) for item in value]
    return value


def cited_ids(value):
    """Walk already bound citation nodes in bodies or surrounding records."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        if value.get("kind") == "citation":
            yield value["target"]
        for child in value.values():
            yield from cited_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from cited_ids(child)
