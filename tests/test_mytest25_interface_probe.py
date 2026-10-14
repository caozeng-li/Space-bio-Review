import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from spacebio_review.agents.contracts import example_for
from spacebio_review.application import ApplicationBundle, ApplicationSource
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument
from spacebio_review.models.provider import ChatReply, ModelError
from spacebio_review.v4.manuscript_call import ManuscriptConfig


@pytest.fixture(scope="module")
def probe():
    scripts = Path(__file__).parents[1] / "scripts"
    spec = importlib.util.spec_from_file_location(
        "probe_mytest25_interfaces", scripts / "probe_mytest25_interfaces.py"
    )
    value = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(scripts))
    try:
        spec.loader.exec_module(value)
    finally:
        sys.path.remove(str(scripts))
    return value


def model(**changes):
    return ManuscriptConfig.load().model.model_copy(update={"max_tokens": 100, **changes})


def settle(budget, ticket, prompt=20, completion=10):
    budget.settle(ticket, SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion))


def test_fixed_matrix_is_exactly_52_and_full_output_reservation_fits(tmp_path, probe):
    assert sum(cap for _, cap in probe.CASES.values()) == 52
    with probe.ProbeSession(tmp_path) as session:
        preview = probe.matrix_preview(session)
    assert preview["maximum_output_reservation"] == 999424
    assert preview["output_matrix_fits"]
    assert preview["model_limits"] == {"requests": 52, "input": 2000000, "output": 1000000}
    assert len(preview["unpreviewed_formal_cases"]) == 11


def test_same_budget_object_records_sequential_groups_and_prevents_session_overlap(tmp_path, probe):
    with probe.ProbeSession(tmp_path) as session:
        with pytest.raises(ValueError, match="another_probe_session"):
            with probe.ProbeSession(tmp_path):
                pass
        for case in ("B-q4-1", "C2-1"):
            with session.budget.case(case, "initial"):
                ticket = session.budget.reserve(model(), [{"role": "user", "content": "Original"}])
                settle(session.budget, ticket)
        report = session.budget.report()
        assert report["models"]["requests"] == 2
        assert report["groups"]["B"]["requests"] == report["groups"]["C"]["requests"] == 1
    assert not (tmp_path / ".probe-session.lock").exists()
    with probe.ProbeSession(tmp_path, "next") as session:
        assert session.budget.report()["models"]["input"] == 40


def test_matrix_scope_and_retry_checks_precede_dispatch(tmp_path, probe):
    with probe.ProbeSession(tmp_path) as session:
        with pytest.raises(ModelError, match="case_not_selected"):
            session.budget.reserve(model(), [{"role": "user", "content": "Original"}])
        with session.budget.case("B-q4-1", "initial"):
            with pytest.raises(ModelError, match="embedding_only_in_E"):
                session.budget.reserve(model(), texts=["Original"])
            with pytest.raises(ModelError, match="retries_must_be_zero"):
                session.budget.reserve(model(retries=1), [{"role": "user", "content": "Original"}])
            ticket = session.budget.reserve(model(), [{"role": "user", "content": "Original"}])
            settle(session.budget, ticket)
            with pytest.raises(ModelError, match="case_requests_limit") as caught:
                session.budget.reserve(model(), [{"role": "user", "content": "Original"}])
            assert caught.value.request_sent is False
        assert session.budget.report()["models"]["requests"] == 1


def test_prior_revision_failures_still_count_unknown_usage(tmp_path, probe):
    with probe.ProbeSession(tmp_path) as session:
        for n in range(6):
            with session.budget.case("B-q4-1", f"revision-{n}"):
                session.budget.reserve(model(), [{"role": "user", "content": "Original"}])
        with session.budget.case("B-q4-1", "revision-7"):
            with pytest.raises(ModelError, match="probe_B_requests_limit"):
                session.budget.reserve(model(), [{"role": "user", "content": "Original"}])
        report = session.budget.report()["models"]
        assert report["requests"] == report["unknown_usage_requests"] == 6
        assert report["input"] is None and report["output"] is None
        assert report["accounted"]["output"] == 600


