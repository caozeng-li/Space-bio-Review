"""Public notes select a bounded source view without rewriting source identities."""

import json

import pytest
from pydantic import ValidationError

from spacebio_review.v4.context import TaskContext


def source(i):
    return {
        "reference": {"source_id": "paper", "passage_id": f"p{i}"},
        "title": "Paper",
        "kind": "external",
        "quote": f"Evidence {i}",
        "locator": f"p{i}",
        "material_version": f"v{i}",
    }


def notes(**updates):
    return {
        "current_claims": [],
        "counterevidence": [],
        "revisions": [],
        "unresolved": [],
        "next_steps": [],
        "source_ids": [],
        "active_refs": [],
        **updates,
    }


def test_notes_own_the_working_set_without_scanning_their_prose():
    c = TaskContext(compact_sources=True, structured_refs=True)
    c.add({f"ev{i}": source(i) for i in range(96)}, "E")
    original = c.mapping()
    wire = notes(
        current_claims=[{"text": "Experimental group (E90), not a citation", "cites": ["E1"]}],
        counterevidence=[{"text": "Exception", "cites": ["E3"]}],
        active_refs=["E5"],
    )
    stored = c.bind_memory(c.memory_type().model_validate(wire))
    c.checkpoint(stored)
    assert c.active == {"ev0", "ev2", "ev4"}
    assert set(c.citation_choices()) == {"E1", "E3", "E5"}
    assert set(c.source_view()["evidence"]) == set(c.citation_choices())
    assert c.mapping() == original
    c.activate_read({"source_id": "paper", "passages": [{"passage_id": "p95"}]})
    assert c.citation_choices()["E96"] == "ev95"
    assert c.mapping() == original


def test_memory_wire_choices_and_stored_identity_use_one_mapping():
    c = TaskContext(structured_refs=True)
    c.add({"real": source(1)}, "E")
    raw = json.dumps({"memory": notes(current_claims=[{"text": "Finding", "cites": ["E1"]}])})
    bound = json.loads(c.tool_arguments(raw))
    assert bound["memory"]["current_claims"][0]["cites"] == ["real"]
    assert c.project(bound)["memory"]["current_claims"][0]["cites"] == ["E1"]
    with pytest.raises(ValidationError):
        c.memory_type().model_validate(
            notes(current_claims=[{"text": "Finding", "cites": ["R_notes"]}])
        )


def test_unselected_arguments_do_not_force_all_ancestors_into_context():
    c = TaskContext(compact_sources=True, structured_refs=True)
    c.add({"ev": source(1)}, "E")
    c.add(
        {
            "r1": {"text": "Selected", "scope_note": "Only here", "evidence_refs": ["ev"]},
            "r2": {"text": "Unrelated", "scope_note": "Elsewhere"},
        },
        "R",
    )
    c.checkpoint(notes(current_claims=[{"text": "Result", "cites": ["R1"]}]))
    assert set(c.citation_choices()) == {"R1"}
    view = c.source_view()
    assert list(view["research"]) == ["r1"]
    assert view["research"]["r1"]["scope"] == [{"text": "Only here", "cites": []}]
    assert c.mapping()["E1"]["identity"] == "ev"


def test_read_reactivates_exact_version_not_all_versions_at_position():
    c = TaskContext(compact_sources=True, structured_refs=True)
    first = source(1)
    second = {**first, "quote": "Updated observation", "material_version": "v2"}
    c.add({"first": first, "second": second}, "E")
    c.checkpoint(notes())
    c.activate_read({"source_id": "paper", "passages": [{"passage_id": "p1"}]})
    assert c.active == set()
    c.activate_read(
        {
            "source_id": "paper",
            "passages": [
                {
                    "passage_id": "p1",
                    "text": second["quote"],
                    "evidence_handle": "E2",
                }
            ],
        }
    )
    assert c.active == {"second"}


def test_legacy_input_uses_relations_without_teaching_inline_reference_syntax():
    c = TaskContext(compact_sources=True, structured_refs=True)
    c.add({"ev": source(1)}, "E")
    archive = {
        "text": "Group (E1); finding [@ev]. Literal [@not-a-source].",
        "evidence_refs": ["ev"],
        "result_refs": [],
    }
    view = c.project(archive)
    assert "evidence_refs" not in view
    assert view["sources"] == ["E1"]
    assert view["content"] == [
        {"text": "Group (E1); finding ", "cites": ["E1"]},
        {"text": ". Literal [@not-a-source].", "cites": []},
    ]
    assert archive["text"].count("[@ev]") == 1
