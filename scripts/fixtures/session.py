"""Offline provider fixtures for local runtime and entry checks."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

from spacebio_review.models.config import ModelConfig
from spacebio_review.models.provider import ChatReply
from spacebio_review.v5.session import ResearchRuntime


def tool_call(name, arguments):
    return {
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


def reply(*calls, content=None):
    return ChatReply(
        {
            "role": "assistant",
            "content": content,
            "tool_calls": [dict(call, id=f"call-{index}") for index, call in enumerate(calls)],
        },
        "tool_calls" if calls else "stop",
        prompt_tokens=120,
        completion_tokens=40,
    )


def require_content(messages, *pieces):
    text = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    for piece in pieces:
        assert piece in text, f"Missing from continued context: {piece}"


class ScriptModel:
    """Replace only model responses; runtime, tools and persistence remain real."""

    def __init__(self, steps, *, name="local-author"):
        self.config = ModelConfig(
            model=name,
            base_url="https://local.invalid/v1",
            tools=True,
            max_tokens=512,
            retries=0,
        )
        self.steps, self.requests = list(steps), []

    async def complete(self, messages, tools=None, *, response_schema=None):
        assert response_schema is None
        assert tools is not None
        self.requests.append({"messages": deepcopy(messages), "tools": deepcopy(tools)})
        assert self.steps, "Unexpected extra model request"
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(messages) if callable(step) else step

    async def close(self):
        pass


class MeteredScript(ScriptModel):
    async def complete(self, messages, tools=None, *, response_schema=None):
        ticket = self.budget.reserve(self.config, messages, tools, response_schema)
        try:
            return await super().complete(messages, tools, response_schema=response_schema)
        finally:
            self.budget.settle(ticket, SimpleNamespace(prompt_tokens=120, completion_tokens=40))


class AssemblyFixture:
    config = ModelConfig(
        model="local-assembly", base_url="https://local.invalid/v1", tools=False, max_tokens=512
    )

    async def complete(self, messages, tools=None, *, response_schema=None):
        return reply(content='{"annotations": {}}')


def runtime(root, model, **kwargs):
    return ResearchRuntime(
        root,
        {"local": model, "assembly": AssemblyFixture()},
        {"chief": "local", "researcher": "local"},
        **kwargs,
    )
