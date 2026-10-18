"""Inquiry identity through local topic/reader/chief boundaries, without model calls."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from spacebio_review.evidence.store import SourceStore
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument
from spacebio_review.models.config import ModelConfig
from spacebio_review.persistence import write_json
from spacebio_review.prose import cited_ids
from spacebio_review.v5.assessment import AssessmentSubmission
from spacebio_review.v5.budget import Budget, SessionAllowance
from spacebio_review.v5.chief import ChiefReview, TopicBatch
from spacebio_review.v5.records import ReportSelection, ResearchNote
from spacebio_review.v5.session import ResearchRuntime
from spacebio_review.v5.source_reading import SourceQuestion
from spacebio_review.v5.topic import ApplicationRef, TopicResearch, TopicTask


@pytest.fixture
def local_topics(tmp_path):
    model = SimpleNamespace(config=ModelConfig(
        model="local", base_url="https://unused.invalid", max_tokens=128, retries=0
    ))
    runtime = ResearchRuntime(
        tmp_path, {"local": model}, {"chief": "local", "researcher": "local"},
        budget=Budget(tmp_path),
        allowances={"researcher": SessionAllowance(completion_input_tokens=1000)},
    )
    sources = SourceStore(tmp_path / "sources", client=None, env_file=None)
    application = sources.register(SourceCandidate(title="Application", kind="application"))
    sources._save(SourceDocument(
        source_id=application, candidate=sources.candidate(application), availability="full_text",
        passages=[
            Passage(passage_id="p1", locator="Methods / p1", text="Live imaging occurs in orbit."),
            Passage(
                passage_id="p2", locator="Methods / p2", text="Returned samples are sequenced."
            ),
        ],
    ))
    paper = sources.register(SourceCandidate(title="Methods paper", abstract="A measured signal."))
    sources._save(SourceDocument(
        source_id=paper, candidate=sources.candidate(paper), availability="full_text",
        passages=[Passage(passage_id="p1", locator="Results / p1", text="A measured signal.")],
    ))
    topics = TopicResearch(runtime, sources, None, application_ids=[application])
    return topics, application, paper


def create_topic(topics, application, paper, **kwargs):
    return topics.create(TopicTask(
        objective="Investigate the assumed returned fixed-sample imaging",
        impact="Determine whether fixed-sample compatibility limits the project",
        application_refs=[ApplicationRef(source_id=application, passage_id="p1")],
        source_ids=[paper], **kwargs,
    ))


def save_contribution(topics, inquiry_id, focus="Explain live imaging in orbit"):
    topics.runtime.store.set_focus(inquiry_id, focus)
    state = topics.state(inquiry_id)
    alias = next(iter(state.aliases))
    note = ResearchNote.model_validate({
        "title": "Scientific explanation", "body": {"paragraphs": [{"parts": [
            {"text": "The imaging stage supports longitudinal observation.", "cites": [alias]}
        ]}]},
    })
    return topics.runtime.store.save_note(state, note, absorbed_through=state.observation_index)


def test_topic_identity_and_focus_replace_initial_assignment_in_live_context(local_topics):
    topics, application, paper = local_topics
    inquiry_id = create_topic(topics, application, paper)
    state, inquiry = topics.state(inquiry_id), topics.inquiry(inquiry_id)
    assert inquiry_id.startswith("inquiry-")
    assert state.session_id != inquiry_id
    assert state.inquiry_id == inquiry_id
    assert inquiry.budget_account_id == state.session_id
    assert inquiry.assignment["objective"].startswith("Investigate the assumed")
    assert inquiry.application_anchors == [{"source_id": application, "passage_id": "p1"}]
    assert "task" not in topics._task(inquiry_id)
    assert "fixed-sample" not in state.task.objective
    assert "scientific_use" not in json.loads(state.task.evaluation)
    assert state.task.application["sources"][0]["passages"][0]["text"].startswith("Live imaging")

    access = topics.access(inquiry_id)
    topics.runtime.store.set_focus(inquiry_id, "Explain what live imaging in orbit can reveal")
    view = topics.runtime._context(
        topics.state(inquiry_id), lambda: topics._context(inquiry_id), purpose="composition"
    )
    assert access.objective == "Explain what live imaging in orbit can reveal"
    assert "fixed-sample" not in json.dumps(view)
    assert topics.inquiry(inquiry_id).assignment == inquiry.assignment


def test_current_contribution_is_readable_before_final_submission_and_after_reframing(local_topics):
    topics, application, paper = local_topics
    inquiry_id = create_topic(topics, application, paper)
    ref = save_contribution(topics, inquiry_id)
    assert topics.state(inquiry_id).submissions == []
    topics.runtime.store.set_focus(inquiry_id, "Explain returned-sample sequencing")
    result = topics.result(inquiry_id)
    assert result.inquiry_id == result.topic_id == inquiry_id
    assert result.note_ref == ref
    assert result.current_focus == "Explain returned-sample sequencing"
    assert result.contribution_focus == "Explain live imaging in orbit"
    assert result.author_session_id == topics.state(inquiry_id).session_id
    current = topics.completed()[0]
    assert current["note_ref"] == ref
    assert current["objective"] == result.contribution_focus
    assert current["body"] == result.note["body"]

    next_id = create_topic(topics, application, paper, research_ids=[inquiry_id])
    reused = json.loads(topics.state(next_id).task.evaluation)["existing_research"][0]
    assert reused["body"]["paragraphs"][0]["parts"][0]["text"].startswith("The imaging stage")
    assert reused["body"]["paragraphs"][0]["parts"][0]["cites"][0] in topics.state(next_id).aliases


def test_supplemented_anchors_and_reader_belong_to_the_question_and_original_budget(local_topics):
    topics, application, paper = local_topics
    inquiry_id = create_topic(topics, application, paper)
    original = topics.inquiry(inquiry_id).assignment
    owner = topics.state(inquiry_id).session_id
    topics.runtime._messages[owner] = ["stale context"]
    topics.supplement_application(
        inquiry_id, [ApplicationRef(source_id=application, passage_id="p2")]
    )
    inquiry = topics.inquiry(inquiry_id)
    assert inquiry.assignment == original
    assert [ref["passage_id"] for ref in inquiry.application_anchors] == ["p1", "p2"]
    assert topics.runtime._messages[owner] == ["stale context"]
    state = topics.state(inquiry_id)
    assert topics.refresh_application_basis(inquiry_id, state)
    topics.runtime.store.save(state)

    topics.runtime.budget.register("shared-question-account", SessionAllowance())
    inquiry.budget_account_id = "shared-question-account"
    topics.runtime.store.save_inquiry(inquiry)
    accounts_before = set(topics.runtime.budget.data["sessions"])
    reading_id = topics.reader.create(inquiry_id, SourceQuestion(
        source_id=paper, question="Explain the measurement and its experimental conditions"
    ))
    reader = topics.runtime.load(reading_id)
    assert reader.inquiry_id is None
    assert reader.budget_owner == "shared-question-account"
    assert set(topics.runtime.budget.data["sessions"]) == accounts_before
    assert topics.reader.manifest(reading_id)["inquiry_id"] == inquiry_id
    assert topics._task(inquiry_id)["reading_ids"] == [reading_id]
    assert [source["passages"][0]["passage_id"]
            for source in reader.task.application["application_basis"]] == ["p1", "p2"]


def test_supplemented_anchors_update_the_running_state_without_overwriting_progress(local_topics):
    topics, application, paper = local_topics
    inquiry_id = create_topic(topics, application, paper)
    running = topics.state(inquiry_id)
    running.turns_used = 4
    running.observations.append({"index": 1, "kind": "scientific_progress", "content": "Live work"})
    running.observation_index = 1
    topics.supplement_application(
        inquiry_id, [ApplicationRef(source_id=application, passage_id="p2")]
    )
    # The independently running author can still save its state before the next boundary.
    topics.runtime.store.save(running)
    assert topics.refresh_application_basis(inquiry_id, running)
    topics.runtime.store.save(running)
    saved = topics.state(inquiry_id)
    assert saved.turns_used == 4
    assert saved.observations == running.observations
    assert [row["passages"][0]["passage_id"]
            for row in json.loads(saved.task.evaluation)["application_basis"]] == ["p1", "p2"]
    assert not topics.refresh_application_basis(inquiry_id, running)


async def test_chief_registration_returns_without_running_or_waiting_for_authors(
    local_topics, monkeypatch
):
    topics, application, paper = local_topics
    inquiry_id = create_topic(topics, application, paper)
    ref = save_contribution(topics, inquiry_id)
    owner = topics.state(inquiry_id).session_id
    scheduled = []

    async def run(session_id, **kwargs):
        raise AssertionError("Registering work must not execute or wait for an author.")

    def schedule(topic_id, followup=None):
        scheduled.append((topic_id, followup))
        return {
            "inquiry_id": topic_id, "request_id": f"request-{len(scheduled)}", "status": "queued",
        }

    monkeypatch.setattr(topics.runtime, "run", run)
    monkeypatch.setattr(topics.collaboration, "schedule", schedule)
    chief = ChiefReview(topics, admission={"status": "skipped"})
    chief_id = chief.create("Understand the scientific contribution")
    manifest = chief._manifest(chief_id)
    manifest.update(topic_ids=[inquiry_id])
    write_json(chief._path(chief_id), manifest)
    result = await chief.tools(chief_id).execute("research_topics", json.dumps({"requests": [
        {"topic_id": inquiry_id, "followup": "Explain the time course"},
        {"topic_id": inquiry_id, "followup": "Explain the time course"},
    ]}))
    assert result.ok
    assert scheduled == [(inquiry_id, "Explain the time course")] * 2
    assert [row["request_id"] for row in result.content["topics"]] == ["request-1", "request-2"]
    assert topics.runtime.load(chief_id).coordination_phase == "synthesis"
    assert topics.runtime.budget.data["sessions"][owner]["active"] is True
    assert chief._coverage(chief_id)[0]["current_focus"] == "Explain live imaging in orbit"
    assert chief._context(chief_id)["topics"][0]["inquiry_id"] == inquiry_id
    _, _, mapping_context = AssessmentSubmission(chief, chief_id).authoring_contract({})
    assert mapping_context["candidate_options"] == [
        {"candidate_id": "S1", "inquiry_id": inquiry_id, "contribution_ref": ref}
    ]

    assert "pending_batch" not in chief._manifest(chief_id)
    assert topics.result(inquiry_id, note_ref=ref).note_ref == ref
    fresh_service = TopicResearch(
        topics.runtime, topics.store, None, application_ids=[application]
    )
    assert fresh_service.completed() == []
    with pytest.raises(KeyError):
        fresh_service.access(inquiry_id)


async def test_chief_assigns_existing_request_once_with_explicit_new_topic_budget(
    local_topics, monkeypatch
):
    topics, application, paper = local_topics
    chief = ChiefReview(topics, admission={"status": "skipped"})
    chief_id = chief.create("Arrange open scientific responsibilities")
    assignments = []

    def assign(request_id, inquiry_id):
        assignments.append((request_id, inquiry_id))
        return {"request_id": request_id, "inquiry_id": inquiry_id, "status": "queued"}

    def duplicate_schedule(*args, **kwargs):
        raise AssertionError("Assigning a consultation must not send another followup.")

    monkeypatch.setattr(topics.collaboration, "assign", assign)
    monkeypatch.setattr(topics.collaboration, "schedule", duplicate_schedule)
    result = await chief._research(chief_id, TopicBatch.model_validate({"requests": [{
        "request_id": "request-unassigned",
        "task": {
            "objective": "Explain dynamic information from live imaging",
            "application_refs": [{"source_id": application, "passage_id": "p1"}],
            "source_ids": [paper],
        },
        "allowance": {"max_input_tokens": 12000, "completion_input_tokens": 1000},
    }]}))
    assert result.ok
    inquiry_id = result.content["topics"][0]["topic_id"]
    assert assignments == [("request-unassigned", inquiry_id)]
    assert chief._manifest(chief_id)["topic_ids"] == [inquiry_id]
    account = topics.inquiry(inquiry_id).budget_account_id
    assert topics.runtime.budget.data["sessions"][account]["allowance"]["max_input_tokens"] == 12000
    assert topics.state(inquiry_id).status == "ready"


async def test_waiting_segment_releases_topic_permit_and_passes_owner_hook(
    local_topics, monkeypatch
):
    topics, application, paper = local_topics
    first = create_topic(topics, application, paper)
    second = create_topic(topics, application, paper)
    topics._parallel = asyncio.Semaphore(1)
    hook, calls = object(), []

    async def run(session_id, **kwargs):
        calls.append((session_id, kwargs["collaboration"]))
        assert topics._parallel.locked()
        state = topics.runtime.load(session_id)
        state.status = "waiting"
        topics.runtime.store.save(state)

    monkeypatch.setattr(topics.runtime, "run", run)
    assert (await topics._run_segment(first, collaboration=hook)).status == "waiting"
    assert not topics._parallel.locked()
    assert (await topics._run_segment(second, collaboration=hook)).status == "waiting"
    assert calls == [
        (topics.state(first).session_id, hook), (topics.state(second).session_id, hook),
    ]


def test_owner_transfer_keeps_budget_public_material_and_exact_old_versions(
    local_topics, monkeypatch
):
    topics, application, paper = local_topics
    first = create_topic(topics, application, paper)
    save_contribution(topics, first)
    inquiry_id = create_topic(topics, application, paper, research_ids=[first])
    original_ref = save_contribution(topics, inquiry_id)
    old = topics.state(inquiry_id)
    original_aliases = dict(old.aliases)
    topics.runtime._observe(old, {
        "kind": "research_observation", "content": "Clarify the timing of this observation [E1].",
    })
    budget_before = topics.runtime.budget.usage(old.session_id)
    topics.runtime.models["chief-author"] = SimpleNamespace(config=ModelConfig(
        model="chief-author", base_url="https://unused.invalid", max_tokens=128, retries=0
    ))
    topics.runtime.routes["chief"] = "chief-author"
    new_id = topics.transfer_owner(
        inquiry_id, reason="Connect the observation to the project judgment", model_role="chief"
    )
    new = topics.state(inquiry_id)
    assert new.session_id == new_id != old.session_id
    assert new.task.role == "researcher" and new.model_alias == "chief-author"
    assert topics.inquiry(inquiry_id).budget_account_id == old.session_id
    assert topics.runtime.budget.usage(old.session_id) == budget_before
    assert new_id not in topics.runtime.budget.data["sessions"]
    assert new.aliases == original_aliases
    assert new.observations[0] == old.observations[0]
    existing = json.loads(new.task.evaluation)["existing_research"][0]
    assert set(cited_ids(existing["body"])) <= set(new.aliases)
    latest_ref = save_contribution(topics, inquiry_id, focus="Explain the temporal mechanism")
    assert topics.contribution_refs(inquiry_id) == [latest_ref, original_ref]
    assert topics.runtime.store.note(latest_ref)["previous_ref"] == original_ref
    assert topics.state(inquiry_id).submissions == []

    chief = ChiefReview(topics, admission={"status": "skipped"})
    chief_id = chief.create("Choose a precise scientific contribution")
    manifest = chief._manifest(chief_id)
    manifest["topic_ids"] = [inquiry_id]
    write_json(chief._path(chief_id), manifest)
    submission = AssessmentSubmission(chief, chief_id)
    assert [row["contribution_ref"] for row in submission.composition.catalogue()] == [
        latest_ref, original_ref,
    ]
    # Select the previous author's precise version; no scoring logic is involved here.
    monkeypatch.setattr("spacebio_review.v5.assessment.finalize_assessment", lambda draft, _: draft)
    chief_state = topics.runtime.load(chief_id)
    submission.prepare(chief_state, ReportSelection(main_sections=[{
        "inquiry_id": inquiry_id, "contribution_ref": original_ref,
    }]))
    raw_ref = topics.runtime.store.save_author_text(
        chief_state, "A supplied chief manuscript.", "submit_assessment", absorbed_through=0
    )
    submission._finalize(
        chief_state, {}, raw_ref=raw_ref, parse_ref="parse-local",
        mapping=SimpleNamespace(model_dump=lambda **_: {}),
    )
    artifact = submission._artifact(topics.runtime.load(chief_id).assessment_ref)
    assert artifact["contributions"][0]["contribution_ref"] == original_ref
    assert artifact["contributions"][0]["author_session_id"] == old.session_id


async def test_source_reader_follows_up_completed_work_but_does_not_restart_partial_work(
    local_topics, monkeypatch
):
    topics, application, paper = local_topics
    inquiry_id = create_topic(topics, application, paper)
    request = SourceQuestion(source_id=paper, question="Explain the observed signal")
    reading_id = topics.reader.create(inquiry_id, request)
    calls = []

    async def run(session_id, **kwargs):
        calls.append((session_id, kwargs["followup"]))
        state = topics.runtime.load(session_id)
        alias = next(iter(state.aliases))
        topics.runtime.store.save_note(state, ResearchNote.model_validate({
            "title": "Source explanation", "body": {"paragraphs": [{"parts": [{
                "text": "The measurement distinguishes conditions.", "cites": [alias],
            }]}]},
        }), absorbed_through=state.observation_index)
        state.status = "submitted"
        topics.runtime.store.save(state)

    monkeypatch.setattr(topics.runtime, "run", run)
    reused = request.model_copy(update={"reading_id": reading_id})
    assert (await topics.reader.research(inquiry_id, reused)).ok
    assert calls == [(reading_id, None)]
    assert (await topics.reader.research(inquiry_id, reused)).ok
    assert len(calls) == 1
    followup = reused.model_copy(update={"question": "Explain the calibration controls"})
    assert (await topics.reader.research(inquiry_id, followup)).ok
    assert calls[-1] == (reading_id, followup.question)
    assert topics.reader.access(reading_id).objective == followup.question

    state = topics.runtime.load(reading_id)
    state.status, state.reason = "partial", "interrupted"
    topics.runtime.store.save(state)
    assert (await topics.reader.research(inquiry_id, followup)).ok
    assert len(calls) == 2


async def test_research_close_freezes_available_manuscripts_before_resources_close(local_topics):
    topics, application, paper = local_topics
    inquiry_id = create_topic(topics, application, paper)
    no_draft_id = create_topic(topics, application, paper)
    prior_ref = save_contribution(topics, inquiry_id)
    selected_ref = save_contribution(topics, inquiry_id, focus="Explain the temporal mechanism")
    chief = ChiefReview(topics, admission={"status": "skipped"})
    chief_id = chief.create("Compose the supported project judgment")
    manifest = chief._manifest(chief_id)
    manifest["topic_ids"] = [inquiry_id, no_draft_id]
    write_json(chief._path(chief_id), manifest)
    resource_closed = []

    async def close_resource():
        assert topics.collaboration.closed
        assert topics.collaboration.selected_contributions == {inquiry_id: selected_ref}
        resource_closed.append(True)

    topics._owned = [SimpleNamespace(close=close_resource)]
    await topics.close()
    assert resource_closed == [True]
    late_ref = save_contribution(topics, inquiry_id, focus="A late scientific revision")
    late_first_ref = save_contribution(topics, no_draft_id, focus="A late first contribution")
    assert topics.inquiry(inquiry_id).current_contribution_ref == late_ref
    assert topics.result(inquiry_id).note_ref == selected_ref
    assert topics.result(inquiry_id).contribution_focus == "Explain the temporal mechanism"
    assert topics.result(inquiry_id, note_ref=late_ref).note_ref == late_ref
    assert topics.result(no_draft_id).note_ref is None
    assert topics.result(no_draft_id, note_ref=late_first_ref).note_ref == late_first_ref
    assert topics.contribution_refs(inquiry_id) == [selected_ref, prior_ref]
    assert [row["note_ref"] for row in topics.completed()] == [selected_ref]

    submission = AssessmentSubmission(chief, chief_id)
    assert [row["contribution_ref"] for row in submission.context()["scientific_manuscripts"]] == [
        selected_ref, prior_ref,
    ]
    _, _, contract = submission.authoring_contract({})
    assert contract["candidate_options"] == [
        {"candidate_id": "S1", "inquiry_id": inquiry_id, "contribution_ref": selected_ref},
    ]
    with pytest.raises(ValueError, match="report_contribution_not_available"):
        submission.prepare(
            topics.runtime.load(chief_id),
            ReportSelection(main_sections=[{
                "inquiry_id": inquiry_id, "contribution_ref": late_ref,
            }]),
        )
