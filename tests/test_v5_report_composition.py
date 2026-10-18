"""Exact multi-author publication and two-call composition, without external services."""

import json
from copy import deepcopy

import pytest

from examples.multi_author_report_probe import (
    AMPLICON,
    APPLICATION,
    APPLICATION_GOAL,
    CLONAL,
    MANUSCRIPTS,
    WHOLE_GENOME,
    fixed_manuscript,
    prepare_fixture,
    save_scientific_manuscript,
)
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument
from spacebio_review.models.config import ModelConfig
from spacebio_review.models.provider import ChatReply, ModelError
from spacebio_review.persistence import write_json
from spacebio_review.prose import cited_ids
from spacebio_review.publication import Renderer, compile_publication
from spacebio_review.publication.document import plain_text, walk_nodes
from spacebio_review.v5.assessment import AssessmentSubmission
from spacebio_review.v5.authoring import research_note
from spacebio_review.v5.budget import Budget, BudgetLimits
from spacebio_review.v5.records import ReportSelection
from spacebio_review.v5.report_composition import ReportComposition


def local_materials():
    rows = (
        (APPLICATION, "src-application", "p20", "在轨第 3、6、9 天成像；返回后测序。"),
        (APPLICATION_GOAL, "src-application", "p13", "成像和测序提供互补认识。"),
        (AMPLICON, "src-paper", "p38", "Illumina iSeq 100，2 × 150 bp 双端测序。"),
        (WHOLE_GENOME, "src-paper", "p39", "20–40 ng DNA，两组引物，约 8.5 kb 产物。"),
        (CLONAL, "src-paper", "p36", "分选的单细胞在 Matrigel 培养约两周后挑选类器官。"),
    )
    evidence, sources = {}, []
    for source_id, kind in (("src-application", "application"), ("src-paper", "external")):
        candidate = SourceCandidate(
            title=source_id, kind="literature" if kind == "external" else kind
        )
        passages = []
        for handle, sid, passage_id, quote in rows:
            if sid != source_id:
                continue
            passages.append(Passage(passage_id=passage_id, text=quote, locator=passage_id))
            evidence[handle] = {
                "reference": {"source_id": sid, "passage_id": passage_id},
                "title": candidate.title, "kind": kind, "locator": passage_id,
                "quote": quote, "reading_scope": "text",
            }
        sources.append(SourceDocument(
            source_id=source_id, candidate=candidate, availability="full_text", passages=passages
        ).model_dump(mode="json"))
    return {"evidence": evidence, "sources": sources}


def accept_fixed(fixture, *, fallback=None):
    runtime, state = fixture.runtime, fixture.runtime.load(fixture.chief_id)
    submission = AssessmentSubmission(fixture.chief, fixture.chief_id)
    content, values = fixed_manuscript(submission.system, main_sections=fallback)
    raw_ref = runtime.store.save_author_text(
        state, content, "submit_assessment", absorbed_through=state.observation_index
    )
    raw = runtime.store.author_text(raw_ref)
    schema, _, _ = submission.authoring_contract(raw)
    mapping = schema.model_validate(values)
    parse_ref = runtime.store.save_delivery_parse(raw_ref, {}, mapping)
    accepted = submission.accept_authoring(state, raw_ref, raw, mapping, parse_ref=parse_ref)
    assert accepted.ok, accepted
    return submission.result(), raw, mapping


def compile_result(result):
    return compile_publication(
        result["assessment"], evidence=result["evidence"],
        report_selection=result["report_selection"], contributions=result["contributions"],
        author_sources=result["author_sources"],
    )


