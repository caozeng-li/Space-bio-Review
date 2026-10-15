"""Exact evidence identity through literature persistence and parent projection."""

import pytest
from pydantic import BaseModel

from spacebio_review.evidence.knowledge import KnowledgeAnswer
from spacebio_review.evidence.submissions import resolve_basis, scientific_contract
from spacebio_review.v4.context import TaskContext
from spacebio_review.v4.handoff import knowledge_artifact
from spacebio_review.v4.records import Records


def professional(blocks, *, evidence_map=None, scope="Shared condition", scope_links=()):
    return {
        "title": "Professional review",
        "scope_note": scope,
        "scope_links": list(scope_links),
        "blocks": blocks,
        "evidence": evidence_map or {},
    }


def test_actual_argument_closure_preserves_basis_without_inheriting_direct_citations(tmp_path):
    from spacebio_review.v4.assessment import assessment_request
    from spacebio_review.v4.assessment_input import freeze_review, select_materials
    from spacebio_review.v4.report_authoring import author_materials

    records = Records(tmp_path)
    basis = {
        "text": "Model paraphrase, not the raw quote",
        "kind": "external",
        "evidence_refs": ["reading"],
        "support_explanation": "Fragment-only support",
        "quantitative_basis": {"amount": "37", "unit": "C", "origin": "quoted"},
    }
    child = knowledge_artifact(answer(basis, {"reading": evidence()}), "child", "Finding", [])
    child["blocks"].append({"block_id": "unused", "text": "UNUSED_NEIGHBOR", "result_refs": []})
    child_ref = records.put("knowledge", child)
    unused_ref = records.put(
        "knowledge", professional([{"block_id": "b1", "text": "DEPENDENCY_ONLY"}])
    )
    parent = professional(
        [
            {
                "block_id": "b1",
                "text": "Interpretation [@" + child_ref + "::f1]",
                "result_refs": [child_ref + "::f1"],
            },
            {
                "block_id": "b2",
                "text": "Independent use [@reading][@" + child_ref + "::f1]",
                "evidence_refs": ["reading"],
                "result_refs": [child_ref + "::f1"],
            },
        ],
        evidence_map={"reading": evidence()},
    )
    parent_ref = records.put("research", parent, [child_ref, unused_ref])
    frozen = freeze_review(records, [parent_ref], [], {"prompt": "Review"})
    lookup = {
        (row["artifact"], row["block"]): oid
        for oid, row in frozen["provenance"]["opinions"].items()
    }
    first, second, finding = (
        lookup[(parent_ref, "b1")],
        lookup[(parent_ref, "b2")],
        lookup[(child_ref, "f1")],
    )
    assert len(lookup) == 3
    assert "UNUSED_NEIGHBOR" not in str(frozen)
    assert "DEPENDENCY_ONLY" not in str(frozen)
    materials = frozen["materials"]
    assert len(materials["evidence"]) == 1
    assert materials["opinions"][first]["text"]["parts"] == [
        {"text": "Interpretation ", "cites": [], "related_opinions": [finding]}
    ]
    assert materials["opinions"][second]["text"]["parts"][0]["cites"] == ["E1"]
    assert materials["opinions"][finding]["basis"][0]["text"]["parts"][0]["text"] == basis["text"]
    assert materials["evidence"]["E1"]["quote"] == evidence()["quote"]
    selected = select_materials(frozen, [first])
    assert set(selected["opinions"]) == {first, finding}
    assert selected["opinions"][finding]["basis"][0]["evidence_ids"] == ["E1"]
    assert selected["opinion_contexts"]["G2"]["unknowns"]
    from spacebio_review.v4.assessment_input import review_materials

    assert assessment_request(frozen)[1] == review_materials(frozen)
    assessment = {
        "dimensions": {},
        "overall": {"paragraphs": []},
        "recommendation": "revise",
        "appendix_topics": [
            {
                "title": "Evidence",
                "focus": "Conditions",
                "main_sections": ["overall"],
                "opinion_ids": [first],
            }
        ],
    }
    appendix = author_materials(frozen, assessment, ["topic_1"])
    assert appendix["materials"] == selected
    assert select_materials(frozen, [], evidence_ids=["E1"])["opinion_contexts"] == {}


def test_scope_links_and_finding_links_follow_only_used_blocks(tmp_path):
    from spacebio_review.v4.assessment_input import freeze_review, select_materials

    records = Records(tmp_path)
    leaf = records.put(
        "research",
        professional(
            [
                {"block_id": "used", "text": "Leaf [@leaf]", "evidence_refs": ["leaf"]},
                {"block_id": "unused", "text": "Unused leaf"},
            ],
            evidence_map={"leaf": evidence("leaf")},
        ),
    )
    mid = records.put(
        "knowledge",
        professional(
            [
                {"block_id": "f1", "text": "Derived", "result_refs": [leaf + "::used"]},
            ]
        ),
    )
    root = records.put(
        "research",
        professional(
            [
                {"block_id": "b1", "text": "Expert claim"},
            ],
            scope="Only when [@" + mid + "::f1]",
            scope_links=[mid + "::f1"],
        ),
    )
    frozen = freeze_review(records, [root], [], {})
    assert len(frozen["materials"]["opinions"]) == 3
    assert frozen["materials"]["opinion_contexts"]["G1"]["related_opinions"] == ["O2"]
    selected = select_materials(frozen, ["O1"])
    assert set(selected["opinions"]) == {"O1", "O2", "O3"}
    assert selected["evidence"]["E1"]["quote"] == evidence("leaf")["quote"]


