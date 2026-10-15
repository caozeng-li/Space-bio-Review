"""Compile authoritative main prose and scoped authorship into the publication tree."""

from spacebio_review.review.evaluation import GENERAL
from spacebio_review.review.project import REVIEW_ASPECTS

from .assessment import assessment_system
from .publication_document import (
    Location,
    Node,
    PublicationDocument,
    Section,
    Source,
    walk_nodes,
    walk_sections,
)
from .publication_prose import prose_nodes


def main_titles(system=GENERAL):
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


MAIN_TITLES = main_titles()


def compile_publication(assessment, authored_sections, frozen):
    system = assessment_system(assessment)
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
        admission=frozen["materials"].get("admission", {}),
    )
    for key, (title, title_en) in main_titles(system).items():
        if key == "grades":
            blocks = [Node(kind="grade_table")]
            if document.safety_veto:
                blocks.extend(
                    [
                        Node(kind="label_heading", attrs={"label": "safety_veto"}),
                        *prose_nodes(assessment["safety_veto"]["reason"]),
                    ]
                )
        elif key == "summary":
            blocks = [Node(kind="recommendation"), *prose_nodes(authored_sections[key])]
        elif key == "overall" and system.score_grades:
            blocks = prose_nodes(authored_sections[key])
        elif key in system.aspects:
            blocks = [
                Node(kind="grade_line", attrs={"aspect": key}),
                *prose_nodes(assessment["dimensions"][key]["body"]),
            ]
        elif key == "unknowns":
            blocks = []
            for row in assessment[key]:
                blocks.append(
                    Node(
                        kind="heading",
                        attrs={"level": 3},
                        children=[Node(kind="text", text=row["title"])],
                    )
                )
                blocks.extend(prose_nodes(row["text"]))
        else:
            blocks = prose_nodes(assessment[key])
            if key == "scope":
                blocks.insert(0, Node(kind="evaluation_scope"))
            if key == "overall" and "support_scope" in assessment:
                blocks.extend(
                    [
                        Node(kind="label_heading", attrs={"label": "support_scope"}),
                        *prose_nodes(assessment["support_scope"]),
                    ]
                )
        document.main.append(Section(section_id=key, title=title, title_en=title_en, blocks=blocks))
    for i, topic in enumerate(assessment["appendix_topics"], 1):
        key = f"topic_{i}"
        document.appendix.append(
            Section(
                section_id=f"topic-{i}",
                title=topic["title"],
                kind="topic",
                blocks=prose_nodes(authored_sections[key]),
            )
        )
    audit = {"opinions": frozen["provenance"]["opinions"], "evidence": {}, "sources": {}}
    for section in walk_sections(document.main + document.appendix):
        for node in walk_nodes(section.blocks):
            if node.kind != "citation":
                continue
            key = node.target
            value = frozen["materials"]["evidence"][key]
            source = value["source"]
            document.sources[source] = Source.model_validate(frozen["materials"]["sources"][source])
            document.locations[key] = Location(
                source=source, locator=value["locator"], reading_scope=value["reading_scope"]
            )
            document.numbering.setdefault(source, len(document.numbering) + 1)
            node.kind = "evidence"
            audit["evidence"][key] = frozen["provenance"]["evidence"][key]
            audit["sources"][source] = frozen["materials"]["sources"][source]
    return document, audit


def compile_scientific_publication(scientific, decision, frozen):
    """Publish the final scientific prose unchanged; decisions never rewrite it."""
    assessment = {
        **decision,
        "project": scientific["project"],
        "scope": scientific["scope"],
        "dimensions": {
            key: {"body": scientific["dimensions"][key], **decision["grades"][key]}
            for key in REVIEW_ASPECTS
        },
        "appendix_topics": [{"title": row["title"]} for row in scientific["topics"]],
    }
    sections = {
        "summary": decision["summary"],
        **{f"topic_{i}": row["body"] for i, row in enumerate(scientific["topics"], 1)},
    }
    document, audit = compile_publication(assessment, sections, frozen)
    audit["decision_basis"] = [
        {"condition": i, "paragraphs": row["based_on"]}
        for i, row in enumerate(decision["conditions"], 1)
    ]
    return document, audit


def compile_review_result(records, result_ref):
    """Consume a complete result without exposing its two contracts to callers."""
    from .scientific_review import RESULT_VERSION

    result = records.value(result_ref)
    if result.get("format_version") != RESULT_VERSION or result.get("status") != "completed":
        raise ValueError("review_result_not_completed")
    return compile_scientific_publication(
        records.value(result["scientific"]),
        records.value(result["decision"]),
        records.value(result["frozen_review"]),
    )