@pytest.mark.parametrize("system", ["gbt42048_2022", "general"])
def test_selected_manuscripts_are_main_text_with_bound_local_aliases(tmp_path, system):
    fixture = prepare_fixture(tmp_path, local_materials(), system)
    runtime, state = fixture.runtime, fixture.runtime.load(fixture.chief_id)
    selection = fixture.selection.model_copy(deep=True)
    selection.main_sections.reverse()
    submission = AssessmentSubmission(fixture.chief, fixture.chief_id)
    submission.prepare(state, selection)
    result, raw, mapping = accept_fixed(fixture)
    document, audit = compile_result(result)
    scientific = [section for section in document.main if section.kind == "scientific"]
    assert [section.title for section in scientific] == [
        MANUSCRIPTS[1]["title"], MANUSCRIPTS[0]["title"]
    ]
    assert not document.appendix
    expected_handles = {handle for item in MANUSCRIPTS for handle in item["handles"]}
    assert expected_handles <= set(document.locations)
    assert len(document.numbering) == 2
    assert document.locations[APPLICATION].source == document.locations[APPLICATION_GOAL].source
    first, second = fixture.selection.main_sections
    first_raw = runtime.store.author_text(
        runtime.store.note(first.contribution_ref)["author_text_ref"]
    )
    second_raw = runtime.store.author_text(
        runtime.store.note(second.contribution_ref)["author_text_ref"]
    )
    assert first_raw["aliases"]["E1"] == APPLICATION
    assert second_raw["aliases"]["E1"] == AMPLICON
    for selected in result["contributions"]:
        assert selected["note"] == runtime.store.note(selected["contribution_ref"])["note"]
        for paragraph in selected["note"]["body"]["paragraphs"]:
            text = "".join(part["text"] for part in paragraph["parts"])
            assert text in "".join(plain_text(section.blocks) for section in scientific)
    manuscript_origins = [
        row for row in audit["paragraphs"].values() if row.get("contribution_ref")
    ]
    assert manuscript_origins and all(row["author_text_ref"] for row in manuscript_origins)
    assert all(row["author_paragraph_id"].startswith("p") for row in manuscript_origins)
    assert result["assessment"]["summary"] == result["assessment"]["overall"]
    assert result["last_author_text_ref"] == result["report_author_source"]["author_text_ref"]
    assert result["author_sources"]["summary"]["selection"] == mapping.summary
    assert raw["content"] == fixed_manuscript(submission.system)[0]
    if system == "gbt42048_2022":
        assert result["assessment"]["total_score"] == 85
        assert result["assessment"]["score_grade"] == "A"
    else:
        assert "total_score" not in result["assessment"]
        assert result["assessment"]["recommendation"] == "support"
    assert runtime.budget.usage()["model_calls"] == 0


def test_selection_survives_late_revision_and_new_selection_changes_publication(tmp_path):
    fixture = prepare_fixture(tmp_path, local_materials())
    runtime, state = fixture.runtime, fixture.runtime.load(fixture.chief_id)
    composition = ReportComposition(fixture.chief, fixture.chief_id)
    first = fixture.selection.main_sections[0]
    composition.prepare(state, fixture.selection)
    replacement = deepcopy(MANUSCRIPTS[0])
    replacement["content"] = "新的完整稿保留在轨第 3、6、9 天成像安排。[@E1]"
    new_version = save_scientific_manuscript(
        fixture.topics, first.inquiry_id, replacement, fixture.materials["evidence"]
    )
    result, _, _ = accept_fixed(fixture)
    document, _ = compile_result(result)
    old_render = Renderer(document).render("zh")
    assert "返回后固定样本" in old_render
    assert "新的完整稿" not in old_render
    saved_result = deepcopy(result)
    state = runtime.load(fixture.chief_id)
    new_selection = ReportSelection(main_sections=[new_version])
    composition.prepare(state, new_selection)
    new_result, _, _ = accept_fixed(fixture)
    new_document, _ = compile_result(new_result)
    assert "新的完整稿" in Renderer(new_document).render("zh")
    assert "新的完整稿" not in Renderer(compile_result(saved_result)[0]).render("zh")
    assert runtime.store.inquiry(first.inquiry_id).current_contribution_ref == new_version[
        "contribution_ref"
    ]


class ScriptedCompositionModel:
    def __init__(self, budget, *, parser=False):
        self.budget, self.parser, self.requests = budget, parser, []
        self.config = ModelConfig(
            model="fixed-parser" if parser else "fixed-author",
            base_url="https://unused.invalid", max_tokens=4096, retries=0,
            tools=not parser, structured_output="json_object" if parser else "tool_call",
        )
        self.content = None

    async def complete(self, messages, tools=None, *, response_schema=None):
        assert not tools
        assert bool(response_schema) == self.parser
        assert not self.requests, "Only one author call and one mapper call are allowed."
        ticket = self.budget.reserve(
            self.config, messages=messages, response_schema=response_schema
        )
        self.requests.append(deepcopy(messages))
        reply = ChatReply(
            message={"role": "assistant", "content": self.content},
            prompt_tokens=100, completion_tokens=100,
        )
        self.budget.settle(ticket, reply)
        return reply


