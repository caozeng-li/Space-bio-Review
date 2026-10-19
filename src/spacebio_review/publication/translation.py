"""Translate fixed text slots directly while preserving the publication tree."""

from __future__ import annotations

import json
import re
from contextlib import nullcontext
from pathlib import Path
from time import perf_counter

from pydantic import ConfigDict, Field, ValidationError, create_model

from spacebio_review.agents.diagnostics import digest
from spacebio_review.persistence import write_json

from .document import Node, Section, text_slots

PROMPT = """将本批文字单元准确翻译为英文。你只负责翻译，不重新评审或增加建议。
输入包含小节标题、各 U 单元原文及完整段落／列表项／表格语境。保留对象、条件、否定、
研究阶段、判断强度、数字和单位倍率；例如万元可译为 ten-thousand yuan，不换算数量级。
保留原文的计划／已实施状态与时态。原文未说明实验已完成时，用计划或中性表述，
不要擅自译成已经实施、已经观察到结果的过去时。
contexts 中的引用、强调、链接等位置只供理解，程序会原位保留；不要在译文中重复这些标记。
分别翻译每个单元，仍使同一句中的各单元在固定位置接起来组成完整英文。
只返回一个平面 JSON 对象，键与输入 units 完全一致，值为该单元完整英文字符串。
例如 {"U1": "Translated heading", "U2": "Complete English text"}。
所有单元均须翻译；短标题、术语也必须有自己的译文，不把它们当作可省略的结构标题。
不要添加其他键、说明、Markdown 围栏或映射数组。
输入 units 给出文字主体；单元边缘标点由程序按原文保留，译文不添加边缘标点。
"""


def _translation_type(unit_keys):
    return create_model(
        "TranslatedUnits",
        __config__=ConfigDict(extra="forbid"),
        **{key: (str, Field(min_length=1, strict=True)) for key in unit_keys},
    )


def translation_schema(unit_keys):
    """Every code-owned unit is one required string in the author's JSON object."""
    return _translation_type(unit_keys).model_json_schema()


class TranslationFailure(Exception):
    """The failed batch and its saved author response."""

    def __init__(self, details):
        self.details = details
        super().__init__(details["error"])


def text_batches(section, limit):
    def sized_blocks(block):
        chunk = Section(section_id=section.section_id, title=section.title, blocks=[block])
        values, _ = text_slots(chunk)
        size = sum(len(v) for k, v in values.items() if k != "title")
        if size > limit and block.kind in {
            "bullet_list",
            "ordered_list",
            "list_item",
            "blockquote",
        }:
            for child in block.children:
                yield from sized_blocks(child)
        else:
            yield block, size

    groups, group, size = [], [], 0
    for block, length in (pair for root in section.blocks for pair in sized_blocks(root)):
        if group and size + length > limit:
            groups.append(group)
            group, size = [], 0
        group.append(block)
        size += length
    if group:
        groups.append(group)
    return groups


def translation_fragment(text):
    """Keep source edge punctuation; a decimal point remains part of its number."""
    leading = re.match(r"[\s。，；：！？.,;:!?]*", text)[0]
    if leading.endswith(".") and text[len(leading) : len(leading) + 1].isdigit():
        leading = leading[:-1]
    rest = text[len(leading) :]
    trailing = re.search(r"[\s。，；：！？.,;:!?]*$", rest)[0]
    core = rest[: len(rest) - len(trailing)] if trailing else rest
    table = str.maketrans("。，；：！？", ".,;:!?")
    prefix, suffix = leading.translate(table), trailing.translate(table)
    if prefix and prefix[-1] in ".,;:!?":
        prefix += " "
    return core, prefix, suffix


def _contexts(blocks, labels):
    """Give the translator ordered inline positions, without sending the full AST."""

    def inline(node):
        if node.kind == "text":
            label = labels.get(id(node))
            return f"[{label}: {node.text}]" if label else node.text
        if node.kind in {"citation", "evidence", "crossref"}:
            return "".join(inline(child) for child in node.children) + f"〈固定引用:{node.target}〉"
        body = "".join(inline(child) for child in node.children)
        if node.kind in {"strong", "em", "s", "link"}:
            return f"〈{node.kind}〉{body}〈/{node.kind}〉"
        if node.kind in {"softbreak", "hardbreak"}:
            return "\n"
        return node.text + body

    rows = []

    def visit(node):
        if node.kind in {"paragraph", "heading", "th", "td"}:
            rows.append({"kind": node.kind, "text": inline(node)})
        elif node.children:
            for child in node.children:
                visit(child)
        elif node.kind == "text":
            rows.append({"kind": node.kind, "text": inline(node)})

    for block in blocks:
        visit(block)
    return rows