def test_E_embedding_is_separate_and_global_input_protection_visible_to_runtime(
    tmp_path, probe, monkeypatch
):
    with probe.ProbeSession(tmp_path) as session:
        with session.budget.case("E-q3-1", "initial"):
            config = SimpleNamespace(model="embedding", retries=0)
            ticket = session.budget.reserve(config, texts=["A retained source"])
            settle(session.budget, ticket, prompt=1000, completion=0)
            assert session.budget.remaining_calls == 52
            assert session.budget.report()["embedding"]["input"] == 1000
            monkeypatch.setitem(probe.MODEL_LIMITS, "input", 100)
            session.budget.active["formal"] = {"calls": 1, "input": 75, "output": 100}
            assert (
                session.budget.exploration_reason(input_tokens=30)
                == "completion_model_input_reserve"
            )
            assert session.budget.report()["models"]["requests"] == 0


def small_question_fixture(probe):
    def fixture(case, _folder):
        text = (
            "Sample 2 actual organoid observation."
            if case.startswith("C2-")
            else "Sample 3 stem cells."
        )
        doc = SourceDocument(
            source_id="proposal",
            candidate=SourceCandidate(title="Proposal", kind="application"),
            passages=[Passage(passage_id="p1", section="Method", locator="Method", text=text)],
            availability="full_text",
        )
        bundle = ApplicationBundle(
            sources=[
                ApplicationSource(
                    source_id="proposal",
                    title="Proposal",
                    role="main",
                    availability="full_text",
                    sections=["Method"],
                )
            ]
        )
        return (
            bundle,
            probe.FrozenStore({"proposal": doc}),
            {
                "experts": {
                    "expert-1": {
                        "purpose": "Understand observation and interpretation.",
                        "profile_labels": ["Biology"],
                        "method_skill_ids": ["measurement-analysis"],
                    }
                }
            },
            {},
        )

    return fixture


def fake_models(calls):
    class Model:
        def __init__(self, config, *, budget, **_):
            self.config, self.budget = config, budget

        async def complete(self, messages, tools=None, *, response_schema=None):
            ticket = self.budget.reserve(self.config, messages, tools, response_schema)
            calls.append({"messages": messages, "schema": response_schema})
            self.budget.settle(ticket, SimpleNamespace(prompt_tokens=500, completion_tokens=200))
            return ChatReply(
                {"role": "assistant", "content": json.dumps(example_for(response_schema))},
                prompt_tokens=500,
                completion_tokens=200,
                provider_response={"usage": {"input_tokens": 500, "output_tokens": 200}},
            )

        async def close(self):
            pass

    return lambda config: Model


@pytest.mark.asyncio
async def test_question_preview_and_live_use_same_production_wire_and_replay_is_free(
    tmp_path, probe, monkeypatch
):
    monkeypatch.setattr(probe, "question_fixture", small_question_fixture(probe))
    calls = []
    monkeypatch.setattr(probe, "model_class", fake_models(calls))
    cases = ["C2-1", "C2-2", "C2-3", "C3-1", "C3-2"]
    with probe.ProbeSession(tmp_path) as session:
        preview = await probe.run_cases(session, cases)
        assert all(row["status"] == "previewed" and row["new_requests"] == 0 for row in preview)
        assert preview[0]["wire_digest"] == preview[1]["wire_digest"] == preview[2]["wire_digest"]
        assert preview[3]["wire_digest"] == preview[4]["wire_digest"]
        assert not calls
        first = await probe.run_case(session, "C2-1", live=True)
        replay = await probe.run_case(session, "C2-1", live=True)
        second = await probe.run_case(session, "C2-2", live=True)
        assert first["new_requests"] == second["new_requests"] == 1
        assert replay["cache_or_local_replay"] and replay["new_requests"] == 0
        assert len(calls) == 2
        assert session.budget.report()["models"]["input"] == 1000
        response = probe.read(session.output / "cases/C2-1/request-001/reply.json")
        assert response["provider_response"]["usage"]["output_tokens"] == 200
        for row in calls:
            assert set(row["schema"]["properties"]) == {"questions"}


@pytest.mark.asyncio
async def test_changed_preview_is_not_dispatched_or_treated_as_new_repetition(
    tmp_path, probe, monkeypatch
):
    monkeypatch.setattr(probe, "question_fixture", small_question_fixture(probe))
    calls = []
    monkeypatch.setattr(probe, "model_class", fake_models(calls))
    with probe.ProbeSession(tmp_path) as session:
        await probe.run_case(session, "C2-1")
        original = probe.implementation_hashes()
        monkeypatch.setattr(probe, "implementation_hashes", lambda: {**original, "changed": "new"})
        with pytest.raises(ValueError, match="implementation_or_configuration_changed"):
            await probe.run_case(session, "C2-1", live=True)
        assert not calls
        assert not (session.output / "cases/C2-1/result.json").exists()


