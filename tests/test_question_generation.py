import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.diagnostics import SubmissionRecorder
from spacebio_review.application import ApplicationBundle, ApplicationSource
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument
from spacebio_review.models.config import load_config
from spacebio_review.models.provider import ChatReply
from spacebio_review.review.materials import ApplicationReader
from spacebio_review.review.project import REVIEW_ASPECTS
from spacebio_review.v4.config import V4Config
from spacebio_review.v4.coordinator import Coordinator
from spacebio_review.v4.execution import Execution
from spacebio_review.v4.questions import (
    QUESTION_ASPECTS,
    QUESTION_OBJECTIVE,
    QUESTION_VERSION,
    QuestionGeneration,
    question_contract,
    research_task,
    select_questions,
)
from spacebio_review.v4.records import Records


def question(index=1, expert="expert-1", evidence=()):
    return {
        "expert_id": expert,
        "question": f"Scientific judgment {index}?",
        "aspects": ["rigor", "feasibility"],
        "evidence_handles": list(evidence),
    }


def package():
    return {
        "project": {
            "title": "Application",
            "objective": "Study dynamics",
            "paths": [],
            "understanding_limits": [],
            "evidence": {},
        },
        "experts": {
            "expert-1": {
                "purpose": "Physical measurement",
                "profile_labels": ["Imaging"],
                "method_skill_ids": [],
                "skills": [],
                "model_tier": "normal",
            },
            "expert-2": {
                "purpose": "Implementation",
                "profile_labels": ["Operations"],
                "method_skill_ids": [],
                "skills": [],
                "model_tier": "normal",
            },
        },
    }


def preparation(ex, passages=None, reading_gaps=()):
    doc = SourceDocument(
        source_id="application",
        candidate=SourceCandidate(title="Application"),
        passages=passages
        or [
            Passage(
                passage_id="p1",
                section="Methods",
                locator="Methods",
                text="SOURCE_ONLY_ONCE. Nucleation on the ground; growth in orbit.",
            )
        ],
        reading_gaps=list(reading_gaps),
    )
    bundle = ApplicationBundle(
        sources=[
            ApplicationSource(
                source_id="application",
                title="Application",
                role="main",
                availability="full_text",
                sections=["Methods"],
            )
        ]
    )
    store = SimpleNamespace(retained=lambda sid: doc, candidate=lambda sid: doc.candidate)
    prep = SimpleNamespace(ex=ex, reader=lambda: ApplicationReader(bundle, store, ex.runtime))
    prep.questions = lambda value: QuestionGeneration(prep, value)
    return prep


def runtime_with_response(tmp_path, response=None, finish="stop"):
    observed = []
    response = response or {"questions": [question(evidence=["E1"])]}

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, *, response_schema=None):
            observed.append(
                {
                    "messages": deepcopy(messages),
                    "tools": deepcopy(tools),
                    "schema": response_schema,
                    "config": self.config,
                }
            )
            return ChatReply(
                {"role": "assistant", "content": json.dumps(response)},
                finish,
                prompt_tokens=100,
                completion_tokens=50,
            )

        async def close(self):
            pass

    runtime = AgentRuntime.from_config(load_config("config/runtime.yaml"), model_factory=Model)
    runtime.recorder = SubmissionRecorder(tmp_path)
    return runtime, observed


def execution(tmp_path, runtime, limit=10):
    return Execution(runtime, Records(tmp_path), V4Config(max_questions=limit), {})


def test_contract_only_expresses_questions_and_unique_expert():
    contract = question_contract(package()["experts"])
    assert contract.version == QUESTION_VERSION == "questions-9"
    schema = contract.schema()
    assert set(schema["properties"]) == {"questions"}
    assert "maxItems" not in schema["properties"]["questions"]
    assert "minItems" not in schema["properties"]["questions"]
    item = schema["$defs"]["ReviewQuestion"]
    assert set(item["properties"]) == {"question", "expert_id", "aspects", "evidence_handles"}
    text = json.dumps(schema)
    for retired in (
        "merge_into",
        "joint_work",
        "member_ids",
        "disposition",
        "rank",
        "max_questions",
    ):
        assert retired not in text
    value = question_contract(package()["experts"])
    with pytest.raises(ValueError):
        value.parse({"questions": [question(expert="not-active")]})


