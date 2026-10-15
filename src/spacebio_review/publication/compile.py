"""Compile the approved chief assessment and selected immutable topic notes."""

from spacebio_review.agents.diagnostics import digest
from spacebio_review.review.evaluation import EvaluationSystem

from .document import (
    Location,
    Node,
    PublicationDocument,
    Section,
    Source,
    walk_nodes,
    walk_sections,
)
from .prose import prose_nodes


def main_titles(system):
    return {
        "summary": ("决策摘要", "Decision summary"),
        "grades": ("指标评分与遴选结论", "Indicator scores and selection outcome")
        if system.score_grades
        else ("维度评分", "Dimension ratings"),
        "project": ("项目理解", "Project understanding"),
        "scope": ("实际评审范围", "Scope of assessment"),
        **{item.id: (item.label, item.label_en) for item in system.criteria},
        "overall": ("综合评价", "Overall assessment"),
        "unknowns": ("依据局限与未知", "Evidence limitations and unknowns"),
    }


def compile_publication(assessment, *, evidence, topics=(), admission=None):
    """Consume bound prose; no authoring, scoring, reference inference or text repair."""
    system = EvaluationSystem.model_validate(assessment["evaluation_system"])
    document = PublicationDocument(
        main=[],
        appendix=[],
        recommendation=assessment["recommendation"],
        evaluation_system=system,
        grades={}
        if system.score_grades
        else {
            key: {name: row[name] for name in ("grade", "confidence")}
            for key, row in assessment["dimensions"].items()
        },
        scores={key: row["score"] for key, row in assessment["dimensions"].items()}
        if system.score_grades
        else {},
        total_score=assessment.get("total_score"),
        score_grade=assessment.get("score_grade"),
        safety_veto=assessment.get("safety_veto", {}).get("applies", False),
        admission=admission or {},
    )
    audit = {
        "assessment_digest": digest(assessment),
        "paragraphs": {},
        "topics": [],
        "evidence": {},
    }

    def body(prose, origin):
        nodes = prose_nodes(prose)
        for index, node in enumerate(nodes, 1):
            key = "paragraph-" + digest([origin, index])[:20]
            node.node_id = key
            audit["paragraphs"][key] = {**origin, "paragraph": index}
        return nodes

    for key, (title, title_en) in main_titles(system).items():
        origin = {"assessment": audit["assessment_digest"], "section": key}
        if key == "grades":
            blocks = [Node(kind="grade_table")]
            if document.safety_veto:
                blocks.extend(
                    [
                        Node(kind="label_heading", attrs={"label": "safety_veto"}),
                        *body(assessment["safety_veto"]["reason"], origin),
                    ]
                )
        elif key == "summary":
            blocks = [Node(kind="recommendation"), *body(assessment[key], origin)]
        elif key in system.aspects:
            blocks = [
                Node(kind="grade_line", attrs={"aspect": key}),
                *body(assessment["dimensions"][key]["body"], origin),
            ]
        elif key == "unknowns":
            blocks = []
            for index, row in enumerate(assessment[key], 1):
                blocks.append(
                    Node(
                        kind="heading",
                        attrs={"level": 3},
                        children=[Node(kind="text", text=row["title"])],
                    )
                )
                blocks.extend(body(row["text"], {**origin, "item": index}))
        else:
            blocks = body(assessment[key], origin)
            if key == "scope":
                blocks.insert(0, Node(kind="evaluation_scope"))
            if key == "overall" and "support_scope" in assessment:
                blocks.extend(
                    [
                        Node(kind="label_heading", attrs={"label": "support_scope"}),
                        *body(assessment["support_scope"], {**origin, "section": "support_scope"}),
                    ]
                )
        document.main.append(Section(section_id=key, title=title, title_en=title_en, blocks=blocks))

    originals = dict(evidence)
    versions = {(row["topic_id"], row["note_ref"]): row for row in topics}
    for index, selected in enumerate(assessment["appendix_topics"], 1):
        topic = versions[(selected["topic_id"], selected["note_ref"])]
        note = topic["note"]
        origin = {"topic_id": topic["topic_id"], "note_ref": topic["note_ref"]}
        blocks = body(note["body"], origin)
        if note.get("unknowns"):
            blocks.append(Node(kind="label_heading", attrs={"label": "unknowns"}))
            blocks.extend(
                Node(kind="paragraph", children=[Node(kind="text", text=text)])
                for text in note["unknowns"]
            )
        document.appendix.append(
            Section(section_id=f"topic-{index}", title=note["title"], kind="topic", blocks=blocks)
        )
        originals.update(topic["readings"])
        audit["topics"].append(origin)

    for section in walk_sections(document.main + document.appendix):
        for node in walk_nodes(section.blocks):
            if node.kind != "citation":
                continue
            value = originals[node.target]
            source = value["reference"]["source_id"]
            document.sources[source] = Source(
                title=value["title"],
                kind=value["kind"],
                url=value.get("url") or "",
                authors=value.get("authors") or [],
                publication_date=value.get("publication_date") or "",
            )
            document.locations[node.target] = Location(
                source=source,
                locator=value.get("locator") or "",
                reading_scope=value.get("reading_scope") or "",
            )
            document.numbering.setdefault(source, len(document.numbering) + 1)
            node.kind = "evidence"
            audit["evidence"][node.target] = value
    return document, audit
