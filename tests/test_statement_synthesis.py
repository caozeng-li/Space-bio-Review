from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from spacebio_review.v4.contracts import Product, grounded_contract
from spacebio_review.v4.records import Records, statement_registry
from spacebio_review.v4.synthesis import ArgumentAccess, Synthesis


def contract(overall=False):
    evidence = {
        "reference": {"source_id": "paper", "passage_id": "p1"},
        "title": "Paper",
        "kind": "external",
        "quote": "ORIGINAL_PASSAGE_ONCE",
        "locator": "p1",
        "material_version": "v1",
        "reading_scope": "text",
    }
    statement = {
        "statement_id": "statement-fact",
        "kind": "external",
        "parts": [{"text": "Observation at day 3", "cites": ["exact"]}],
        "scope": [{"text": "Imaging, not sequencing", "cites": []}],
    }
    return grounded_contract(
        "assessment" if overall else "synthesis",
        lambda ev, rs: Product,
        results={
            "prior::b1": {
                "artifact_ref": "prior",
                "title": "Prior",
                "scope_note": "",
                "text": "Legacy text",
                "statement_origins": ["statement-fact"],
                "statements": {"statement-fact": statement},
                "evidence": {"exact": evidence},
            }
        },
        inherited=True,
        compact_sources=True,
    )


def draft():
    return {
        "title": "Synthesis",
        "scope_note": {"parts": [{"text": "Scoped review", "cites": []}]},
        "blocks": [
            {"items": [{"use": "C1"}]},
            {
                "items": [
                    {
                        "kind": "interpretation",
                        "content": {"parts": [{"text": "A conditional inference", "cites": []}]},
                        "scope": {
                            "parts": [{"text": "Cannot establish mutation timing", "cites": []}]
                        },
                        "premise_ids": ["C1"],
                        "based_on": [],
                    }
                ]
            },
        ],
        "followups": [],
    }


def test_synthesis_reuses_fact_and_keeps_new_inference_distinct():
    c = contract()
    product = c.product(c.parse(draft())).model_dump()
    assert product["blocks"][0]["evidence_refs"] == ["exact"]
    assert product["blocks"][1]["evidence_refs"] == []
    assert "Imaging, not sequencing" in product["blocks"][0]["text"]
    assert "Observation at day 3" not in product["blocks"][1]["text"]
    assert product["statement_content"]["/blocks/0"]["selected"] == ["statement-fact"]
    assert len(statement_registry(product)) == 2


def test_new_direct_evidence_requires_current_original_not_only_inherited_identity():
    c = contract()
    value = draft()
    value["blocks"][1]["items"][0]["content"]["parts"][0]["cites"] = ["E1"]
    with pytest.raises(ValidationError):
        c.parse(value)
    c.task_context.activate(["exact"])
    c.refresh(c)
    assert c.product(c.parse(value)).blocks[1]["evidence_refs"] == ["exact"]


def test_synthesis_prompt_does_not_expand_archived_originals():
    c = contract()
    view = c.task_context.source_view()
    assert view["evidence"] == {}
    assert view["statements"]["C1"]["parts"][0]["cites"] == ["E1"]
    before = deepcopy(view)
    c.task_context.add({f"unused{i}": c.task_context.evidence["exact"] for i in range(100)}, "E")
    c.task_context.active = {"prior::b1"}
    assert c.task_context.source_view() == before


def test_conditions_unknowns_and_followups_share_statement_compilation():
    c = contract(overall=True)
    value = draft()
    paragraph = {"items": [{"use": "C1"}]}
    value.update(
        recommendation="revise",
        conditions=[{"text": paragraph, "timing": "before_support"}],
        options=[{"text": paragraph}],
        unresolved=[paragraph],
        followups=[
            {
                k: paragraph
                for k in ("question", "missing_information", "decision_impact", "information_route")
            }
        ],
    )
    product = c.product(c.parse(value)).model_dump()
    for path in ("/conditions/0/text", "/options/0/text", "/unresolved/0", "/followups/0/question"):
        assert product["statement_content"][path]["selected"] == ["statement-fact"]
        assert product["citation_content"][path][0]["cites"] == ["exact"]


def test_synthesis_builder_uses_the_configured_statement_contract():
    config = SimpleNamespace(formal_routes={"assessment": SimpleNamespace(protocol="inherited")})
    execution = SimpleNamespace(runtime=SimpleNamespace(config=config), records=None)
    synthesis = Synthesis(execution, None)
    initial = contract().task_context.results
    access = ArgumentAccess(None, initial, initial)
    c = synthesis.contract(SimpleNamespace(references=None), access)
    assert c.task_context.purpose == "synthesis"
    assert access.contract is c
    assert c.product(c.parse(draft())).blocks[0]["evidence_refs"] == ["exact"]


def test_two_archive_roundtrips_and_large_history_keep_same_working_set(tmp_path):
    records = Records(tmp_path)
    first = contract()
    original = first.product(first.parse(draft())).model_dump()

    def store(value):
        value = deepcopy(value)
        value["blocks"] = [{**b, "block_id": f"b{i}"} for i, b in enumerate(value["blocks"], 1)]
        return records.put("fixture", value)

    def load(refs):
        return grounded_contract(
            "synthesis",
            lambda ev, rs: Product,
            results=records.result_blocks(refs),
            inherited=True,
            compact_sources=True,
        )

    ref = store(original)
    same = load([ref, ref])
    assert len(same.task_context.book.values) == 2
    before = deepcopy(same.task_context.source_view())
    inflated = deepcopy(original)
    registry = inflated["statement_content"]["/registry"]["statements"]
    for i in range(100):
        registry[f"statement-unrelated{i}"] = {
            "statement_id": f"statement-unrelated{i}",
            "kind": "external",
            "parts": [{"text": "UNRELATED_BODY" * 100, "cites": [f"unused{i}"]}],
        }
        inflated["evidence"][f"unused{i}"] = deepcopy(inflated["evidence"]["exact"])
    with_history = load([store(inflated)])
    assert with_history.task_context.source_view() == before
    assert with_history.schema() == same.schema()
    book = same.task_context.book
    selected = [book.aliases[s] for s in statement_registry(original)]
    second_draft = {
        "title": "Regrouped",
        "scope_note": {"parts": [{"text": "Local", "cites": []}]},
        "blocks": [{"items": [{"use": k} for k in reversed(selected)]}],
        "followups": [],
    }
    second = same.product(same.parse(second_draft)).model_dump()
    restored = load([store(second)])
    assert {
        v["statement_id"]: v for v in restored.task_context.book.values.values()
    } == statement_registry(original)
    assert "ORIGINAL_PASSAGE_ONCE" not in str(restored.task_context.source_view())