def test_limit_is_exact_prefix_not_a_semantic_filter():
    batch = {"questions": [question(1), question(1), question(3, "expert-2")]}
    before = deepcopy(batch)
    for limit in (1, 2, 10):
        selected = select_questions(batch, limit)
        assert [q["proposal"] for q in selected.values()] == batch["questions"][:limit]
        assert [research_task(qid, q)["expert_id"] for qid, q in selected.items()] == [
            row["expert_id"] for row in batch["questions"][:limit]
        ]
        for qid, row in selected.items():
            assert research_task(qid, row)["analysis_scope"] == row["proposal"]["question"]
    assert batch == before
    assert V4Config().max_questions == 10


@pytest.mark.asyncio
async def test_real_runtime_one_native_submission_full_material_once_and_no_limit(tmp_path):
    runtime, observed = runtime_with_response(tmp_path)
    try:
        ex = execution(tmp_path, runtime, 1)
        value = package()
        value["proposals"] = ["OLD_PROPOSAL_POOL"]
        value["project"]["paths"] = ["OLD_MAPPED_INTERPRETATION"]
        value["experts"]["expert-1"]["orientation"] = {"understanding": "OLD_ORIENTATION"}
        generation = preparation(ex).questions(value)
        assert generation.preview()["fits"]
        batch = await generation.run()
        assert len(observed) == 1
        assert observed[0]["config"].request_protocol == "messages"
        assert observed[0]["config"].retries == 0
        assert not observed[0]["tools"]
        text = json.dumps(observed[0]["messages"], ensure_ascii=False)
        materials = json.loads(observed[0]["messages"][1]["content"])
        aspects = next(
            row["content"] for row in materials if row["source_id"] == "question-aspects"
        )
        assert aspects == QUESTION_ASPECTS
        assert list(aspects) == list(REVIEW_ASPECTS)
        schema_aspects = observed[0]["schema"]["$defs"]["ReviewQuestion"]["properties"]["aspects"]
        assert schema_aspects["items"]["enum"] == list(aspects)
        assert "uniqueItems" not in schema_aspects and "maxItems" not in schema_aspects
        for meaning in QUESTION_ASPECTS.values():
            assert text.count(meaning) == 1
        assert "标签用于索引" in text
        assert text.count("SOURCE_ONLY_ONCE") == 1
        for excluded in ("max_questions", "OLD_PROPOSAL_POOL", "OLD_ORIENTATION", "merge_into"):
            assert excluded not in text
        assert "补充遗漏问题" not in text
        assert "需要新事实时提出具体研究请求" not in text
        for excluded in (
            "共同评价语境",
            "working_notes",
            "evaluation-context",
            "OLD_MAPPED_INTERPRETATION",
            "changes_judgment_if",
            "information_route",
            "importance",
            "保留已得认识",
            "不再开展研究",
            "工作笔记",
            "委派",
            "R 是",
            "选择 C",
            "回读",
            "usage",
        ):
            assert excluded not in text
        assert batch["questions"][0]["evidence_handles"][0] in batch["evidence"]
        assert len(batch["evidence"]) == 1
        restored = execution(tmp_path, runtime, 10)
        assert ex.configuration_for("question_generation") == restored.configuration_for(
            "question_generation"
        )
        assert await preparation(restored).questions(value).run() == batch
        assert len(observed) == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_question_preview_matches_wire_and_preserves_original_sequence(tmp_path, monkeypatch):
    from spacebio_review.agents.context import TokenEstimator

    runtime, observed = runtime_with_response(tmp_path)
    measured = []
    original = TokenEstimator.measure

    def measure(self, messages, *args, **kwargs):
        measured.append((deepcopy(messages), deepcopy(kwargs.get("response_schema"))))
        return original(self, messages, *args, **kwargs)

    monkeypatch.setattr(TokenEstimator, "measure", measure)
    try:
        rows = [
            Passage(
                passage_id="p7",
                section="Preparation",
                locator="Preparation",
                text="Prepare 22 control wells.",
            ),
            Passage(
                passage_id="p2",
                section="Observation",
                locator="Observation",
                text="Observe at 3 and 9 hours.",
            ),
        ]
        gen = preparation(execution(tmp_path, runtime), rows, ["Figure 2 unreadable"]).questions(
            package()
        )
        gen.preview()
        preview_messages, preview_schema = measured[-1]
        await gen.run()
        assert observed[0]["messages"] == preview_messages
        assert observed[0]["schema"] == preview_schema
        text = json.dumps(preview_messages)
        for row in rows:
            assert text.count(row.text) == 1
        assert text.index(rows[0].text) < text.index(rows[1].text)
        assert "Figure 2 unreadable" in text
        view = json.loads(preview_messages[-2]["content"])["task_sources"]
        assert [item["passage_id"] for item in view["evidence"].values()] == ["p7", "p2"]
        assert view["sources"]["S1"]["title"] == "Application"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_local_probe_handles_duplicate_selection_limits(tmp_path):
    from scripts.probe_question_generation import dispatch_fixture

    result = await dispatch_fixture(tmp_path, package(), {"questions": [question(1), question(2)]})
    assert set(result) == {"1", "3", "10"}
    assert result["3"]["tasks"] == 2
    assert all(row["resume_tasks"] == 0 for row in result.values())