def test_two_exact_snapshots_and_inherited_statement_scope_survive_freezing(tmp_path):
    from spacebio_review.v4.assessment_input import freeze_review, select_materials

    records = Records(tmp_path)
    raw = professional(
        [
            {"block_id": "b1", "text": "Rendered text [@old]", "evidence_refs": ["old"]},
        ],
        evidence_map={"old": evidence(), "new": evidence("v2")},
    )
    raw["statement_content"] = {
        "/blocks/0": {"selected": ["statement-2"]},
        "/registry": {
            "statements": {
                "statement-1": {
                    "parts": [{"text": "Earlier reading", "cites": ["old"]}],
                    "scope": [],
                    "premise_ids": [],
                },
                "statement-2": {
                    "parts": [{"text": "New interpretation", "cites": ["new"]}],
                    "scope": [{"text": "Only in measured condition", "cites": ["new"]}],
                    "premise_ids": ["statement-1"],
                },
            }
        },
    }
    raw["citation_content"] = {
        "/scope_note": [{"text": "Shared condition", "cites": []}],
        "/blocks/0": [{"text": "Interpretation", "cites": ["new", "statement-1"]}],
    }
    ref = records.put("research", raw)
    frozen = freeze_review(records, [ref], [], {})
    selected = select_materials(frozen, ["O1"])
    assert len(selected["evidence"]) == 2
    row = selected["opinions"]["O1"]
    assert row["text"]["parts"][0]["related_statements"] == ["statement-1"]
    assert (
        row["statements"]["statement-2"]["scope"]["parts"][0]["text"]
        == "Only in measured condition"
    )
    assert selected["opinion_contexts"]["G1"]["origin"] == "inherited"


def test_same_quote_different_snapshot_is_not_deduplicated(tmp_path):
    from spacebio_review.v4.assessment_input import freeze_review

    records = Records(tmp_path)
    second = {**evidence(), "material_version": "another-snapshot"}
    ref = records.put(
        "research",
        professional(
            [
                {"block_id": "b1", "text": "Both versions [@one][@two]"},
            ],
            evidence_map={"one": evidence(), "two": second},
        ),
    )
    frozen = freeze_review(records, [ref], [], {})
    assert len(frozen["materials"]["evidence"]) == 2


def evidence(version="v1"):
    return {
        "reference": {"source_id": "paper", "passage_id": "p1", "visual_id": None},
        "title": "Paper",
        "kind": "external",
        "locator": "p1",
        "quote": "Only at 37 C: " + version,
        "material_version": version,
        "reading_scope": "text",
    }


def answer(basis, evidence_map):
    return {
        "contract_version": "scientific-3",
        "findings": [{"explanation": "Conditional finding", "basis": [basis]}],
        "applicability": ["Only at 37 C"],
        "unknowns": ["Other temperatures untested"],
        "reading_scope": "retained",
        "stop_reason": "done",
        "evidence_resolution": evidence_map,
    }


def test_exact_basis_identity_survives_child_persistence_and_parent_aliases(tmp_path):
    basis = resolve_basis(
        [{"evidence_handle": "E1", "text": "Observation", "support_explanation": "Direct"}],
        {"E1": evidence()},
        identities={"E1": "ev-child"},
    )[0]
    value = KnowledgeAnswer.model_validate(answer(basis, {"ev-child": evidence()}))
    saved = value.model_dump(mode="json")
    assert saved["findings"][0]["basis"][0]["evidence_refs"] == ["ev-child"]
    records = Records(tmp_path)
    artifact = knowledge_artifact(saved, "child-task", "Conditions?", ["Limited search"])
    ref = records.put("knowledge", artifact)
    assert records.put("knowledge", artifact) == ref
    context = TaskContext(compact_sources=True)
    context.add({"ev-parent": evidence("parent")}, "E")
    context.add(records.result_blocks([ref]), "R")
    context.collect(list(context.results.values()))
    assert context.aliases["ev-child"] == "E2"
    view = context.source_view()["research"][ref]
    assert view["origin"] == "delegated"
    assert view["unknowns"] == ["Other temperatures untested"]
    assert view["limitations"] == ["Limited search"]
    assert view["scope"] == "Only at 37 C"
    assert view["arguments"]["R1"]["sources"] == ["E2"]
    assert "evidence_refs" not in str(view)
    assert "result_refs" not in str(view)


