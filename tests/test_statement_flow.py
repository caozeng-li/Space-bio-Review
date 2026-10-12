from copy import deepcopy

from spacebio_review.v4.context import TaskContext


def evidence():
    return {
        "reference": {"source_id": "paper", "passage_id": "p21"},
        "kind": "external",
        "title": "Paper",
        "quote": "N-IDR at 10% PEG, observed for 2 minutes.",
        "locator": "p21",
        "reading_scope": "text",
        "material_version": "v1",
    }


def context():
    c = TaskContext(compact_sources=True, structured_refs=True, inherited=True)
    c.members = {"q1": {}}
    c.add({"exact": evidence()}, "E")
    c.add(
        {
            "research-child::f1": {
                "text": "A scoped finding",
                "artifact_ref": "research-child",
                "origin": "delegated",
                "scope_note": "Only the tested fragment; not full-length TFEB.",
                "evidence_refs": ["exact"],
                "basis": [
                    {
                        "text": evidence()["quote"],
                        "kind": "external",
                        "evidence_refs": ["exact"],
                        "quantitative_basis": {"amount": "10", "unit": "% PEG", "origin": "quoted"},
                    }
                ],
            }
        },
        "R",
    )
    c.book.import_results()
    return c


def memory():
    return {
        "current_claims": [{"use": "C2"}],
        "counterevidence": [],
        "revisions": [],
        "unresolved": [],
        "next_steps": [],
        "active_refs": [],
        "source_ids": [],
        "followups": [],
        "scope_outcomes": [
            {
                "member_id": "q1",
                "status": "partial",
                "explanation": {"parts": [{"text": "Other constructs untested.", "cites": []}]},
            }
        ],
    }


def test_selected_statement_preserves_conditions_and_reference_without_original_expansion():
    c = context()
    original = deepcopy(c.book.values["C2"])
    value = c.bind_memory(c.memory_type().model_validate(memory()))
    c.checkpoint(value)
    assert value.current_claims[0].model_dump() == original
    assert c.source_view()["evidence"] == {}
    assert "E1" in c.citation_choices()
    assert c.source_view()["statements"]["C2"]["parts"][0]["cites"] == ["E1"]
    assert "full-length" in str(c.source_view()["scopes"])
    assert c.project(value.model_dump())["current_claims"] == [{"use": "C2"}]
    before = c.source_view()
    c.add({f"unused{i}": evidence() for i in range(100)}, "E")
    c.checkpoint(value)
    assert c.source_view() == before


def test_revision_gets_new_identity_and_keeps_previous_as_dependency_not_current_claim():
    c = context()
    m = memory()
    m["revisions"] = [
        {
            "kind": "interpretation",
            "content": {
                "parts": [
                    {"text": "No conclusion about the full-length construct.", "cites": ["E1"]}
                ]
            },
            "scope": {"parts": [{"text": "For this experiment only.", "cites": []}]},
            "based_on": ["C2"],
            "supersedes": ["C2"],
        }
    ]
    value = c.bind_memory(c.memory_type().model_validate(m))
    assert value.current_claims == []
    assert value.revisions[0].statement_id != c.book.values["C2"]["statement_id"]
    assert "C2" in c.book.choices()
    assert c.book.selected["current_claims"] == []


def test_keep_result_preserves_internal_evidence_eligibility():
    c = context()
    c.active = {"research-child::f1"}
    assert set(c.citation_choices()) == {"E1", "R1"}
    assert c.source_view()["evidence"] == {}


def test_compiler_preserves_scope_and_archive_roundtrip(tmp_path):
    from spacebio_review.v4.contracts import professional_contract
    from spacebio_review.v4.records import Records
    from spacebio_review.v4.research import save_artifact

    c = context()
    m = memory()
    m["scope_outcomes"][0]["explanation"]["parts"][0]["cites"] = ["E1"]
    m["followups"] = [
        {
            k: {"parts": [{"text": k, "cites": ["E1"]}]}
            for k in ("question", "missing_information", "decision_impact", "information_route")
        }
    ]
    value = c.bind_memory(c.memory_type().model_validate(m))
    c.checkpoint(value)
    product = c.book.compile(
        {"title": "Finding", "blocks": [{"statement_ids": ["C2"], "member_ids": ["q1"]}]}
    )
    assert evidence()["quote"] in product["blocks"][0]["text"]
    assert "not full-length TFEB" in product["blocks"][0]["text"]
    assert product["citation_content"]["/scope_outcomes/0/explanation"][0]["cites"] == ["exact"]
    assert product["followups"][0]["question"] == "question[@exact]"
    records = Records(tmp_path)
    ref = save_artifact(
        records,
        "research",
        {**product, "evidence": c.evidence},
        task="local",
        author="expert",
        receipt={"input": "fixture", "reading": {}},
    )
    saved = records.value(ref)
    assert saved["statement_content"] == product["statement_content"]
    parent = professional_contract(None, records.result_blocks([ref]), {"q1": {}}, inherited=True)
    child = parent.task_context.book.values["C1"]
    assert child["origin"] == "inherited"
    assert child["parts"] == product["citation_content"]["/blocks/0"]


