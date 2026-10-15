"""One specialist handoff, selected originals, and a single literature submission."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from spacebio_review.agents.skills import SkillLibrary
from spacebio_review.agents.types import TaskResult
from spacebio_review.evidence.knowledge import KnowledgeAnswer, KnowledgeQuestion, KnowledgeRun
from spacebio_review.evidence.literature import LiteratureResearcher
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument
from spacebio_review.models.config import load_config
from spacebio_review.models.scheduling import RequestScheduler
from spacebio_review.reference_contract import SourceScope
from spacebio_review.review.access import ReviewAccess
from spacebio_review.review.evaluation import GENERAL
from spacebio_review.tools import ToolRegistry
from spacebio_review.v4.handoff import literature_question
from spacebio_review.v4.records import Records
from spacebio_review.v4.research import LiteratureRequest, Research


def snapshot(sid, quote, *, kind="external", pid="p1", scope="text"):
    return {
        "reference": {"source_id": sid, "passage_id": pid, "visual_id": None},
        "title": sid,
        "kind": kind,
        "locator": "Methods",
        "quote": quote,
        "reading_scope": scope,
        "material_version": "immutable-version",
    }


def store_and_reader(tmp_path):
    docs = {
        sid: SourceDocument(
            source_id=sid,
            candidate=SourceCandidate(
                title=sid, kind="application" if sid == "app" else "literature"
            ),
            passages=[Passage(passage_id="p1", text=text, locator="Methods")],
            availability="full_text",
        )
        for sid, text in {
            "app": "Observe days 3, 6 and 9; storage conditions are not specified.",
            "selected": "Optimal at 20 pg/uL for human control-region targets.",
            "candidate": "An unselected search abstract.",
        }.items()
    }
    store = SimpleNamespace(
        root=tmp_path / "sources",
        candidate=lambda sid: docs[sid].candidate,
        retained=lambda sid: docs.get(sid),
        related_versions=lambda sid: [],
    )
    scope = SourceScope(store, lambda: {"app"}, lambda: {("app", "p1", None)})
    scope.capture_passages("app", docs["app"].passages)
    reader = SimpleNamespace(
        docs={"app": docs["app"]},
        references=scope,
        read_locations=scope.locations,
        tools=lambda: ToolRegistry(),
        coverage=lambda: {},
    )
    return store, reader


def request(handle):
    return LiteratureRequest(
        question="What does this method measure?",
        purpose="method",
        needed_information=["Measured targets and applicable conditions"],
        changes_judgment_if="Whether observations support the intended comparison",
        evidence_handles=[handle],
    )


def completed_run(evidence=None):
    answer = KnowledgeAnswer(
        contract_version="scientific-3",
        findings=[{"explanation": "Only within the measured conditions.", "basis": []}],
        applicability=["Transfer to other targets is not established."],
        unknowns=["Other target conditions"],
        reading_scope="selected",
        stop_reason="done",
        evidence_resolution=evidence or {},
    )
    result = TaskResult(task_id="child", agent_id="child", status="completed", reason="done")
    return KnowledgeRun(
        research_id="child",
        question=KnowledgeQuestion(question="Method conditions?"),
        answer=answer,
        task_result=result,
    )


def test_request_selects_application_original_without_rewriting(tmp_path):
    _, reader = store_and_reader(tmp_path)
    originals = reader.references.evidence_choices()
    handle = next(iter(originals))
    base = KnowledgeQuestion(question="Parent", background="Organoids", cutoff_date="2023-01-01")
    question = literature_question(request(handle), base, originals)
    assert question.application_statements == {handle: originals[handle]}
    assert question.background == ""
    assert "Organoids" not in question.model_dump_json()
    assert "known_project_facts" not in question.model_dump_json()
    assert question.cutoff_date == base.cutoff_date
    assert question.purpose == "method"
    with pytest.raises(ValueError, match="application_original_required"):
        literature_question(request("external"), base, {"external": snapshot("paper", "Claim")})


def test_review_question_roundtrip_preserves_exact_records_but_legacy_stays_legacy(tmp_path):
    from spacebio_review.evidence.knowledge import ReviewKnowledgeQuestion

    _, reader = store_and_reader(tmp_path)
    originals = reader.references.evidence_choices()
    handle = next(iter(originals))
    current = literature_question(request(handle), KnowledgeQuestion(question="Parent"), originals)
    run = completed_run()
    run.question = current
    restored = KnowledgeRun.model_validate_json(run.model_dump_json())
    assert isinstance(restored.question, ReviewKnowledgeQuestion)
    assert restored.question.model_dump() == current.model_dump()
    historical = {
        "text": "An old summary rather than an exact original.",
        "kind": "interpretation",
        "support_explanation": "Legacy summary",
    }
    run.question = KnowledgeQuestion(question="Old", known_project_facts=[historical])
    legacy = KnowledgeRun.model_validate_json(run.model_dump_json())
    assert type(legacy.question) is KnowledgeQuestion
    assert legacy.question.known_project_facts[0].text == historical["text"]
    assert "application_statements" not in legacy.question.model_dump()


def test_application_statements_are_originals_once_and_read_qualified(tmp_path):
    from spacebio_review.evidence.literature import KnowledgeSession
    from spacebio_review.evidence.submissions import scientific_contract

    store, reader = store_and_reader(tmp_path)
    originals = reader.references.evidence_choices()
    handle = next(iter(originals))
    question = literature_question(request(handle), KnowledgeQuestion(question="Parent"), originals)
    researcher = SimpleNamespace(
        store=store,
        discovery=None,
        index=None,
        runtime=SimpleNamespace(models={"task/rerank/normal": None}),
        config=SimpleNamespace(max_actions=12),
    )
    session = KnowledgeSession(researcher, question)
    assert session.read_locations() == {("app", "p1", None)}
    assert session.references.submission_choices()[handle] == originals[handle]
    contract = scientific_contract(session.references, KnowledgeAnswer, session=session)
    messages = contract.task_context.messages(
        [{"role": "user", "content": question.model_dump_json()}], formal=True
    )
    body = json.dumps(messages)
    assert body.count(originals[handle]["quote"]) == 1
    assert "known_project_facts" not in body
    request_view = next(
        json.loads(message["content"])
        for message in messages
        if "application_statements" in message["content"]
    )
    assert request_view["application_statements"] == [{"evidence_ref": "E1"}]


@pytest.mark.asyncio
async def test_real_specialist_wire_keeps_topic_separate_from_original_and_delegation(tmp_path):
    from copy import deepcopy

    from test_question_generation import execution, package, preparation

    from spacebio_review.agents import AgentRuntime
    from spacebio_review.models.provider import ChatReply
    from spacebio_review.v4.questions import research_task, select_questions

    requests, delegated = [], []

    class StopObservation(BaseException):
        pass

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, **kwargs):
            requests.append({"messages": deepcopy(messages), "tools": deepcopy(tools)})
            if len(requests) > 1:
                raise StopObservation()
            return ChatReply(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "knowledge-request",
                            "type": "function",
                            "function": {
                                "name": "research_knowledge",
                                "arguments": request("E1").model_dump_json(),
                            },
                        }
                    ],
                },
                "tool_calls",
                prompt_tokens=10,
                completion_tokens=10,
            )

        async def close(self):
            pass

    runtime = AgentRuntime.from_config(load_config("config/runtime.yaml"), model_factory=Model)
    ex = execution(tmp_path, runtime)
    prep = preparation(ex)
    prep.store = prep.reader().references.store
    prep.config = SimpleNamespace(evaluation=SimpleNamespace(historical_as_of="2023-01-01"))
    doc = prep.store.retained("application")
    doc.candidate.kind = "application"
    source_reader = prep.reader()
    source_reader.record_context_read(
        {"source_id": "application", "passages": [p.model_dump() for p in doc.passages]}
    )
    originals = source_reader.references.snapshot_choices()
    value = package()
    value["project"]["objective"] = "DERIVED_REVIEW_OPINION"
    value["project"]["paths"] = ["DERIVED_METHOD_FAILURE"]
    value["question_evidence"] = originals
    topic = {
        "question": "WRONG_TOPIC_PREMISE?",
        "expert_id": "expert-1",
        "aspects": ["rigor"],
        "evidence_handles": list(originals),
    }
    task = research_task("q1", select_questions({"questions": [topic]}, 10)["q1"])

    async def investigate(question, **kwargs):
        delegated.append(question)
        run = completed_run()
        run.answer = None
        run.task_result.status = "failed"
        run.task_result.reason = "no_effective_answer"
        run.limitations = ["No usable external finding was obtained."]
        return run

    literature = SimpleNamespace(
        runtime=runtime, minimum_turns=lambda **_: 3, investigate=investigate
    )
    try:
        with pytest.raises(StopObservation):
            await Research(prep, value, literature).run(task)
    finally:
        await runtime.close()
    assert len(requests) == 2 and len(delegated) == 1
    first = json.dumps(requests[0]["messages"])
    assert "WRONG_TOPIC_PREMISE" in first
    assert doc.passages[0].text in first
    assert "DERIVED_REVIEW_OPINION" not in first and "DERIVED_METHOD_FAILURE" not in first
    assert "professional-input-5" in first
    names = {tool["function"]["name"] for tool in requests[0]["tools"]}
    assert {"read_materials", "browse_materials", "research_knowledge"} <= names
    child = delegated[0].model_dump(mode="json")
    assert "WRONG_TOPIC_PREMISE" not in json.dumps(child)
    assert child["background"] == "" and child["cutoff_date"] == "2023-01-01"
    assert child["application_statements"] == originals
    assert child["question"] == request("E1").question
    tool_response = next(m for m in requests[1]["messages"] if m["role"] == "tool")
    result = json.loads(tool_response["content"])
    assert result["ok"] is False and result["content"]["answer"] is None
    assert result["error"] == "no_effective_answer"


@pytest.mark.asyncio
async def test_only_selected_originals_are_delivered_and_rereadable(tmp_path):
    store, reader = store_and_reader(tmp_path)
    access = ReviewAccess(store, reader)
    selected = snapshot("selected", "Optimal at 20 pg/uL for human control-region targets.")
    run = completed_run({"exact-selected": selected})
    # The child's full ledger is audit data, not the parent's read set.
    from spacebio_review.evidence.knowledge import ReadingRecord

    run.sources = {
        sid: ReadingRecord(source=store.candidate(sid), passage_ids=["p1"])
        for sid in ("selected", "candidate")
    }
    answer = access.register_knowledge(run)
    assert answer["evidence_resolution"] == {"exact-selected": selected}
    assert access.visible_sources() == {"app", "selected"}
    assert set(access.reading_records()) == {"selected"}
    tools = access.tools()
    assert await tools.material_providers[0]("candidate") is None
    assert (await tools.material_providers[0]("selected"))["passages"][0]["text"] == selected[
        "quote"
    ]
    assert access.source_scope.evidence_choices()["exact-selected"] == selected


@pytest.mark.asyncio
async def test_selected_abstract_and_failure_keep_actual_scope(tmp_path):
    store, reader = store_and_reader(tmp_path)
    access = ReviewAccess(store, reader)
    abstract = snapshot(
        "candidate", "Available abstract only.", pid="abstract-1", scope="metadata/abstract"
    )
    access.register_knowledge(completed_run({"abstract": abstract}))
    material = await access.tools().material_providers[0]("candidate")
    assert material["passages"][-1]["text"] == abstract["quote"]
    assert (
        access.source_scope.evidence_choices()["abstract"]["reading_scope"] == "metadata/abstract"
    )
    failed = completed_run()
    failed.answer = None
    before = access.read_locations()
    assert access.register_knowledge(failed) is None
    assert access.read_locations() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["none", "success", "budget", "error"])
async def test_specialist_has_one_external_entry_and_originals_in_same_response(tmp_path, outcome):
    store, reader = store_and_reader(tmp_path)
    scheduler = RequestScheduler()
    calls = []
    selected = snapshot("selected", store.retained("selected").passages[0].text)

    async def investigate(question, **kwargs):
        calls.append((question, kwargs))
        assert scheduler.research_pool.get().reserves["parent"] == 3
        if outcome == "error":
            raise RuntimeError("fixture_child_failed")
        return completed_run({"exact-selected": selected})

    literature = SimpleNamespace(
        runtime=SimpleNamespace(scheduler=scheduler),
        minimum_turns=lambda **kwargs: 3,
        investigate=investigate,
    )
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            formal_routes={"specialist": SimpleNamespace(protocol="cited_parts")}
        )
    )

    async def call(key, kind, objective, contract, **kwargs):
        tools = kwargs["tools"]
        assert {s["function"]["name"] for s in tools.schemas()} == {
            "read_review_references",
            "research_knowledge",
            "read_retained_visual",
        }
        assert all("acquire" not in s["function"]["name"] for s in tools.schemas())
        if outcome != "none":
            original = next(iter(reader.references.evidence_choices()))
            tools.argument_transform = contract.task_context.tool_arguments
            alias = contract.task_context.aliases[original]
            response = await tools.execute("research_knowledge", request(alias).model_dump_json())
            if outcome in {"budget", "error"}:
                assert not response.ok
                assert response.error == (
                    "shared_research_turns_reserved_for_completion"
                    if outcome == "budget"
                    else "tool_execution_failed"
                )
                assert "exact-selected" not in contract.task_context.evidence
                return SimpleNamespace(status="failed", product=None, reason="fixture_stop")
            assert response.ok, response.error
            delivered = response.content["answer"]["evidence_resolution"]["exact-selected"]
            assert delivered["quote"] == selected["quote"]
            assert delivered["material_version"] == selected["material_version"]
            assert contract.task_context.evidence["exact-selected"] == selected
            assert response.source_ids == ["selected"]
            assert selected["quote"] in json.dumps(contract.task_context.source_view())
            assert set(kwargs["reading"]()["literature"]) == {"selected"}
        return SimpleNamespace(status="failed", product=None, reason="fixture_stop")

    ex = SimpleNamespace(
        runtime=runtime,
        system=GENERAL,
        records=Records(tmp_path / "v4"),
        config=SimpleNamespace(literature_enabled=True),
        call=call,
    )
    prep = SimpleNamespace(
        ex=ex,
        store=store,
        reader=lambda: reader,
        config=SimpleNamespace(evaluation=SimpleNamespace(historical_as_of=None)),
    )
    package = {
        "experts": {"expert": {"skills": []}},
        "project": {"objective": "Compare observations"},
    }
    task = {
        "expert_id": "expert",
        "members": {"q1": {}},
        "analysis_scope": "Measurements?",
        "external_needs": [],
        "retained_unknowns": [],
        "task_id": "parent",
    }
    with scheduler.research_calls("parent", 5 if outcome == "budget" else 12, 2) as pool:
        with pytest.raises(ValueError, match="fixture_stop"):
            await Research(prep, package, literature).run(task)
        assert pool.reserves == {"parent": 2}
    assert len(calls) == int(outcome in {"success", "error"})


@pytest.mark.asyncio
async def test_retained_visual_is_reinterpreted_without_acquisition(tmp_path):
    from spacebio_review.evidence.session import VisualRequest
    from spacebio_review.evidence.types import Visual

    store, reader = store_and_reader(tmp_path)
    path = tmp_path / "figure.png"
    path.write_bytes(b"fixture-pixels")
    store.retained("selected").visuals = [
        Visual(visual_id="v1", locator="Figure 1", path=str(path)),
        Visual(visual_id="v2", locator="Figure 2", url="https://example.org/not-downloaded.png"),
    ]
    calls = []

    async def pixels(source_id, visual):
        calls.append(source_id)
        assert visual.path == str(path)
        return path.read_bytes(), "image/png"

    async def run_task(task, profile, view, *args):
        assert view.materials[0].image_urls[0].startswith("data:image/png;base64,")
        assert task.max_repairs == task.max_truncation_recoveries == 0
        return TaskResult(
            task_id=task.task_id,
            agent_id=profile.agent_id,
            status="completed",
            reason="done",
            product={"readability": "readable", "interpretation": "Measured at 37 C"},
        )

    store.visual_bytes = pixels
    access = ReviewAccess(store, reader, runtime=SimpleNamespace(run_task=run_task))
    access.register_knowledge(completed_run({"selected": snapshot("selected", "Context")}))
    result = await access.read_visual(
        VisualRequest(source_id="selected", visual_id="v1", question="Axes?")
    )
    assert result.ok
    assert ("selected", None, "v1") in access.read_locations()
    assert any(v.get("image_digest") for v in access.source_scope.snapshot_choices().values())
    unavailable = await access.read_visual(
        VisualRequest(source_id="selected", visual_id="v2", question="Axes?")
    )
    assert unavailable.error == "retained_image_unavailable"
    hidden = await access.read_visual(
        VisualRequest(source_id="candidate", visual_id="v1", question="Axes?")
    )
    assert hidden.error == "source_not_visible"
    assert calls == ["selected"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer_ok", [False, True])
async def test_literature_single_submission_starts_research_without_query_plan(tmp_path, answer_ok):
    store, _ = store_and_reader(tmp_path)
    tasks = []

    async def run_task(task, *args, **kwargs):
        tasks.append(task)
        assert task.max_repairs == 0
        assert task.max_truncation_recoveries == 0
        assert "max_truncation_recoveries" in task.model_fields_set
        assert task.kind == "literature"
        product = completed_run().answer.model_dump(mode="json")
        return TaskResult(
            task_id=task.task_id,
            agent_id=task.task_id,
            status="completed" if answer_ok else "failed",
            product=product if answer_ok else None,
            reason="fixture",
        )

    runtime = SimpleNamespace(
        config=load_config("config/runtime.yaml"),
        skills=SkillLibrary(Path("skills")),
        scheduler=RequestScheduler(),
        models={"task/rerank/normal": None},
        recorder=SimpleNamespace(root=tmp_path),
        run_task=run_task,
    )
    researcher = LiteratureResearcher(
        runtime, store, SimpleNamespace(config=SimpleNamespace(enabled=["tavily"]))
    )
    run = await researcher.investigate(
        KnowledgeQuestion(question="Method?"), single_submission=True
    )
    assert len(tasks) == 1
    assert bool(run.answer) == answer_ok
    assert run.plan_result is None
    assert researcher.minimum_turns(single_submission=True) == 2
    assert (tmp_path / "knowledge" / run.research_id / "answer.json").exists()
    assert tasks[0].reserve_actions == tasks[0].reserve_model_turns == 0


@pytest.mark.asyncio
async def test_cache_is_exact_for_facts_configuration_and_submission_policy(tmp_path, monkeypatch):
    store, _ = store_and_reader(tmp_path)
    runtime = SimpleNamespace(
        config=load_config("config/runtime.yaml"),
        skills=SkillLibrary(Path("skills")),
        recorder=SimpleNamespace(root=tmp_path),
    )
    researcher = LiteratureResearcher(runtime, store, None)
    calls = []

    async def investigate(question, context, **kwargs):
        calls.append((question, kwargs))
        return completed_run()

    monkeypatch.setattr(researcher, "_investigate", investigate)
    question = KnowledgeQuestion(question="Method?")
    await researcher.investigate(question, single_submission=True)
    assert (await researcher.investigate(question, single_submission=True)).reused
    await researcher.investigate(question)
    researcher.config.max_actions += 1
    await researcher.investigate(question, single_submission=True)
    await researcher.investigate(
        question.model_copy(update={"conditions": ["Different tissue"]}), single_submission=True
    )
    assert len(calls) == 4
    skill = runtime.skills.get("literature-understanding")
    runtime.skills._skills[skill.name] = replace(skill, body=skill.body + "\nUpdated method.")
    await researcher.investigate(question, single_submission=True)
    assert len(calls) == 5
