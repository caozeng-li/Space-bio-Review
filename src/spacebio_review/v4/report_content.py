"""Read adapters for legacy marker text and references without sentence-level positions."""

import re

from .generation import render_parts

MARKER = re.compile(r"(?<!\\)\[@([^]]+)\]")


def block_text(block, parts=None):
    if parts is not None:
        return render_parts(parts)
    text = block["text"]
    refs = block.get("evidence_refs", []) + block.get("result_refs", [])
    if refs and not MARKER.search(text):
        text += "\n\n该论述所列来源与关联论证：" + "".join("[@" + r + "]" for r in refs)
    return text
