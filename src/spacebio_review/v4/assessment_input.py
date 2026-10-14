"""Immutable scientific inputs. Archive bookkeeping never becomes writing material."""

import json
import re
from copy import deepcopy
from pathlib import Path

from spacebio_review.agents.context import evaluation_prompt
from spacebio_review.agents.diagnostics import digest

from .material_view import reading_view
from .records import VERSION as STATE_VERSION
from .records import Records, content_references

MARKER = re.compile(r"(?<!\\)\[@([^]]+)\]")

VERSION = "frozen-review-4"


def build_evaluation_context(skill, runtime_core, evaluation):
    """Snapshot the selected standard and run purpose, without requiring tool access."""
    prompt = evaluation_prompt(skill, runtime_core)
    for path, text in sorted(skill.references.items()):
        prompt += f"\n共同评价参考（{path}）：\n{text}"
    run_prompt = evaluation.prompt()
    return {
        "format_version": "evaluation-context-1",
        "skill": skill.name,
        "skill_digest": digest({"body": skill.body, "references": skill.references}),
        "configuration": evaluation.model_dump(mode="json"),
        "run_prompt": run_prompt,
        "prompt": prompt + "\n本轮评价配置：\n" + run_prompt,
    }


class ReviewArchive(Records):
    """Read both supported archive generations without opening a writable checkpoint."""

    def __init__(self, root):
        self.root = Path(root) / "v4"
        self.path = self.root / "state.json"
        if self.load_state()["format_version"] not in {
            "v4.1-3",
            "v4.2-questions-1",
            "v4.3-publication-1",
            STATE_VERSION,
        }:
            raise ValueError("unsupported_review_archive")

    def put(self, *args, **kwargs):
        raise TypeError("read_only_review_archive")

    def save_state(self, *args, **kwargs):
        raise TypeError("read_only_review_archive")

    def receipt(self, *args, **kwargs):
        raise TypeError("read_only_review_archive")

    def application(self):
        result = []
        for row in self.state["metadata"]["application"]["sources"]:
            path = self.root.parent / "sources" / row["source_id"] / "source.json"
            source = json.loads(path.read_text("utf-8"))
            if source["source_id"] != row["source_id"]:
                raise ValueError("application_source_identity_mismatch")
            result.append(source)
        return result

    def selected(self):
        return list(dict.fromkeys(ref for refs in self.state["heads"].values() for ref in refs))


