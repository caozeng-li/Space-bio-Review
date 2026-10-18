from copy import deepcopy
from types import SimpleNamespace

import pytest

from spacebio_review.v5.records import SessionTask
from spacebio_review.v5.research_context import (
    compact_tool_payload,
    composition_context,
    context_references,
)
from spacebio_review.v5.session import ResearchRuntime


def snapshot(source="paper", passage="p1", quote="New measured result", **kwargs):
    return {
        "reference": {"source_id": source, "passage_id": passage, "visual_id": None},
        "title": source,
        "quote": quote,
        "material_version": "v1",
        **kwargs,
    }


def note(ref="note-1", text="The method supports this inference", cites=None):
    return {
        "note_ref": ref,
        "note": {"title": "Scientific explanation", "body": [[
            {"text": text, "cites": cites or ["E1"]}
        ]]},
    }


def observation(name, content, readings=None, **kwargs):
    return {
        "kind": "tool", "name": name,
        "output": {"result": {"ok": True, "content": content}, "readings": readings or {}},
        **kwargs,
    }


def context(**kwargs):
    return {
        "task": {"role": "researcher", "objective": "Explain the scientific result"},
        "skills": {"methods": "Preserve the actual comparison"},
        "current_research": [], "originals": {}, "available_readings": {},
        "unabsorbed_observations": [], "followup": None, "working_context": {},
        "resources": {"session_model_turns_left": 4},
        **kwargs,
    }


def test_tool_abstract_uses_source_and_passage_identity_without_changing_storage():
    original = snapshot(passage="abstract-1", quote="The complete candidate abstract")
    result = {"ok": True, "content": {"candidates": [
        {"source_id": "paper", "abstract_passage_id": "abstract-1",
         "abstract": "The truncated candidate abstract", "title": "Paper"},
        {"source_id": "other", "abstract_passage_id": "abstract-1", "abstract": "Other"},
    ]}}
    before = deepcopy(result)
    output = compact_tool_payload(result, {"E1": original}, {"E1": "evidence-1"})
    candidates = output["result"]["content"]["candidates"]
    assert candidates[0]["abstract_reference"] == "E1"
    assert "abstract" not in candidates[0]
    assert candidates[1]["abstract"] == "Other"
    assert output["readings"]["E1"]["quote"] == "The complete candidate abstract"
    assert result == before


def test_tool_original_and_citations_share_the_session_alias():
    result = {"content": {"source_id": "paper", "passages": [
        {"passage_id": "p1", "text": "New measured result", "evidence_handle": "handle-1"}
    ], "note": note(cites=["handle-1"])["note"]}}
    output = compact_tool_payload(result, {"E1": snapshot()}, {"E1": "handle-1"})
    passage = output["result"]["content"]["passages"][0]
    assert passage == {"passage_id": "p1", "evidence_handle": "E1", "text_reference": "E1"}
    assert output["result"]["content"]["note"]["body"][0][0]["cites"] == ["E1"]


def test_composition_keeps_each_scientific_note_and_original_once():
    explanation = note()
    view = context(
        current_research=[note("current", "Current synthesis", ["E2"])],
        originals={"E2": snapshot(passage="p2", quote="Earlier cited basis")},
        available_readings={"E1": {"title": "Paper"}, "E2": {"title": "Earlier"},
                            "E9": {"title": "Old unused search candidate"}},
        unabsorbed_observations=[
            observation("read_source_for_question", explanation, {"E1": snapshot()}),
            observation("read_source_notes", {"source_readings": [explanation]},
                        {"E1": snapshot()}),
            {"kind": "research_observation", "content": "A new comparison [@E1]"},
        ],
        working_context={"source_readings": [explanation],
                         "assessment": {"dimensions": ["Scientific significance"],
                                        "instruction": "Give complete reasons"}},
    )
    before = deepcopy(view)
    output = composition_context(view)
    assert output["originals"] == {"E2": view["originals"]["E2"], "E1": snapshot()}
    first, second, scientific = output["unabsorbed_observations"]
    assert first["output"]["result"]["content"] == explanation
    assert "readings" not in first["output"]
    assert second["output"]["result"]["content"]["source_readings"] == [{"note_ref": "note-1"}]
    assert output["working_context"]["source_readings"] == [{"note_ref": "note-1"}]
    assert output["working_context"]["assessment"] == view["working_context"]["assessment"]
    assert scientific["content"] == "A new comparison [@E1]"
    assert set(output["available_readings"]) == {"E1", "E2"}
    assert "resources" not in output
    assert view == before


def test_search_process_is_removed_but_abstract_only_findings_survive():
    view = context(unabsorbed_observations=[observation("search_sources", {
        "candidates": [
            {"source_id": "paper", "title": "Paper", "abstract_passage_id": "abstract-1",
             "abstract": "Captured abstract", "search_origins": [{"query": "long query"}]},
            {"source_id": "uncaptured", "title": "Other paper", "abstract": "Only finding"},
        ],
        "channels": {"bm25": "used"}, "query_feedback": [{"query": "long query"}],
        "existing_research": [note()],
    }, {"E1": snapshot(passage="abstract-1", quote="Captured abstract")})])
    output = composition_context(view)
    content = output["unabsorbed_observations"][0]["output"]["result"]["content"]
    assert content == {
        "candidate_abstracts": [{"source_id": "uncaptured", "title": "Other paper",
                                "abstract": "Only finding"}],
        "existing_research": [note()],
    }
    assert output["originals"]["E1"]["quote"] == "Captured abstract"


