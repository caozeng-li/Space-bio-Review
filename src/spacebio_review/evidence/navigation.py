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
                    (
                        "Based on the question and the source's catalog, select the most "
                        'worthwhile original text locations to open first, returning a JSON '
                        '{"ids":[integer IDs]}. Choose locations by reading priority; each will'
                        ' expand to the complete unit, this page, and the previous page. '
                        'Usually 1 to 3 locations are sufficient, up to 6 for complex '
                        'comparisons. First locate the specific experimental subjects or '
                        'equipment mentioned in the question, then select their conditions, '
                        'methods, results, or usage instructions, preferentially forming a '
                        'coherent reading range for the same subject. Expand across subjects '
                        'only for comparative relationships clearly required by the question. '
                        "The 'opening' entries in the catalog are locational excerpts. Choose "
                        'content pages that determine the answer, prioritizing them over '
                        'covers, tables of contents, and general introductions. Here, only '
                        'select locations; main text reading will occur later.'
                    )
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
