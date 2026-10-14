"""Compile authoritative main prose and scoped authorship into the publication tree."""

from spacebio_review.review.project import REVIEW_ASPECTS

from .publication_document import (
    EN_LABELS,
    LABELS,
    Location,
    Node,
    PublicationDocument,
    Section,
    Source,
    walk_nodes,
    walk_sections,
)
from .publication_prose import prose_nodes

CONDITION_TIMINGS = ("before_support", "implementation_milestone", "recommendation")

MAIN_TITLES = {
    "summary": ("决策摘要", "Decision summary"),
    "grades": ("维度评分", "Dimension ratings"),
    "project": ("项目理解", "Project understanding"),
    "scope": ("实际评审范围", "Scope of assessment"),
    "overall": ("整体判断", "Overall assessment"),
    **{key: (LABELS[key], EN_LABELS[key]) for key in REVIEW_ASPECTS},
    "conditions": ("支持条件", "Support conditions"),
    "alternatives": ("替代方案", "Alternatives"),
    "unknowns": ("未决事项与外部依赖", "Open matters and external dependencies"),
}


def compile_publication(assessment, authored_sections, frozen):
    document = PublicationDocument(
        format_version="publication-2",
        main=[],
        appendix=[],
        recommendation=assessment["recommendation"],
        grades={
            key: {name: row[name] for name in ("grade", "confidence")}
            for key, row in assessment["dimensions"].items()
        },
    )
    for key, (title, title_en) in MAIN_TITLES.items():
        if key == "grades":
            blocks = [Node(kind="grade_table")]
        elif key == "summary":
            blocks = [Node(kind="recommendation"), *prose_nodes(authored_sections[key])]
        elif key in REVIEW_ASPECTS:
            blocks = [
                Node(kind="grade_line", attrs={"aspect": key}),
                *prose_nodes(assessment["dimensions"][key]["body"]),
            ]
        elif key == "conditions":
            blocks = []
            for timing in CONDITION_TIMINGS:
                conditions = [row for row in assessment[key] if row["timing"] == timing]
                if conditions:
                    blocks.append(Node(kind="label_heading", attrs={"label": timing}))
                    blocks.append(
                        Node(
                            kind="ordered_list",
                            children=[
                                Node(kind="list_item", children=prose_nodes(row["text"]))
                                for row in conditions
                            ],
                        )
                    )
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
            if key == "overall":
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