def test_revision_is_current_and_old_content_is_lineage_only():
    c = context()
    m = memory()
    m["revisions"] = [
        {
            "kind": "interpretation",
            "content": {
                "parts": [{"text": "The earlier quantity was incorrect.", "cites": ["E1"]}]
            },
            "scope": {"parts": [{"text": "Only this case.", "cites": []}]},
            "based_on": ["C2"],
            "supersedes": ["C2"],
        }
    ]
    c.checkpoint(c.bind_memory(c.memory_type().model_validate(m)))
    key = c.book.selected["revisions"][0]
    product = c.book.compile(
        {"title": "Corrected", "blocks": [{"statement_ids": [key], "member_ids": ["q1"]}]}
    )
    assert evidence()["quote"] not in product["blocks"][0]["text"]
    assert evidence()["quote"] in str(product["statement_content"])
    assert "C2" not in c.book.current_choices()


def test_new_results_remain_visible_after_checkpoint_and_omitted_reservations_survive():
    c = context()
    m = memory()
    m["unresolved"] = [
        {
            "kind": "unknown",
            "content": {"parts": [{"text": "Reserved unknown", "cites": []}]},
            "scope": {"parts": [{"text": "Cannot generalize.", "cites": []}]},
            "based_on": [],
            "supersedes": [],
        }
    ]
    c.checkpoint(c.bind_memory(c.memory_type().model_validate(m)))
    c.add({"new::f1": {"text": "New incoming result", "scope_note": "Only here"}}, "R")
    c.book.import_results()
    assert any(
        v["parts"][0]["text"] == "New incoming result"
        for k, v in c.book.values.items()
        if k in c.book.choices()
    )
    product = c.book.compile(
        {"title": "Finding", "blocks": [{"statement_ids": ["C2"], "member_ids": ["q1"]}]}
    )
    assert "Reserved unknown" in product["blocks"][-1]["text"]


def test_handoff_snapshot_restores_science_without_rebinding_ids():
    c = context()
    c.checkpoint(c.bind_memory(c.memory_type().model_validate(memory())))
    state = c.book.snapshot()
    restored = TaskContext(compact_sources=True, structured_refs=True, inherited=True)
    restored.book.restore(state)
    assert restored.book.snapshot() == state
    assert restored.source_view() == c.source_view()


def test_working_tool_schema_refs_are_rooted_in_request_not_nested_memory():
    from spacebio_review.evidence.submissions import record

    c = context()
    schema = record("MemoryRequest", memory=(c.memory_type(), ...)).model_json_schema()

    def visit(node):
        if isinstance(node, dict):
            if "$ref" in node:
                target = schema
                for part in node["$ref"].removeprefix("#/").split("/"):
                    target = target[part]
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)


def test_multiple_aspects_of_member_keep_all_explanations_and_partial_status():
    c = context()
    m = memory()
    m["scope_outcomes"].append(
        {
            "member_id": "q1",
            "status": "analyzed",
            "explanation": {"parts": [{"text": "Measured aspect complete.", "cites": ["E1"]}]},
        }
    )
    c.checkpoint(c.bind_memory(c.memory_type().model_validate(m)))
    product = c.book.compile(
        {"title": "Scoped", "blocks": [{"statement_ids": ["C2"], "member_ids": ["q1"]}]}
    )
    outcome = product["scope_outcomes"][0]
    assert "Other constructs untested." in outcome["explanation"]
    assert "Measured aspect complete." in outcome["explanation"]
    assert outcome["status"] == "partial"


def test_upstream_damaged_text_is_not_silently_repaired():
    c = context()
    text = "~8.54×10^-4 N74]"
    key = c.book.add({"kind": "external", "parts": [{"text": text, "cites": ["exact"]}]})
    c.book.selected["current_claims"] = [key]
    c.book.has_checkpoint = True
    product = c.book.compile(
        {"title": "Negative fixture", "blocks": [{"statement_ids": [key], "member_ids": ["q1"]}]}
    )
    assert product["citation_content"]["/blocks/0"][0]["text"] == text
