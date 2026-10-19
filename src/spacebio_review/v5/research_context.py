"""Purpose-specific views of the existing research records, without summarizing prose."""

from __future__ import annotations

from copy import deepcopy

from spacebio_review.evidence.reading_render import reading_groups, render_originals
from spacebio_review.prose import cited_ids, compile_authored_body, prose_inputs


def expand_captured_units(readings, selected):
    """Retain the same native reading window through handoff and later rereading."""
    selected = set(selected)
    sources = {}
    for key, item in readings.items():
        sources.setdefault(item.get("reference", {}).get("source_id"), []).append({
            **item.get("reading_structure", {}),
            "reading_id": key, "material": item.get("material", {}),
        })
    retained = {
        key for rows in sources.values()
        for keys, _ in reading_groups(rows, selected, position_key="reading_id") for key in keys
    }
    return {key: item for key, item in readings.items() if key in retained}


def select_original_units(originals, selected):
    """Keep the displayed reading window when a decision selects its citations."""
    result = []
    for source in originals:
        rows, wanted = [], []
        for material in source["materials"]:
            for unit in material["units"]:
                key = len(rows)
                rows.append({**unit, "reading_id": key, "material": material})
                if any(passage.get("evidence_handle") in selected
                       or any(f"(evidence:{alias})" in passage.get("citation", "")
                              for alias in selected)
                       for passage in unit["passages"]):
                    wanted.append(key)
        retained = {key for keys, _ in reading_groups(rows, wanted, position_key="reading_id")
                    for key in keys}
        materials = []
        index = 0
        for material in source["materials"]:
            units = [unit for offset, unit in enumerate(material["units"])
                     if index + offset in retained]
            index += len(material["units"])
            if units:
                materials.append({**material, "units": units})
        if materials:
            result.append({**source, "materials": materials})
    return result


def _positions(readings):
    positions = {}
    for alias, snapshot in readings.items():
        ref = snapshot.get("reference", {})
        key = (ref.get("source_id"), ref.get("passage_id"))
        if all(key) and snapshot.get("quote"):
            # A position alone cannot choose between distinct captured versions.
            positions[key] = alias if key not in positions else None
    return positions


def compact_tool_payload(
    output: dict, readings: dict, *, group_originals=True,
) -> dict:
    """Retain citation identities and display captured originals once in a tool response.

    ``output`` is the stored ToolResult. This is a display projection:
    neither the result nor the captured originals are changed in storage.
    """
    output = prose_inputs(deepcopy(output))
    readings = {
        alias: {**deepcopy(item), "citation": f"[{alias}](evidence:{alias})"}
        for alias, item in readings.items()
    }
    positions = _positions(readings)
    snapshots = {
        (
            item.get("reference", {}).get("source_id"),
            item.get("reference", {}).get("passage_id"),
            item.get("reference", {}).get("visual_id"),
            item.get("material_version"), item.get("quote"),
        ): alias for alias, item in readings.items()
    }

    def positioned_original(source_id, passage):
        matches = [alias for alias, item in readings.items()
                   if item.get("reference", {}).get("source_id") == source_id
                   and item.get("reference", {}).get("passage_id") == passage.get("passage_id")
                   and item.get("quote") == passage.get("text")
                   and (not passage.get("material_version")
                        or item.get("material_version") == passage["material_version"])]
        return matches[0] if len(matches) == 1 else None

    def link(value, source_id=None):
        if isinstance(value, list):
            for child in value:
                link(child, source_id)
        elif isinstance(value, dict):
            source_id = value.get("source_id", source_id)
            # The reading window and snapshot projection share this native layout.
            # Move an already captured source to its one home in `readings` rather
            # than repeat the source header and its text in the result envelope.
            if source_id and isinstance(value.get("materials"), list):
                passages = [
                    passage
                    for material in value["materials"]
                    for unit in material.get("units", [])
                    for passage in unit.get("passages", [])
                ]
                refs = [positioned_original(source_id, p) for p in passages]
                if passages and all(
                    ref and readings[ref].get("quote") == p.get("text")
                    for ref, p in zip(refs, passages)
                ):
                    value.clear()
                    value.update(source_id=source_id, reading_references=refs)
                    return
            if isinstance(value.get("reference"), dict) and "quote" in value:
                reference = value["reference"]
                original = snapshots.get((
                    reference.get("source_id"), reference.get("passage_id"),
                    reference.get("visual_id"), value.get("material_version"), value["quote"],
                ))
                if original:
                    value.pop("quote")
                    value["text_reference"] = original
            handle = value.get("evidence_handle")
            alias = handle
            if not alias and value.get("passage_id"):
                matched = positioned_original(source_id, value)
                if matched and value.get("text") == readings[matched].get("quote"):
                    alias = matched
            if alias:
                value["evidence_handle"] = alias
                value["citation"] = f"[{alias}](evidence:{alias})"
                original = readings.get(alias)
                if original and "text" in value and value["text"] == original.get("quote"):
                    value.pop("text")
                    value["text_reference"] = alias
            abstract = positions.get((source_id, value.get("abstract_passage_id")))
            if abstract and "abstract" in value:
                value.pop("abstract")
                value["abstract_reference"] = abstract
            for child in value.values():
                link(child, source_id)

    link(output)
    return {
        "result": output,
        "readings": render_originals(readings) if group_originals else readings,
    }


