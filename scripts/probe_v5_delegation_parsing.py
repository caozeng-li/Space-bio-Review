"""Offline checks of the narrow delegation structure parser; no provider or child calls."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_session import ScriptModel  # noqa: E402

from spacebio_review.models.config import ModelConfig  # noqa: E402
from spacebio_review.models.provider import ChatReply, ModelError  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.tools import Tool, ToolRegistry, ToolResult  # noqa: E402
from spacebio_review.v5.authoring import DeliveryParser  # noqa: E402
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance  # noqa: E402
from spacebio_review.v5.chief import TopicBatch  # noqa: E402
from spacebio_review.v5.delegation import parse_delegation_arguments  # noqa: E402
from spacebio_review.v5.records import SessionTask  # noqa: E402
from spacebio_review.v5.session import ResearchRuntime  # noqa: E402


class FixedParser:
    def __init__(self, response, *, finish="stop", budget=None):
        self.config = ModelConfig(
            model="fixed-delegation-parser",
            base_url="https://fixed.invalid/v1",
            tools=False,
            structured_output="json_object",
            max_tokens=512,
            retries=0,
        )
        self.response, self.finish, self.requests = response, finish, []
        self.budget = budget

    async def complete(self, messages, tools=None, *, response_schema=None):
        assert tools is None and response_schema == TopicBatch.model_json_schema()
        ticket = (
            self.budget.reserve(self.config, messages, tools, response_schema)
            if self.budget
            else None
        )
        self.requests.append(messages)
        if ticket is not None:
            self.budget.settle(ticket, SimpleNamespace(prompt_tokens=20, completion_tokens=10))
        return ChatReply({"role": "assistant", "content": self.response}, self.finish)


async def runtime_boundary(output):
    expected = {
        "requests": [
            {
                "task": {
                    "objective": '比较"首次"的实际范围。',
                    "impact": "确定创新比较基准。",
                    "application_refs": [{"source_id": "src-application", "passage_id": "p2"}],
                }
            }
        ]
    }
    valid = json.dumps(expected, ensure_ascii=False)
    malformed = valid.replace('\\"', '"') + "}"
    identity_error = json.loads(valid)
    identity_error["requests"][0]["task"]["application_refs"][0]["source_id"] = "src-external"
    schema_error = {"requests": [{"task": {"objective": 3}}]}
    cases = {}
    for name, arguments, tool_name, cap, response in (
        ("syntax", malformed, "research_topics", 1, valid),
        ("valid", valid, "research_topics", None, valid),
        ("identity", json.dumps(identity_error), "research_topics", None, valid),
        ("schema", json.dumps(schema_error), "research_topics", None, valid),
        ("other_tool", malformed, "other_control", None, valid),
        ("budget_exhausted", malformed, "research_topics", 0, valid),
        ("parser_invalid", malformed, "research_topics", None, "invalid parser JSON"),
    ):
        root = output / name
        budget = (
            Budget(root, BudgetLimits(max_model_calls=cap, max_seconds=300))
            if cap is not None
            else None
        )
        model = FixedParser(response, budget=budget)
        author = ScriptModel([])
        engine = ResearchRuntime(
            root,
            {"fixed": author},
            {"chief": "fixed", "researcher": "fixed"},
            budget=budget,
            delivery_parser=model,
        )
        session_id = engine.create(
            SessionTask(role="chief", objective="固定派题入口验收", application="固定原文"),
            allowance=SessionAllowance(completion_calls=0),
        )
        dispatched, checked = [], []

        async def dispatch(batch):
            checked.append(batch)
            if any(
                ref.source_id != "src-application"
                for request in batch.requests
                for ref in request.task.application_refs
            ):
                return ToolResult(
                    ok=False, error="application_reference_requires_current_application"
                )
            dispatched.append(batch)
            return ToolResult(ok=True, content={"captured": len(batch.requests)})

        registry = ToolRegistry([Tool(tool_name, "固定派题结构", TopicBatch, dispatch)])
        try:
            result = await engine._execute_tool(
                engine.load(session_id), registry, tool_name, arguments
            )
            needs_parser = name in {"syntax", "budget_exhausted", "parser_invalid"}
            expected_calls = int(name in {"syntax", "parser_invalid"})
            assert len(model.requests) == expected_calls, (name, model.requests)
            assert len(dispatched) == int(name in {"syntax", "valid"}), (name, dispatched)
            assert result.ok == (name in {"syntax", "valid"}), (name, result)
            inputs = list((root / "v5/delegation-inputs").glob("*.json"))
            parses = list((root / "v5/delegation-parses").glob("*.json"))
            assert len(inputs) == int(needs_parser)
            assert len(parses) == int(name == "syntax")
            if inputs:
                raw = json.loads(inputs[0].read_text("utf-8"))
                assert raw == {"session_id": session_id, "tool": tool_name, "arguments": arguments}
            if parses:
                parsed = json.loads(parses[0].read_text("utf-8"))
                assert parsed["arguments"] == TopicBatch.model_validate(expected).model_dump(
                    mode="json"
                )
                assert parsed["parser"] == model.config.model_dump(mode="json")
            if name == "budget_exhausted":
                assert budget.usage()["model_calls"] == 0
                assert not checked
            elif budget:
                assert budget.usage()["model_calls"] == 1
                assert budget.data["requests"][0]["session_id"] == session_id
                assert budget.data["requests"][0]["purpose"] == "auxiliary"
            assert not author.requests
            cases[name] = {
                "parser_requests": len(model.requests),
                "dispatched": len(dispatched),
                "tool_result": result.model_dump(mode="json"),
                "raw_inputs_saved": len(inputs),
                "parses_saved": len(parses),
            }
            write_json(root / "result.json", cases[name])
        finally:
            await engine.close()
    return cases


async def probe(output):
    output.mkdir(parents=True, exist_ok=False)
    task = {
        "objective": '比较"首次"主张与已知小肠模型，保留 Ca²⁺、100 nm 和 [@E4]。',
        "impact": '判断"首次"的范围；本句即使有未经论证的前提也不由解析器修改。',
        "source_ids": ["src-external"],
        "research_ids": ["E4"],
        "application_refs": [{"source_id": "src-application", "passage_id": "p2"}],
    }
    expected = {"requests": [{"task": task}]}
    # Intentionally malformed fixture, supplied unchanged to the LLM interface.
    raw = json.dumps(expected, ensure_ascii=False).replace('\\"', '"') + "}"
    model = FixedParser(json.dumps(expected, ensure_ascii=False))
    result = await parse_delegation_arguments(DeliveryParser(model), raw, TopicBatch)
    payload = json.loads(model.requests[0][1]["content"])
    assert payload["arguments"] == raw
    parsed = result.requests[0].task
    assert parsed.objective == task["objective"] and parsed.impact == task["impact"]
    assert parsed.research_ids == ["E4"], "Structural parser must not replace identities"
    failures = []
    for text, finish in (("not JSON", "stop"), (json.dumps(expected), "length")):
        broken = FixedParser(text, finish=finish)
        try:
            await parse_delegation_arguments(DeliveryParser(broken), raw, TopicBatch)
        except ModelError as exc:
            assert str(exc) == "invalid_delegation_parse"
            assert exc.public_reply["content"] == text
            assert len(broken.requests) == 1
            failures.append(finish)
        else:
            raise AssertionError("Invalid parser output was accepted")
    summary = {
        "mode": "offline",
        "real_model_requests": 0,
        "child_topics_executed": 0,
        "raw_argument_preserved_in_request": True,
        "exact_text_and_identity_binding": True,
        "incomplete_or_invalid_parse_retains_public_reply": failures,
        "actual_runtime_boundary": await runtime_boundary(output / "runtime-boundary"),
        "scientific_quality": "Fixed parser oracle checks interface only, not semantic fidelity.",
    }
    write_json(output / "result.json", result)
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(probe(parser.parse_args().output.resolve()))


if __name__ == "__main__":
    main()