def test_historical_B_materials_exclude_failed_candidate_and_are_hashed(probe):
    for case in ("B-q4-1", "B-q6-1"):
        item = probe.literature_fixture(case)
        assert set(item) == {"question", "evidence", "source_hashes"}
        assert item["evidence"]
        assert "findings" not in item["question"]
        assert all(
            row["quote"] and row["reference"]["source_id"] for row in item["evidence"].values()
        )
        assert all(
            probe.file_hash(path) == expected for path, expected in item["source_hashes"].items()
        )
        assert not any("candidate" in key for key in item)
        assert not item["question"].get("background")
        assert item["question"]["input_version"] == "review-knowledge-2"
        assert set(item["question"]["application_statements"]) <= set(item["evidence"])
        parsed = probe.parse_knowledge_question(item["question"])
        assert parsed.application_statements == item["question"]["application_statements"]


@pytest.mark.asyncio
async def test_B_uses_production_native_contract_and_repeats_exact_material(
    tmp_path, probe, monkeypatch
):
    calls = []
    monkeypatch.setattr(probe, "model_class", fake_models(calls))
    with probe.ProbeSession(tmp_path) as session:
        first = await probe.run_case(session, "B-q4-1")
        second = await probe.run_case(session, "B-q4-2")
        assert first["status"] == second["status"] == "previewed"
        assert first["wire_digest"] == second["wire_digest"]
        wire = probe.read(session.output / "preview/B-q4-1/request-001/wire.json")
        assert wire["model"]["structured_output"] == "json_schema"
        assert wire["model"]["max_tokens"] == 24576
        assert set(wire["schema"]["properties"]) == {"findings", "scope", "unknowns"}
        assert not wire["tools"]
        assert not calls
        result = await probe.run_case(session, "B-q4-1", live=True)
        assert result["status"] == "completed"
        assert result["product"]["contract_version"] == "literature-5"
        assert result["new_requests"] == 1


@pytest.mark.asyncio
async def test_tool_call_arguments_are_retained_without_running_tools(tmp_path, probe, monkeypatch):
    raw = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "research_knowledge",
                    "arguments": '{"question":"Actual source question"}',
                },
            }
        ],
    }

    class Model:
        def __init__(self, *_args, **_kwargs):
            pass

        async def complete(self, *_args, **_kwargs):
            return ChatReply(
                raw, finish_reason="tool_calls", provider_response={"choices": [{"message": raw}]}
            )

        async def close(self):
            pass

    monkeypatch.setattr(probe, "model_class", lambda config: Model)
    recorder = probe.WireRecorder(tmp_path)
    adapter = recorder.factory()(model(max_tokens=16384))
    await adapter.complete(
        [{"role": "user", "content": "Read the proposal"}],
        [{"type": "function", "function": {"name": "research_knowledge"}}],
    )
    await adapter.close()
    saved = probe.read(tmp_path / "request-001/reply.json")
    assert saved["message"] == raw
    assert saved["provider_response"]["choices"][0]["message"] == raw


@pytest.mark.asyncio
async def test_recorder_captures_real_native_adapter_payload_without_headers(
    tmp_path, probe, monkeypatch
):
    from spacebio_review.models.messages import MessagesModel

    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }

    async def receive(request):
        assert request.headers["x-api-key"] == "test-only-secret"
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": '{"text":"A retained answer"}'}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 13, "output_tokens": 8},
            },
        )

    def factory(config):
        def create(config, *, budget, **_):
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(receive), headers={"x-api-key": "test-only-secret"}
            )
            return MessagesModel(config, client=client, budget=budget)

        return create

    monkeypatch.setattr(probe, "model_class", factory)
    recorder = probe.WireRecorder(tmp_path)
    adapter = recorder.factory()(model(max_tokens=16384))
    await adapter.complete(
        [{"role": "system", "content": "Use the source."}, {"role": "user", "content": "Source"}],
        response_schema=schema,
    )
    await adapter.close()
    wire = probe.read(tmp_path / "request-001/provider-request.json")
    assert wire["output_config"]["format"] == {"type": "json_schema", "schema": schema}
    assert wire["system"] == [{"type": "text", "text": "Use the source."}]
    assert (
        probe.read(tmp_path / "request-001/provider-response.json")["body"]["usage"]["input_tokens"]
        == 13
    )
    assert all(
        "test-only-secret" not in path.read_text("utf-8") for path in tmp_path.rglob("*.json")
    )
