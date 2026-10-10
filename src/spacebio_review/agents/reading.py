"""Intent-based, task-local reading of whole passages and structured entries."""

import hashlib
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PassageSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    passage_ids: list[str] = Field(min_length=1)


class BrowseSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    section: str | None = None
    continuation: str | None = None

    @model_validator(mode="after")
    def one_selection(self):
        if self.continuation and self.section is not None:
            raise ValueError("continuation_already_binds_selection")
        return self


class ReadPassages(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reads: list[PassageSelection] = Field(min_length=1, max_length=12)


class BrowseMaterials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reads: list[BrowseSelection] = Field(min_length=1, max_length=12)


class ReadingWindow:
    def __init__(self, budget=8000):
        self.budget = budget
        self.cursors = {}

    def read(self, selection, *, passages=(), data=None, budget=None):
        from .context import TokenEstimator

        limit = self.budget if budget is None else max(0, budget)
        source = selection.source_id
        rows = [p.model_dump() if hasattr(p, "model_dump") else p for p in passages]
        kind = "passages" if rows else "entries"
        if not rows:
            # A judgment/dictionary is indivisible; top-level collections are navigable.
            fields = data.items() if isinstance(data, dict) else [("data", data)]
            rows = [
                {"entry_id": f"{key}/{i}", "field": key, "value": value}
                for key, values in fields
                for i, value in enumerate(values if isinstance(values, list) else [values])
            ]
        id_key = "passage_id" if kind == "passages" else "entry_id"
        fingerprint = hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        mapping = {row[id_key]: row for row in rows}
        cursor = getattr(selection, "continuation", None)
        if cursor:
            saved = self.cursors.get(cursor)
            if saved is None or saved[:2] != (source, fingerprint):
                return {"source_id": source, "error": "invalid_or_stale_continuation"}
            wanted = saved[2]
        elif isinstance(selection, PassageSelection):
            wanted = list(dict.fromkeys(selection.passage_ids))
        else:
            wanted = [
                r[id_key]
                for r in rows
                if selection.section is None
                or r.get("section", r.get("field")) == selection.section
            ]
        missing = [key for key in wanted if key not in mapping]
        returned, pending, oversized = [], [], []
        used = 0
        estimator = TokenEstimator()
        for key in wanted:
            if key not in mapping:
                continue
            cost = estimator.measure([{"role": "tool", "content": mapping[key]}])["input_tokens"]
            if cost > self.budget:
                oversized.append(key)
            elif used + cost <= limit:
                returned.append(mapping[key])
                used += cost
            else:
                pending.append(key)
        token = None
        if pending:
            token = uuid4().hex
            self.cursors[token] = (source, fingerprint, pending)
        return {
            "source_id": source,
            kind: returned,
            "requested_ids": wanted,
            "returned_ids": [r[id_key] for r in returned],
            "pending_ids": pending,
            "missing_ids": missing,
            "oversized_ids": oversized,
            "continuation": token,
            "complete": not (pending or missing or oversized),
            "input_tokens_estimate": used,
        }