async def test_resource_close_uses_frozen_candidates_and_only_two_requests(tmp_path):
    budget = Budget(tmp_path, BudgetLimits(max_model_calls=2, max_embedding_calls=0))
    models = {
        "author": ScriptedCompositionModel(budget),
        "parser": ScriptedCompositionModel(budget, parser=True),
    }
    fixture = prepare_fixture(tmp_path, local_materials(), models=models, budget=budget)
    submission = AssessmentSubmission(fixture.chief, fixture.chief_id)
    state = fixture.runtime.load(fixture.chief_id)
    submission.prepare(state)
    frozen = deepcopy(state.report_candidates)
    replacement = deepcopy(MANUSCRIPTS[1])
    replacement["content"] = "这篇新稿在候选快照之后写成。[@E1]"
    save_scientific_manuscript(
        fixture.topics, fixture.selection.main_sections[1].inquiry_id, replacement,
        fixture.materials["evidence"],
    )
    content, mapping = fixed_manuscript(submission.system, main_sections=["S2"])
    models["author"].content, models["parser"].content = content, json.dumps(mapping)
    await fixture.runtime._write_phase(
        state, models["author"], phase="final_manuscript", operation="submit_assessment",
        context=lambda: fixture.chief._context(fixture.chief_id), submission=submission,
    )
    result = submission.result()
    assert result["report_ref"]
    assert budget.usage()["model_calls"] == 2
    assert len(models["author"].requests) == len(models["parser"].requests) == 1
    assert result["report_selection"]["main_sections"] == [{
        "inquiry_id": frozen[1]["inquiry_id"], "contribution_ref": frozen[1]["contribution_ref"]
    }]
    assert len(result["contributions"]) == 1
    document, _ = compile_result(result)
    text = Renderer(document).render("zh")
    assert "20–40 ng" in text
    assert "这篇新稿" not in text
    assert MANUSCRIPTS[0]["title"] not in [section.title for section in document.main]
    request_text = json.dumps(models["author"].requests[0], ensure_ascii=False)
    assert "scientific_manuscripts" in request_text
    assert "这篇新稿" not in request_text


async def test_no_scientific_manuscripts_yields_partial_without_model_request(tmp_path):
    fixture = prepare_fixture(tmp_path, local_materials())
    manifest = fixture.chief._manifest(fixture.chief_id)
    manifest["topic_ids"] = []
    write_json(fixture.chief._path(fixture.chief_id), manifest)
    submission = AssessmentSubmission(fixture.chief, fixture.chief_id)
    with pytest.raises(ModelError, match="scientific_contribution_required"):
        submission.prepare(fixture.runtime.load(fixture.chief_id))
    result = await fixture.chief.assess(fixture.chief_id, allow_research=False)
    assert result["status"] == "partial"
    assert result["reason"] == "scientific_contribution_required"
    assert result["report_ref"] is None
    assert fixture.runtime.budget.usage()["model_calls"] == 0


def test_focus_only_change_does_not_rename_selected_scientific_argument(tmp_path):
    fixture = prepare_fixture(tmp_path, local_materials())
    first = fixture.selection.main_sections[0]
    fixture.runtime.store.set_focus(first.inquiry_id, "新问题：解释返回后变异检测")
    state = fixture.runtime.load(fixture.chief_id)
    composition = ReportComposition(fixture.chief, fixture.chief_id)
    composition.prepare(state, fixture.selection)
    selected = composition.selected(state)[0]
    assert selected["focus"] == MANUSCRIPTS[0]["focus"]
    assert selected["current_focus"] == "新问题：解释返回后变异检测"
    result, _, _ = accept_fixed(fixture)
    document, _ = compile_result(result)
    citations = {node.target for section in document.main for node in walk_nodes(section.blocks)
                 if node.kind == "evidence"}
    assert set(cited_ids(selected["note"])) <= citations


