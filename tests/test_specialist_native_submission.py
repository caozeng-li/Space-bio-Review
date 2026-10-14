"""The configured specialist closes research through the actual native adapter."""

import json
from pathlib import Path

import httpx
import pytest
from model_helpers import offline_model
from pydantic import BaseModel, ValidationError

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.types import AgentTask, ContextView, RoleProfile
from spacebio_review.models.config import load_config
from spacebio_review.tools import Tool, ToolRegistry, ToolResult
from spacebio_review.v4.contracts import professional_contract


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["normal", "strong"])
async def test_configured_research_notes_close_with_native_bound_citations(tier):
    config = load_config("config/runtime.yaml")
    route = config.formal_routes["specialist"]
    assert route.model == "question_generator"
    assert route.protocol == "cited_parts"
    research_model = config.routed_model("specialist", tier)
    formal_model = config.routed_model("specialist", tier, formal=True)
    assert research_model.request_protocol == "chat_completions"
    assert research_model.tools
    assert formal_model.request_protocol == "messages"
    assert formal_model.structured_output == "json_schema"
    assert not formal_model.tools
    assert formal_model.max_tokens == 16384
    assert formal_model.reasoning_reserve == 4096
    assert formal_model.minimum_body_tokens == 8192
    assert formal_model.retries == 0

    requests = []
    contract = professional_contract(None, {}, {"q1": {}}, structured_refs=True)
    evidence = {
        "reference": {"source_id": "paper", "passage_id": "p1"},
        "title": "Paper",
        "kind": "external",
        "quote": "The effect was measured only at 37 C.",
        "locator": "p1",
        "material_version": "v1",
        "reading_scope": "text",
    }

    class Empty(BaseModel):
        pass

    async def read(args):
        contract.task_context.add({"paper-original": evidence}, "E")
        contract.task_context.add(
            {
                "knowledge::f1": {
                    "text": "Delegated scientific finding.",
                    "scope_note": "The tested temperature, not other temperatures.",
                    "origin": "delegated",
                    "evidence_refs": ["paper-original"],
                }
            },
            "R",
        )
        return ToolResult(ok=True, content={"quote": evidence["quote"]})

    notes = {
        "current_claims": [
            {"text": "A scoped interpretation of the delegated finding.", "cites": ["R1", "E1"]}
        ],
        "counterevidence": [],
        "revisions": [],
        "unresolved": [{"text": "Other temperatures remain unknown.", "cites": []}],
        "next_steps": [],
        "source_ids": [],
        "active_refs": ["R1", "E1"],
    }

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if request.url.path.endswith("/messages"):
            schema = payload["output_config"]["format"]
            assert schema["type"] == "json_schema"
            assert schema["schema"]["additionalProperties"] is False
            assert "CitedProfessionalDraft" in str(schema)
            assert "R1" in str(schema) and "E1" in str(schema)
            assert "evidence_refs" not in str(schema)
            assert "result_refs" not in str(schema)
            assert "tools" not in payload
            assert "response_format" not in payload
            assert payload["max_tokens"] == 16384
            assert payload["thinking"] == {"type": "enabled"}
            assert payload["output_config"]["effort"] == "high"
            prompt = json.dumps([payload["system"], payload["messages"]])
            submission = payload["messages"][-1]["content"][0]["text"]
            assert json.dumps(schema["schema"], ensure_ascii=False) in submission
            assert "唯一输出对象示例" not in submission
            assert "A scoped interpretation of the delegated finding." in prompt
            assert "Other temperatures remain unknown." in prompt
            assert prompt.count(evidence["quote"]) == 1
            draft = {
                "title": "Scoped interpretation",
                "scope_note": {"parts": [{"text": "Only at 37 C.", "cites": ["E1"]}]},
                "blocks": [
                    {
                        "parts": [
                            {"text": "Measured at the tested temperature.", "cites": ["E1"]},
                            {"text": " The delegated finding stays conditional.", "cites": ["R1"]},
                        ]
                    }
                ],
                "followups": [],
                "scope_outcomes": [
                    {
                        "member_id": "q1",
                        "status": "analyzed",
                        "explanation": "Restricted to the measured conditions.",
                        "block_indices": [1],
                    }
                ],
            }
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": json.dumps(draft)}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            )

        assert payload["model"] == research_model.model
        assert "response_format" not in payload
        assert any(tool["function"]["name"] == "read_fixture" for tool in payload["tools"])
        name, arguments = (
            ("read_fixture", {})
            if len(requests) == 1
            else ("submit_result", {"reason": "Ready", "unfinished": [], "memory": notes})
        )
        return httpx.Response(
            200,
            json={
                "id": "offline",
                "object": "chat.completion",
                "created": 0,
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": f"call-{len(requests)}",
                                    "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(arguments)},
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
        )

    runtime = AgentRuntime.from_config(
        config, model_factory=lambda model: offline_model(model, respond)
    )
    try:
        result = await runtime.run_task(
            AgentTask(
                task_id="native-specialist",
                phase="local",
                kind="specialist",
                model_tier=tier,
                objective="Interpret only the measured conditions.",
                expected_product="professional",
                mode="research",
                max_model_turns=3,
                max_actions=4,
                max_repairs=0,
                max_truncation_recoveries=0,
            ),
            RoleProfile(agent_id="local", responsibility="Study", allowed_tools=["read_fixture"]),
            ContextView(agent_id="local", phase="local"),
            ToolRegistry([Tool("read_fixture", "Read", Empty, read)]),
            contract,
        )
    finally:
        await runtime.close()

    assert result.status == "completed", result.reason
    assert len(requests) == 3
    assert result.memory.current_claims[0].cites == ["knowledge::f1", "paper-original"]
    assert result.product["blocks"][0]["evidence_refs"] == ["paper-original"]
    assert result.product["blocks"][0]["result_refs"] == ["knowledge::f1"]
    assert result.product["scope_links"] == ["paper-original"]
    assert result.product["citation_content"]["/blocks/0"] == [
        {"text": "Measured at the tested temperature.", "cites": ["paper-original"]},
        {"text": " The delegated finding stays conditional.", "cites": ["knowledge::f1"]},
    ]


def test_native_parts_are_not_silently_reindexed_as_blocks():
    contract = professional_contract(None, {}, {"q2": {}}, structured_refs=True)
    draft = {
        "title": "One argument with seven cited segments",
        "scope_note": {"parts": [{"text": "A shared scope.", "cites": []}]},
        "blocks": [{"parts": [{"text": f"Segment {i}.", "cites": []} for i in range(7)]}],
        "followups": [],
        "scope_outcomes": [
            {
                "member_id": "q2",
                "status": "analyzed",
                "explanation": "Scoped analysis.",
                "block_indices": [0, 1, 2, 3, 4, 5],
            }
        ],
    }
    with pytest.raises(ValidationError, match="scope_outcome_block_out_of_range"):
        contract.parse(draft)
    assert len(draft["blocks"]) == 1
    assert draft["scope_outcomes"][0]["block_indices"] == [0, 1, 2, 3, 4, 5]


def test_explicit_native_prompt_is_not_changed_by_generic_schema_guidance():
    contract = professional_contract(None, {}, {"q1": {}}, structured_refs=True)
    contract.native_prompt = "Purpose-specific submission instructions."
    assert contract.prompt(native=True) == contract.native_prompt


def test_retained_native_q2_failure_is_not_reinterpreted_as_a_valid_product():
    root = Path(
        "runs/probe-mytest26-science-20261014/revisions/initial/cases/C-native-q2-1/request-001"
    )
    if not root.exists():
        pytest.skip("retained local fixture is not distributed")
    wire = json.loads((root / "wire.json").read_text("utf-8"))
    reply = json.loads((root / "reply.json").read_text("utf-8"))
    choices = wire["schema"]["$defs"]["CitedPart"]["properties"]["cites"]["items"]["enum"]
    contract = professional_contract(None, {}, {"q2": {}}, structured_refs=True)
    for prefix in ("E", "R"):
        contract.task_context.add({key: {} for key in choices if key.startswith(prefix)}, prefix)
    contract.refresh(contract)
    assert contract.schema() == wire["schema"]
    with pytest.raises(ValidationError, match="scope_outcome_block_out_of_range"):
        contract.parse(json.loads(reply["message"]["content"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [True, False])
async def test_native_professional_prompt_changes_receipt_without_invalidating_legacy(
    tmp_path, native
):
    from spacebio_review.agents.diagnostics import digest
    from spacebio_review.agents.types import TaskResult
    from spacebio_review.v4.config import V4Config
    from spacebio_review.v4.execution import Execution
    from spacebio_review.v4.records import Records, plain

    class Runtime:
        config = load_config("config/runtime.yaml")

        def __init__(self):
            self.calls = 0
            if not native:
                self.config.formal_routes["specialist"].model = None
                self.config.formal_routes["specialist"].structured_output = "yaml"

        async def run_task(self, task, profile, view, tools, contract):
            self.calls += 1
            return TaskResult(
                task_id=task.task_id,
                agent_id=profile.agent_id,
                status="completed",
                reason="Fixture submission.",
                product={"call": self.calls},
            )

    runtime = Runtime()
    records = Records(tmp_path)
    execution = Execution(runtime, records, V4Config(), {})
    contract = professional_contract(None, {}, {"q1": {}}, structured_refs=True)
    old_fingerprint = digest(
        plain(
            {
                "configuration": execution.configuration_for("specialist"),
                "key": "professional",
                "objective": "Compare conditions.",
                "context": execution.context_view("professional", contract),
                "schema": contract.schema(),
                "skills": [],
                "tier": "normal",
            }
        )
    )
    old_identity = {"task": "professional", "input": old_fingerprint}
    old_receipt = {
        "result": TaskResult(
            task_id="professional",
            agent_id="professional",
            status="completed",
            reason="Historical fixture.",
            product={"call": "old"},
        ).model_dump(mode="json")
    }
    records.receipt(old_identity, old_receipt)
    saved_old = records.receipt(old_identity)
    for suffix in ("", " Updated instruction.", " Updated instruction."):
        contract = professional_contract(None, {}, {"q1": {}}, structured_refs=True)
        original = contract.prompt
        contract.prompt = lambda *args, original=original, suffix=suffix, **kwargs: (
            original(*args, **kwargs) + suffix
        )
        product = await execution.required(
            "professional", "specialist", "Compare conditions.", contract, read_actions=0
        )
        expected = (1 if not suffix else 2) if native else "old"
        assert product["call"] == expected
    assert runtime.calls == (2 if native else 0)
    assert records.receipt(old_identity) == saved_old
