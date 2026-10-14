"""Immutable artifacts and durable receipts; task state belongs to Coordinator."""

import json
import re
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from spacebio_review.agents.diagnostics import digest

VERSION = "v4.4-direct-publication-1"


def content_references(value):
    """Read only explicit links in scientific content, not artifact dependencies."""
    if isinstance(value, str):
        return re.findall(r"(?<!\\)\[@([^]]+)\]", value)
    if isinstance(value, list):
        return [key for item in value for key in content_references(item)]
    if isinstance(value, dict):
        # Structured prose binds cites explicitly; its text is not legacy reference markup.
        return [
            key
            for field, item in value.items()
            if field != "text" or "cites" not in value
            for key in (
                item
                if field in {"cites", "evidence_refs", "result_refs", "scope_links"}
                else content_references(item)
            )
        ]
    return []


def statement_registry(artifact):
    return {
        key: value
        for row in (artifact.get("statement_content") or {}).values()
        for key, value in row.get("statements", {}).items()
    }


def statement_subset(registry, selected):
    result, pending = {}, list(selected)
    while pending:
        key = pending.pop()
        if key in result or key not in registry:
            continue
        value = registry[key]
        result[key] = value
        pending.extend(
            value.get("premise_ids", []) + value.get("based_on", []) + value.get("supersedes", [])
        )
    return result


def plain(value):
    if hasattr(value, "model_dump"):
        return plain(value.model_dump(mode="json"))
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise TypeError("persisted keys must be strings")
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError(f"unsupported_persisted_type: {type(value).__name__}")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(plain(value), stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            import os

            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


class Records:
    def __init__(self, root):
        self.root = Path(root) / "v4"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "state.json"
        if self.path.exists() and self.load_state().get("format_version") != VERSION:
            raise ValueError("legacy_checkpoint_read_only_use_new_output")

    def load_state(self):
        return (
            json.loads(self.path.read_text("utf-8"))
            if self.path.exists()
            else {
                "format_version": VERSION,
                "questions": {},
                "tasks": {},
                "events": [],
                "heads": {},
                "metadata": {},
            }
        )

    @property
    def state(self):
        """Read-only snapshot; mutations cannot affect coordinator state."""
        return self.load_state()

    def save_state(self, state):
        write_json(self.path, state)

    def put(self, kind, value, dependencies=()):
        deps = sorted(set(dependencies))
        for ref in deps:
            self.get(ref)
        data = {"kind": kind, "value": plain(value), "dependencies": deps}
        key = kind + "-" + digest(data)
        path = self.root / (key + ".json")
        if not path.exists():
            write_json(path, data)
        self.get(key)
        return key

    def get(self, key):
        if (
            not isinstance(key, str)
            or not key
            or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in key)
        ):
            raise ValueError("invalid_record_handle")
        data = json.loads((self.root / (key + ".json")).read_text("utf-8"))
        if key != data["kind"] + "-" + digest(data):
            raise ValueError("record_integrity_failure")
        return deepcopy(data)

    def value(self, key):
        return self.get(key)["value"]

    def receipt(self, identity, value=None):
        path = self.root / "receipts" / (digest(identity) + ".json")
        if value is not None:
            ref = self.put("receipt", {"identity": identity, **plain(value)})
            write_json(path, {"ref": ref})
        if not path.exists():
            return None
        return self.value(json.loads(path.read_text("utf-8"))["ref"])

    def has_final_receipt(self, task, configuration):
        # Only needed after the attempt ceiling: completed scientific work must remain recoverable.
        for path in (self.root / "receipts").glob("*.json"):
            value = self.value(json.loads(path.read_text("utf-8"))["ref"])
            if (
                value["identity"].get("task") == task
                and value.get("configuration") == configuration
                and value.get("result", {}).get("status") == "completed"
            ):
                return True
        return False

    def result_blocks(self, refs):
        """Read full local blocks and scope; never recursively copy ancestors."""
        result = {}
        for ref in refs:
            artifact = self.value(ref)
            scope_identity = {
                key: artifact[key]
                for key in ("knowledge_contract_version", "scope_kind")
                if key in artifact
            }
            if artifact.get("origin") == "delegated":
                scope_identity.setdefault("scope_kind", "historical_unspecified")
            registry = statement_registry(artifact)
            content = artifact.get("citation_content", {})
            common = {
                "scope_note": artifact["scope_note"],
                "scope_links": artifact.get("scope_links", []),
                "scope_parts": artifact.get("scope_parts", content.get("/scope_note", [])),
                "unknowns": artifact.get("unknowns", []),
                "followups": artifact.get("followups", []),
            }
            common_refs = list(dict.fromkeys(content_references(common)))
            for index, block in enumerate(artifact.get("blocks", [])):
                selected = (
                    (artifact.get("statement_content") or {})
                    .get(f"/blocks/{index}", {})
                    .get("selected", [])
                )
                statements = statement_subset(registry, selected)
                cited = list(
                    dict.fromkeys(
                        content_references([block, content.get(f"/blocks/{index}", []), statements])
                    )
                )
                key = ref + "::" + block["block_id"]
                result[key] = {
                    "artifact_ref": ref,
                    "author": artifact.get("author", ""),
                    "task_id": artifact.get("task_id", ""),
                    "origin": artifact.get(
                        "origin",
                        "inherited" if artifact.get("statement_content") else "professional",
                    ),
                    "unknowns": artifact.get("unknowns", []),
                    "followups": artifact.get("followups", []),
                    "limitations": artifact.get("limitations", []),
                    "block_id": block["block_id"],
                    "title": artifact["title"],
                    "scope_note": artifact["scope_note"],
                    **scope_identity,
                    **(
                        {"scope_parts": common["scope_parts"]}
                        if "scope_parts" in artifact or "/scope_note" in content
                        else {}
                    ),
                    "scope_links": common_refs,
                    "scope_sources": {
                        "evidence": {
                            k: artifact["evidence"][k]
                            for k in common_refs
                            if k in artifact.get("evidence", {})
                        }
                    },
                    **block,
                    "result_refs": list(
                        dict.fromkeys(
                            block.get("result_refs", []) + [k for k in cited if "::" in k]
                        )
                    ),
                    **(
                        {
                            "statement_origins": artifact["statement_content"][f"/blocks/{index}"][
                                "selected"
                            ],
                            "statements": statements,
                        }
                        if f"/blocks/{index}" in (artifact.get("statement_content") or {})
                        else {}
                    ),
                    **(
                        {
                            "parts": content[f"/blocks/{index}"],
                        }
                        if f"/blocks/{index}" in content
                        else {}
                    ),
                    "evidence": {
                        k: v for k, v in artifact.get("evidence", {}).items() if k in cited
                    },
                }
        return result

    def research_blocks(self, refs):
        """Selected products plus only the individual arguments they actually use."""
        result = self.result_blocks(refs)
        pending = list(result)
        for key in pending:
            row = result[key]
            for related in row.get("result_refs", []) + row.get("scope_links", []):
                if "::" in related and related not in result:
                    result[related] = self.block(related)
                    pending.append(related)
        return result

    def block(self, key):
        ref, block_id = key.split("::", 1)
        return self.result_blocks([ref])[ref + "::" + block_id]
