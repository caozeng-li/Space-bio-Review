import importlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from spacebio_review.models.provider import ChatReply

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
shared = importlib.import_module("probe_mytest25_interfaces")
probe = importlib.import_module("probe_mytest25_professional")
sys.path.remove(str(SCRIPTS))


@pytest.mark.asyncio
async def test_professional_previews_use_real_inputs_without_network(tmp_path, monkeypatch):
    if not shared.HISTORICAL_ROOT.exists():
        pytest.skip("historical fixture is local")

    def unexpected(*args, **kwargs):
        raise AssertionError("preview_must_not_construct_provider")

    monkeypatch.setattr(shared, "model_class", unexpected)
    with shared.ProbeSession(tmp_path) as session:
        for case in ("D1-q1-1", "D2-q6-1"):
            value = await probe.run_case(session, case)
            assert value["status"] == "previewed"
            assert value["new_requests"] == 0
            folder = session.output / "preview" / case
            wire_path = folder / (
                "request-001/wire.json"
                if case.startswith("D1")
                else "preview/request-001/wire.json"
            )
            wire = shared.read(wire_path)
            text = json.dumps(wire["messages"], ensure_ascii=False)
            assert "professional-input-5" in text
            assert wire["model"]["retries"] == 0
            if case.startswith("D1"):
                assert "22" in text
                assert any(t["function"]["name"] == "research_knowledge" for t in wire["tools"])
            else:
                assert not wire["tools"]
                assert wire["model"]["structured_output"] == "json_schema"
                assert wire["model"]["request_protocol"] == "messages"
                response = shared.read(folder / "preview/knowledge-tool-result.json")
                assert not response["ok"] and response["content"]["answer"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["knowledge", "natural", "turn_limit"])
async def test_observation_stops_before_delegation_or_formal(tmp_path, monkeypatch, reason):
    if not shared.HISTORICAL_ROOT.exists():
        pytest.skip("historical fixture is local")
    observed = []

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, **kwargs):
            observed.append({"messages": deepcopy(messages), "tools": deepcopy(tools)})
            assert self.config.request_protocol == "chat_completions"
            if reason == "natural":
                message = {"role": "assistant", "content": "Research can close."}
            else:
                name = "research_knowledge" if reason == "knowledge" else "read_review_references"
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call-{len(observed)}",
                            "type": "function",
                            "function": {"name": name, "arguments": "{}"},
                        }
                    ],
                }
            return ChatReply(
                message,
                "tool_calls" if reason != "natural" else "stop",
                prompt_tokens=10,
                completion_tokens=10,
            )

        async def close(self):
            pass

    monkeypatch.setattr(shared, "model_class", lambda config: lambda *args, **kwargs: Model(config))
    folder = tmp_path / "observed"
    recorder = shared.WireRecorder(folder)
    runtime = probe.scoped_runtime(recorder, observe=True)
    try:
        with pytest.raises(probe.ObservationStopped) as error:
            await probe.professional_operation("D1-q1-1", folder, runtime)
    finally:
        await runtime.close()
    assert len(observed) == (4 if reason == "turn_limit" else 1)
    assert (
        str(error.value)
        == {
            "knowledge": "first_knowledge_request",
            "natural": "natural_end",
            "turn_limit": "four_response_limit",
        }[reason]
    )
    assert len(list(folder.glob("request-*/reply.json"))) == len(observed)
    assert not (folder / "delegation-question.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.parametrize("has_finding", [True, False])
async def test_real_handoff_then_exactly_one_professional_submission(
    tmp_path, monkeypatch, valid, has_finding
):
    from spacebio_review.evidence.knowledge import KnowledgeAnswer

    if not shared.HISTORICAL_ROOT.exists():
        pytest.skip("historical fixture is local")
    calls = []

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, **kwargs):
            calls.append(deepcopy(messages))
            assert not tools
            assert self.config.structured_output == "json_schema"
            assert self.config.request_protocol == "messages"
            assert kwargs["response_schema"]["additionalProperties"] is False
            data = (
                {
                    "title": "Scoped analysis",
                    "scope_note": {"parts": [{"text": "Local conditions", "cites": []}]},
                    "blocks": [
                        {
                            "parts": [
                                {
                                    "text": "The interpretation applies conditionally.",
                                    "cites": ["R1"] if has_finding else ["E1"],
                                }
                            ]
                        }
                    ],
                    "followups": [],
                    "scope_outcomes": [
                        {
                            "member_id": "q3",
                            "status": "analyzed",
                            "explanation": "Scoped result",
                            "block_indices": [1],
                        }
                    ],
                }
                if valid
                else {}
            )
            return ChatReply(
                {"role": "assistant", "content": json.dumps(data)},
                "stop",
                prompt_tokens=10,
                completion_tokens=10,
            )

        async def close(self):
            pass

    monkeypatch.setattr(shared, "model_class", lambda config: lambda *args, **kwargs: Model(config))
    with shared.ProbeSession(tmp_path) as session:
        run, _ = probe.fixed_knowledge(session, "q6")
        handle, original = next(iter(run.question.application_statements.items()))
        run.answer = KnowledgeAnswer.model_validate(
            {
                "contract_version": "literature-4",
                "findings": [
                    {
                        "paragraphs": [
                            {
                                "parts": [
                                    {"text": "Scoped delegated explanation.", "cites": [handle]}
                                ]
                            }
                        ]
                    }
                ],
                "applicability": {
                    "paragraphs": [{"parts": [{"text": "Only within this scope.", "cites": []}]}]
                },
                "unknowns": [],
                "evidence_resolution": {handle: original},
                "reading_scope": "fixed original",
                "stop_reason": "done",
            }
        )
        run.task_result.status = "completed"
        if not has_finding:
            run.answer.findings = []
        with session.budget.case("E-q3-1", session.revision):
            operation = probe.professional_formal(
                session, "E-q3-1", knowledge_run=run, store=None, folder=tmp_path / "professional"
            )
            if valid:
                result = await operation
                assert result["status"] == "completed"
                blocks = result["records"].result_blocks([result["research_ref"]])
                assert len(blocks) == 1
                refs = next(iter(blocks.values()))["result_refs"]
                if has_finding:
                    reference = refs[0]
                    assert (
                        result["records"].result_blocks([reference.split("::")[0]])[reference][
                            "origin"
                        ]
                        == "delegated"
                    )
                else:
                    assert not refs
                    response = shared.read(tmp_path / "professional/knowledge-tool-result.json")
                    assert response["ok"] and response["content"]["answer"]["findings"] == []
            else:
                with pytest.raises(ValueError):
                    await operation
    assert len(calls) == 1
