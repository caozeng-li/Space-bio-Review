"""Compact navigation from native source text, never a generated scientific summary."""

import json
from collections import OrderedDict


async def select_reading_start(doc, question, model, *, material_id=None):
    """Use the existing ranking model to open relevant locations before interpretation.

    This model selects source positions only. Scientific prose is written later,
    after the normal reading window has delivered originals and original pages.
    """
    entries = [
        entry
        for entry in source_navigation(doc)
        if material_id is None or entry["material_id"] == material_id
    ]
    if len(entries) <= 2:
        return [entry["start_passage_id"] for entry in entries]
    materials = {m.material_id: m.title or m.role for m in doc.materials}
    candidates = [
        {
            "id": i,
            "material": materials.get(entry["material_id"], ""),
            "page": entry["page"],
            "headings": entry["headings"],
            "tables": entry["tables"],
            **(
                {"opening": entry["opening"]}
                if entry["page"] is None or not entry["headings"] and not entry["tables"]
                else {}
            ),
        }
        for i, entry in enumerate(entries)
    ]
    reply = await model.complete(
        [
            {
                "role": "system",
                "content": (
                    '根据问题和来源目录选择最值得首先打开的原文位置，返回 JSON {"ids":[整数ID]}。'
                    "按阅读优先级选择位置，每处会展开完整单元、本页及前页，通常1至3处足够，"
                    "复杂比较最多6处。先定位问题中的具体实验对象或设备，再选它的条件、"
                    "方法、结果或使用说明，优先形成同一对象的连贯阅读范围。"
                    "跨对象扩展用于问题明确需要的比较关系。目录中的 opening 是定位摘录。"
                    "选择决定答案的内容页，优先于封面、目录和泛泛介绍。这里只选位置，正文阅读稍后进行。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": question,
                        "source": doc.candidate.title,
                        "navigation": candidates,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
    )
    ids = json.loads(reply.message.get("content") or "")["ids"]
    if not ids or any(type(i) is not int or not 0 <= i < len(entries) for i in ids):
        raise ValueError("invalid_reading_start")
    # Ranking supplies priorities; code owns the size of the initial reading window.
    return [entries[i]["start_passage_id"] for i in list(dict.fromkeys(ids))[:6]]


def source_navigation(doc):
    """Map all pages/sections before the reader chooses an object or search terms.

    Titles and table captions are navigation excerpts, not recorded readings.
    Page numbers remain scoped to their own file, including supplements.
    """
    membership = {
        pid: material.material_id for material in doc.materials for pid in material.passage_ids
    }
    entries = OrderedDict()
    for passage in doc.passages:
        material = membership.get(passage.passage_id, "")
        location = (
            passage.page if passage.page is not None else (passage.unit_id or passage.passage_id)
        )
        key = material, location
        if key not in entries:
            entries[key] = {
                "material_id": material,
                "page": passage.page,
                "start_passage_id": passage.passage_id,
                "end_passage_id": passage.passage_id,
                "headings": [],
                "tables": [],
                "opening": passage.text[:160],
            }
        entry = entries[key]
        entry["end_passage_id"] = passage.passage_id
        for title in passage.heading_path or [passage.section]:
            if title and title not in entry["headings"]:
                entry["headings"].append(title)
        if passage.kind == "table":
            caption = passage.text.split("\n", 1)[0]
            if not caption.startswith("|") and caption not in entry["tables"]:
                entry["tables"].append(caption)
    return list(entries.values())
