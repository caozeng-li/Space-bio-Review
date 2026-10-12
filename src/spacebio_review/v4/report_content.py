"""Report references retain local evidence and link to arguments without flattening them."""

import re

from spacebio_review.agents.diagnostics import digest

from .generation import render_parts, statement_parts
from .records import statement_registry

MARKER = re.compile(r"(?<!\\)\[@([^]]+)\]")


def text_units(text):
    units = []
    for paragraph, text in enumerate(text.split("\n\n")):
        start = 0
        parts = []
        for match in MARKER.finditer(text):
            preceding = text[start : match.start()]
            if preceding or not parts:
                parts.append({"text": preceding, "references": []})
            parts[-1]["references"].append(match[1])
            start = match.end()
        if start < len(text) or not parts:
            parts.append({"text": text[start:], "references": []})
        for part in parts:
            units.append({"unit_id": f"U{len(units) + 1}", "paragraph": paragraph, **part})
    return units


def render_units(units):
    paragraphs = {}
    for unit in units:
        text = unit["text"] + "".join(unit.get("reference_marks", []))
        paragraphs.setdefault(unit["paragraph"], []).append(text)
    return "\n\n".join("".join(parts) for parts in paragraphs.values())


def block_text(block, parts=None):
    if parts is not None:
        return render_parts(parts)
    text = block["text"]
    refs = block.get("evidence_refs", []) + block.get("result_refs", [])
    if refs and not MARKER.search(text):
        text += "\n\n该论述所列来源与关联论证：" + "".join("[@" + r + "]" for r in refs)
    return text


def artifact_block_text(artifact, index):
    content = artifact.get("statement_content") or {}
    selected = content.get(f"/blocks/{index}", {}).get("selected", [])
    registry = statement_registry(artifact)
    if selected and registry:
        return "\n\n".join(render_parts(statement_parts(registry[key])) for key in selected)
    return block_text(
        artifact["blocks"][index], (artifact.get("citation_content") or {}).get(f"/blocks/{index}")
    )


class ReportReferences:
    def __init__(self, records, evidence, statements=None):
        self.records, self.evidence = records, dict(evidence)
        self.sources, self.arguments, self.numbering = {}, {}, {}
        self.statements = dict(statements or {})

    def collect(self, sections):
        def scan(row):
            sources = []
            for key in MARKER.findall(row["text"]):
                if "::" in key or key.startswith("statement-"):
                    self.arguments.setdefault(key, "argument-" + digest(key)[:16])
                else:
                    item = self.evidence[key]
                    identity = digest(
                        {"reference": item["reference"], "version": item["material_version"]}
                    )
                    self.sources.setdefault(identity, item)
                    sources.append(identity)
            row["sources"] = sorted(set(sources))

        for row in sections:
            scan(row)
        arguments, visited = [], set()
        while pending := [key for key in self.arguments if key not in visited]:
            for key in pending:
                visited.add(key)
                if key.startswith("statement-"):
                    value = self.statements[key]
                    parts = statement_parts(value)
                    row = {
                        "section_id": self.arguments[key],
                        "title": "论述 / " + value["kind"],
                        "title_en": "",
                        "text": render_parts(parts),
                        "lineage": {
                            field: list(value.get(field, []))
                            for field in ("based_on", "supersedes")
                        },
                    }
                    for refs in row["lineage"].values():
                        for target in refs:
                            self.arguments.setdefault(target, "argument-" + digest(target)[:16])
                    scan(row)
                    arguments.append(row)
                    continue
                ref, bid = key.split("::", 1)
                artifact = self.records.value(ref)
                self.evidence.update(artifact.get("evidence", {}))
                self.statements.update(statement_registry(artifact))
                index, block = next(
                    (i, b) for i, b in enumerate(artifact["blocks"]) if b["block_id"] == bid
                )
                row = {
                    "section_id": self.arguments[key],
                    "title": artifact["title"] + " / " + bid,
                    "title_en": "",
                    "text": "\n\n".join(
                        filter(
                            None,
                            [
                                artifact.get("scope_note", ""),
                                artifact_block_text(artifact, index),
                                f"[原始产物](v4/{ref}.json)",
                            ],
                        )
                    ),
                }
                scan(row)
                arguments.append(row)
        source_ids = sorted({v["reference"]["source_id"] for v in self.sources.values()})
        numbers = {sid: i for i, sid in enumerate(source_ids, 1)}
        self.numbering = {
            key: numbers[value["reference"]["source_id"]] for key, value in self.sources.items()
        }
        for row in [*sections, *arguments]:
            row["units"] = self.units(row["text"])
            for field, targets in row.get("lineage", {}).items():
                if targets:
                    row["units"].append(
                        {
                            "unit_id": f"U{len(row['units']) + 1}",
                            "paragraph": row["units"][-1]["paragraph"] + 1,
                            "text": "内容沿袭：" if field == "based_on" else "修订前论述：",
                            "references": [{"kind": field, "target": k} for k in targets],
                            "reference_marks": [
                                f"[相关论证](arguments.md#{self.arguments[k]})" for k in targets
                            ],
                        }
                    )
            row["text"] = render_units(row["units"])
        return arguments

    def units(self, text):
        units = text_units(text)
        for unit in units:
            marks, evidence, relations = [], {}, []
            for key in dict.fromkeys(unit["references"]):
                if key in self.arguments:
                    marks.append(f"[相关论证](arguments.md#{self.arguments[key]})")
                    relations.append(
                        {
                            "kind": "premise" if key.startswith("statement-") else "argument",
                            "target": key,
                        }
                    )
                else:
                    item = self.evidence[key]
                    identity = digest(
                        {"reference": item["reference"], "version": item["material_version"]}
                    )
                    evidence.setdefault(self.numbering[identity], []).append((identity, item))
                    relations.append({"kind": "evidence", "target": identity, "evidence_id": key})
            evidence_marks = []
            for number, rows in evidence.items():
                locators = "; ".join(dict.fromkeys(v.get("locator", "") for _, v in rows)).replace(
                    '"', "'"
                )
                label = f"申请 {number}" if rows[0][1].get("kind") == "application" else str(number)
                evidence_marks.append(f'[{label}](#evidence-{rows[0][0]} "{locators}")')
            unit["references"], unit["reference_marks"] = relations, evidence_marks + marks
        return units
