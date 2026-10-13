import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_question_generation import (
    execution,
    package,
    preparation,
    question,
    runtime_with_response,
)

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.diagnostics import SubmissionRecorder
from spacebio_review.models.config import load_config
from spacebio_review.models.messages import MessagesModel
from spacebio_review.models.provider import ModelError
from spacebio_review.v4.config import V4Config
from spacebio_review.v4.coordinator import Coordinator
from spacebio_review.v4.publication_input import publication_catalog, request_view
from spacebio_review.v4.questions import QUESTION_OBJECTIVE, question_contract
from spacebio_review.v4.records import Records


@pytest.mark.asyncio
async def test_archive_before_parse_recovers_bound_sources_without_resampling(tmp_path):
    runtime, observed = runtime_with_response(tmp_path)

    class Interrupted(BaseException):
        pass

    try:
        ex = execution(tmp_path, runtime)
        gen = preparation(ex).questions(package())
        original = gen.contract()
        original.parse = lambda _: (_ for _ in ()).throw(Interrupted())
        with pytest.raises(Interrupted):
            await ex.call(
                "prepare/questions",
                "question_generation",
                QUESTION_OBJECTIVE,
                original,
                materials=gen.materials(),
                read_actions=0,
            )
        restored = execution(tmp_path, runtime)
        result = await preparation(restored).questions(package()).run()
        assert len(observed) == 1
        assert result["questions"][0]["evidence_handles"][0] in result["evidence"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_schema_failure_is_retained_not_repaired_or_resampled(tmp_path):
    row = question(evidence=["E1"])
    row["unexpected"] = "No repair"
    runtime, observed = runtime_with_response(tmp_path, {"questions": [row]})
    try:
        for _ in range(2):
            with pytest.raises(ValueError):
                await preparation(execution(tmp_path, runtime)).questions(package()).run()
        assert len(observed) == 1
        archives = [
            json.loads(p.read_text("utf-8")) for p in Path(tmp_path).glob("submissions/*/*.json")
        ]
        assert len(archives) == 1
        assert archives[0]["budget"]["ordinary_repairs"] == 0
        assert archives[0]["raw_content"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("not_sent", [False, True])
async def test_resume_distinguishes_admission_from_possible_inflight_request(tmp_path, not_sent):
    posts = []

    class Budget:
        allow = False

        def reserve(self, *args):
            if not_sent and not self.allow:
                raise ModelError("input_token_budget_exhausted", category="budget")
            return 0

        def settle(self, *args):
            pass

    budget = Budget()

    def respond(request):
        posts.append(1)
        if not not_sent:
            raise httpx.ReadTimeout("response unknown")
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": json.dumps({"questions": [question()]})}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        )

    config = load_config("config/runtime.yaml")
    config.models = {"question_generator": config.models["question_generator"]}
    config.task_routes = {"question_generation": config.task_routes["question_generation"]}
    config.formal_routes = {"question_generation": config.formal_routes["question_generation"]}
    config.strong_model = "question_generator"
    runtime = AgentRuntime.from_config(
        config,
        model_factory=lambda cfg: MessagesModel(
            cfg, budget=budget, client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        ),
    )
    runtime.recorder = SubmissionRecorder(tmp_path)
    try:
        ex = execution(tmp_path, runtime)
        first = await ex.call(
            "local", "question_generation", "Local", question_contract(package()["experts"])
        )
        assert first.status == "failed"
        budget.allow = True
        result = await execution(tmp_path, runtime).call(
            "local", "question_generation", "Local", question_contract(package()["experts"])
        )
        assert result.status == ("completed" if not_sent else "failed")
        assert posts == [1]
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("after", [False, True])
async def test_selection_and_success_are_one_durable_state(tmp_path, monkeypatch, after):
    records = Records(tmp_path)
    ex = SimpleNamespace(records=records, configuration="fixture", config=V4Config())
    c = Coordinator(ex)
    c._state["package"] = package()
    calls = []

    async def generate():
        calls.append(1)
        return {"questions": [question()], "evidence": {}}

    prep = SimpleNamespace(questions=lambda _: SimpleNamespace(run=generate))
    original = records.save_state

    class Interrupted(BaseException):
        pass

    def save(state):
        if state.get("question_selection"):
            if after:
                original(state)
            raise Interrupted()
        original(state)

    with monkeypatch.context() as patch:
        patch.setattr(records, "save_state", save)
        with pytest.raises(Interrupted):
            await c.prepare_questions(prep)
    restored = Coordinator(ex)
    assert bool(restored.state.get("question_selection")) is after
    assert await restored.prepare_questions(prep)
    assert calls == [1]
    assert list(restored.state["questions"]) == ["q1"]


@pytest.mark.asyncio
async def test_preparation_has_no_specialist_or_allocation_stage_and_reuses_prerequisites(tmp_path):
    records = Records(tmp_path)
    ex = SimpleNamespace(
        records=records,
        configuration="fixture",
        config=V4Config(),
        configuration_for=lambda _: "preparation",
    )
    c = Coordinator(ex)
    calls = []

    async def stage(name, result):
        calls.append(name)
        return result

    async def generate():
        calls.append("questions")
        return {"questions": [question()], "evidence": {}}

    p = package()
    prep = SimpleNamespace(
        bundle=SimpleNamespace(model_dump=lambda **_: {"sources": []}),
        config=SimpleNamespace(model_dump=lambda **_: {}),
        catalogue=SimpleNamespace(brief=lambda: {"science": "Measurement"}),
        read=lambda: stage("read", {"notes": []}),
        map_project=lambda _: stage("map", p["project"]),
        team=lambda _: stage("team", p["experts"]),
        questions=lambda _: SimpleNamespace(run=generate),
    )
    assert await c.prepare(prep)
    assert await Coordinator(ex).prepare(prep)
    assert calls == ["read", "map", "team", "questions"]
    assert list(c.state["tasks"]) == [
        "prepare/reading",
        "prepare/project",
        "prepare/team",
        "prepare/questions",
    ]


def test_old_checkpoint_is_rejected_without_modification(tmp_path):
    path = tmp_path / "v4" / "state.json"
    path.parent.mkdir()
    path.write_text('{"format_version":"v4.1-3"}', "utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="legacy_checkpoint_read_only"):
        Records(tmp_path)
    assert path.read_bytes() == before


def test_new_catalog_and_unresolved_projection_do_not_need_routing_fields():
    p = package()
    state = {
        "package": p,
        "questions": {"q1": {"proposal": question()}},
        "tasks": {"research/q1/expert-1/r0": {"artifact": "analysis-a"}},
    }
    topics = publication_catalog(state, ["analysis-a"])
    assert topics[0]["analyses"][0]["perspective"] == "Imaging"
    assert topics[0]["title"] == question()["question"]
    view = request_view(
        [
            {
                "status": "unresolved",
                "origin": "analysis-a",
                "request": {
                    "question": "Unknown timing",
                    "missing_information": "A date is not yet known",
                    "decision_impact": "Conclusion remains conditional",
                    "information_route": "Future data",
                },
            }
        ]
    )[0]
    assert view["remaining_gap"] == "A date is not yet known"
    assert "origin" not in view


@pytest.mark.asyncio
async def test_runner_protocol_assembly_and_limit_only_resume(tmp_path, monkeypatch):
    from model_helpers import offline_model

    import spacebio_review.runner as runner

    constructed = []

    def adapter(config):
        def factory(cfg, **kwargs):
            model = offline_model(cfg, lambda _: pytest.fail("No HTTP in assembly test"))
            constructed.append((cfg.request_protocol, type(model).__name__))
            return model

        return factory

    async def close():
        pass

    resources = SimpleNamespace(store=SimpleNamespace(restore=lambda: None), close=close)
    monkeypatch.setattr(runner, "model_class", adapter)
    monkeypatch.setattr(runner.EvidenceResources, "create", lambda *a, **k: resources)

    async def load(*args):
        return runner.ApplicationBundle(sources=[])

    async def admission(*args):
        return SimpleNamespace(
            model_dump=lambda **_: {"status": "completed", "decision": {"decision": "reject"}}
        )

    monkeypatch.setattr(runner, "load_application", load)
    monkeypatch.setattr(runner.ProposalAdmission, "check", admission)
    application = tmp_path / "application.txt"
    application.write_text("Frozen application", "utf-8")
    settings = tmp_path / "review.yaml"
    review = V4Config(max_questions=1, preparation_config=Path("config/preparation.yaml").resolve())
    settings.write_text(review.model_dump_json(), "utf-8")
    config = runner.ReviewConfig(output=tmp_path / "output", review_config=settings)
    assert (await runner.run_review([application], config)).status == "rejected"
    assert set(constructed) == {
        ("messages", "MessagesModel"),
        ("chat_completions", "DashScopeModel"),
    }
    review.max_questions = 10
    settings.write_text(review.model_dump_json(), "utf-8")
    config.resume = True
    assert (await runner.run_review([application], config)).status == "rejected"
    records = Records(config.output)
    state = records.load_state()
    state["question_selection"] = {"max_questions": 10}
    records.save_state(state)
    before = len(constructed)
    review.max_questions = 11
    settings.write_text(review.model_dump_json(), "utf-8")
    with pytest.raises(ValueError, match="Resume input/configuration differs"):
        await runner.run_review([application], config)
    assert len(constructed) == before


@pytest.mark.asyncio
async def test_excess_context_stops_before_any_request(tmp_path, monkeypatch):
    runtime, observed = runtime_with_response(tmp_path)
    try:
        gen = preparation(execution(tmp_path, runtime)).questions(package())
        monkeypatch.setattr(gen, "preview", lambda: {"fits": False})
        with pytest.raises(ValueError, match="question_context_exceeds_capacity"):
            await gen.run()
        assert not observed
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_actual_map_and_team_contracts_have_no_review_question_generation(tmp_path):
    from spacebio_review.review.project import load_preparation_config
    from spacebio_review.v4.preparation import Preparation

    runtime, observed = runtime_with_response(tmp_path)
    try:
        ex = execution(tmp_path, runtime)
        prep = Preparation(ex, None, None, load_preparation_config("config/preparation.yaml"))
        stages = []
        profile = next(iter(prep.catalogue.entries))

        async def required(key, kind, objective, contract, **kwargs):
            stages.append(key)
            fields = contract.schema()["properties"]
            if key == "prepare/map":
                assert set(fields) == {
                    "title",
                    "project_objective",
                    "paths",
                    "understanding_limits",
                }
                return {
                    "title": "Project",
                    "project_objective": "Applicant's scientific objective",
                    "paths": [{"question": "Applicant's research question"}],
                    "understanding_limits": [],
                    "evidence": {},
                }
            assert key == "prepare/team"
            assert set(fields) == {"experts"}
            text = json.dumps(contract.schema())
            assert "proposals" not in text and "orientation" not in text
            return {
                "experts": [
                    {
                        "purpose": "Scientific perspective",
                        "model_tier": "normal",
                        "profile_ids": [profile],
                        "method_skill_ids": [],
                    }
                ]
            }

        ex.required = required
        project = await prep.map_project(
            {"notes": [{"evidence": {}}], "limits": [], "coverage": {}}
        )
        assert "questions" not in project
        experts = await prep.team(project)
        assert experts["expert-1"]["profile_labels"] == [prep.catalogue.entries[profile].expertise]
        assert experts["expert-1"]["skills"]
        assert stages == ["prepare/map", "prepare/team"]
        assert not observed
        assert all(not hasattr(prep, name) for name in ("orient", "allocate", "route_requests"))
    finally:
        await runtime.close()