def _inline_spacing(blocks):
    """Join English lexical fragments across fixed inline nodes, preserving punctuation."""

    def visible(node):
        if node.kind in {"evidence", "citation", "crossref"}:
            return "[ref]"
        if node.kind in {"softbreak", "hardbreak"}:
            return "\n"
        return node.text + "".join(visible(child) for child in node.children)

    def join(nodes):
        result = []
        for node in nodes:
            if node.children:
                node.children = join(node.children)
            before = visible(result[-1])[-1:] if result else ""
            first = visible(node)[:1]
            # Keep the space outside emphasis/link markup, and let the renderer
            # own the space before a reference. Punctuation/decimals stay attached.
            if (
                before
                and first
                and not before.isspace()
                and not first.isspace()
                and node.kind not in {"evidence", "citation", "crossref"}
                and first not in ",.;:!?)]}"
                and before not in "([{"
                and not (before == "." and first.isdigit())
            ):
                if node.kind == "text":
                    node.text = " " + node.text
                elif result[-1].kind == "text":
                    result[-1].text += " "
                else:
                    result.append(Node(kind="text", text=" "))
            result.append(node)
        return result

    for node in blocks:
        if node.kind in {"paragraph", "heading", "th", "td"}:
            node.children = join(node.children)
        else:
            _inline_spacing(node.children)


def _save(path, value):
    if path:
        write_json(path, value)


def _error_fields(exc):
    cause = exc
    while cause is not None:
        if isinstance(cause, ValidationError):
            return [".".join(map(str, row["loc"])) for row in cause.errors()]
        cause = cause.__cause__
    return []


async def translate_section(
    model,
    section,
    *,
    batch_chars=18000,
    artifact_dir=None,
    prepare_requests=None,
    request_scope=None,
):
    section = section.model_copy(deep=True)
    artifacts = Path(artifact_dir) if artifact_dir else None
    title = Node(kind="text", text=section.title)
    for batch_index, blocks in enumerate(text_batches(section, batch_chars) or [[]], 1):
        chunk = Section(section_id="chunk", title=section.title, blocks=blocks)
        slots, nodes = text_slots(chunk)
        units, targets, edges = {}, {}, {}
        if batch_index == 1 and not section.title_en and title.text:
            value, prefix, suffix = translation_fragment(title.text)
            units["U1"], targets["U1"], edges["U1"] = value, title, (prefix, suffix)
        for key, value in slots.items():
            if key == "title":
                continue
            body, prefix, suffix = translation_fragment(value)
            if body:
                local = f"U{len(units) + 1}"
                units[local], targets[local], edges[local] = body, nodes[key], (prefix, suffix)
            else:
                nodes[key].text = prefix + suffix
        if not units:
            continue
        data = {
            "title": section.title,
            "units": units,
            "contexts": _contexts(blocks, {id(node): key for key, node in targets.items()}),
        }
        response_type = _translation_type(units)
        schema = response_type.model_json_schema()
        messages = [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ]
        key = digest(
            {
                "model": model.config.model_dump(mode="json"),
                "prompt": PROMPT,
                "data": data,
                "response_schema": schema,
                "section_id": section.section_id,
                "batch_index": batch_index,
            }
        )
        path = artifacts / "batches" / (key + ".json") if artifacts else None
        state = {
            "batch_id": key,
            "section_id": section.section_id,
            "batch_index": batch_index,
            "input": data,
            "response_schema": schema,
            "status": "not_authored",
        }
        stage = "author"
        _save(path, state)
        try:
            if prepare_requests:
                prepare_requests([
                    {
                        "name": "translation:author",
                        "config": model.config,
                        "messages": messages,
                        "response_schema": schema,
                    }
                ])
            started = perf_counter()
            with request_scope("translation") if request_scope else nullcontext():
                reply = await model.complete(messages, response_schema=schema)
            content = reply.message.get("content") or ""
            raw = {
                "operation": "translation",
                "batch_id": key,
                "input": data,
                "model": model.config.model_dump(mode="json"),
                "content": content,
                "message": reply.message,
                "finish_reason": reply.finish_reason,
                "usage": {
                    "input_tokens": reply.prompt_tokens,
                    "output_tokens": reply.completion_tokens,
                    "reasoning_tokens": reply.reasoning_tokens,
                },
                "elapsed_seconds": perf_counter() - started,
                "effective_request": reply.effective_request,
            }
            raw_ref = "authors/" + digest(raw) + ".json"
            _save(artifacts / raw_ref if artifacts else None, raw)
            state.update(raw_ref=raw_ref, status="authored")
            _save(path, state)
            stage = "parse"
            if raw["finish_reason"] != "stop":
                raise ValueError("translation_response_" + str(raw["finish_reason"]))
            values = response_type.model_validate_json(content).model_dump()
            state.update(status="parsed", values=values)
            _save(path, state)
            stage = "assembly"
            for local, node in targets.items():
                prefix, suffix = edges[local]
                node.text = prefix + translation_fragment(values[local])[0] + suffix
            _inline_spacing(blocks)
            state.update(
                status="assembled", assembled={key: node.text for key, node in targets.items()}
            )
            _save(path, state)
        except Exception as exc:
            details = {
                "task": "translation",
                "section": section.section_id,
                "batch": key,
                "batch_index": batch_index,
                "stage": stage,
                "raw_ref": state.get("raw_ref"),
                "error": str(exc),
                "error_type": type(exc).__name__,
                "fields": _error_fields(exc),
            }
            state["status"] = "failed"
            state["failure"] = details
            _save(path, state)
            raise TranslationFailure(details) from exc
    section.title = section.title_en or title.text
    return section
