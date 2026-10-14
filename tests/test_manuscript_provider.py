import json

import httpx
import pytest

from spacebio_review.agents.contracts import example_for
from spacebio_review.models.config import load_config
from spacebio_review.models.messages import MessagesModel
from spacebio_review.models.provider import ModelError
from spacebio_review.v4.budget import Budget
from spacebio_review.v4.config import ReportingConfig, V4Config
from spacebio_review.v4.manuscript_call import ManuscriptCall, ManuscriptConfig
from spacebio_review.v4.records import Records
from spacebio_review.v4.report_authoring import author_contract


def test_publication_routes_are_owned_by_downstream_not_research_runtime():
    runtime = load_config("config/runtime.yaml")
    assert not {"assessment", "writer"}.intersection(runtime.task_routes)
    assert not {"assessment", "writer"}.intersection(runtime.formal_routes)
    assert "writer" not in runtime.models
    assert ManuscriptConfig.load().model.request_protocol == "messages"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["assessment", "author", "summary"])
async def test_each_configured_output_budget_reaches_native_provider(tmp_path, stage):
    requests = []
    contract = author_contract(["summary"], {})

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert "tools" not in payload
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": json.dumps(example_for(contract.schema()))}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 100, "output_tokens": 40},
            },
        )

    records = Records(tmp_path)
    budget = Budget(records, V4Config(max_model_calls=3), ReportingConfig())
    config = ManuscriptConfig.load()
    caller = ManuscriptCall(
        records,
        config,
        budget=budget,
        model_factory=lambda cfg: MessagesModel(
            cfg, budget=budget, client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        ),
    )
    key = "assessment/global/test" if stage == "assessment" else "writer/" + stage
    tokens = getattr(config, stage + "_tokens")
    first = await caller.submit(key, "Only public writing", {}, contract, max_tokens=tokens)
    assert await caller.submit(key, "Only public writing", {}, contract, max_tokens=tokens) == first
    assert len(requests) == 1
    assert requests[0]["max_tokens"] == tokens
    assert len(budget.data["requests"]) == 1
    row = budget.data["requests"][0]
    assert row["input"] == 100 and row["output"] == 40
    assert row["writing"] == (stage != "assessment")


@pytest.mark.asyncio
async def test_local_capability_refusal_is_not_a_sent_request():
    config = ManuscriptConfig.load().model.model_copy(update={"max_tokens": 4096})

    def forbidden(_):
        pytest.fail("Capacity refusal must precede HTTP")

    model = MessagesModel(
        config, client=httpx.AsyncClient(transport=httpx.MockTransport(forbidden))
    )
    try:
        with pytest.raises(ModelError, match="insufficient_formal_body_budget") as error:
            await model.complete([], response_schema=author_contract(["summary"], {}).schema())
        assert error.value.request_sent is False
    finally:
        await model.close()
