import json

import pytest
from pydantic import ValidationError

from spacebio_review.agents.contracts import example_for
from spacebio_review.models.provider import ChatReply, ModelError
from spacebio_review.review.project import REVIEW_ASPECTS
from spacebio_review.v4.assessment import assessment_contract, synthesize
from spacebio_review.v4.manuscript_call import ManuscriptCall, ManuscriptConfig
from spacebio_review.v4.records import Records


def fixture():
    return {
        "evaluation": {"prompt": "Shared evaluation standard"},
        "materials": {
            "opinions": {"O1": {"context": "G1", "text": "Claim [@E1]"}},
            "opinion_contexts": {
                "G1": {"title": "Analysis", "scope": "Condition", "opinion_ids": ["O1"]}
            },
            "evidence": {"E1": {"source": "proposal", "quote": "Source text"}},
            "application": ["E1"],
            "sources": {"proposal": {"title": "Proposal", "kind": "application"}},
        },
    }


def test_global_contract_is_independent_and_citations_are_materials_only():
    contract = assessment_contract(fixture())
    value = example_for(contract.schema())
    assert set(value["dimensions"]) == set(REVIEW_ASPECTS)
    assert not {"blocks", "scope_note", "followups", "scope_outcomes"} & value.keys()
    value["dimensions"][REVIEW_ASPECTS[0]]["grade"] = None
    value["dimensions"][REVIEW_ASPECTS[0]]["confidence"] = None
    contract.parse(value)
    assert list(value).index("dimensions") < list(value).index("recommendation")
    assert list(value["dimensions"][REVIEW_ASPECTS[0]]) == ["body", "grade", "confidence"]
    value["overall"]["paragraphs"][0]["parts"][0]["cites"] = ["O1"]
    with pytest.raises(ValidationError):
        contract.parse(value)


@pytest.mark.asyncio
async def test_one_call_cached_and_raw_response_can_be_replayed(tmp_path):
    records, calls = Records(tmp_path), []

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, *, response_schema=None):
            calls.append(messages)
            return ChatReply({"content": json.dumps(example_for(response_schema))})

        async def close(self):
            pass

    caller = ManuscriptCall(records, ManuscriptConfig.load(), model_factory=Model)
    ref = records.put("frozen-review", fixture())
    first = await synthesize(caller, ref)
    assert await synthesize(caller, ref) == first
    assert len(calls) == 1
    assert calls[0][0]["content"].count("Shared evaluation standard") == 1
    assert "Shared evaluation standard" not in calls[0][1]["content"]
    # Simulate interruption between receiving and parsing a response.
    pointer = next((records.root / "receipts").glob("*.json"))
    saved = records.value(json.loads(pointer.read_text())["ref"])
    saved.pop("product")
    saved["status"] = "received"
    records.receipt(saved["identity"], saved)
    assert await synthesize(caller, ref) == first
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_failure_never_buys_another_sample(tmp_path):
    calls = []

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, *args, **kwargs):
            calls.append(1)
            raise ModelError("response_unknown")

        async def close(self):
            pass

    caller = ManuscriptCall(Records(tmp_path), ManuscriptConfig.load(), model_factory=Model)
    ref = caller.records.put("frozen-review", fixture())
    with pytest.raises(ModelError):
        await synthesize(caller, ref)
    with pytest.raises(ValueError, match="already_attempted"):
        await synthesize(caller, ref)
    assert len(calls) == 1