def returned_originals(value, readings):
    """Recognize actual native originals in a tool return, including an explicit reread."""
    selected = set(context_references(value, readings))

    def visit(item, source_id=None):
        if isinstance(item, list):
            for child in item:
                visit(child, source_id)
        elif isinstance(item, dict):
            source_id = item.get("source_id", source_id)
            if item.get("passage_id") and "text" in item:
                for alias, snapshot in readings.items():
                    reference = snapshot.get("reference", {})
                    if (reference.get("source_id") == source_id
                            and reference.get("passage_id") == item["passage_id"]
                            and snapshot.get("quote") == item["text"]
                            and (not item.get("material_version") or
                                 snapshot.get("material_version") == item["material_version"])):
                        selected.add(alias)
            for child in item.values():
                visit(child, source_id)

    visit(value)
    return {alias: item for alias, item in readings.items()
            if alias in selected}


def _scientific_content(value, *, discovery=False, navigation=False):
    """Remove known discovery/navigation envelopes, never fields inside scientific prose."""
    if isinstance(value, list):
        return [
            _scientific_content(item, discovery=discovery, navigation=navigation)
            for item in value
        ]
    if not isinstance(value, dict):
        return value
    if discovery and isinstance(value.get("candidates"), list):
        # Captured abstracts are already in originals. Preserve uncaptured abstracts
        # as actual obtained material, even when there has been no further reading.
        candidates = [
            {
                key: item[key]
                for key in (
                    "source_id", "title", "publication_date", "abstract_passage_id", "abstract"
                )
                if key in item
            }
            for item in value["candidates"]
            if item.get("abstract")
        ]
        kept = {"candidate_abstracts": candidates} if candidates else {}
        if value.get("existing_research"):
            kept["existing_research"] = value["existing_research"]
        return kept
    if navigation and value.get("source_id") and (
        "reading_directory" in value or {"sections", "materials", "read_with"} <= value.keys()
    ):
        keys = {
            "source_id", "title", "abstract", "abstract_passage_id", "abstract_reference",
            "availability", "limitations", "reading_gaps", "reading",
        }
        value = {key: item for key, item in value.items() if key in keys}
    elif navigation and value.get("source_id") and isinstance(value.get("passages"), list):
        keys = {
            "source_id", "title", "passages", "reading_scope", "limitations", "reading_gaps",
            "missing_ids", "complete", "unit_continues", "error",
        }
        value = {key: item for key, item in value.items() if key in keys}
    elif navigation and "sources" in value and "channels" in value:
        value = {
            key: item for key, item in value.items()
            if key in {"sources", "limitations", "reading_gaps"}
        }
    return {
        key: _scientific_content(item, navigation=navigation) for key, item in value.items()
    }


def context_references(value, readings=None, *, include_notes=True):
    """Yield explicit citations and unambiguous captured abstract positions."""
    positions = _positions(readings or {})

    def references(item, source_id=None):
        if isinstance(item, list):
            for child in item:
                yield from references(child, source_id)
        elif isinstance(item, dict):
            if set(item) == {"blocks"}:
                yield from cited_ids(item)
                return
            if item.get("kind") == "citation":
                yield item["target"]
                return
            source_id = item.get("source_id", source_id)
            abstract = positions.get((source_id, item.get("abstract_passage_id")))
            if abstract:
                yield abstract
            for key, child in item.items():
                if key == "reading_references":
                    yield from child
                    continue
                if key in {"originals", "available_readings", "readings"}:
                    continue
                # An inquiry's status explanation belongs to its author's local
                # session. The public consultation answer carries a separately
                # compiled reason with stable handles and the captured originals.
                if isinstance(child, str) and (
                    key == "author_reason" or key == "reason" and "author_reason" not in item and {
                        "inquiry_id", "current_owner_session_id", "status"
                    } <= item.keys()
                ):
                    continue
                if key == "note" and not include_notes and (
                    item.get("note_ref") or item.get("contribution_ref")
                ):
                    continue
                if key in {"evidence_handle", "text_reference", "abstract_reference"}:
                    if isinstance(child, str):
                        yield child
                else:
                    yield from references(child, source_id)
        elif isinstance(item, str) and "evidence:" in item:
            yield from cited_ids(compile_authored_body(item))

    yield from references(value)


