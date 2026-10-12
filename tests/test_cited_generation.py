"""Typed citation draft compiles once; no citation interpretation of arbitrary prose."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from spacebio_review.v4.contracts import professional_contract
from spacebio_review.v4.records import Records
from spacebio_review.v4.research import artifact_dependencies, save_artifact


def evidence():
    return {
        "reference": {"source_id": "paper", "passage_id": "p1"},
        "title": "Paper",
        "kind": "external",
        "quote": "Only at 37 C",
        "locator": "p1",
        "material_version": "v1",
        "reading_scope": "text",
    }


def narrative(text, cites=()):
    return {"parts": [{"text": text, "cites": list(cites)}]}


def draft():
    return {
        "title": "Finding",
        "scope_note": narrative("Only at 37 C", ["E1"]),
        "blocks": [
            narrative(
                'Group (E1), literal [@not-a-source], "quotes", C:\\data.\nConditions.', ["E1"]
            )
        ],
        "followups": [
            {
                k: narrative("Still unknown")
                for k in ("question", "missing_information", "decision_impact", "information_route")
            }
        ],
        "scope_outcomes": [
            {"member_id": "q1", "status": "analyzed", "explanation": "Scoped", "block_indices": [1]}
        ],
    }


def contract():
    return professional_contract(
        None, {}, {"q1": {}}, evidence={"exact": evidence()}, structured_refs=True
    )


def test_generated_reference_values_are_choices_not_free_text():
    c = contract()
    value = draft()
    value["blocks"][0]["parts"][0]["cites"] = ["R_working_notes"]
    with pytest.raises(ValidationError, match="literal_error"):
        c.parse(value)
    value = draft()
    value["blocks"][0]["evidence_refs"] = ["E1"]
    with pytest.raises(ValidationError, match="extra_forbidden"):
        c.parse(value)


def test_storage_readback_and_other_task_projection_keep_identity_and_literal_text(tmp_path):
    c = contract()
    product = c.product(c.parse(draft())).model_dump()
    assert product["blocks"][0]["evidence_refs"] == ["exact"]
    assert "Group (E1)" in product["blocks"][0]["text"]
    assert r"\[@not-a-source]" in product["blocks"][0]["text"]
    assert (
        product["citation_content"]["/blocks/0"][0]["text"]
        == draft()["blocks"][0]["parts"][0]["text"]
    )
    assert product["citation_content"]["/blocks/0"][0]["cites"] == ["exact"]
    records = Records(tmp_path)
    ref = save_artifact(
        records,
        "research",
        product,
        task="t",
        author="expert",
        receipt={"input": "input", "reading": {}},
    )
    stored = records.value(ref)
    assert stored["citation_content"] == product["citation_content"]
    parent = professional_contract(
        None,
        records.result_blocks([ref]),
        {"q1": {}},
        evidence={"other": evidence()},
        structured_refs=True,
    )
    view = parent.task_context.source_view()["research"][ref]
    body = view["arguments"]["R1"]["content"]
    assert "Group (E1)" in body[0]["text"]
    assert body[0]["cites"] == ["E2"]
    assert "[@not-a-source]" in body[0]["text"]
    assert view["scope"][0]["cites"] == ["E2"]


def test_only_structured_references_own_dependencies():
    c = contract()
    value = draft()
    value["blocks"][0] = narrative("Literal [@fake::b1] and [@exact]", [])
    value["scope_note"] = narrative("No external source", [])
    product = c.product(c.parse(value)).model_dump()
    assert product["evidence"] == {}
    assert artifact_dependencies(product) == []


def test_frozen_draft_choices_match_working_view_without_archive_growth():
    c = contract()
    for i in range(80):
        c.task_context.add({f"unused{i}": evidence()}, "E")
    c.task_context.active = {"exact"}
    c.refresh(c)
    before = deepcopy(c.schema())
    assert c.directory()["evidence_refs"] == ["E1"]
    c.task_context.freeze()
    assert c.product(c.parse(draft())).model_dump()["evidence"] == {"exact": evidence()}
    assert c.schema() == before


def test_legacy_contract_remains_text_only():
    c = professional_contract(None, {}, {"q1": {}})
    assert set(c.schema()["$defs"]["DraftBlock"]["properties"]) == {"text"}


def test_empty_sources_and_long_literal_prose_are_not_reinterpreted():
    c = professional_contract(None, {}, {"q1": {}}, structured_refs=True)
    value = draft()
    text = 'Group (E1), "quoted mechanism", C:\\data; conditional inference.\n' * 200
    value["scope_note"] = narrative("Limited scope")
    value["blocks"] = [narrative(text)]
    product = c.product(c.parse(value)).model_dump()
    assert product["blocks"][0]["text"] == text
    assert product["evidence"] == {}
    assert c.schema()["$defs"]["CitedPart"]["properties"]["cites"]["maxItems"] == 0
    value["blocks"][0]["parts"][0]["cites"] = ["E1"]
    with pytest.raises(ValidationError):
        c.parse(value)


def test_references_in_followup_and_scope_retain_full_dependency_graph():
    c = contract()
    c.task_context.add({"research-abc::b1": {"text": "Derived", "scope_note": "Only here"}}, "R")
    c.refresh(c)
    value = draft()
    value["blocks"][0] = narrative("Own interpretation")
    value["followups"][0]["decision_impact"] = narrative("Related result", ["R1", "E1", "R1"])
    product = c.product(c.parse(value)).model_dump()
    assert set(product["evidence"]) == {"exact"}
    assert artifact_dependencies(product) == ["research-abc"]
    assert product["followups"][0]["decision_impact"].count("[@research-abc::b1]") == 1