def freeze_review(records, selected_research_refs, application, evaluation_context):
    """Return a self-contained value; the destination owns its immutable artifact ID."""
    evidence, identities, originals, sources = {}, {}, {}, {}

    def add(value, original=None):
        value = deepcopy(value)
        reference = value["reference"]
        sid = reference["source_id"]
        identity = digest(
            [
                reference,
                value.get("quote", ""),
                value.get("reading_scope", ""),
                value.get("material_version"),
                value.get("image_digest"),
            ]
        )
        if identity not in identities:
            key = f"E{len(evidence) + 1}"
            identities[identity] = key
            evidence[key] = {
                "source": sid,
                "locator": value.get("locator", ""),
                "reading_scope": value.get("reading_scope", ""),
                "quote": value.get("quote", ""),
            }
            sources.setdefault(
                sid,
                {
                    "title": "项目申请材料"
                    if value.get("kind") == "application"
                    else value["title"],
                    "kind": value.get("kind", ""),
                    "url": value.get("url", "")
                    if value.get("url", "").startswith(("https://", "http://"))
                    else "",
                    "authors": value.get("authors", []),
                    "publication_date": value.get("publication_date") or "",
                },
            )
        key = identities[identity]
        saved = {"handle": original, "record": value}
        if saved not in originals.setdefault(key, []):
            originals[key].append(saved)
        return key

    application_keys, material_limits = [], []
    for document in application:
        sid = document["source_id"]
        for passage in document["passages"]:
            application_keys.append(
                add(
                    {
                        "reference": {
                            "source_id": sid,
                            "passage_id": passage["passage_id"],
                            "visual_id": None,
                        },
                        "title": document["candidate"]["title"],
                        "kind": "application",
                        "locator": passage["locator"],
                        "reading_scope": "text",
                        "quote": passage["text"],
                    }
                )
            )
        material_limits.extend(document.get("limitations", []))
        material_limits.extend(document.get("reading_gaps", []))

    opinions, contexts, lineage, unknowns, limitations = {}, {}, {}, [], {}
    refs = list(dict.fromkeys(selected_research_refs))
    blocks = records.research_blocks(refs)
    opinion_aliases = {key: f"O{i + 1}" for i, key in enumerate(blocks)}
    groups = {}
    for identity, block in blocks.items():
        groups.setdefault(block["artifact_ref"], {})[identity] = block
    for ref, group in groups.items():
        common = next(iter(group.values()))
        aliases = {
            key: add(row, key)
            for block in group.values()
            for key, row in {
                **block.get("evidence", {}),
                **block.get("scope_sources", {}).get("evidence", {}),
            }.items()
        }
        statements = {key for block in group.values() for key in block.get("statements", {})}

        def prose(text, structured=None):
            # Argument links retain their location without becoming direct evidence citations.
            parts, related, end = [], [], 0

            def bind(target, key):
                if key in aliases:
                    target["cites"].append(aliases[key])
                elif key in statements:
                    target.setdefault("related_statements", []).append(key)
                else:
                    alias = opinion_aliases[key]
                    target.setdefault("related_opinions", []).append(alias)
                    related.append(alias)

            if structured is not None:
                for part in structured:
                    target = {"text": part["text"], "cites": []}
                    for key in part["cites"]:
                        bind(target, key)
                    parts.append(target)
            else:
                for match in MARKER.finditer(text):
                    prefix = text[end : match.start()]
                    if prefix or not parts:
                        parts.append({"text": prefix, "cites": []})
                    bind(parts[-1], match[1])
                    end = match.end()
                if text[end:]:
                    parts.append({"text": text[end:], "cites": []})
            return {"parts": parts}, list(dict.fromkeys(related))

        scope, scope_related = prose(common["scope_note"], common.get("scope_parts"))
        scope_related = list(
            dict.fromkeys(
                scope_related
                + [
                    opinion_aliases[key]
                    for key in common.get("scope_links", [])
                    if key in opinion_aliases
                ]
            )
        )
        context_id = f"G{len(contexts) + 1}"
        contexts[context_id] = {
            "title": common["title"],
            "origin": common["origin"],
            **{
                key: common[key]
                for key in ("scope_kind", "knowledge_contract_version")
                if key in common
            },
            "scope": scope,
            "evidence_ids": [
                aliases[key] for key in common.get("scope_links", []) if key in aliases
            ],
            "related_opinions": scope_related,
            "unknowns": [prose(text)[0] for text in common.get("unknowns", [])],
            "followups": [
                {
                    k: prose(row[k])[0]
                    for k in ("question", "missing_information", "decision_impact")
                    if row.get(k)
                }
                for row in common.get("followups", [])
            ],
            "opinion_ids": [],
        }
        for identity, block in group.items():
            key = opinion_aliases[identity]
            body, body_related = prose(block["text"], block.get("parts"))
            opinions[key] = {
                "context": context_id,
                "text": body,
                "related_opinions": list(
                    dict.fromkeys(
                        body_related
                        + [opinion_aliases[ref] for ref in block.get("result_refs", [])]
                    )
                ),
            }
            if block.get("basis"):
                opinions[key]["basis"] = [
                    {
                        "text": prose(basis.get("text", ""))[0],
                        "support": prose(basis.get("support_explanation", ""))[0],
                        "kind": basis.get("kind"),
                        "quantity": deepcopy(basis.get("quantitative_basis")),
                        "evidence_ids": [aliases[ref] for ref in basis.get("evidence_refs", [])],
                        **(
                            {"unresolved_locations": deepcopy(basis.get("source_refs", []))}
                            if not basis.get("evidence_refs")
                            else {}
                        ),
                    }
                    for basis in block["basis"]
                ]
            if block.get("statements"):
                opinions[key]["statements"] = {
                    sid: {
                        "text": prose("", value["parts"])[0],
                        "scope": prose("", value.get("scope", []))[0],
                        **{
                            field: deepcopy(value[field])
                            for field in (
                                "origin",
                                "support",
                                "premise_ids",
                                "based_on",
                                "supersedes",
                            )
                            if field in value
                        },
                    }
                    for sid, value in block["statements"].items()
                }
            contexts[context_id]["opinion_ids"].append(key)
            lineage[key] = {"artifact": ref, "block": block["block_id"]}
        unknowns.extend(contexts[context_id]["followups"])
        if common.get("limitations"):
            limitations[ref] = deepcopy(common["limitations"])

    return {
        "format_version": VERSION,
        "evaluation": deepcopy(evaluation_context),
        "materials": {
            "application": list(dict.fromkeys(application_keys)),
            "evidence": evidence,
            "sources": sources,
            "opinion_contexts": contexts,
            "opinions": opinions,
            "expert_questions_remaining": unknowns,
            "material_limits": material_limits,
        },
        "provenance": {
            "research_refs": refs,
            "opinions": lineage,
            "limitations": limitations,
            "evidence": originals,
            "application_digest": digest(application),
        },
    }


