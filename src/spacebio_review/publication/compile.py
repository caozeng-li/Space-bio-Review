"""Assemble the chief assessment and the selected immutable scientific manuscripts."""

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


def compile_publication(
    assessment, *, evidence, report_selection, contributions, author_sources=None, admission=None
):
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
        "report_selection": report_selection,
        "contributions": [],
        "author_sources": author_sources or {},
        "evidence": {},
    }

    def body(prose, origin, *, paragraph_ids=(), author_paragraph_ids=()):
        nodes = []
        for index, node in enumerate(prose_nodes(prose)):
            source = {**node.origin, **origin, "paragraph": index + 1}
            authored_id = node.origin.get("paragraph_id")
            if authored_id:
                source["author_paragraph_id"] = authored_id
            elif author_paragraph_ids:
                source["author_paragraph_id"] = author_paragraph_ids[index]
            if paragraph_ids:
                source["paragraph_id"] = paragraph_ids[index]
            node.origin.update({key: str(value) for key, value in origin.items()})
            key = "paragraph-" + digest([source, index])[:20]
            node.node_id = key
            audit["paragraphs"][key] = source
            nodes.append(node)
        return nodes

    titles = main_titles(system)
    originals = dict(evidence)
    versions = {
        (row["inquiry_id"], row["contribution_ref"]): row for row in contributions
    }

    def scientific_section(selected):
        contribution = versions[(selected["inquiry_id"], selected["contribution_ref"])]
        note = contribution["note"]
        origin = {
            key: contribution[key]
            for key in ("inquiry_id", "contribution_ref", "author_session_id", "author_text_ref")
        }
        section = Section(
            section_id="scientific-" + digest(selected)[:24],
            title=note["title"],
            kind="scientific",
            blocks=body(
                note["body"], origin,
                paragraph_ids=contribution["paragraph_ids"],
                author_paragraph_ids=contribution["author_paragraph_ids"],
            ),
        )
        originals.update(contribution["evidence"])
        audit["contributions"].append({**origin, "section_id": section.section_id})
        return section

    main_order = ["summary", "project", "scope", "overall", "grades", *system.aspects, "unknowns"]
    for key in main_order:
        title, title_en = titles[key]
        origin = {"assessment": audit["assessment_digest"], "section": key}
        if key == "grades":
            blocks = [Node(kind="grade_table")]
            if document.safety_veto:
                blocks.extend(
                    [
                        Node(kind="label_heading", attrs={"label": "safety_veto"}),
                        *body(
                            assessment["safety_veto"]["reason"],
                            {**origin, "section": "safety_veto"},
                        ),
                    ]
                )
        elif key == "summary":
            blocks = [Node(kind="recommendation"), *body(assessment[key], origin)]
        elif key in system.aspects:
            blocks = [
                Node(kind="grade_line", attrs={"aspect": key}),
                *body(
                    assessment["dimensions"][key]["body"], origin,
                ),
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
                        *body(
                            assessment["support_scope"], {**origin, "section": "support_scope"},
                        ),
                    ]
                )
        document.main.append(Section(section_id=key, title=title, title_en=title_en, blocks=blocks))
        if key == "scope":
            document.main.extend(
                scientific_section(selected) for selected in report_selection["main_sections"]
            )

    document.appendix.extend(
        scientific_section(selected) for selected in report_selection["appendix_sections"]
    )

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