def _once_per_note(value, seen):
    if isinstance(value, list):
        return [_once_per_note(item, seen) for item in value]
    if not isinstance(value, dict):
        return value
    ref, note = value.get("note_ref") or value.get("contribution_ref"), value.get("note")
    if ref and isinstance(note, dict) and note.get("body"):
        if ref in seen:
            value = {key: item for key, item in value.items() if key != "note"}
        else:
            seen.add(ref)
    return {key: _once_per_note(item, seen) for key, item in value.items()}


def application_originals(application, readings):
    """Locate exact positioned originals; ambiguous captured versions stay separate."""
    if not isinstance(application, dict):
        return {}
    matches = {}
    for source in [*application.get("sources", []), *application.get("application_basis", [])]:
        for passage in source.get("passages", []):
            candidates = [
                alias for alias, snapshot in readings.items()
                if snapshot.get("reference", {}).get("source_id") == source["source_id"]
                and snapshot.get("reference", {}).get("passage_id") == passage["passage_id"]
                and snapshot.get("reference", {}).get("visual_id") is None
                and snapshot.get("quote") == passage.get("text")
                and (
                    not passage.get("material_version")
                    or snapshot.get("material_version") == passage["material_version"]
                )
            ]
            versions = {readings[alias].get("material_version") for alias in candidates}
            if len(candidates) == 1 or len(versions) == 1 and None not in versions:
                matches.update((alias, passage) for alias in candidates)
    return matches


def _input_context(view: dict, *, composition: bool, readings=None) -> dict:
    """Give each immutable note and original one readable home in this request.

    The existing absorption watermark decides which observations are new. All their
    originals and explanations survive; only known process/catalogue envelopes go.
    Originals are collected by the session's permanent evidence identities, and notes by note_ref.
    Unknown tool content and assessment instructions are retained unchanged.
    """
    result = deepcopy(view)
    if composition:
        result.pop("resources", None)
    originals = result.get("originals", {}).copy()
    observations = result.get("unabsorbed_observations", [])
    for row in observations:
        originals.update(row.get("output", {}).get("readings", {}))

    # A fixed-source reader's initial selection is already captured. Keep that
    # original in the same grouped home as later reads; the application itself
    # remains the primary positioned text and is handled separately below.
    task = result.get("task", {})
    application = task.pop("application", None)
    result["task"] = compact_tool_payload(task, originals)["result"]
    if application is not None:
        result["task"]["application"] = application

    for row in observations:
        output = row.get("output")
        if not isinstance(output, dict) or "result" not in output:
            continue
        # Use all originals: a repeated response may reference evidence captured by
        # an earlier response and therefore carry no new readings of its own.
        payload = compact_tool_payload(output["result"], originals)["result"]
        if composition and "content" in payload:
            payload["content"] = _scientific_content(
                payload["content"],
                discovery=row.get("name") in {"search_sources", "related_papers"},
                navigation=row.get("name") in {
                    "acquire_source", "read_application", "read_materials",
                    "browse_materials", "retrieve_knowledge",
                },
            )
        row["output"] = {"result": payload}

    working = compact_tool_payload(result.get("working_context", {}), originals)["result"]
    if composition and "source" in working:
        working["source"] = _scientific_content(working["source"], navigation=True)
    if composition and "topics" in working:
        working["topics"] = [
            {key: item for key, item in topic.items() if key != "materials"}
            for topic in working["topics"]
        ]
    result["working_context"] = working

    # The full application remains the primary source. Do not repeat a captured
    # application passage beside its identical, positioned text in that source.
    application = result.get("task", {}).get("application")
    present = application_originals(application, readings if readings is not None else originals)
    result["originals"] = render_originals({
        alias: snapshot for alias, snapshot in originals.items()
        if alias not in present
    })
    seen = set()
    order = (
        ("working_context", "current_research", "unabsorbed_observations")
        if "scientific_manuscripts" in working
        else ("current_research", "unabsorbed_observations", "working_context")
    )
    for key in (*order, "task"):
        if key in result:
            result[key] = _once_per_note(result[key], seen)
    used = set(context_references(
        result
    )) | set(originals)
    if composition:
        result["available_readings"] = {
            alias: entry for alias, entry in result.get("available_readings", {}).items()
            if alias in used
        }
    if "available_sources" in result:
        displayed_sources = {source["source_id"] for source in result["originals"]}
        displayed_sources.update(
            item["source_id"] for field in ("sources", "application_basis")
            for item in (application if isinstance(application, dict) else {}).get(field, [])
        )
        directory_sources = {
            item.get("reference", {}).get("source_id")
            for item in result.get("available_readings", {}).values()
        }
        result["available_sources"] = {
            source: metadata for source, metadata in result["available_sources"].items()
            if source in directory_sources - displayed_sources
        }
    return result


def research_context(view: dict, *, readings=None) -> dict:
    """Keep the full reading directory and unabsorbed findings, without duplicate text."""
    return _input_context(view, composition=False, readings=readings)


def composition_context(view: dict, *, readings=None) -> dict:
    """Keep scientific writing material and remove known navigation envelopes."""
    return _input_context(view, composition=True, readings=readings)
