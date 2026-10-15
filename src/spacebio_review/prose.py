"""Shared cited prose: models write content, code binds immutable source identities."""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, create_model


class CitedPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, description="完整论述及其条件。")
    cites: list[str]


class Paragraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parts: list[CitedPart] = Field(min_length=1)


class Prose(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paragraphs: list[Paragraph] = Field(min_length=1)


def narrative_type(choices):
    from typing import Literal

    refs = (list[Literal[tuple(choices)]], ...) if choices else (list[str], Field(max_length=0))
    part = create_model("CitedPart", __base__=CitedPart, cites=refs)
    return create_model("Narrative", __base__=Paragraph, parts=(list[part], Field(min_length=1)))


def prose_type(choices):
    return create_model(
        "CitedProse",
        __base__=Prose,
        paragraphs=(list[narrative_type(choices)], Field(min_length=1)),
    )


def prose_input_type(choices):
    """Tool authors supply paragraphs of citation spans, without storage wrappers."""
    parts = narrative_type(choices).model_fields["parts"].annotation
    return Annotated[
        list[Annotated[parts, Field(min_length=1)]],
        Field(min_length=1, description="正文为二维数组：外层分段，段内按顺序写 text/cites 片段。"),
    ]


def assemble_prose(paragraphs):
    """Assemble already validated input, preserving authored text and citation positions."""
    return {"paragraphs": [{"parts": parts} for parts in paragraphs]}


def prose_inputs(value):
    """Show stored prose in the same shallow shape used by authoring tools."""
    if isinstance(value, dict):
        if set(value) == {"paragraphs"}:
            return [paragraph["parts"] for paragraph in value["paragraphs"]]
        return {key: prose_inputs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [prose_inputs(item) for item in value]
    return value


def render_parts(parts, aliases=None):
    aliases = aliases or {}
    return "".join(
        re.sub(r"(?<!\\)\[@", r"\\[@", p["text"])
        + "".join("[@" + aliases.get(k, k) + "]" for k in dict.fromkeys(p["cites"]))
        for p in parts
    )


def prose_parts(prose):
    parts = []
    for paragraph in prose["paragraphs"]:
        if parts:
            parts.append({"text": "\n\n", "cites": []})
        parts.extend(paragraph["parts"])
    return parts


def cited_ids(value):
    if isinstance(value, dict):
        if set(value) == {"text", "cites"}:
            yield from value["cites"]
        else:
            for child in value.values():
                yield from cited_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from cited_ids(child)


def bind_prose(prose, identities):
    return {
        "paragraphs": [
            {
                "parts": [
                    {
                        "text": part["text"],
                        "cites": list(dict.fromkeys(identities[k] for k in part["cites"])),
                    }
                    for part in paragraph["parts"]
                ]
            }
            for paragraph in prose["paragraphs"]
        ]
    }
