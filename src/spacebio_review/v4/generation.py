"""Typed prose and citation choices; storage and display are program-owned."""

from copy import deepcopy

from spacebio_review.prose import narrative_type as narrative_type
from spacebio_review.prose import render_parts as render_parts


def statement_parts(value):
    parts = deepcopy(value["parts"])
    if value.get("origin") == "delegated":
        parts.insert(0, {"text": "委派资料：", "cites": []})
    if value.get("support"):
        parts.append({"text": "\n依据作用：" + value["support"], "cites": []})
    if value.get("scope"):
        parts.extend([{"text": "\n适用范围与限制：", "cites": []}, *deepcopy(value["scope"])])
    if value.get("premise_ids"):
        parts.append({"text": "\n推理前提：", "cites": list(value["premise_ids"])})
    return parts


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