def test_composition_preview_is_read_only_and_shows_selected_manuscript_once(tmp_path):
    fixture = prepare_fixture(tmp_path, local_materials())
    runtime = fixture.runtime
    stale = runtime.load(fixture.chief_id)
    current = runtime.load(fixture.chief_id)
    runtime.store.set_focus(current.inquiry_id, "连接已完成的科学认识")
    raw_ref = runtime.store.save_author_text(
        current, "新形成的项目理解。[@E1]", "update_research", absorbed_through=0,
    )
    runtime.store.save_note(
        current, research_note(runtime.store.author_text(raw_ref), title="项目理解"),
        absorbed_through=0, author_text_ref=raw_ref,
    )
    current.turns_used = 7
    runtime.store.save(current)
    before = runtime.load(fixture.chief_id).model_dump(mode="json")
    submission = AssessmentSubmission(fixture.chief, fixture.chief_id)
    preview = submission.completion_input(stale, "submit_assessment")
    assert runtime.load(fixture.chief_id).model_dump(mode="json") == before
    assert "新形成的项目理解" in preview[-1]["content"]
    assert not runtime.load(fixture.chief_id).report_candidates

    selected = fixture.selection.main_sections[0]
    note = runtime.store.note(selected.contribution_ref)
    runtime._observe(current, {
        "kind": "author_delivery", "output": {
            "result": {"ok": True, "content": {
                "inquiry_id": selected.inquiry_id, "contribution_ref": selected.contribution_ref,
                "note": note["note"],
            }}, "readings": {},
        },
    })
    submission.prepare(current, fixture.selection)
    view = json.loads(submission.completion_input(current, "submit_assessment")[-1]["content"])
    application = view["task"]["application"]["sources"][0]
    for passage in application["passages"]:
        alias = passage["evidence_handle"]
        assert view["available_readings"][alias]["reference"]["passage_id"] == passage["passage_id"]
        assert alias not in view["originals"]  # The original is already beside its identity.
    assert view["working_context"]["scientific_manuscripts"][0]["note"]
    delivered = view["unabsorbed_observations"][0]["output"]["result"]["content"]
    assert "note" not in delivered
    assert delivered["contribution_ref"] == selected.contribution_ref


def test_selected_appendix_keeps_its_own_manuscript_and_reference_identity(tmp_path):
    fixture = prepare_fixture(tmp_path, local_materials())
    state = fixture.runtime.load(fixture.chief_id)
    selection = ReportSelection(
        main_sections=[fixture.selection.main_sections[0]],
        appendix_sections=[fixture.selection.main_sections[1]],
    )
    AssessmentSubmission(fixture.chief, fixture.chief_id).prepare(state, selection)
    result, _, _ = accept_fixed(fixture)
    document, audit = compile_result(result)
    assert [section.title for section in document.main if section.kind == "scientific"] == [
        MANUSCRIPTS[0]["title"]
    ]
    assert [section.title for section in document.appendix] == [MANUSCRIPTS[1]["title"]]
    assert "20–40 ng" in Renderer(document).render("appendix")
    assert len(document.numbering) == 2
    assert audit["report_selection"] == selection.model_dump(mode="json")


def test_chief_sources_follow_original_paragraph_order_when_mapper_ids_are_unsorted(tmp_path):
    fixture = prepare_fixture(tmp_path, local_materials())
    runtime, state = fixture.runtime, fixture.runtime.load(fixture.chief_id)
    submission = AssessmentSubmission(fixture.chief, fixture.chief_id)
    submission.prepare(state, fixture.selection)
    content, values = fixed_manuscript(submission.system)
    expected = [*values["project"], *values["overall"]]
    values["summary"] = list(reversed(expected))
    raw_ref = runtime.store.save_author_text(
        state, content, "submit_assessment", absorbed_through=state.observation_index
    )
    raw = runtime.store.author_text(raw_ref)
    schema, _, _ = submission.authoring_contract(raw)
    mapping = schema.model_validate(values)
    parse_ref = runtime.store.save_delivery_parse(raw_ref, {}, mapping)
    assert submission.accept_authoring(state, raw_ref, raw, mapping, parse_ref=parse_ref).ok
    result = submission.result()
    assert result["author_sources"]["summary"]["selection"] == expected
    document, audit = compile_result(result)
    summary_origins = [row for row in audit["paragraphs"].values()
                       if row.get("section") == "summary"]
    assert [row["author_paragraph_id"] for row in summary_origins] == expected
    text = plain_text(next(section for section in document.main
                           if section.section_id == "summary").blocks)
    assert text.index("本项目拟") < text.index("本项目的价值")
