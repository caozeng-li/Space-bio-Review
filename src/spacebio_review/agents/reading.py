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
    start_passage_id: str | None = None

    @model_validator(mode="after")
    def one_selection(self):
        if self.continuation and (self.section is not None or self.start_passage_id):
            raise ValueError("continuation_already_binds_selection")
        return self


class ReadPassages(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reads: list[PassageSelection] = Field(min_length=1, max_length=12)


class BrowseMaterials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reads: list[BrowseSelection] = Field(min_length=1, max_length=12)


class ReadingWindow:
    def __init__(self, budget=8000, *, whole_units=True):
        self.budget = budget
        self.whole_units = whole_units
        self.cursors = {}

    def read(self, selection, *, passages=(), data=None, budget=None):
        from .context import TokenEstimator

        limit = self.budget if budget is None else max(0, budget)
        source = selection.source_id
        rows = [p.model_dump() if hasattr(p, "model_dump") else p for p in passages]
        if not self.whole_units:
            rows = [{k: v for k, v in row.items() if k != "unit_id"} for row in rows]
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
            start = next(
                (i for i, row in enumerate(rows) if row[id_key] == selection.start_passage_id), 0
            )
            wanted = []
            for row in rows[start:]:
                matches = (
                    selection.section is None
                    or row.get("section", row.get("field")) == selection.section
                )
                if matches:
                    wanted.append(row[id_key])
                elif wanted and selection.section is not None:
                    break  # A repeated heading belongs to another contiguous section.
        missing = [key for key in wanted if key not in mapping]
        returned, pending = [], []
        estimator = TokenEstimator()
        token = uuid4().hex
        result = {
            "source_id": source,
            kind: returned,
            "requested_count": len(wanted),
            "returned_ids": [],
            "pending_count": 0,
            "missing_ids": missing,
            "continuation": token,
            "complete": False,
            "unit_continues": False,
            "input_tokens_estimate": 0,
        }

        def measure():
            return estimator.measure(
                [{"role": "tool", "content": json.dumps(result, ensure_ascii=False)}]
            )["input_tokens"]

        # Only an initial explicit selection expands to a complete original unit.
        # A browse position or continuation already specifies exactly what remains.
        group_ids = (
            list(mapping) if isinstance(selection, PassageSelection) and not cursor else wanted
        )
        units, seen = [], set()
        for key in wanted:
            if key not in mapping or key in seen:
                continue
            group = mapping[key].get("unit_id") if self.whole_units else None
            keys = (
                [k for k in group_ids if k in mapping and mapping[k].get("unit_id") == group]
                if group
                else [key]
            )
            units.append(keys)
            seen.update(keys)
        for i, keys in enumerate(units):
            remaining = [k for unit in units[i + 1 :] for k in unit]
            returned.extend(mapping[key] for key in keys)
            result["returned_ids"].extend(keys)
            result["pending_count"] = len(remaining)
            full_size = measure() + 16
            if full_size > limit:
                if i == 0 and limit >= self.budget and (
                    kind == "entries" or not self.whole_units
                ):
                    # Keep legacy individual reads and indivisible judgments unchanged.
                    pending = remaining
                else:
                    del returned[-len(keys) :]
                    del result["returned_ids"][-len(keys) :]
                    pending = keys + remaining
                    if (
                        i == 0
                        and kind == "passages"
                        and self.whole_units
                        and full_size > self.budget
                    ):
                        # An oversized unit continues at existing passage boundaries.
                        for j, key in enumerate(keys):
                            returned.append(mapping[key])
                            result["returned_ids"].append(key)
                            result["unit_continues"] = j + 1 < len(keys)
                            result["pending_count"] = len(keys) - j - 1 + len(remaining)
                            if measure() + 16 > limit:
                                returned.pop()
                                result["returned_ids"].pop()
                                pending = keys[j:] + remaining
                                result["unit_continues"] = bool(returned)
                                if not returned:
                                    result["unread_reason"] = "passage_exceeds_read_budget"
                                break
                            pending = keys[j + 1 :] + remaining
                break
        result["pending_count"] = len(pending)
        result["continuation"] = token if pending else None
        result["complete"] = not (pending or missing)
        result["input_tokens_estimate"] = measure() + 8
        if pending:
            self.cursors[token] = (source, fingerprint, pending)
        return result