def select_materials(frozen, opinion_ids, *, evidence_ids=(), include_application=False):
    """Expand complete opinions and their scope, with each piece of evidence once."""
    materials = frozen["materials"]
    selected_opinions = dict.fromkeys(opinion_ids)
    pending = list(selected_opinions)
    for key in pending:
        row = materials["opinions"][key]
        context = materials["opinion_contexts"][row["context"]]
        for related in row.get("related_opinions", []) + context.get("related_opinions", []):
            if related not in selected_opinions:
                selected_opinions[related] = None
                pending.append(related)
    opinions = {key: deepcopy(materials["opinions"][key]) for key in selected_opinions}
    contexts = {}
    for row in opinions.values():
        key = row["context"]
        if key not in contexts:
            contexts[key] = deepcopy(materials["opinion_contexts"][key])
            if "opinion_ids" in contexts[key]:
                contexts[key]["opinion_ids"] = [
                    oid for oid in contexts[key]["opinion_ids"] if oid in opinions
                ]
    selected = dict.fromkeys(evidence_ids)
    if include_application:
        selected.update(dict.fromkeys(materials["application"]))

    def evidence_ids(value):
        if isinstance(value, dict):
            yield from value.get("evidence_ids", [])
            for child in value.values():
                yield from evidence_ids(child)
        elif isinstance(value, list):
            for child in value:
                yield from evidence_ids(child)

    scientific = [*opinions.values(), *contexts.values()]
    selected.update(dict.fromkeys(content_references(scientific)))
    selected.update(dict.fromkeys(evidence_ids(scientific)))
    evidence = {key: materials["evidence"][key] for key in selected}
    return {
        "opinions": opinions,
        "opinion_contexts": contexts,
        "evidence": evidence,
        "sources": {
            row["source"]: materials["sources"][row["source"]] for row in evidence.values()
        },
    }


def review_materials(frozen):
    """Present exact scientific contents without changing the archive contract."""
    materials = frozen["materials"]
    groups = {}
    for key, context in materials["opinion_contexts"].items():
        groups[key] = {
            **{k: v for k, v in context.items() if k != "opinion_ids"},
            "arguments": {
                identity: {k: v for k, v in opinion.items() if k != "context"}
                for identity, opinion in materials["opinions"].items()
                if opinion["context"] == key
            },
        }
    return {
        **reading_view(
            materials["evidence"],
            materials["sources"],
            groups,
            application=materials.get("application", []),
        ),
        **{
            key: deepcopy(materials[key])
            for key in ("expert_questions_remaining", "material_limits")
            if key in materials
        },
    }
