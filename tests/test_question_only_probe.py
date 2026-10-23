"""Structural checks of the experiment, not automated scientific grading."""

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
probe = importlib.import_module("probe_question_only_context_live")
from spacebio_review.v5.conversation import ActiveConversation


def test_fresh_input_retains_originals_and_questions_but_no_old_interpretations():
    runtime = object.__new__(probe.QuestionOnlyRuntime)
    runtime.plan = {"assignment": {"responsibility": "GLOBAL TOPIC"}, "global_topics": ["TOPIC"]}
    runtime.pending = ["PENDING QUESTION"]
    runtime.studied = ["STUDIED QUESTION"]
    runtime.input_watermark = 2
    runtime.resources = lambda _: {"turns_left": 10}
    state = SimpleNamespace(
        session_id="same-author", settings=SimpleNamespace(context_tokens=128000),
        task=SimpleNamespace(application={"text": "ORIGINAL APPLICATION"},
                             evaluation={"available_sources": []}),
        skill_bodies={}, evidence={}, note_ref="OLD MANUSCRIPT",
        observations=[
            {"index": 1, "kind": "research_observation", "content": "OLD SELF CLAIM"},
            {"index": 2, "kind": "tool", "name": "read_materials", "output": {
                "result": {"ok": True, "content": "OLD TOOL RESULT"}, "readings": {}}},
            {"index": 3, "kind": "tool", "name": "read_materials", "output": {
                "result": {"ok": True, "content": "FRESH ORIGINAL"}, "readings": {}}},
        ],
    )
    conversation = ActiveConversation([
        {"role": "assistant", "content": "OLD SELF CLAIM"},
        {"role": "tool", "content": "OLD MANUSCRIPT"},
    ])
    messages = runtime.next_input(state, conversation)
    encoded = json.dumps(messages)
    for text in ["GLOBAL TOPIC", "PENDING QUESTION", "STUDIED QUESTION",
                 "ORIGINAL APPLICATION", "FRESH ORIGINAL"]:
        assert text in encoded
    for text in ["OLD SELF CLAIM", "OLD TOOL RESULT", "OLD MANUSCRIPT"]:
        assert text not in encoded
    assert not any(m["role"] in {"assistant", "tool"} for m in messages)


def test_step_actions_keep_native_contract_and_three_question_limit():
    native = [{"type": "function", "function": {
        "name": "read_materials", "description": "Read an actual source",
        "parameters": {"type": "object", "properties": {
            "source": {"$ref": "#/$defs/Source"}}, "required": ["source"],
            "$defs": {"Source": {"type": "string"}}},
    }}]
    schema = probe.step_schema(native)[0]["function"]["parameters"]
    row = {"findings": "finding", "studied_questions": [], "next_questions": {"first": "q"},
           "actions": [{"name": "read_materials", "arguments": {"source": "s"}}],
           "stop_research": False, "stop_reason": ""}
    jsonschema.validate(row, schema)
    assert "$ref" not in json.dumps(schema)
    row["next_questions"] = {"first": "q", "second": "q", "third": "q", "fourth": "q"}
    try:
        jsonschema.validate(row, schema)
    except jsonschema.ValidationError:
        pass
    else:
        raise AssertionError("question limit lost")


def test_record_reference_binding_never_guesses_an_unknown_source():
    text, unresolved, refs = probe.bind_record("已知[证据1]；未知[证据3]。", {"evidence-0000000000000001": {}})
    assert "evidence:evidence-0000000000000001" in text
    assert unresolved == ["证据3"]
    assert "未绑定引用：证据3" in text
    assert refs == {"证据1": "evidence-0000000000000001"}
