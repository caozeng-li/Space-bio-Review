"""Single-source reading windows over native document units."""

import hashlib
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PassageSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    passage_ids: list[str] = Field(min_length=1)
    reading_tokens: int | None = Field(default=None, gt=0)


class BrowseSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    section: str | None = None
    pages: list[int] = Field(
        default_factory=list, description="The physical PDF page number selected from the catalog."
    )
    material_id: str | None = None
    continuation: str | None = None
    start_passage_id: str | None = None
    reading_tokens: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def one_selection(self):
        if self.continuation and (
            self.section is not None or self.start_passage_id or self.pages or self.material_id
        ):
            raise ValueError("continuation_already_binds_selection")
        return self


class ReadPassages(PassageSelection):
    pass


class BrowseMaterials(BrowseSelection):
    pass


def reading_output(result):
    """The grouped display is public; fine rows remain internal for snapshots."""
    return {key: value for key, value in result.items() if key != "passages"}


class ReadingWindow:
    def __init__(self, budget=None, *, max_budget=None, capacity_tokens: int | None = None):
        from spacebio_review.evidence.research import EvidenceConfig

        defaults = EvidenceConfig()
        self.budget = defaults.reading_tokens if budget is None else budget
        self.max_budget = defaults.max_reading_tokens if max_budget is None else max_budget
        self.capacity_tokens = capacity_tokens
        self.cursors = {}

    def read(
        self,
        selection,
        *,
        passages=(),
        data=None,
        source=None,
        materials=(),
        position_key="passage_id",
    ):
        from spacebio_review.evidence.reading_render import reading_groups, render_source

        from .context import TokenEstimator

        limit = min(getattr(selection, "reading_tokens", None) or self.budget, self.max_budget)
        if self.capacity_tokens is not None:
            limit = min(limit, self.capacity_tokens)
        sid = selection.source_id
        rows = [
            p.model_dump(mode="json") if hasattr(p, "model_dump") else dict(p) for p in passages
        ]
        kind = "passages" if rows else "entries"
        if not rows:
            # Top-level structured collections are navigable; judgments remain whole.
            fields = data.items() if isinstance(data, dict) else [("data", data)]
            rows = [
                {"entry_id": f"{key}/{i}", "field": key, "value": value}
                for key, values in fields
                for i, value in enumerate(values if isinstance(values, list) else [values])
            ]
        id_key = position_key if kind == "passages" else "entry_id"
        materials = [
            m.model_dump(mode="json") if hasattr(m, "model_dump") else dict(m) for m in materials
        ]
        fingerprint = hashlib.sha256(
            json.dumps([rows, materials], ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        mapping = {row[id_key]: row for row in rows}
        order = {key: index for index, key in enumerate(mapping)}
        cursor = getattr(selection, "continuation", None)
        if cursor:
            saved = self.cursors.get(cursor)
            if saved is None or saved[:2] != (sid, fingerprint):
                return {"source_id": sid, "error": "invalid_or_stale_continuation"}
            units = saved[2]
            wanted = [key for keys, _ in units for key in keys]
        elif isinstance(selection, PassageSelection):
            wanted = list(dict.fromkeys(selection.passage_ids))
        else:
            selected_material = getattr(selection, "material_id", None)
            material_ids = {
                pid
                for material in materials
                if material.get("material_id") == selected_material
                for pid in material.get("passage_ids", [])
            }
            start = next(
                (i for i, row in enumerate(rows) if row[id_key] == selection.start_passage_id), 0
            )
            wanted = []
            for row in rows[start:]:
                if selected_material and row[id_key] not in material_ids:
                    continue
                if selection.pages and row.get("page") not in selection.pages:
                    continue
                matches = (
                    selection.section is None
                    or row.get("section", row.get("field")) == selection.section
                    or selection.section in row.get("heading_path", [])
                )
                if matches:
                    wanted.append(row[id_key])
                elif wanted and selection.section is not None:
                    break
        missing = [key for key in wanted if key not in mapping]
        if not cursor:
            units = reading_groups(rows, wanted, position_key=id_key, materials=materials)
        returned, pending = [], []
        result = {
            "source_id": sid,
            kind: returned,
            "requested_count": len({key for keys, _ in units for key in keys}) + len(missing),
            "returned_ids": [],
            "pending_count": 0,
            "missing_ids": missing,
            "continuation": None,
            "complete": False,
            "unit_continues": False,
            "reading_tokens": limit,
            "input_tokens_estimate": 0,
        }
        token = uuid4().hex
        estimator = TokenEstimator()

        def display(remaining):
            returned.sort(key=lambda row: order[row[id_key]])
            result["returned_ids"] = [row[id_key] for row in returned]
            result["pending_count"] = len(
                {key for keys, _ in remaining for key in keys} - set(result["returned_ids"])
            )
            result["continuation"] = token if remaining else None
            result["complete"] = not (remaining or missing)
            if kind == "passages":
                result["originals"] = render_source(
                    sid, returned, source=source, materials=materials
                )
            return reading_output(result)

        def measure(remaining):
            return estimator.measure(
                [
                    {
                        "role": "tool",
                        "content": json.dumps(display(remaining), ensure_ascii=False),
                    }
                ]
            )["input_tokens"]

        def fits(remaining):
            # Captured originals later receive permanent citation identities and reading
            # references. Reserve their small display cost without inventing IDs.
            alias_tokens = 32 * len(returned) + 256 if kind == "passages" else 8
            return measure(remaining) + alias_tokens <= limit

        for i, (group_keys, contextual) in enumerate(units):
            already = {row[id_key] for row in returned}
            keys = [key for key in group_keys if key not in already]
            if not keys:
                continue
            remaining = units[i + 1 :]
            returned.extend(mapping[key] for key in keys)
            if fits(remaining):
                continue
            returned[:] = [row for row in returned if row[id_key] in already]
            # Keep the complete contextual bundle for a later, larger window. Its
            # preceding page must never consume the budget without the target.
            pending = units[i:]
            if contextual and not returned:
                result["unread_reason"] = "reading_context_exceeds_read_budget"
            elif not contextual and not returned:
                # Oversized native units continue at complete paragraph/row-group
                # boundaries supplied by the parser, never at character positions.
                for j, key in enumerate(keys):
                    returned.append(mapping[key])
                    rest = ([(keys[j + 1 :], False)] if j + 1 < len(keys) else []) + remaining
                    result["unit_continues"] = j + 1 < len(keys)
                    if not fits(rest):
                        returned[:] = [row for row in returned if row[id_key] != key]
                        pending = [(keys[j:], False)] + remaining
                        result["unit_continues"] = bool(returned)
                        if not returned:
                            result["unread_reason"] = "native_block_exceeds_read_budget"
                        break
                    pending = rest
            break
        display(pending)
        # Include the numeric estimate itself in the measured final public payload.
        result["input_tokens_estimate"] = measure(pending)
        result["input_tokens_estimate"] = measure(pending)
        if pending:
            self.cursors[token] = (sid, fingerprint, pending)
        return result
