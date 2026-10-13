from copy import deepcopy

from test_report_reference_relations import fixture

from spacebio_review.v4.publication import build_publication
from spacebio_review.v4.publication_document import (
    parse_text,
    plain_text,
    summary_input,
    walk_nodes,
    walk_sections,
)
from spacebio_review.v4.publication_render import Renderer


def nodes(document):
    return [
        n for s in walk_sections(document.main + document.appendix) for n in walk_nodes(s.blocks)
    ]


def test_tree_keeps_exact_citations_and_one_canonical_analysis(tmp_path):
    _, records = fixture(tmp_path)
    records.rows["assessment"]["blocks"][0]["text"] += " Again[@prior::b1]."
    doc, audit = build_publication(records, "assessment")
    current = next(s for s in doc.main if s.section_id == "overall")
    refs = [n for n in walk_nodes(current.blocks) if n.kind == "evidence"]
    assert len(refs) == 1
    assert audit["evidence"][refs[0].target]["evidence_id"] == "exact"
    related = [n for n in walk_nodes(current.blocks) if n.kind == "crossref"]
    assert len({n.target for n in related}) == 1
    assert len(doc.appendix[0].sections) == 1
    assert not any(n.kind == "citation" for n in nodes(doc))


def test_markdown_adapter_preserves_structure_and_literal_markers():
    parsed = parse_text(
        "## Title\n\n- **bold** [@e1]\n- `[@literal]`\n\n\\[@escaped]\n\n"
        "| A | B |\n|---|---|\n| 1 | 2 |"
    )
    kinds = [n.kind for n in walk_nodes(parsed)]
    assert {"heading", "bullet_list", "list_item", "strong", "table"} <= set(kinds)
    assert [n.target for n in walk_nodes(parsed) if n.kind == "citation"] == ["e1"]


def test_document_is_independent_of_audit_changes_and_unused_history(tmp_path):
    _, records = fixture(tmp_path)
    frozen = {
        "assessment": "assessment",
        "requests": [{"status": "deferred", "reason": "x", "request": {"question": "Open"}}],
    }
    first, _ = build_publication(records, "assessment", frozen)
    frozen["requests"][0]["reason"] = "Multiple R decisions"
    records.rows["prior"]["task_id"] = "new/internal/task"
    for value in records.rows.values():
        for evidence in value.get("evidence", {}).values():
            evidence["material_version"] = "another-snapshot"
            evidence["snapshot_path"] = "D:/other/local/snapshot.json"
    records.rows["unused"] = {"blocks": [{"text": "HUGE" * 10000}]}
    second, _ = build_publication(records, "assessment", frozen)
    assert first == second
    assert summary_input(first) == summary_input(second)


def test_legacy_aggregate_references_are_still_block_level(tmp_path):
    _, records = fixture(tmp_path, explicit=False)
    doc, _ = build_publication(records, "assessment")
    block = doc.appendix[0].sections[0].blocks[-1]
    assert len(block.children) == 2
    assert not any(n.kind == "evidence" for n in walk_nodes([block.children[0]]))
    assert any(n.kind == "evidence" for n in walk_nodes([block.children[1]]))


def test_c_lineage_is_not_a_public_argument_or_evidence(tmp_path):
    _, records = fixture(tmp_path)
    old = {"kind": "external", "parts": [{"text": "Old", "cites": ["neighbor"]}]}
    new = {
        "kind": "interpretation",
        "parts": [{"text": "Interpretation", "cites": []}],
        "scope": [{"text": "Only at 2 min", "cites": []}],
        "based_on": ["statement-old"],
        "supersedes": ["statement-old"],
    }
    records.rows["assessment"]["blocks"][0]["text"] = "Current[@statement-new]"
    records.rows["assessment"]["statement_content"] = {
        "/registry": {"statements": {"statement-new": new, "statement-old": old}}
    }
    doc, audit = build_publication(records, "assessment")
    supplement = doc.appendix[-1].sections[0]
    assert not any(n.kind == "evidence" for n in walk_nodes(supplement.blocks))
    assert "statement-old" not in str(doc.model_dump())
    assert audit["lineage"]["statement-new"]["based_on"] == ["statement-old"]


def test_same_source_has_one_number_but_all_locations(tmp_path):
    _, records = fixture(tmp_path)
    records.rows["prior"]["evidence"]["neighbor"]["reference"]["source_id"] = "exact"
    doc, audit = build_publication(records, "assessment")
    source = next(key for key, value in audit["sources"].items() if value == "exact")
    assert len([loc for loc in doc.locations.values() if loc.source == source]) == 2
    assert doc.numbering[source] == 1


