"""Code-owned author-local citation choices, backed by permanent evidence identities.

Evidence is append-only within a session, so its insertion order gives stable short
choices without a second registry. Each manuscript persists its exact lookup table.
Cross-author handoffs use compiled permanent identities, never another author's numbers.
"""

import json
import re

_CITATION = re.compile(
    r"\[[^\]\n]*\]\(evidence:(evidence-[0-9a-f]{16})\)"
    r"|\[(evidence-[0-9a-f]{16})\]"
)


def citation_refs(readings, selected=None):
    return {
        f"证据{index}": handle for index, handle in enumerate(readings, 1)
        if selected is None or handle in selected
    }


def author_messages(messages, readings):
    labels = {handle: f"[{label}]" for label, handle in citation_refs(readings).items()}

    def text(value):
        return _CITATION.sub(
            lambda match: labels.get(match[1] or match[2], match[0]), value,
        )

    def content(value):
        if isinstance(value, dict):
            return {key: content(item) for key, item in value.items()}
        if isinstance(value, list):
            return [content(item) for item in value]
        if isinstance(value, str):
            # Tool results and arguments are serialized JSON inside messages.
            # Project their prose leaves without changing identity keys/lookup values.
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError):
                return text(value)
            if isinstance(parsed, (dict, list)):
                return json.dumps(content(parsed), ensure_ascii=False)
            return text(value)
        return value

    return content(messages)