def test_acquisition_keeps_unabsorbed_passages_and_coverage_not_navigation():
    view = context(unabsorbed_observations=[observation("acquire_source", {
        "source_id": "paper", "title": "Paper", "sections": ["Methods", "Results"],
        "materials": [{"material_id": "fulltext"}], "passage_directory": [{"passage_id": "p1"}],
        "read_with": "read_materials", "instruction": "Continue reading",
        "reading_gaps": ["Supplement unavailable"],
        "reading": {"channels": {"dense": "used"}, "sources": [{
            "source_id": "paper", "passages": [{"passage_id": "p1", "text_reference": "E1"}],
            "input_tokens_estimate": 300, "continuation": "cursor", "unit_continues": True,
        }]},
    }, {"E1": snapshot()})])
    content = composition_context(view)["unabsorbed_observations"][0]["output"]["result"]["content"]
    assert content == {
        "source_id": "paper", "title": "Paper", "reading_gaps": ["Supplement unavailable"],
        "reading": {"sources": [{"source_id": "paper", "passages": [
            {"passage_id": "p1", "text_reference": "E1"}], "unit_continues": True}]},
    }


def test_application_and_failed_author_prose_remain_available_without_repeating_application():
    application = {"sources": [{"source_id": "application", "passages": [
        {"passage_id": "p1", "text": "Live imaging happens in orbit"}
    ]}]}
    view = context(
        task={"application": application},
        available_readings={"E1": {"reference": {"source_id": "application", "passage_id": "p1"}}},
        unabsorbed_observations=[{
            "kind": "author_delivery", "author_text": "The corrected explanation [@E1]",
            "output": {"result": {"ok": False, "error": "parse_failed"},
                       "readings": {"E1": snapshot(
                           "application", quote="Live imaging happens in orbit"
                       )}},
        }],
    )
    output = composition_context(view)
    assert output["task"]["application"] == application
    assert output["originals"] == {}
    assert "E1" in output["available_readings"]
    assert output["unabsorbed_observations"][0]["author_text"] == "The corrected explanation [@E1]"


def test_unknown_scientific_tool_content_is_not_filtered_by_words_or_field_names():
    content = {"method": "search and acquisition bias", "limitations": ["Measured confounding"],
               "channels": ["Fluorescence channel"], "observations": [{"text": "A new result"}],
               "candidates": [{"mechanism": "Alternative biological explanation"}],
               "sources": [{"condition": "Actual comparison"}]}
    output = composition_context(context(
        unabsorbed_observations=[observation("new_tool", content)]
    ))
    assert output["unabsorbed_observations"][0]["output"]["result"]["content"] == content


@pytest.mark.parametrize("row, expected", [
    (observation("read_source_notes", note(cites=["E2"])), {"E2"}),
    ({"kind": "research_observation", "content": "New interpretation [E2]"}, {"E2"}),
    ({"kind": "research_observation", "content": "Comparison [@E2,@E3]"}, {"E2", "E3"}),
    (observation("search_sources", {"candidates": [{
        "source_id": "paper", "abstract_passage_id": "abstract-2",
        "abstract": "Earlier captured abstract", "title": "Paper",
    }]}), {"E2"}),
])
def test_runtime_includes_prior_evidence_used_by_new_observations(row, expected):
    previous = {"title": "Prior synthesis", "body": {"paragraphs": [{"parts": [
        {"text": "The current synthesis did not use the older evidence", "cites": []}
    ]}]}}
    evidence = {
        "handle-2": snapshot(passage="abstract-2", quote="Earlier captured abstract"),
        "handle-3": snapshot(passage="p3", quote="Earlier measured condition"),
    }
    state = SimpleNamespace(
        task=SessionTask(role="researcher", objective="Explain the new result", application={}),
        evidence=evidence, aliases={"E2": "handle-2", "E3": "handle-3"}, observations=[row],
        note_ref="current", skill_bodies={}, followup=None,
        session_id="local-context-case", inquiry_id=None,
    )
    runtime = ResearchRuntime.__new__(ResearchRuntime)
    runtime.store = SimpleNamespace(note=lambda ref: {"note": previous})
    runtime.resources = lambda session_id: {}
    result = ResearchRuntime._context(runtime, state, purpose="composition")
    assert set(result["originals"]) == expected
    assert set(result["available_readings"]) == expected
    for alias in expected:
        assert result["originals"][alias] == evidence[state.aliases[alias]]


def test_context_reference_grammar_matches_authoring_and_does_not_choose_ambiguous_versions():
    readings = {"E2": snapshot(passage="abstract-2"),
                "E3": snapshot(passage="abstract-2", material_version="v2", quote="Changed result")}
    content = {"source_id": "paper", "abstract_passage_id": "abstract-2", "abstract": "Excerpt"}
    assert list(context_references(content, readings)) == []
    assert compact_tool_payload(content, readings, {})["result"]["abstract"] == "Excerpt"
    assert set(context_references(r"Sources [E2], [@E2,@E3], ［E4；@E5］; literal \[E6]")) == {
        "E2", "E3", "E4", "E5"
    }
