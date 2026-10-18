"""Purpose-specific views of the existing research records, without summarizing prose."""

from __future__ import annotations

import re
from copy import deepcopy

from .authoring import _CITATION


def _positions(readings):
    positions = {}
    for alias, snapshot in readings.items():
        ref = snapshot.get("reference", {})
        key = (ref.get("source_id"), ref.get("passage_id"))
        if all(key) and snapshot.get("quote"):
            # A position alone cannot choose between distinct captured versions.
            positions[key] = alias if key not in positions else None
    return positions


def compact_tool_payload(output: dict, readings: dict, aliases: dict) -> dict:
    """Bind local citations and display captured originals once in a tool response.

    ``output`` is the prose-input view of a ToolResult. This is a display projection:
    neither the result nor the captured originals are changed in storage.
    """
    output, readings = deepcopy(output), deepcopy(readings)
    reverse = {handle: alias for alias, handle in aliases.items()}
    positions = _positions(readings)

    def link(value, source_id=None):
        if isinstance(value, list):
            for child in value:
                link(child, source_id)
        elif isinstance(value, dict):
            source_id = value.get("source_id", source_id)
            if isinstance(value.get("cites"), list):
                value["cites"] = [reverse.get(key, key) for key in value["cites"]]
            handle = value.get("evidence_handle")
            alias = reverse.get(handle, handle)
            if alias:
                value["evidence_handle"] = alias
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
    return {"result": output, "readings": readings}


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
        "passage_directory" in value or {"sections", "materials", "read_with"} <= value.keys()
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


def context_references(value, readings=None):
    """Yield explicit citations and unambiguous captured abstract positions."""
    positions = _positions(readings or {})

    def references(item, source_id=None):
        if isinstance(item, list):
            for child in item:
                yield from references(child, source_id)
        elif isinstance(item, dict):
            source_id = item.get("source_id", source_id)
            abstract = positions.get((source_id, item.get("abstract_passage_id")))
            if abstract:
                yield abstract
            for key, child in item.items():
                if key == "cites" and isinstance(child, list):
                    yield from child
                elif key in {"evidence_handle", "text_reference", "abstract_reference"}:
                    if isinstance(child, str):
                        yield child
                else:
                    yield from references(child, source_id)
        elif isinstance(item, str):
            # Use the author's actual citation grammar, including grouped anchors.
            for match in _CITATION.finditer(item):
                yield from re.findall(r"E[1-9][0-9]*", match.group(1))

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


def composition_context(view: dict) -> dict:
    """Project an assembled runtime context into a complete scientific writing input.

    The existing absorption watermark decides which observations are new. All their
    originals and explanations survive; only known process/catalogue envelopes go.
    Originals are collected by the session's evidence aliases, and notes by note_ref.
    Unknown tool content and assessment instructions are retained unchanged.
    """
    result = deepcopy(view)
    result.pop("resources", None)
    originals = result.get("originals", {}).copy()
    observations = result.get("unabsorbed_observations", [])
    for row in observations:
        originals.update(row.get("output", {}).get("readings", {}))

    for row in observations:
        output = row.get("output")
        if not isinstance(output, dict) or "result" not in output:
            continue
        # Use all originals: a repeated response may reference evidence captured by
        # an earlier response and therefore carry no new readings of its own.
        payload = compact_tool_payload(output["result"], originals, {})["result"]
        if "content" in payload:
            payload["content"] = _scientific_content(
                payload["content"],
                discovery=row.get("name") in {"search_sources", "related_papers"},
                navigation=row.get("name") in {
                    "acquire_source", "read_sources", "read_application", "read_materials",
                    "browse_materials", "retrieve_knowledge",
                },
            )
        row["output"] = {"result": payload}

    working = compact_tool_payload(result.get("working_context", {}), originals, {})["result"]
    if "source" in working:
        working["source"] = _scientific_content(working["source"], navigation=True)
    if "topics" in working:
        working["topics"] = [
            {key: item for key, item in topic.items() if key != "materials"}
            for topic in working["topics"]
        ]
    result["working_context"] = working

    # The full application remains the primary source. Do not repeat a captured
    # application passage beside its identical, positioned text in that source.
    application = result.get("task", {}).get("application")
    passages = {}
    if isinstance(application, dict):
        passages = {
            (source["source_id"], passage["passage_id"]): passage.get("text")
            for source in application.get("sources", [])
            for passage in source.get("passages", [])
        }
    result["originals"] = {
        alias: snapshot for alias, snapshot in originals.items()
        if passages.get((
            snapshot.get("reference", {}).get("source_id"),
            snapshot.get("reference", {}).get("passage_id"),
        )) != snapshot.get("quote") or not snapshot.get("quote")
    }
    seen = set()
    order = (
        ("working_context", "current_research", "unabsorbed_observations")
        if "scientific_manuscripts" in working
        else ("current_research", "unabsorbed_observations", "working_context")
    )
    for key in order:
        if key in result:
            result[key] = _once_per_note(result[key], seen)
    used = set(context_references(result)) | set(originals)
    result["available_readings"] = {
        alias: entry for alias, entry in result.get("available_readings", {}).items()
        if alias in used
    }
    return result
