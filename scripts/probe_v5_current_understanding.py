"""Exercise current-manuscript updates and completion plans without network requests."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from probe_v5_session import ParserModel, ScriptModel, reply, runtime, tool_call
from pydantic import BaseModel

from spacebio_review.models.provider import ModelError
from spacebio_review.persistence import write_json
from spacebio_review.tools import Tool, ToolRegistry, ToolResult
from spacebio_review.v5 import SessionSettings, SessionTask
from spacebio_review.v5.budget import Budget, BudgetLimits

ROOT = Path(__file__).resolve().parents[1]


def body_text(context):
    return json.dumps(context["current_research"], ensure_ascii=False)


async def explicit_revision(root):
    def revised(messages):
        context = json.loads(messages[-1]["content"])
        assert "INITIAL_INTERPRETATION" in body_text(context)
        assert "PLAN_ORIGINAL" in json.dumps(context["task"])
        return reply(content="CURRENT_REVISED_INTERPRETATION")

    author = ScriptModel(
        [
            reply(content="INITIAL_INTERPRETATION"),
            reply(tool_call("update_understanding", {"reason": "重新理解申请原文中的计划安排"})),
            revised,
            ModelError("local_stop_after_revision", category="exploration"),
        ]
    )
    parser = ParserModel()
    engine = runtime(root, author, delivery_parser=parser)
    sid = engine.create(
        SessionTask(role="chief", objective="修订当前理解", application="PLAN_ORIGINAL")
    )
    outcome = await engine.run(sid)
    assert outcome.reason == "local_stop_after_revision"
    state = engine.load(sid)
    assert state.initial_understanding_ref != state.note_ref
    assert state.revision == 2 and not state.observations
    current = engine._context(state)
    assert "CURRENT_REVISED_INTERPRETATION" in body_text(current)
    assert "INITIAL_INTERPRETATION" not in body_text(current)
    assert "INITIAL_INTERPRETATION" in json.dumps(
        engine.store.note(state.initial_understanding_ref)["note"]
    )
    artifact = engine.store.note(state.note_ref)
    raw = engine.store.author_text(artifact["author_text_ref"])
    assert raw["replaces"] == [state.initial_understanding_ref]
    assert not author.requests[0]["tools"] and not author.requests[2]["tools"]
    assert not parser.requests
    persisted = json.loads(engine.store._path("sessions", sid).read_text("utf-8"))
    assert "active_note_refs" not in persisted
    assert "context_organizing_revision" not in persisted
    assert "schema_version" not in persisted
    return {"current_replaces_initial": True, "explicit_revision_without_external_data": True}


async def fresh_observations(root):
    class Empty(BaseModel):
        pass

    visits = []

    async def read(_):
        marker = f"NEW_RESULT_{len(visits) + 1}"
        visits.append(marker)
        return ToolResult(ok=True, content={"result": marker, "original": "x" * 6000})

    def organized(number):
        def output(messages):
            context = json.loads(messages[-1]["content"])
            assert f"NEW_RESULT_{number}" in json.dumps(context["unabsorbed_observations"])
            previous = "INITIAL_STATE" if number == 1 else "ORGANIZED_1"
            assert previous in body_text(context)
            return reply(content=f"ORGANIZED_{number}")

        return output

    author = ScriptModel(
        [
            reply(content="INITIAL_STATE"),
            reply(tool_call("read_fixture", {})),
            organized(1),
            reply(tool_call("read_fixture", {})),
            organized(2),
            reply(tool_call("finish_research", {"reason": "已有研究可以形成意见"})),
            reply(content="FINAL_RESEARCH_OPINION"),
        ]
    )
    parser = ParserModel()
    engine = runtime(
        root,
        author,
        delivery_parser=parser,
        settings=SessionSettings(context_tokens=512, max_turns=16),
    )
    sid = engine.create(SessionTask(role="chief", objective="研究新材料", application="PLAN"))
    registry = ToolRegistry()
    registry.add(Tool("read_fixture", "读取固定材料", Empty, read))
    outcome = await engine.run(sid, tools=registry)
    assert outcome.status == "submitted" and visits == ["NEW_RESULT_1", "NEW_RESULT_2"]
    state = engine.load(sid)
    assert state.revision == 4 and not state.observations
    raw_files = [
        json.loads(path.read_text("utf-8")) for path in (root / "v5/author-texts").glob("*.json")
    ]
    organized_raw = sorted(
        (row for row in raw_files if row["phase"] == "organize_research"),
        key=lambda row: row["note_revision"],
    )
    assert len(organized_raw) == 2
    assert organized_raw[0]["absorbed_through"] < organized_raw[1]["absorbed_through"]
    assert all(row["phase"] != "batch_absorption" for row in raw_files)
    assert not parser.requests and len(author.requests) == 7
    return {"later_results_trigger_organization": True, "no_repeat_without_new_observations": True}


async def current_resume(root):
    author = ScriptModel([
        reply(content="CURRENT_MANUSCRIPT"),
        ModelError("fixed_pause", category="exploration"),
    ])
    parser = ParserModel()
    engine = runtime(root, author, delivery_parser=parser)
    sid = engine.create(SessionTask(role="chief", objective="恢复当前研究", application="PLAN"))
    first = await engine.run(sid)
    assert first.reason == "fixed_pause"
    path = engine.store._path("sessions", sid)
    saved = json.loads(path.read_text("utf-8"))
    assert "schema_version" not in saved
    initial_ref = saved["note_ref"]
    resumed_author = ScriptModel([
        reply(tool_call("finish_research", {"reason": "已有研究可以形成意见"})),
        reply(content="FINAL_MANUSCRIPT"),
    ])
    resumed = runtime(root, resumed_author, delivery_parser=parser)
    assert "CURRENT_MANUSCRIPT" in body_text(resumed._context(resumed.load(sid)))
    outcome = await resumed.run(sid)
    assert outcome.status == "submitted"
    state = resumed.load(sid)
    assert state.initial_understanding_ref == initial_ref and state.revision == 2
    assert len(resumed_author.requests) == 2 and not parser.requests
    return {"current_state_restored": True, "initial_manuscript_not_rewritten": True}


def completion_plans(root):
    class Mapping(BaseModel):
        value: int = 1

    budget = Budget(root, BudgetLimits(max_model_calls=100))
    author = ScriptModel([])
    parser = ParserModel(budget=budget)
    engine = runtime(root, author, delivery_parser=parser, budget=budget)
    chief = engine.load(
        engine.create(SessionTask(role="chief", objective="评价预算", application="PLAN"))
    )
    submission = SimpleNamespace(
        completion_operations=lambda state: ["submit_assessment"],
        parsed_operations={"submit_assessment"},
        authoring_contract=lambda raw: (Mapping, "Extract explicit values", None),
    )

    def check(state, expected, submission=None, *, include_request=False):
        reason = engine._completion_plan(
            state, author, [], [], submission, include_request=include_request
        )
        assert reason is None
        plan = budget.data["sessions"][state.session_id]["completion_plan"]
        assert plan["calls"] == expected, plan
        assert all("update_research" not in step for step in plan["steps"])
        return plan["steps"]

    results = {"chief_next_research": check(chief, 2, submission, include_request=True)}
    raw_ref = engine.store.save_author_text(
        chief, "FINAL_ASSESSMENT", "submit_assessment", absorbed_through=0
    )
    chief.pending_delivery = {
        "raw_ref": raw_ref,
        "purpose": "completion",
        "operation": "submit_assessment",
        "parse_ref": None,
    }
    results["assessment_raw"] = check(chief, 1, submission)
    chief.pending_delivery["parse_ref"] = engine.store.save_delivery_parse(
        raw_ref, parser.config.model_dump(mode="json"), Mapping()
    )
    results["assessment_mapped"] = check(chief, 0, submission)
    researcher = engine.load(
        engine.create(SessionTask(role="researcher", objective="研究预算", application="PLAN"))
    )
    results["research_unwritten"] = check(researcher, 1)
    raw_ref = engine.store.save_author_text(
        researcher, "FINAL_RESEARCH", "submit_research", absorbed_through=0
    )
    researcher.pending_delivery = {
        "raw_ref": raw_ref,
        "purpose": "completion",
        "operation": "submit_research",
        "parse_ref": None,
    }
    results["research_raw"] = check(researcher, 0)
    researcher.pending_delivery["purpose"] = "auxiliary"
    results["research_raw_auxiliary"] = check(researcher, 0)
    assert not author.requests and not parser.requests and not budget.data["requests"]
    return results


async def run(root):
    summary = {
        "explicit_revision": await explicit_revision(root / "explicit-revision"),
        "observation_watermarks": await fresh_observations(root / "fresh-observations"),
        "current_resume": await current_resume(root / "current-resume"),
        "completion_plans": completion_plans(root / "completion-plans"),
        "real_model_requests": 0,
        "real_tool_requests": 0,
    }
    write_json(root / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "runs/v5-local"
        / ("change25-current-understanding-" + datetime.now().strftime("%Y%m%d-%H%M%S")),
    )
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Choose a new output directory; prior probe results are retained.")
    asyncio.run(run(args.output))


if __name__ == "__main__":
    main()
