"""Shared cited prose: models write content, code binds immutable source identities."""

import re

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
