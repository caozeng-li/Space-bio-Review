import json
from copy import deepcopy
from pathlib import Path

import pytest

from spacebio_review.v4.assessment import assessment_request
from spacebio_review.v4.assessment_input import ReviewArchive, freeze_review, review_materials
from spacebio_review.v4.context import TaskContext
from spacebio_review.v4.material_view import VERSION, reading_view


def test_conditions_and_first_original_stay_near_argument_without_copying_quotes():
    evidence = {
        "E1": {
            "source": "s1",
            "quote": "Measured a selected range, not a threshold.",
            "reading_scope": "abstract",
            "locator": "Abstract",
        },
        "E2": {"source": "s2", "quote": "Application arrangement.", "reading_scope": "text"},
    }
    sources = {
        "s1": {"title": "A conditional study", "kind": "external"},
        "s2": {"title": "Proposal", "kind": "application"},
    }
    research = {
        "G1": {
            "title": "Interpretation",
            "scope": "Only the actual sample and method",
            "unknowns": ["Unmeasured parameter"],
            "arguments": {
                "O1": {"text": {"parts": [{"text": "Interpretation", "cites": ["E1"]}]}},
                "O2": {"text": {"parts": [{"text": "Alternative", "cites": ["E1"]}]}},
            },
        },
    }
    original = deepcopy((evidence, sources, research))
    view = reading_view(evidence, sources, research, application=["E2"])
    assert view["format_version"] == VERSION
    assert view["application"]["E2"]["quote"] == evidence["E2"]["quote"]
    group = view["research"]["G1"]
    assert group["scope"] == research["G1"]["scope"]
    assert group["unknowns"] == research["G1"]["unknowns"]
    assert group["arguments"]["O1"]["originals"]["E1"]["reading_scope"] == "abstract"
    assert group["arguments"]["O1"]["originals"]["E1"]["title"] == sources["s1"]["title"]
    assert group["arguments"]["O2"]["originals"]["E1"]["see"] == (
        "research.G1.arguments.O1.originals.E1"
    )
    assert json.dumps(view).count(evidence["E1"]["quote"]) == 1
    assert (evidence, sources, research) == original


def test_lineage_is_not_promoted_to_direct_evidence_and_literal_is_not_bound():
    evidence = {"E1": {"source": "s", "quote": "Actual evidence"}}
    research = {
        "G1": {
            "scope": "Common scope",
            "arguments": {
                "O1": {
                    "text": {"parts": [{"text": "Literal [@E1]", "cites": []}]},
                    "related_opinions": ["O2"],
                },
            },
        },
        "G2": {
            "scope": "Delegated scope",
            "arguments": {
                "O2": {"text": {"parts": [{"text": "Finding", "cites": ["E1"]}]}},
            },
        },
    }
    view = reading_view(evidence, {"s": {"title": "Paper"}}, research)
    argument = view["research"]["G1"]["arguments"]["O1"]
    assert argument["originals"] == {}
    assert argument["related_opinions"] == ["O2"]
    assert argument["text"]["parts"][0]["cites"] == []
    assert view["research"]["G2"]["arguments"]["O2"]["originals"]["E1"]["quote"]


def test_formal_professional_uses_same_view_and_retains_selected_scope_originals():
    context = TaskContext(compact_sources=True, structured_refs=True)
    record = {
        "reference": {"source_id": "paper", "passage_id": "p1"},
        "title": "Study",
        "quote": "The measured scope.",
        "reading_scope": "text",
    }
    context.add({"raw": record}, "E")
    context.add(
        {
            "finding": {
                "text": "Finding",
                "parts": [{"text": "Finding", "cites": ["raw"]}],
                "title": "Knowledge",
                "origin": "delegated",
                "artifact_ref": "knowledge",
                "scope_note": "Shared limitation",
                "scope_links": ["raw"],
                "scope_kind": "external_research_scope",
                "evidence_refs": ["raw"],
            }
        },
        "R",
    )
    context.active = {"finding"}
    before = context.mapping()
    assert "E1" in context.citation_choices()
    messages = context.messages(
        [
            {"role": "user", "content": "Research"},
            {"role": "user", "content": "Formal contract"},
        ],
        formal=True,
    )
    view = json.loads(messages[-2]["content"])["task_sources"]
    assert view["format_version"] == VERSION
    assert view["research"]["knowledge"]["scope_kind"] == "external_research_scope"
    assert "The measured scope." in str(view)
    assert context.mapping() == before
    assert context.active == {"finding"}
    assert messages[-1]["content"] == "Formal contract"


def test_mytest26_scientific_materials_reach_actual_request_without_loss_or_rewrite():
    root = Path("runs/mytest26")
    if not root.exists():
        pytest.skip("historical local fixture")
    archive = ReviewArchive(root)
    frozen = freeze_review(archive, archive.selected(), archive.application(), {"prompt": "Review"})
    before = deepcopy(frozen)
    _, request = assessment_request(frozen)
    assert request == review_materials(frozen)
    assert request != frozen["materials"]
    opinions = {
        key: row
        for group in request["research"].values()
        for key, row in group["arguments"].items()
    }
    assert len(opinions) == len(frozen["materials"]["opinions"]) == 45
    for key, row in frozen["materials"]["opinions"].items():
        assert opinions[key]["text"] == row["text"]
        assert (
            request["research"][row["context"]]["scope"]
            == (frozen["materials"]["opinion_contexts"][row["context"]]["scope"])
        )
    quotes = []

    def collect(node):
        if isinstance(node, dict):
            if "quote" in node:
                quotes.append(node["quote"])
            for child in node.values():
                collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)

    collect(request)
    assert sorted(quotes) == sorted(
        row["quote"] for row in frozen["materials"]["evidence"].values()
    )
    assert frozen == before


def test_old_and_new_literature_scope_identity_survives_freeze_and_wire(tmp_path):
    from spacebio_review.v4.handoff import knowledge_artifact
    from spacebio_review.v4.records import Records

    def prose(text):
        return {"paragraphs": [{"parts": [{"text": text, "cites": []}]}]}

    records = Records(tmp_path)
    refs = []
    for version, field, text in (
        ("literature-4", "applicability", "Historical project interpretation"),
        ("literature-5", "scope", "External experiment scope"),
    ):
        refs.append(
            records.put(
                "knowledge",
                knowledge_artifact(
                    {
                        "contract_version": version,
                        "findings": [prose("Finding")],
                        field: prose(text),
                        "unknowns": [],
                        "evidence_resolution": {},
                    },
                    version,
                    "Question",
                    [],
                ),
            )
        )
    frozen = freeze_review(records, refs, [], {"prompt": "Standard"})
    groups = review_materials(frozen)["research"]
    assert groups["G1"]["scope_kind"] == "legacy_project_applicability"
    assert groups["G1"]["knowledge_contract_version"] == "literature-4"
    assert "Historical project interpretation" in str(groups["G1"]["scope"])
    assert groups["G2"]["scope_kind"] == "external_research_scope"
    assert groups["G2"]["knowledge_contract_version"] == "literature-5"
    assert "External experiment scope" in str(groups["G2"]["scope"])
