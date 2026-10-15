"""Persist delegated arguments with exact evidence identities before parent projection."""

import re
from copy import deepcopy

from spacebio_review.prose import cited_ids, prose_parts, render_parts


def literature_question(request, base, evidence, *, previous_question=None):
    """Resolve a specialist's selected application reads without restating their content."""
    from spacebio_review.evidence.knowledge import ReviewKnowledgeQuestion

    originals = deepcopy((previous_question or {}).get("application_statements", {}))
    for handle in dict.fromkeys(request.evidence_handles):
        item = evidence[handle]
        if item["kind"] != "application" or not item.get("quote"):
            raise ValueError("application_original_required")
        originals[handle] = deepcopy(item)
    if not originals:
        raise ValueError("application_original_required")
    return ReviewKnowledgeQuestion.model_validate(
        {
            "cutoff_date": base.cutoff_date,
            "existing_source_ids": base.existing_source_ids,
            "question": request.question,
            "purpose": request.purpose,
            "needed_information": request.needed_information,
            "assessment_focus": request.changes_judgment_if,
            "application_statements": originals,
            "previous_research_id": request.previous_research_id,
        }
    )


def knowledge_artifact(answer, task_id, title, limitations):
    evidence = deepcopy(answer.get("evidence_resolution", {}))
    version = answer.get("contract_version", "legacy-3")
    prose_answer = version in {"literature-4", "literature-5"}

    def inline_refs(text):
        return [k for k in re.findall(r"(?<!\\)\[@([^]]+)\]", text) if k in evidence]

    blocks = []
    for i, finding in enumerate(answer.get("findings", []), 1):
        if prose_answer:
            parts = prose_parts(finding)
            blocks.append(
                {
                    "block_id": f"f{i}",
                    "text": render_parts(parts),
                    "parts": parts,
                    "evidence_refs": list(dict.fromkeys(cited_ids(finding))),
                    "result_refs": [],
                }
            )
            continue
        bases = deepcopy(finding.get("basis", []))
        used = []
        for basis in bases:
            refs = list(basis.get("evidence_refs", []))
            # Legacy answers retain positions; never pick a version arbitrarily.
            if not refs:
                for location in basis.get("source_refs", []):
                    matches = [
                        k
                        for k, v in evidence.items()
                        if all(
                            v["reference"].get(f) == location.get(f)
                            for f in ("source_id", "passage_id", "visual_id")
                        )
                    ]
                    if len(matches) == 1:
                        refs.extend(matches)
            basis["evidence_refs"] = list(dict.fromkeys(refs))
            used.extend(refs)
        text = finding.get("explanation", "")
        used.extend(inline_refs(text))
        blocks.append(
            {
                "block_id": f"f{i}",
                "text": text,
                "basis": bases,
                "evidence_refs": list(dict.fromkeys(used)),
                "result_refs": [],
            }
        )
    scope_value = answer.get("scope") if version == "literature-5" else answer.get("applicability")
    scope_parts = prose_parts(scope_value) if prose_answer and scope_value else None
    scope = render_parts(scope_parts) if scope_parts is not None else "\n".join(scope_value or [])
    return {
        "origin": "delegated",
        "author": task_id,
        "task_id": task_id,
        "title": title,
        "knowledge_contract_version": version,
        "scope_kind": (
            "external_research_scope"
            if version == "literature-5"
            else "legacy_project_applicability"
        ),
        "scope_note": scope,
        **({"scope_parts": scope_parts} if scope_parts is not None else {}),
        "scope_links": list(dict.fromkeys(inline_refs(scope))),
        "unknowns": [
            render_parts([{"text": text, "cites": []}]) if prose_answer else text
            for text in answer.get("unknowns", [])
        ],
        "limitations": list(limitations),
        "blocks": blocks,
        "evidence": evidence,
    }
