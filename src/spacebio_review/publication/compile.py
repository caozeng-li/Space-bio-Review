"""Publish one authored report with a code-owned score table and source numbering."""

from spacebio_review.agents.diagnostics import digest
from spacebio_review.review.evaluation import EvaluationSystem

from .document import (
    Location,
    Node,
    PublicationDocument,
    Section,
    Source,
    walk_nodes,
)
from .prose import prose_nodes


def compile_publication(assessment, *, evidence, author_sources=None, admission=None):
    """Consume bound prose; no authoring, scoring, reference inference or text repair."""
    system = EvaluationSystem.model_validate(assessment["evaluation_system"])
    document = PublicationDocument(
        main=[],
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
        "author_sources": author_sources or {},
        "evidence": {},
    }

    def body(prose, origin):
        nodes = []
        for index, node in enumerate(prose_nodes(prose)):
            source = {**node.origin, **origin, "paragraph": index + 1}
            authored_id = node.origin.get("paragraph_id")
            if authored_id:
                source["author_paragraph_id"] = authored_id
            node.origin.update({key: str(value) for key, value in origin.items()})
            key = "paragraph-" + digest([source, index])[:20]
            node.node_id = key
            audit["paragraphs"][key] = source
            nodes.append(node)
        return nodes

    originals = dict(evidence)
    document.main = [
        Section(
            section_id="body",
            title="",
            blocks=body(
                assessment["body"], {"assessment": audit["assessment_digest"], "section": "body"}
            ),
        ),
        Section(
            section_id="grades",
            title="指标评分与遴选结论" if system.score_grades else "维度评分与评审结论",
            title_en="Scores and review outcome",
            blocks=[Node(kind="grade_table")],
        ),
    ]

    for section in document.main:
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