def test_statement_selected_and_referenced_has_existing_target(tmp_path):
    _, records = fixture(tmp_path)
    records.rows["prior"]["statement_content"] = {
        "/blocks/0": {
            "selected": ["statement-one"],
            "statements": {"statement-one": {"parts": [{"text": "First", "cites": ["exact"]}]}},
        }
    }
    records.rows["assessment"]["statement_content"] = deepcopy(
        records.rows["prior"]["statement_content"]
    )
    doc, _ = build_publication(records, "assessment")
    assert len(doc.appendix) == 1


def test_main_target_is_not_copied_into_appendix(tmp_path):
    _, records = fixture(tmp_path)
    records.rows["assessment"]["aspect_refs"] = {"safety": ["prior"]}
    records.rows["assessment"]["professional_refs"] = []
    doc, _ = build_publication(records, "assessment")
    target = next(iter(doc.targets.values()))
    assert target.document == "main"
    assert target.section == "aspect-safety"
    assert doc.appendix == []
    overall = next(s for s in doc.main if s.section_id == "overall")
    english = Renderer(doc, "en", available_main={"overall"}).render("en", [overall])
    assert "Section 3, paragraph 1" in english
    assert "report.md#aspect-safety-p1" in english


def test_intermediate_blocks_share_one_entry_and_one_scope(tmp_path):
    _, records = fixture(tmp_path)
    records.rows["assessment"]["professional_refs"] = []
    records.rows["prior"]["blocks"].append(
        {"block_id": "b2", "text": "Second block", "evidence_refs": [], "result_refs": []}
    )
    records.rows["assessment"]["blocks"][0]["text"] = (
        "First[@prior::b1]. Second[@prior::b2]. Reused[@prior::b1]."
    )
    doc, _ = build_publication(records, "assessment")
    assert len(doc.appendix) == len(doc.appendix[0].sections) == 1
    supplement = doc.appendix[0].sections[0]
    assert [n.kind for n in supplement.blocks] == ["scope", "block", "block"]
    assert plain_text(supplement.blocks).count("Common scope") == 1
    assert {t.paragraph for t in doc.targets.values()} == {1, 2}
    assert len({t.section for t in doc.targets.values()}) == 1


def test_c_support_and_scientific_scope_remain_with_statement(tmp_path):
    _, records = fixture(tmp_path)
    records.rows["assessment"]["blocks"][0]["text"] = "Interpretation[@statement-one]"
    records.rows["assessment"]["statement_content"] = {
        "/registry": {
            "statements": {
                "statement-one": {
                    "parts": [{"text": "Observed change", "cites": ["exact"]}],
                    "support": "Only supports this comparison",
                    "scope": [{"text": "At 2 min", "cites": []}],
                }
            }
        }
    }
    doc, _ = build_publication(records, "assessment")
    supplement = doc.appendix[-1].sections[0]
    text = plain_text(supplement.blocks)
    assert all(t in text for t in ("Observed change", "Only supports this comparison", "At 2 min"))
    assert len([n for n in walk_nodes(supplement.blocks) if n.kind == "evidence"]) == 1


def test_intermediate_analysis_joins_its_known_topic(tmp_path):
    _, records = fixture(tmp_path)
    records.rows["intermediate"] = {
        "title": "Combined",
        "input_refs": ["prior"],
        "blocks": [{"block_id": "b1", "text": "Combined analysis", "result_refs": []}],
    }
    records.rows["assessment"]["blocks"][0]["text"] = "Conclusion[@intermediate::b1]"
    doc, _ = build_publication(records, "assessment")
    assert len(doc.appendix) == 1
    assert [s.kind for s in doc.appendix[0].sections] == ["analysis", "supplement"]
    assert "补充综合分析" in doc.appendix[0].sections[1].title


def test_duplicate_perspectives_remain_distinct_entries(tmp_path):
    _, records = fixture(tmp_path)
    records.rows["second"] = deepcopy(records.rows["prior"])
    records.rows["assessment"]["professional_refs"].append("second")
    frozen = {
        "assessment": "assessment",
        "publication_catalog": [
            {
                "title": "Question",
                "analyses": [
                    {"ref": "prior", "perspective": "Optics"},
                    {"ref": "second", "perspective": "Optics"},
                ],
            }
        ],
    }
    doc, _ = build_publication(records, "assessment", frozen)
    analyses = doc.appendix[0].sections
    assert len(analyses) == 2
    assert analyses[0].title != analyses[1].title
    assert all("Optics" in analysis.title for analysis in analyses)
