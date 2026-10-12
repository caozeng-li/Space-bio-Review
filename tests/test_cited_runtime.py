"""Research-to-formal transition through the actual runtime and provider adapter."""

import json

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.types import AgentTask, ContextView, RoleProfile
from spacebio_review.models.config import load_config
from spacebio_review.models.provider import DashScopeModel
from spacebio_review.tools import Tool, ToolRegistry, ToolResult
from spacebio_review.v4.contracts import professional_contract


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["submit", "budget", "checkpoint", "failure"])
@pytest.mark.parametrize("inherited", [False, True])
async def test_structured_submission_uses_notes_or_unabsorbed_science(scenario, inherited):
    requests = []
    c = professional_contract(None, {}, {"q1": {}}, structured_refs=True, inherited=inherited)
    evidence = {
        "reference": {"source_id": "paper", "passage_id": "p1"},
        "title": "Paper",
        "kind": "external",
        "quote": "Only at 37 C",
        "locator": "p1",
        "material_version": "v1",
        "reading_scope": "text",
    }

    class Empty(BaseModel):
        pass

    async def read(args):
        c.task_context.add({"exact": evidence}, "E")
        if inherited:
            c.task_context.add(
                {
                    "child::f1": {
                        "text": "UNABSORBED_SCIENTIFIC_FINDING",
                        "scope_note": "NEW_SCIENTIFIC_LIMIT",
                        "origin": "delegated",
                        "evidence_refs": ["exact"],
                        "basis": [
                            {
                                "text": evidence["quote"],
                                "kind": "external",
                                "evidence_refs": ["exact"],
                            }
                        ],
                    }
                },
                "R",
            )
        return ToolResult(
            ok=True,
            content={
                "source_id": "paper",
                "passages": [{"passage_id": "p1", "text": evidence["quote"]}],
                "summary": "UNABSORBED_SCIENTIFIC_FINDING",
                "limitations": ["NEW_SCIENTIFIC_LIMIT"],
            },
        )

    notes = {
        k: []
        for k in (
            "current_claims",
            "counterevidence",
            "revisions",
            "unresolved",
            "next_steps",
            "source_ids",
            "active_refs",
        )
    }
    notes["current_claims"] = [{"text": "ABSORBED_FINDING", "cites": ["E1"]}]
    notes["unresolved"] = [{"text": "NEW_SCIENTIFIC_LIMIT", "cites": []}]
    if inherited:
        for group in ("current_claims", "unresolved"):
            notes[group] = [
                {
                    "kind": "interpretation",
                    "content": {"parts": notes[group]},
                    "scope": {"parts": [{"text": "Only this case", "cites": []}]},
                    "based_on": ["C2"],
                    "supersedes": [],
                }
            ]
        notes.update(
            followups=[],
            scope_outcomes=[
                {
                    "member_id": "q1",
                    "status": "partial",
                    "explanation": {"parts": [{"text": "Open", "cites": ["E1"]}]},
                }
            ],
        )

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload.get("response_format"):
            if scenario == "failure":
                return httpx.Response(400, json={"error": {"code": "invalid_request"}})
            assert payload["response_format"]["type"] == "json_schema"
            assert payload["response_format"]["json_schema"]["strict"] is True
            content = {
                "title": "Scoped finding",
                "scope_note": {"parts": [{"text": "Only here", "cites": []}]},
                "blocks": [{"parts": [{"text": "Finding", "cites": ["E1"]}]}],
                "followups": [],
                "scope_outcomes": [
                    {
                        "member_id": "q1",
                        "status": "analyzed",
                        "explanation": "Scoped",
                        "block_indices": [1],
                    }
                ],
            }
            if inherited:
                content = {
                    "title": "Scoped finding",
                    "blocks": [
                        {
                            "statement_ids": c.task_context.book.current_choices(),
                            "member_ids": ["q1"],
                        }
                    ],
                }
                assert "CitedPart" not in str(payload["response_format"])
            message = {"role": "assistant", "content": json.dumps(content)}
        else:
            if len(requests) == 1 or len(requests) == 3:
                name, args = "read_fixture", {}
            elif scenario in {"checkpoint", "failure"}:
                name, args = "record_memory", {"memory": notes}
            else:
                name, args = "submit_result", {"reason": "Ready", "unfinished": [], "memory": notes}
            message = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": f"call{len(requests)}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ],
            }
        return httpx.Response(
            200,
            json={
                "id": "local",
                "object": "chat.completion",
                "created": 0,
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls" if "tool_calls" in message else "stop",
                        "message": message,
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
        )

    def factory(config):
        return DashScopeModel(
            config,
            client=AsyncOpenAI(
                api_key="offline",
                base_url="https://example.invalid/v1",
                max_retries=0,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
            ),
        )

    config = load_config("config/runtime.yaml")
    config.task_routes["specialist"].max_truncation_recoveries = 0
    config.formal_routes["specialist"].structured_output = "json_schema"
    config.formal_routes["specialist"].extra_body = {"enable_thinking": False}
    for model in config.models.values():
        model.schema_supported = True
        model.capability_source = "offline-test"
    runtime = AgentRuntime.from_config(config, model_factory=factory)
    task = AgentTask(
        task_id="local",
        phase="local",
        objective="Conditions?",
        expected_product="professional",
        mode="research",
        max_repairs=0,
        max_model_turns={"submit": 3, "budget": 2, "checkpoint": 4, "failure": 3}[scenario],
        max_actions=4,
    )
    try:
        result = await runtime.run_task(
            task,
            RoleProfile(agent_id="local", responsibility="Study", allowed_tools=["read_fixture"]),
            ContextView(agent_id="local", phase="local"),
            ToolRegistry([Tool("read_fixture", "Read", Empty, read)]),
            c,
        )
    finally:
        await runtime.close()
    assert len(requests) == task.max_model_turns
    if scenario == "failure":
        assert result.status == "failed"
        assert "NEW_SCIENTIFIC_LIMIT" in result.unfinished
        if inherited:
            assert result.statement_handoff["selected"]["current_claims"]
        return
    assert result.status == "completed", result.reason
    formal = json.dumps(requests[-1]["messages"])
    assert "NEW_SCIENTIFIC_LIMIT" in formal
    assert ("ABSORBED_FINDING" in formal) is (scenario != "budget")
    assert ("UNABSORBED_SCIENTIFIC_FINDING" in formal) is (scenario != "submit")
    assert '"$defs"' not in formal
    assert formal.count("Only at 37 C") == 1
    assert result.product["blocks"][0]["evidence_refs"] == ["exact"]
    if scenario != "budget":
        note = result.memory.current_claims[0]
        assert (note.parts[0].cites if inherited else note.cites) == ["exact"]
    if inherited:
        assert result.product["scope_outcomes"][0]["status"] == "partial"
        assert "Only at 37 C" in result.product["blocks"][0]["text"]
        assert result.product["statement_content"]
        if scenario == "checkpoint":
            assert result.product["statement_content"]["/unabsorbed"]["observations"]
        assert result.statement_handoff is not None
    else:
        assert "statement_handoff" not in result.model_dump()


@pytest.mark.asyncio
async def test_empty_inherited_handoff_does_not_spend_formal_call():
    def respond(request):
        raise AssertionError("Empty handoff must not request model output")

    def factory(config):
        return DashScopeModel(
            config,
            client=AsyncOpenAI(
                api_key="offline",
                base_url="https://example.invalid/v1",
                max_retries=0,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
            ),
        )

    runtime = AgentRuntime.from_config(load_config("config/runtime.yaml"), model_factory=factory)
    c = professional_contract(None, {}, {"q1": {}}, inherited=True)
    try:
        result = await runtime.run_task(
            AgentTask(
                task_id="empty",
                phase="local",
                objective="Empty",
                expected_product="professional",
                mode="direct",
                max_actions=1,
                max_model_turns=1,
                max_repairs=0,
                read_actions=0,
            ),
            RoleProfile(agent_id="local", responsibility="Read"),
            ContextView(agent_id="local", phase="local"),
            ToolRegistry(),
            c,
        )
    finally:
        await runtime.close()
    assert result.status == "limited"
    assert result.reason == "no_working_statements"
    assert result.model_turns == 0
