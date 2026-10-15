"""Translate public text slots; citations, ratings and layout stay code-owned."""

import json
import re
from pathlib import Path

from pydantic import ConfigDict, create_model

from spacebio_review.agents.diagnostics import digest
from spacebio_review.agents.serialization import parse_output
from spacebio_review.persistence import write_json

from .document import Section, text_slots

PROMPT = (
    "将同一小节的有序文字单元翻译为英文，保留条件、否定、数字、判断强度及各单元语义归属。"
    "units 每个键返回原单元的译文，保持同段语境；title 仅提供小节语境。"
    "程序负责引用、评分、序号和链接，译文只提交对应文字。"
    "原始数值和单位倍率保持不变：只翻译单位名称，不换算数量级。"
    "例如金额以万元计时可译为 ten-thousand yuan，0 万元仍是 0 ten-thousand yuan。"
    "单元边缘标点由程序保留，译文只包含文字主体。"
)


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


async def translate_section(model, section, *, batch_chars=18000, cache_dir=None):
    section = section.model_copy(deep=True)
    for blocks in text_batches(section, batch_chars):
        chunk = Section(section_id="chunk", title=section.title, blocks=blocks)
        slots, nodes = text_slots(chunk)
        units, targets, edges = {}, {}, {}
        for i, (key, value) in enumerate(slots.items()):
            if key == "title":
                continue
            body, prefix, suffix = translation_fragment(value)
            if body:
                local = f"U{i}"
                units[local], targets[local], edges[local] = body, nodes[key], (prefix, suffix)
            else:
                nodes[key].text = prefix + suffix
        if not units:
            continue
        fields = create_model(
            "PublicationTextUnits",
            __config__=ConfigDict(extra="forbid"),
            **{key: (str, ...) for key in units},
        )
        draft = create_model(
            "PublicationTextDraft", __config__=ConfigDict(extra="forbid"), units=(fields, ...)
        )
        data = {"title": section.title, "units": units}
        key = digest(
            {"model": model.config.model_dump(mode="json"), "prompt": PROMPT, "data": data}
        )
        path = Path(cache_dir) / (key + ".json") if cache_dir else None
        if path and path.exists():
            value = draft.model_validate_json(path.read_text("utf-8")).model_dump()
        else:
            schema = draft.model_json_schema()
            reply = await model.complete(
                [
                    {
                        "role": "system",
                        "content": PROMPT + "\n只返回以下结构的 JSON：" + json.dumps(schema),
                    },
                    {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
                ],
                response_schema=schema,
            )
            if reply.finish_reason != "stop":
                raise ValueError("translation_response_" + reply.finish_reason)
            value = draft.model_validate(
                parse_output(
                    reply.message.get("content") or "",
                    "yaml" if model.config.structured_output == "yaml" else "json",
                )
            ).model_dump()
            if path:
                write_json(path, value)
        for key, node in targets.items():
            prefix, suffix = edges[key]
            node.text = prefix + translation_fragment(value["units"][key].strip())[0] + suffix
    # Every public main section has a code-owned bilingual title.
    section.title = section.title_en or section.title
    return section