@pytest.mark.asyncio
async def test_question_submission_instructions_belong_to_request_identity(tmp_path):
    runtime, observed = runtime_with_response(tmp_path)
    try:
        ex = execution(tmp_path, runtime)
        gen = preparation(ex).questions(package())
        for suffix in ("", " Updated submission instruction.", " Updated submission instruction."):
            contract = gen.contract()
            contract.native_prompt += suffix
            await ex.required(
                "prepare/questions",
                "question_generation",
                QUESTION_OBJECTIVE,
                contract,
                materials=gen.materials(),
                read_actions=0,
            )
        assert len(observed) == 2
        assert observed[1]["messages"][-1]["content"].endswith("Updated submission instruction.")
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_revised_question_objective_gets_new_receipt_without_changing_old_one(tmp_path):
    runtime, observed = runtime_with_response(tmp_path)
    try:
        ex = execution(tmp_path, runtime)
        gen = preparation(ex).questions(package())
        legacy = gen.contract()
        legacy.version = "questions-6"
        await ex.required(
            "prepare/questions",
            "question_generation",
            "Earlier question-generation instructions.",
            legacy,
            materials=gen.materials(),
            read_actions=0,
        )
        saved = {path: path.read_bytes() for path in ex.records.root.rglob("*.json")}
        assert len(observed) == 1
        await gen.run()
        assert len(observed) == 2
        assert observed[-1]["messages"][0]["content"] == QUESTION_OBJECTIVE
        assert all(path.read_bytes() == original for path, original in saved.items())
        await gen.run()
        assert len(observed) == 2
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_research_receives_question_without_generated_plan(tmp_path):
    from spacebio_review.v4.research import Research

    runtime, observed = runtime_with_response(tmp_path)
    try:
        ex = execution(tmp_path, runtime)
        ex.config.literature_tasks_per_question = 0
        prep = preparation(ex)
        reader = prep.reader()
        prep.store = reader.references.store
        prep.config = SimpleNamespace(evaluation=SimpleNamespace(historical_as_of=None))
        inputs = []

        class Captured(Exception):
            pass

        async def call(key, kind, objective, contract, **kwargs):
            work = next(m.data for m in kwargs["materials"] if m.source_id == "professional-work")
            inputs.append(work)
            raise Captured()

        ex.call = call
        selected = select_questions({"questions": [question()]}, 10)
        task = research_task("q1", selected["q1"])
        research = Research(prep, package(), SimpleNamespace())
        with pytest.raises(Captured):
            await research.run(task)
        assert inputs[0]["members"]["q1"] == question()
        assert inputs[0]["external_needs"] == []
        assert inputs[0]["retained_unknowns"] == []
        assert json.dumps(inputs[0]).count(question()["question"]) == 1
        assert not observed
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_truncated_output_is_not_a_prefix_and_resume_does_not_resample(tmp_path):
    runtime, observed = runtime_with_response(tmp_path, finish="length")
    try:
        for _ in range(2):
            ex = execution(tmp_path, runtime)
            with pytest.raises(ValueError):
                await preparation(ex).questions(package()).run()
        assert len(observed) == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_dispatch_is_ranked_once_and_limit_keeps_unselected_scope(tmp_path):
    records = Records(tmp_path)
    ex = SimpleNamespace(records=records, configuration="fixture", config=V4Config(max_questions=2))
    c = Coordinator(ex)
    c._state["package"] = package()
    calls = []

    async def generate():
        calls.append("generation")
        return {"questions": [question(1, "expert-2"), question(2), question(3)], "evidence": {}}

    prep = SimpleNamespace(questions=lambda _: SimpleNamespace(run=generate))
    assert await c.prepare_questions(prep)
    assert len(c.state["questions"]) == 2
    dispatched = []

    async def research(task, **kwargs):
        dispatched.append(task)
        member = next(iter(task["members"]))
        return records.put(
            "research",
            {
                "author": task["expert_id"],
                "followups": [
                    {
                        "question": "Unresolved",
                        "missing_information": "Timing",
                        "decision_impact": "Conditional",
                        "information_route": "Future data",
                    }
                ],
                "scope_outcomes": [
                    {"member_id": member, "status": "analyzed", "block_ids": ["b1"]}
                ],
                "blocks": [{"block_id": "b1", "text": "Analysis"}],
            },
        )

    await c.research(SimpleNamespace(run=research))
    restored = Coordinator(ex)
    assert await restored.prepare_questions(prep)
    await restored.research(SimpleNamespace(run=research))
    assert calls == ["generation"]
    assert [t["expert_id"] for t in dispatched] == ["expert-2", "expert-1"]
    assert all(len(t["members"]) == 1 for t in dispatched)
    coverage = restored.coverage()["rigor"]
    assert coverage["status"] == "partially_assessed"
    assert coverage["unfinished"] == {"q3": "unassessed"}
    assert "Scientific judgment 3" in str(coverage["analysis_limits"])
    assert len(restored.open_issues(restored.results())) == 2
    assert "requests" not in restored.state or restored.state["requests"] == []
    ex.config.max_questions = 3
    with pytest.raises(ValueError, match="frozen_questions_changed"):
        await Coordinator(ex).prepare_questions(prep)


