"""One original-text representation for tool reads and retained author inputs."""

from __future__ import annotations

from collections import OrderedDict


def _row(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)


def _metadata(value, fields):
    value = _row(value) if value is not None else {}
    return {key: value[key] for key in fields if value.get(key) not in (None, "", [])}


def reading_units(passages):
    """Original positions grouped by native membership, in document order."""
    groups = OrderedDict()
    for i, value in enumerate(passages):
        row = _row(value)
        groups.setdefault(row.get("unit_id") or row["passage_id"], []).append(i)
    return list(groups.values())


def reading_groups(passages, wanted, *, position_key="passage_id", materials=()):
    """Select native units within their complete original-page reading window.

    Each pair contains original positions and whether context plus target must travel
    together. Rendered units can supply ``pages``; native paragraphs supply ``page``.
    The document or source boundary is supplied by the caller, never inferred here.
    """
    rows = [_row(value) for value in passages]
    material_rows = [_row(value) for value in materials]
    membership = {
        pid: material.get("material_id")
        for material in material_rows for pid in material.get("passage_ids", [])
    }
    units, positions, pages, unit_pages = {}, {}, {}, {}
    for row in rows:
        key = row[position_key]
        material = (row.get("material") or {}).get("material_id") or membership.get(
            row.get("passage_id")
        )
        unit = (material, row.get("unit_id") or key)
        units.setdefault(unit, []).append(key)
        positions[key] = unit
        row_pages = row.get("pages", []) or (
            [row["page"]] if row.get("page") is not None else []
        )
        unit_pages.setdefault(unit, set()).update(row_pages)
        if material:
            for page in row_pages:
                pages.setdefault((material, page), set()).add(unit)
    mapping = {row[position_key]: row for row in rows}
    order = {key: index for index, key in enumerate(mapping)}
    # Retrieval supplies relevance order; browsing supplies document order. Preserve
    # that choice while allocating the window, then render retained text in source order.
    selected = dict.fromkeys(positions[key] for key in wanted if key in positions)
    groups = []
    for unit in selected:
        keys = units[unit]
        native_pages = unit_pages[unit]
        context = set()
        if unit[0] and native_pages:
            # Subjects, qualifications and notes may surround a table on its own
            # page or the preceding page. Keep this physical reading window without
            # guessing which title/prose supplies scope or recursively growing it.
            window_pages = native_pages | {min(native_pages) - 1}
            neighbors = {neighbor for page in window_pages
                         for neighbor in pages.get((unit[0], page), ())}
            context = {key for neighbor in neighbors for key in units[neighbor]} - set(keys)
        groups.append((sorted(set(keys) | context, key=order.get), bool(context)))
    return groups


def render_source(source_id, passages, *, source=None, materials=()):
    """Keep source metadata once and originals in native material/unit order.

    Input rows may carry ``citation`` and ``order`` from reading snapshots. No text
    is summarized, and a position with a different retained version stays distinct.
    """
    material_rows = [_row(value) for value in materials]
    membership = {
        pid: material for material in material_rows for pid in material.get("passage_ids", [])
    }
    rows = [_row(value) for value in passages]
    rows = [row for _, row in sorted(
        enumerate(rows), key=lambda item: (item[1].get("order", item[0]), item[0])
    )]
    output = {
        "source_id": source_id,
        **_metadata(source, ("title", "url", "authors", "publication_date", "kind")),
        "materials": [],
    }
    versions = {}
    for row in rows:
        position = (row.get("passage_id"), row.get("visual_id"))
        versions.setdefault(position, set()).add(row.get("material_version"))
    groups = OrderedDict()
    for row in rows:
        material = row.get("material") or membership.get(row.get("passage_id"), {})
        material_id = material.get("material_id", "")
        if material_id not in groups:
            group = {
                **_metadata(material, ("material_id", "title", "role", "url", "package_member")),
                "units": [],
            }
            groups[material_id] = (group, {})
            output["materials"].append(group)
        group, units = groups[material_id]
        unit_id = row.get("unit_id") or row.get("passage_id") or row.get("visual_id")
        if unit_id not in units:
            path = row.get("heading_path") or ([row["section"]] if row.get("section") else [])
            unit = {"unit_id": unit_id, "heading_path": path, "pages": [], "passages": []}
            units[unit_id] = unit
            group["units"].append(unit)
        unit = units[unit_id]
        if row.get("page") is not None and row["page"] not in unit["pages"]:
            unit["pages"].append(row["page"])
        for page in row.get("original_pages", []):
            original_pages = unit.setdefault("original_pages", [])
            if page not in original_pages:
                original_pages.append(dict(page))
        display = {
            key: row[key]
            for key in ("passage_id", "visual_id", "locator", "kind", "evidence_handle",
                        "citation", "text")
            if row.get(key) is not None
        }
        if len(versions[(row.get("passage_id"), row.get("visual_id"))]) > 1:
            display["material_version"] = row.get("material_version")
        unit["passages"].append(display)
    return output


def render_originals(readings):
    """Render exact captured originals with their permanent citation identities."""
    sources = OrderedDict()
    for alias, snapshot in readings.items():
        reference = snapshot.get("reference", {})
        source_id = reference.get("source_id", "")
        source, rows = sources.setdefault(source_id, (snapshot, []))
        structure = dict(snapshot.get("reading_structure") or {})
        row = {
            **structure,
            **{key: reference[key] for key in ("passage_id", "visual_id") if reference.get(key)},
            "locator": snapshot.get("locator", ""),
            "kind": structure.get("kind") or snapshot.get("reading_scope", "text"),
            "text": snapshot.get("quote", ""),
            "citation": snapshot.get("citation") or f"[{alias}](evidence:{alias})",
            "evidence_handle": alias,
            "material_version": snapshot.get("material_version"),
        }
        if snapshot.get("material"):
            row["material"] = snapshot["material"]
        rows.append(row)
    return [render_source(source_id, rows, source=source) for source_id, (source, rows)
            in sources.items()]
