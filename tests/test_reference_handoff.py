"""Exact evidence identity through literature persistence and parent projection."""

import pytest
from pydantic import BaseModel

from spacebio_review.evidence.knowledge import KnowledgeAnswer
from spacebio_review.evidence.submissions import resolve_basis, scientific_contract
from spacebio_review.v4.context import TaskContext
from spacebio_review.v4.handoff import knowledge_artifact
from spacebio_review.v4.records import Records


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
    basis = {"text": "N-IDR only, at 10% PEG.", "kind": "external",
             "evidence_refs": ["exact"], "support_explanation": "Measured fragment",
             "quantitative_basis": {"amount": "10", "unit": "% PEG", "origin": "quoted"}}
    records = Records(tmp_path)
    ref = records.put("knowledge", knowledge_artifact(answer(basis, {"exact": evidence()}),
                                                     "child", "Fragment", []))
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
    value = {"findings": [{"explanation": "Under measured conditions", "basis": [{
        "evidence_handles": ["E1", "E2"], "text": "Conditions and value",
        "support_explanation": "Split caption", "quantitative_basis": None}]}],
        "applicability": ["Only that construct"], "open_questions": []}
    product = c.product(c.parse(value)).model_dump()
    assert product["findings"][0]["basis"][0]["evidence_refs"] == ["one", "two"]
    assert set(product["evidence_resolution"]) == {"one", "two"}


def test_literature_contract_persists_basis_and_scope_identities(tmp_path):
    from types import SimpleNamespace

    scope = SimpleNamespace(submission_choices=lambda: {"ev1": evidence(), "ev2": evidence("v2")})
    session = SimpleNamespace(state=SimpleNamespace(stop_reason="done"))
    contract = scientific_contract(scope, KnowledgeAnswer, session=session)
    draft = contract.parse(
        {
            "findings": [
                {
                    "explanation": "Conditional",
                    "basis": [
                        {
                            "evidence_handle": "E2",
                            "text": "Observation",
                            "support_explanation": "Direct",
                            "quantitative_basis": None,
                        }
                    ],
                }
            ],
            "applicability": ["Only here [@E1]"],
            "open_questions": ["Other conditions unknown"],
        }
    )
    saved = contract.product(draft).model_dump(mode="json")
    restored = KnowledgeAnswer.model_validate(saved).model_dump(mode="json")
    assert restored["findings"][0]["basis"][0]["evidence_refs"] == ["ev2"]
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
