"""Formal request policy at the real runtime/SDK boundary, without network calls."""

import json

import httpx
import pytest
from model_helpers import offline_model

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.contracts import SubmissionContract
from spacebio_review.agents.serialization import output_example, parse_output
from spacebio_review.agents.types import AgentTask, ContextView, RoleProfile
from spacebio_review.models.config import load_config
from spacebio_review.tools import ToolRegistry
from spacebio_review.v4.contracts import professional_contract
from spacebio_review.v4.delivery import TextDraft
from spacebio_review.v4.records import Records
from spacebio_review.v4.research import save_artifact

BODY = 'The term "mtDNA nucleoid phase separation" remains a hypothesis.\nPath: C:\\data. [@E1]'


@pytest.mark.parametrize("output_format", ["json_object", "yaml"])
@pytest.mark.parametrize("first_response", ["valid", "invalid_syntax", "missing_field", "length"])
async def test_formal_format_reaches_initial_and_recovery_requests(first_response, output_format):
    requests = []
    wire = "yaml" if output_format == "yaml" else "json"

    def encoded(value):
        return output_example(value, {}, wire)

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        finish = "stop"
        content = encoded({"text": BODY})
        if len(requests) == 1:
            if first_response == "invalid_syntax":
                content = (
                    "text: [broken"
                    if wire == "yaml"
                    else '{"text": "The term "mtDNA" is unverified."}'
                )
            elif first_response == "missing_field":
                content = "{}"
            elif first_response == "length":
                content, finish = '{"text": "The term', "length"
        elif first_response == "missing_field":
            content = encoded({"replacements": {"/text": BODY}})
        return httpx.Response(
            200,
            json={
                "id": "local-prose-test",
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
        return offline_model(config, respond)

    config = load_config("config/runtime.yaml")
    config.formal_routes["writer"].structured_output = output_format
    runtime = AgentRuntime.from_config(config, model_factory=factory)
    task = AgentTask(
        task_id="writer/quote-policy",
        phase="writer/quote-policy",
        kind="writer",
        objective="Translate the supplied sentence faithfully.",
        expected_product="text",
        mode="direct",
        read_actions=0,
        max_model_turns=3,
        max_repairs=1,
        max_truncation_recoveries=1,
    )
    try:
        result = await runtime.run_task(
            task,
            RoleProfile(agent_id=task.task_id, responsibility=task.objective),
            ContextView(agent_id=task.task_id, phase=task.phase),
            ToolRegistry(),
            SubmissionContract(TextDraft, TextDraft),
        )
    finally:
        await runtime.close()

    assert result.status == "completed"
    assert result.product == {"text": BODY}
    assert len(requests) == (1 if first_response == "valid" else 2)
    for payload in requests:
        if wire == "yaml":
            assert "response_format" not in payload
        else:
            assert payload["response_format"] == {"type": "json_object"}
        assert payload["enable_thinking"] is False
        assert "tools" not in payload
        final = payload["messages"][-1]["content"]
        # Recovery wraps its instructions in JSON; inspect the decoded instruction.
        try:
            final = json.loads(final)["instruction"]
        except (ValueError, TypeError):
            pass
        assert wire.upper() in final
        if wire == "yaml":
            assert "|-" in final


def test_literal_prose_preserves_json_like_text_and_nested_product():
    raw = """title: Phase separation
scope_note: |-
  This addresses the claim "mtDNA nucleoid phase separation".
  Literal backslash: \\n; citation: [@E1]; colon: value; # not a comment.
blocks:
  - text: |-
      A quotation does not terminate this argument: "claim". Conditions follow.
scope_outcomes:
  - member_id: q22
    status: analyzed
    explanation: |-
      The analysis is complete; the scientific conclusion remains uncertain.
    block_indices: [1]
followups: []
"""
    product = parse_output(raw, "yaml")
    assert '"claim". Conditions follow.' in product["blocks"][0]["text"]
    assert (
        r"Literal backslash: \n; citation: [@E1]; colon: value; # not a comment."
        in product["scope_note"]
    )
    assert product["scope_outcomes"][0]["block_indices"] == [1]
    assert json.loads(json.dumps(product)) == product


@pytest.mark.parametrize(
    "raw", ["text: first\ntext: second", "!!python/object:builtins.object {}", "- item"]
)
def test_yaml_keeps_existing_single_object_and_safe_parsing_boundary(raw):
    with pytest.raises(ValueError):
        parse_output(raw, "yaml")


def test_only_specialist_and_writer_formal_routes_change():
    config = load_config("config/runtime.yaml")
    for kind in config.task_routes:
        if kind == "question_generation":
            assert config.routed_model(kind, formal=True).structured_output == "json_schema"
            continue
        assert config.routed_model(kind).structured_output == "json_object"
        expected = "yaml" if kind in {"specialist", "writer"} else "json_object"
        assert config.routed_model(kind, formal=True).structured_output == expected


def test_yaml_professional_product_keeps_citations_and_json_artifact(tmp_path):
    source = {
        "reference": {"source_id": "source-1", "passage_id": "p1"},
        "title": "Application",
        "kind": "application",
        "locator": "p1",
        "quote": 'The application claims "phase separation".',
        "material_version": "retained-version",
        "reading_scope": "passage",
    }
    contract = professional_contract(None, {}, {"q22": {}}, evidence={"evidence-1": source})
    raw = """title: Method assessment
scope_note: |-
  The claim "phase separation" needs evidence. [@E1]
blocks:
  - text: |-
      Static morphology cannot establish the claimed "mechanism". [@E1]
followups: []
scope_outcomes:
  - member_id: q22
    status: analyzed
    explanation: Analysis is complete; the conclusion remains uncertain.
    block_indices: [1]
"""
    product = contract.product(contract.parse(parse_output(raw, "yaml")))
    records = Records(tmp_path)
    ref = save_artifact(
        records,
        "research",
        product.model_dump(mode="json"),
        task="research/q22/expert-6/r0",
        author="expert-6",
        receipt={"input": "test-input", "reading": {}},
    )
    stored = records.value(ref)
    assert '"mechanism". [@evidence-1]' in stored["blocks"][0]["text"]
    assert stored["blocks"][0]["evidence_refs"] == ["evidence-1"]
    assert stored["scope_outcomes"][0]["block_ids"] == ["b1"]
    assert stored["evidence"]["evidence-1"]["quote"] == source["quote"]
