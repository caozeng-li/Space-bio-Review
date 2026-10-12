"""Native protocol and formal-only hosted authorization, without network calls."""

import json
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.contracts import ProductBindingError
from spacebio_review.agents.runtime import TextMemoryRequest
from spacebio_review.agents.serialization import parse_output
from spacebio_review.agents.types import AgentTask, ContextView, RoleProfile
from spacebio_review.models.config import load_config
from spacebio_review.models.provider import DashScopeModel
from spacebio_review.tools import ToolRegistry
from spacebio_review.v4.contracts import professional_contract


def test_hosted_override_requires_explicit_formal_route_and_real_provenance():
    config = load_config("config/runtime.yaml")
    original = config.routed_model("specialist")
    config.models["hosted"] = original.model_copy(
        update={
            "model": "hosted-test",
            "open_weights_url": "",
            "hosted_version_url": "",
        }
    )
    config.formal_routes["specialist"].model = "hosted"
    with pytest.raises(ValueError):
        config.routed_model("specialist", formal=True)
    config.formal_routes["specialist"].allow_hosted_model = True
    with pytest.raises(ValueError):
        config.routed_model("specialist", formal=True)
    config.models["hosted"].hosted_version_url = "https://example.org/official-model"
    assert config.routed_model("specialist", formal=True).model == "hosted-test"
    assert config.routed_model("specialist") == original
    config.task_routes["specialist"].model = "hosted"
    with pytest.raises(ValueError):
        config.routed_model("specialist")


def test_unmigrated_memory_tool_keeps_string_only_fields():
    schema = TextMemoryRequest.model_json_schema()["$defs"]["TextWorkingMemory"]
    for field in ("current_claims", "counterevidence", "revisions", "unresolved", "next_steps"):
        assert schema["properties"][field]["items"] == {"type": "string"}


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["syntax", "missing_field", "length"])
async def test_native_recovery_uses_same_frozen_protocol(first):
    contract = professional_contract(None, {}, {"q1": {}}, structured_refs=True)
    value = {
        "title": "Scoped",
        "scope_note": {"parts": [{"text": "Conditions", "cites": []}]},
        "blocks": [{"parts": [{"text": 'Literal "quotes", C:\\data and (E1).', "cites": []}]}],
        "followups": [],
        "scope_outcomes": [
            {
                "member_id": "q1",
                "status": "analyzed",
                "explanation": "Complete",
                "block_indices": [1],
            }
        ],
    }
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        finish = "stop"
        if len(requests) == 1:
            content = (
                "{broken"
                if first != "missing_field"
                else json.dumps({k: v for k, v in value.items() if k != "title"})
            )
            if first == "length":
                finish = "length"
        else:
            content = (
                json.dumps({"replacements": {"/title": "Scoped"}})
                if first == "missing_field"
                else json.dumps(value)
            )
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
                        "finish_reason": finish,
                        "message": {"role": "assistant", "content": content},
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
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
    config.formal_routes["specialist"].structured_output = "json_schema"
    config.formal_routes["specialist"].extra_body = {"enable_thinking": False}
    for model in config.models.values():
        model.schema_supported = True
        model.capability_source = "offline"
    runtime = AgentRuntime.from_config(config, model_factory=factory)
    task = AgentTask(
        task_id="test",
        phase="test",
        objective="Read the supplied sentence",
        expected_product="professional",
        mode="direct",
        read_actions=0,
        max_model_turns=3,
        max_repairs=1,
        max_truncation_recoveries=1,
    )
    try:
        result = await runtime.run_task(
            task,
            RoleProfile(agent_id="test", responsibility=task.objective),
            ContextView(agent_id="test", phase="test"),
            ToolRegistry(),
            contract,
        )
    finally:
        await runtime.close()
    assert result.status == "completed", result.reason
    assert len(requests) == 2
    for request in requests:
        assert request["response_format"]["json_schema"]["strict"] is True
        assert "tools" not in request
    if first != "missing_field":
        assert requests[0]["response_format"] == requests[1]["response_format"]
    assert "repair_schema" not in requests[1]["messages"][-1]["content"]
    assert result.product["blocks"][0]["text"] == value["blocks"][0]["parts"][0]["text"]


@pytest.mark.parametrize(
    "directory,case,error",
    [
        ("context", "q14", ValueError),
        ("formal", "q14", ValueError),
        ("formal-contract", "q14", ValueError),
        ("working", "q5", ProductBindingError),
        ("final-q14", "q14", None),
        ("final-q5", "q5", None),
    ],
)
def test_retained_failures_and_successes_remain_distinguishable(directory, case, error):
    root = Path(f"runs/probe-mytest18-{directory}-20261011")
    if not root.exists():
        pytest.skip("retained local fixture is not distributed")
    saved = json.loads((root / (case + ".json")).read_text("utf-8"))
    event = json.loads(next((root / case).rglob("attempt-001.json")).read_text("utf-8"))
    members = {k["member_id"]: {} for k in (event.get("candidate") or {}).get("scope_outcomes", [])}
    contract = professional_contract(None, {}, members or {case: {}})
    for alias, item in saved["mapping"].items():
        contract.task_context.add({item["identity"]: item["value"]}, alias[0])
    contract.refresh(contract)

    def replay():
        return contract.product(contract.parse(parse_output(event["raw_content"], "yaml")))

    if error:
        with pytest.raises(error):
            replay()
    else:
        assert replay().model_dump()["blocks"]
