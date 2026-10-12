"""Typed prose and citation choices; storage and display are program-owned."""

import re

from pydantic import Field

from spacebio_review.evidence.submissions import record, selection


def narrative_type(choices):
    references = (list[selection(choices)], ...) if choices else (list[str], Field(max_length=0))
    part = record(
        "CitedPart",
        text=(str, Field(min_length=1, description="完整论述及其条件，不手写引用标记。")),
        cites=references,
    )
    return record("Narrative", parts=(list[part], Field(min_length=1)))


def render_parts(parts, aliases=None):
    aliases = aliases or {}
    return "".join(
        re.sub(r"(?<!\\)\[@", r"\\[@", p["text"])
        + "".join("[@" + aliases.get(k, k) + "]" for k in dict.fromkeys(p["cites"]))
        for p in parts
    )


def compile_draft(data, context, *, canonical=False):
    content = {}

    def visit(node, path=""):
        if isinstance(node, dict):
            if set(node) == {"parts"}:
                parts = [
                    {
                        "text": p["text"],
                        "cites": list(p["cites"])
                        if canonical
                        else [context.reverse[k] for k in p["cites"]],
                    }
                    for p in node["parts"]
                ]
                content[path] = parts
                return render_parts(parts)
            return {k: visit(v, path + "/" + k) for k, v in node.items()}
        if isinstance(node, list):
            return [visit(v, path + "/" + str(i)) for i, v in enumerate(node)]
        return node

    result = visit(data)
    blocks = []
    for i, text in enumerate(result["blocks"]):
        refs = list(dict.fromkeys(k for p in content[f"/blocks/{i}"] for k in p["cites"]))
        blocks.append(
            {
                "text": text,
                "evidence_refs": [k for k in refs if k in context.evidence],
                "result_refs": [k for k in refs if k in context.results],
            }
        )
    result["blocks"] = blocks
    result["scope_links"] = list(
        dict.fromkeys(k for p in content["/scope_note"] for k in p["cites"])
    )
    result["citation_content"] = content
    return result