def test_legacy_position_matches_only_unique_snapshot():
    basis = {
        "text": "Observation",
        "kind": "external",
        "support_explanation": "Direct",
        "source_refs": [evidence()["reference"]],
    }
    unique = knowledge_artifact(answer(basis, {"ev1": evidence()}), "child", "Title", [])
    assert unique["blocks"][0]["evidence_refs"] == ["ev1"]
    ambiguous = knowledge_artifact(
        answer(basis, {"ev1": evidence(), "ev2": evidence("v2")}), "child", "Title", []
    )
    assert ambiguous["blocks"][0]["evidence_refs"] == []
    assert ambiguous["blocks"][0]["basis"][0]["source_refs"] == basis["source_refs"]


def test_explicit_version_is_not_reselected_by_position():
    basis = {
        "text": "Observation",
        "kind": "external",
        "support_explanation": "Direct",
        "source_refs": [evidence()["reference"]],
        "evidence_refs": ["ev2"],
    }
    artifact = knowledge_artifact(
        answer(basis, {"ev1": evidence(), "ev2": evidence("v2")}), "child", "Title", []
    )
    assert artifact["blocks"][0]["evidence_refs"] == ["ev2"]
    assert artifact["evidence"]["ev2"]["material_version"] == "v2"


def test_structured_basis_projects_its_binding_without_inline_markers(tmp_path):
    basis = {
        "text": "N-IDR only, at 10% PEG.",
        "kind": "external",
        "evidence_refs": ["exact"],
        "support_explanation": "Measured fragment",
        "quantitative_basis": {"amount": "10", "unit": "% PEG", "origin": "quoted"},
    }
    records = Records(tmp_path)
    ref = records.put(
        "knowledge",
        knowledge_artifact(answer(basis, {"exact": evidence()}), "child", "Fragment", []),
    )
    context = TaskContext(compact_sources=True, structured_refs=True)
    context.add(records.result_blocks([ref]), "R")
    context.collect(list(context.results.values()))
    row = context.source_view()["research"][ref]["arguments"]["R1"]["basis"][0]
    assert row["parts"] == [{"text": basis["text"], "cites": ["E1"]}]
    assert "sources" not in row
    assert row["quantity"] == basis["quantitative_basis"]


def test_multiple_read_fragments_keep_individual_exact_identities():
    from types import SimpleNamespace

    scope = SimpleNamespace(submission_choices=lambda: {"one": evidence(), "two": evidence("v2")})
    session = SimpleNamespace(whole_units=True, state=SimpleNamespace(stop_reason="done"))
    c = scientific_contract(scope, KnowledgeAnswer, session=session)
    value = {
        "findings": [
            {
                "paragraphs": [
                    {"parts": [{"text": "Under measured conditions", "cites": ["E1", "E2"]}]}
                ],
            }
        ],
        "scope": {"paragraphs": [{"parts": [{"text": "Only that construct", "cites": []}]}]},
        "unknowns": [],
    }
    product = c.product(c.parse(value)).model_dump()
    assert product["findings"][0]["paragraphs"][0]["parts"][0]["cites"] == ["one", "two"]
    assert set(product["evidence_resolution"]) == {"one", "two"}


def test_literature_contract_persists_prose_and_scope_identities(tmp_path):
    from types import SimpleNamespace

    scope = SimpleNamespace(submission_choices=lambda: {"ev1": evidence(), "ev2": evidence("v2")})
    session = SimpleNamespace(state=SimpleNamespace(stop_reason="done"))
    contract = scientific_contract(scope, KnowledgeAnswer, session=session)
    draft = contract.parse(
        {
            "findings": [
                {
                    "paragraphs": [
                        {"parts": [{"text": "Conditional observation", "cites": ["E2"]}]}
                    ],
                }
            ],
            "scope": {"paragraphs": [{"parts": [{"text": "Only here", "cites": ["E1"]}]}]},
            "unknowns": ["Other conditions unknown"],
        }
    )
    saved = contract.product(draft).model_dump(mode="json")
    restored = KnowledgeAnswer.model_validate(saved).model_dump(mode="json")
    assert restored["findings"][0]["paragraphs"][0]["parts"][0]["cites"] == ["ev2"]
    artifact = knowledge_artifact(restored, "child", "Scoped", [])
    assert artifact["scope_links"] == ["ev1"]
    records = Records(tmp_path)
    ref = records.put("knowledge", artifact)
    parent = TaskContext(compact_sources=True, structured_refs=True)
    parent.add(records.result_blocks([ref]), "R")
    parent.collect(list(parent.results.values()))
    assert set(parent.evidence) == {"ev1", "ev2"}


@pytest.mark.asyncio
async def test_tool_read_handle_matches_actual_content_not_first_location():
    from spacebio_review.tools import Tool, ToolRegistry, ToolResult

    class Empty(BaseModel):
        pass

    async def read(args):
        return ToolResult(
            ok=True,
            content={
                "source_id": "paper",
                "passages": [{"passage_id": "p1", "text": evidence("v2")["quote"]}],
            },
        )

    tools = ToolRegistry([Tool("read", "Read", Empty, read)])
    tools.evidence_views.append(lambda: {"ev1": evidence(), "ev2": evidence("v2")})
    result = await tools.execute("read", "{}")
    assert result.content["passages"][0]["evidence_handle"] == "ev2"