@pytest.mark.asyncio
async def test_failed_selection_records_failure_and_replays_saved_batch(tmp_path, monkeypatch):
    import spacebio_review.v4.coordinator as module

    records = Records(tmp_path)
    ex = SimpleNamespace(records=records, configuration="fixture", config=V4Config())
    c = Coordinator(ex)
    c._state["package"] = package()
    calls = []

    async def generate():
        calls.append(1)
        return {"questions": [question()], "evidence": {}}

    prep = SimpleNamespace(questions=lambda _: SimpleNamespace(run=generate))
    with monkeypatch.context() as patch:
        patch.setattr(
            module,
            "select_questions",
            lambda *args: (_ for _ in ()).throw(ValueError("local failure")),
        )
        assert await c.prepare_questions(prep) is None
    assert c.state["tasks"]["prepare/questions"]["status"] == "failed"
    assert not c.state["questions"]
    assert c.state["events"][-1]["message"] == "local failure"
    assert c.public_result()["research_status"] == "not_run"
    restored = Coordinator(ex)
    assert await restored.prepare_questions(prep)
    assert calls == [1]
    assert restored.state["tasks"]["prepare/questions"]["status"] == "committed"


def test_prompt_expresses_granularity_not_a_numeric_quota():
    assert "从高到低" in QUESTION_OBJECTIVE
    assert "预研究" in QUESTION_OBJECTIVE and "正式对照" in QUESTION_OBJECTIVE
    assert "测量" in QUESTION_OBJECTIVE and "研究目标" in QUESTION_OBJECTIVE
    assert "计划书原文" in QUESTION_OBJECTIVE
    assert "不" not in QUESTION_OBJECTIVE
    assert "max_questions" not in QUESTION_OBJECTIVE
    assert not any(char.isdigit() for char in QUESTION_OBJECTIVE)


@pytest.mark.asyncio
async def test_budget_wait_does_not_start_research_or_replace_the_ranked_prefix(tmp_path):
    records = Records(tmp_path)
    ex = SimpleNamespace(
        records=records, configuration="fixture", config=V4Config(max_questions=12)
    )
    c = Coordinator(ex, SimpleNamespace(exploration_reason=lambda: "budget_wait"))
    batch = {"questions": [question(i) for i in range(1, 15)]}
    c._state["package"] = package()
    c._state["question_batch"] = records.put("question-batch", batch)
    c._state["questions"] = select_questions(batch, 12)
    c.save()
    calls = []

    async def research(task, **kwargs):
        calls.append(next(iter(task["members"])))
        return records.put("fixture", {"blocks": [], "scope_outcomes": [], "followups": []})

    await c.research(SimpleNamespace(run=research))
    assert not calls
    assert c.public_result()["research_status"] == "not_run"
    assert all(t["status"] == "deferred" for t in c.state["tasks"].values())
    restored = Coordinator(ex)
    await restored.research(SimpleNamespace(run=research))
    assert calls == [f"q{i}" for i in range(1, 13)]
    assert list(restored.state["questions"]) == calls
