"""Continuation and durable literature handoff through the existing review path."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_evidence_acquisition import completed_run, request, snapshot, store_and_reader

from spacebio_review.models.scheduling import RequestScheduler
from spacebio_review.review.evaluation import GBT42048, GENERAL
from spacebio_review.v4.assessment import assessment_request
from spacebio_review.v4.assessment_input import ReviewArchive, freeze_review
from spacebio_review.v4.coordinator import Coordinator
from spacebio_review.v4.handoff import knowledge_artifact
from spacebio_review.v4.records import Records
from spacebio_review.v4.research import Research


def research_fixture(tmp_path, call, literature, *, enabled=True):
    store, reader = store_and_reader(tmp_path)
    records = Records(tmp_path)
    state = records.state
    state["tasks"]["research/q1"] = {"input": "same-input", "status": "running"}
    records.save_state(state)
    execution = SimpleNamespace(
        runtime=SimpleNamespace(
            config=SimpleNamespace(
                formal_routes={"specialist": SimpleNamespace(protocol="cited_parts")}
            )
        ),
        system=GENERAL,
        records=records,
        config=SimpleNamespace(literature_enabled=enabled),
        call=call,
    )
    preparation = SimpleNamespace(
        ex=execution,
        store=store,
        reader=lambda: reader,
        config=SimpleNamespace(evaluation=SimpleNamespace(historical_as_of=None)),
    )
    package = {"experts": {"expert": {"skills": []}}, "project": {"objective": "Compare"}}
    task = {
        "expert_id": "expert",
        "members": {"q1": {}},
        "analysis_scope": "Measurement conditions?",
        "external_needs": [],
        "retained_unknowns": [],
        "task_id": "research/q1",
    }
    return Research(preparation, package, literature), task, reader, records


@pytest.mark.asyncio
async def test_continuation_reuses_originals_and_budget_then_survives_parent_failure(tmp_path):
    scheduler = RequestScheduler()
    questions, remaining = [], []

    async def investigate(question, **kwargs):
        questions.append(question)
        parent_pool = scheduler.research_pool.get()
        assert parent_pool.reserves == {"research/q1": 3}
        child_id = f"child{len(questions)}"
        with scheduler.research_calls(child_id, 30, 1) as child_pool:
            assert child_pool is parent_pool
            child_pool.claim(child_id)
            child_pool.claim(child_id, formal=True)
        remaining.append(parent_pool.remaining)
        run = completed_run()
        run.research_id = child_id
        run.study_id = "child1"
        run.previous_research_id = question.previous_research_id
        run.question = question
        return run

    literature = SimpleNamespace(
        runtime=SimpleNamespace(scheduler=scheduler),
        minimum_turns=lambda **_: 2,
        investigate=investigate,
    )

    async def call(key, kind, objective, contract, **kwargs):
        tools = kwargs["tools"]
        tools.argument_transform = contract.task_context.tool_arguments
        original = next(iter(reader.references.evidence_choices()))
        first = await tools.execute(
            "research_knowledge", request(contract.task_context.aliases[original]).model_dump_json()
        )
        assert first.ok and first.content["research_id"] == "child1"
        assert records.knowledge_refs(parent_task_id=key, research_id="child1")
        followup = request("unused").model_copy(
            update={
                "previous_research_id": "child1",
                "question": "How do the reported controls distinguish the two mechanisms?",
                "evidence_handles": [],
            }
        )
        second = await tools.execute("research_knowledge", followup.model_dump_json())
        assert second.ok
        assert second.content["research_id"] == "child2"
        assert second.content["study_id"] == "child1"
        assert second.content["previous_research_id"] == "child1"
        third = await tools.execute("research_knowledge", followup.model_dump_json())
        assert third.error == "shared_research_turns_reserved_for_completion"
        return SimpleNamespace(status="failed", product=None, reason="parent_failed")

    research, task, reader, records = research_fixture(tmp_path, call, literature)
    with scheduler.research_calls("research/q1", 8, 2) as pool:
        with pytest.raises(ValueError, match="parent_failed"):
            await research.run(task)
        assert pool.remaining == 4
        assert pool.reserves == {"research/q1": 2}
    assert remaining == [6, 4]
    assert len(questions) == 2
    assert questions[0].previous_research_id is None
    assert questions[1].application_statements == questions[0].application_statements
    refs = records.knowledge_refs(question_ids=["q1"])
    assert len(refs) == 2
    assert all(records.value(ref)["origin"] == "delegated" for ref in refs)
    assert records.knowledge_refs(question_ids=["q2"]) == []
    assert not list(records.root.glob("research-*.json"))


@pytest.mark.asyncio
async def test_disabled_literature_has_no_delegate_entry(tmp_path):
    async def call(key, kind, objective, contract, **kwargs):
        names = {row["function"]["name"] for row in kwargs["tools"].schemas()}
        assert "research_knowledge" not in names
        assert "用 research_knowledge" not in objective
        assert "read_review_references" in names
        return SimpleNamespace(status="failed", product=None, reason="inspection_only")

    research, task, _, records = research_fixture(tmp_path, call, SimpleNamespace(), enabled=False)
    with pytest.raises(ValueError, match="inspection_only"):
        await research.run(task)
    assert records.knowledge_refs() == []


def delegated_artifact(*, qid="q1", empty=False, parent_input="current-input"):
    original = snapshot("paper", "At 550 nm and NA 0.4, the calculated resolution is 0.84 um.")
    part = {"text": "Resolution estimate assumes wavelength 550 nm and NA 0.4.", "cites": ["raw"]}
    answer = {
        "contract_version": "literature-5",
        "findings": [] if empty else [{"paragraphs": [{"parts": [part]}]}],
        "scope": {"paragraphs": [{"parts": [part]}]},
        "unknowns": ["The proposal does not establish the objective's numerical aperture."],
        "evidence_resolution": {"raw": original},
    }
    artifact = knowledge_artifact(answer, f"child-{qid}-{empty}", "Optical conditions", [])
    artifact["delegation"] = {
        "parent_task_id": f"research/{qid}",
        "parent_input": parent_input,
        "question_ids": [qid],
        "research_id": f"child-{qid}-{empty}",
        "study_id": f"child-{qid}-{empty}",
        "previous_research_id": None,
        "question": {"question": "What assumptions determine this estimate?"},
    }
    return artifact


@pytest.mark.asyncio
async def test_publish_retains_selected_delegation_and_unknown_only_context(tmp_path, monkeypatch):
    records = Records(tmp_path)
    coordinator = Coordinator(
        SimpleNamespace(
            records=records,
            system=GBT42048,
            evaluation={"system": GBT42048.snapshot(), "prompt": "Review the current proposal"},
            configuration="test-input",
        ),
        manuscript=SimpleNamespace(config=SimpleNamespace(model_dump=lambda **_: {})),
    )
    coordinator._state.update(
        questions={"q1": {}},
        package={},
        tasks={
            "research/q1": {
                "input": "current-input",
                "status": "failed",
                "attempts": 1,
                "reason": "parent failure",
            },
            "research/q2": {"input": "current-input", "status": "failed", "reason": "not selected"},
        },
    )
    coordinator._state["question_batch"] = records.put(
        "question-batch",
        {
            "questions": [
                {
                    "question": "Optical method?",
                    "expert_id": "optics",
                    "aspects": ["experiment_feasibility"],
                }
            ]
        },
    )
    coordinator.save()
    selected = [
        records.put("knowledge", delegated_artifact(empty=empty)) for empty in (False, True)
    ]
    excluded = records.put("knowledge", delegated_artifact(qid="q2"))
    stale = records.put("knowledge", delegated_artifact(parent_input="old-input"))
    assert set(records.knowledge_refs(question_ids=["q1"])) == set(selected)

    async def inspect(caller, frozen_ref):
        frozen = records.value(frozen_ref)
        assert set(frozen["provenance"]["knowledge_refs"]) == set(selected)
        assert excluded not in str(frozen) and stale not in str(frozen)
        assert frozen["provenance"]["research_refs"] == []
        assert set(records.get(frozen_ref)["dependencies"]) == set(selected)
        prompt, material = assessment_request(frozen)
        assert "not_formed" in prompt
        groups = material["research"]
        assert len(groups) == 2 and sum(bool(g["arguments"]) for g in groups.values()) == 1
        assert all(g["origin"] == "delegated" for g in groups.values())
        assert all(g["professional_status"] == "not_formed" for g in groups.values())
        assert all(g["unknowns"] and g["scope"] and g["originals"] for g in groups.values())
        assert "550 nm and NA 0.4" in str(material)
        assert "0.84 um" in str(material)
        coverage = material["research_coverage"]["experiment_feasibility"]
        assert coverage["status"] == "unassessed"
        assert coverage["opinion_ids"] == []
        assert set(coverage["delegated_context_ids"]) == set(groups)
        raise RuntimeError("stop_before_model_call")

    monkeypatch.setattr("spacebio_review.v4.assessment.synthesize", inspect)
    result = await coordinator.publish([], tmp_path)
    assert result["research_status"] == "failed"
    assert result["results"] == {}
    assert coordinator.state["tasks"]["assessment/current"]["reason"] == (
        "RuntimeError: stop_before_model_call"
    )


def test_archive_knowledge_query_is_read_only_and_preserves_legacy_artifacts(tmp_path):
    records = Records(tmp_path)
    state = records.state
    state["tasks"]["research/q1"] = {"input": "current-input", "status": "failed"}
    records.save_state(state)
    artifact = delegated_artifact()
    current = records.put("knowledge", artifact)
    legacy = deepcopy(artifact)
    legacy.pop("delegation")
    historical = records.put("knowledge", legacy)
    before = {p: p.read_bytes() for p in records.root.rglob("*") if p.is_file()}
    archive = ReviewArchive(tmp_path)
    assert archive.knowledge_refs(question_ids=["q1"]) == [current]
    assert archive.value(historical) == legacy
    frozen = freeze_review(archive, [], [], {}, delegated_refs=[current])
    assert len(frozen["materials"]["opinions"]) == 1
    assert {p: p.read_bytes() for p in records.root.rglob("*") if p.is_file()} == before
