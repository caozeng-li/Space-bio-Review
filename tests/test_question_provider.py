import json

import httpx
import pytest

from spacebio_review.models.config import ModelConfig
from spacebio_review.models.messages import MessagesModel
from spacebio_review.models.provider import ModelError


def configuration():
    return ModelConfig(
        model="deepseek-v4-pro",
        base_url="https://example.test/apps/anthropic",
        request_protocol="messages",
        structured_output="json_schema",
        schema_supported=True,
        schema_with_thinking=True,
        capability_source="fixture",
        reasoning_reserve=1024,
        max_tokens=4096,
        extra_body={"thinking": {"type": "enabled"}, "output_config": {"effort": "high"}},
        retries=0,
    )


SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string", "enum": ["a", "b"]}},
    "required": ["value"],
    "additionalProperties": False,
}


def test_only_question_route_uses_messages_and_disables_retry():
    from spacebio_review.models.config import load_config
    from spacebio_review.models.provider import DashScopeModel, model_class

    config = load_config("config/runtime.yaml")
    for kind in config.task_routes:
        for tier in ("normal", "strong"):
            for formal in (False, True):
                model = config.routed_model(kind, tier, formal=formal)
                native = kind == "question_generation" and (formal or tier == "normal")
                assert model_class(model) is (MessagesModel if native else DashScopeModel)
    model = config.routed_model("question_generation", formal=True)
    assert model.structured_output == "json_schema"
    assert model.retries == 0
    assert config.task_routes["question_generation"].read_actions == 0
    assert config.task_routes["question_generation"].max_truncation_recoveries == 0


def test_opt_in_fields_preserve_existing_default_execution_identity(tmp_path):
    from types import SimpleNamespace

    from spacebio_review.models.config import load_config
    from spacebio_review.v4.config import V4Config
    from spacebio_review.v4.execution import Execution
    from spacebio_review.v4.records import Records

    config = load_config("config/runtime.yaml")
    historical = config.model_dump(mode="json")
    for model in historical["models"].values():
        if model["request_protocol"] == "chat_completions":
            model.pop("request_protocol")
    for route in historical["task_routes"].values():
        if route["retries"] is None:
            route.pop("retries")
    records = Records(tmp_path)
    original = Execution(SimpleNamespace(config=historical), records, V4Config(), {})
    current = Execution(SimpleNamespace(config=config), records, V4Config(), {})
    assert current.configuration == original.configuration
    config.task_routes["preparation"].retries = 0
    changed = Execution(SimpleNamespace(config=config), records, V4Config(), {})
    assert changed.configuration != current.configuration


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stop,expected", [("end_turn", "stop"), ("max_tokens", "length"), ("refusal", "content_filter")]
)
async def test_messages_wire_and_response(stop, expected):
    observed = []

    def respond(request):
        observed.append(json.loads(request.content))
        assert str(request.url) == "https://example.test/apps/anthropic/v1/messages"
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "thinking", "thinking": "private reasoning"},
                    {"type": "text", "text": '{"value":"a"}'},
                ],
                "stop_reason": stop,
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 5,
                    "output_tokens": 40,
                },
            },
        )

    model = MessagesModel(
        configuration(), client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    )
    try:
        reply = await model.complete(
            [
                {"role": "system", "content": "System"},
                {"role": "user", "content": [{"type": "text", "text": "Task"}]},
            ],
            response_schema=SCHEMA,
        )
    finally:
        await model.close()
    assert observed[0]["output_config"] == {
        "effort": "high",
        "format": {"type": "json_schema", "schema": SCHEMA},
    }
    assert observed[0]["system"] == [{"type": "text", "text": "System"}]
    assert "response_format" not in observed[0]
    assert reply.message["content"] == '{"value":"a"}'
    assert reply.message["reasoning_content"] == "private reasoning"
    assert reply.prompt_tokens == 35
    assert reply.completion_tokens == 40
    assert reply.reasoning_tokens is None
    assert reply.finish_reason == expected
    assert reply.effective_request["request_protocol"] == "messages"
    assert reply.provider_response["usage"]["input_tokens"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 429, 500])
async def test_messages_http_failure_does_not_retry_or_leak_body(status):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "secret provider detail"}})

    model = MessagesModel(
        configuration(), client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    )
    with pytest.raises(ModelError) as error:
        await model.complete([{"role": "user", "content": "Task"}], response_schema=SCHEMA)
    await model.close()
    assert len(calls) == 1
    assert "secret" not in str(error.value)
    assert error.value.terminal == (status == 401)


@pytest.mark.asyncio
async def test_messages_connection_failure_is_not_retried():
    calls = []

    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout("fixture")

    model = MessagesModel(
        configuration(), client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    )
    with pytest.raises(ModelError, match="model_request_timeout"):
        await model.complete([{"role": "user", "content": "Task"}], response_schema=SCHEMA)
    await model.close()
    assert len(calls) == 1
