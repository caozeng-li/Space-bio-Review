"""Persist delegated arguments with exact evidence identities before parent projection."""

import re
from copy import deepcopy


def knowledge_artifact(answer, task_id, title, limitations):
    evidence = deepcopy(answer.get("evidence_resolution", {}))

    def inline_refs(text):
        return [k for k in re.findall(r"(?<!\\)\[@([^]]+)\]", text) if k in evidence]

    blocks = []
    for i, finding in enumerate(answer.get("findings", []), 1):
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
    scope = "\n".join(answer.get("applicability", []))
    return {
        "origin": "delegated",
        "author": task_id,
        "task_id": task_id,
        "title": title,
        "scope_note": scope,
        "scope_links": list(dict.fromkeys(inline_refs(scope))),
        "unknowns": list(answer.get("unknowns", [])),
        "limitations": list(limitations),
        "blocks": blocks,
        "evidence": evidence,
    }
