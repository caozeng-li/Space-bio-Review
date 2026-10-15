"""Exercise the v5 session Interface locally, without a full review or network requests."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spacebio_review.models.config import ModelConfig  # noqa: E402
from spacebio_review.models.provider import ChatReply, ModelError  # noqa: E402
from spacebio_review.tools import Tool, ToolRegistry, ToolResult  # noqa: E402
from spacebio_review.v5 import ResearchRuntime, SessionSettings, SessionTask  # noqa: E402
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance  # noqa: E402

PRIVATE_SENTINEL = "PRIVATE_REASONING_MUST_NOT_BE_PERSISTED"
APPLICATION = "项目包含地面对照；在轨记录荧光，返回后独立实施分子检测。"
SOURCE_A = "原文A：实验设有地面对照，检测对象为两种肿瘤细胞系。"
SOURCE_B = "原文B：培养条件会改变读数；本文没有直接比较正常原代肝细胞。"


def note(text, cites=(), *, unknowns=()):
    return {
        "title": "细胞模型研究说明",
        "body": {"paragraphs": [{"parts": [{"text": text, "cites": list(cites)}]}]},
        "unknowns": list(unknowns),
    }


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
            "tool_calls": [dict(call, id=f"call-{i}") for i, call in enumerate(calls)],
            "reasoning_content": PRIVATE_SENTINEL,
        },
        "tool_calls" if calls else "stop",
        prompt_tokens=120,
        completion_tokens=40,
    )


def rendered(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def require_content(messages, *pieces):
    text = rendered(messages)
    for piece in pieces:
        assert piece in text, f"Missing from continued context: {piece}"


class ScriptModel:
    """Only the provider response is replaced; session/tools/storage execute normally."""

    def __init__(self, steps, *, name="local-author"):
        self.config = ModelConfig(
            model=name,
            base_url="https://local.invalid/v1",
            tools=True,
            max_tokens=512,
            retries=0,
        )
        self.steps = list(steps)
        self.requests = []

    async def complete(self, messages, tools=None, *, response_schema=None):
        assert response_schema is None, "Direct submission must stay in the research tool loop"
        assert tools, "The selected route must expose tools"
        self.requests.append({"messages": deepcopy(messages), "tools": deepcopy(tools)})
        assert self.steps, "Unexpected extra author/rewrite request"
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            step = step(messages)
        return step

    async def close(self):
        pass


class ReadFixture(BaseModel):
    passage: str


class SortFixture(BaseModel):
    pass


class FixtureReader:
    def __init__(self, *, padding=""):
        self.readings = {}
        self.visits = []
        self.padding = padding
        self.registry = ToolRegistry(
            [Tool("read_fixture", "读取本地固定资料原文。", ReadFixture, self.read)]
        )
        self.registry.evidence_views.append(lambda: deepcopy(self.readings))

    async def read(self, args):
        source = {"a": SOURCE_A, "b": SOURCE_B}[args.passage]
        self.visits.append(args.passage)
        self.readings[f"evidence-{args.passage}"] = {
            "reference": {
                "source_id": "fixture-paper",
                "passage_id": args.passage,
                "visual_id": None,
            },
            "title": "固定方法材料",
            "kind": "external",
            "locator": f"Methods/{args.passage}",
            "quote": source,
            "material_version": f"fixed-{args.passage}",
            "reading_scope": "text",
            "provided_via": ["task_read"],
            "citation_status": "not_semantically_verified",
            "url": "local:fixed-paper",
        }
        return ToolResult(
            ok=True,
            content={
                "source_id": "fixture-paper",
                "passage_id": args.passage,
                "text": source,
                "navigation": self.padding,
            },
            source_ids=["fixture-paper"],
        )


def runtime(root, model, **kwargs):
    return ResearchRuntime(
        root,
        {"local": model},
        {"chief": "local", "researcher": "local"},
        **kwargs,
    )


def assert_private_not_saved(root):
    for path in root.rglob("*.json"):
        assert PRIVATE_SENTINEL not in path.read_text(encoding="utf-8"), path


async def continuity(root):
    first_text = "原研究比较两种肿瘤细胞系，目前尚需确定培养条件的影响。"
    revised_text = "两种肿瘤细胞系的比较受培养条件影响，不能当作正常原代细胞的直接比较。"
    final_text = revised_text + "本项目已有地面对照；模型外推仍应按对应检测终点解释。"

    def first_update(messages):
        require_content(messages, APPLICATION, SOURCE_A)
        assert any(row.get("reasoning_content") == PRIVATE_SENTINEL for row in messages)
        return reply(tool_call("update_research", note(first_text, ["E1"])))

    def revised_update(messages):
        require_content(messages, first_text, SOURCE_A, SOURCE_B)
        return reply(tool_call("update_research", note(revised_text, ["E1", "E2"])))

    model = ScriptModel(
        [
            reply(tool_call("read_fixture", {"passage": "a"})),
            first_update,
            reply(tool_call("read_fixture", {"passage": "b"})),
            revised_update,
            reply(tool_call("submit_research", {"note": None})),
        ]
    )
    reader = FixtureReader()
    engine = runtime(root, model)
    session = engine.create(
        SessionTask(role="researcher", objective="解释模型比较范围", application=APPLICATION)
    )
    outcome = await engine.run(session, tools=reader.registry)
    assert outcome.status == "submitted", outcome
    state = engine.load(session)
    previous_ref = state.note_ref
    artifact = engine.store.note(previous_ref)
    assert artifact["note"] == note(revised_text, ["evidence-a", "evidence-b"])
    assert artifact["paragraph_ids"] and all(artifact["paragraph_ids"])
    assert artifact["session_id"] == session
    assert len(model.requests) == 5 and not model.steps
    assert reader.visits == ["a", "b"]
    assert state.turns_used == 5
    await engine.close()

    def recovered_read(messages):
        require_content(messages, APPLICATION, revised_text, "原代细胞")
        return reply(tool_call("read_references", {"references": ["E1", "E2"]}))

    def recovered_submit(messages):
        require_content(messages, SOURCE_A, SOURCE_B, revised_text)
        return reply(tool_call("submit_research", {"note": note(final_text, ["E1", "E2"])}))

    resumed_model = ScriptModel([recovered_read, recovered_submit])
    resumed = runtime(root, resumed_model)
    followup = await resumed.run(session, followup="请结合已有地面对照解释外推范围。")
    after = resumed.load(session)
    assert followup.status == "submitted", followup
    assert after.turns_used == 7, "Restart must not replenish accumulated session allowance"
    assert after.note_ref != previous_ref
    assert resumed.store.note(previous_ref) == artifact, "Saved versions must remain recoverable"
    final_artifact = resumed.store.note(after.note_ref)
    assert final_artifact["note"]["body"]["paragraphs"][0]["parts"][0]["text"] == final_text
    assert final_artifact["paragraph_ids"] != artifact["paragraph_ids"]
    assert set(after.evidence) == {"evidence-a", "evidence-b"}
    assert_private_not_saved(root)
    await resumed.close()
    return {"session": session, "requests": 7, "saved_revisions": after.revision}


async def compression(root):
    current_text = "当前认识只吸收了细胞系的比较对象。"

    def update_then_read(messages):
        require_content(messages, SOURCE_A)
        return reply(
            tool_call("update_research", note(current_text, ["E1"])),
            tool_call("read_fixture", {"passage": "b"}),
        )

    def complete(messages):
        require_content(messages, current_text, SOURCE_B)
        # A later tool result in the same assistant batch has not yet been absorbed by the note.
        assert any(SOURCE_B in str(row.get("content", "")) for row in messages), (
            "Compaction dropped the unabsorbed reading"
        )
        return reply(
            tool_call("submit_research", {"note": note(current_text + SOURCE_B, ["E1", "E2"])})
        )

    model = ScriptModel(
        [reply(tool_call("read_fixture", {"passage": "a"})), update_then_read, complete]
    )
    engine = runtime(root, model, settings=SessionSettings(context_tokens=1400))
    reader = FixtureReader(padding="定位上下文。" * 2000)
    session = engine.create(
        SessionTask(role="researcher", objective="压缩后继续阅读", application=APPLICATION)
    )
    result = await engine.run(session, tools=reader.registry)
    assert result.status == "submitted", result
    assert len(model.requests) == 3
    assert engine.load(session).context_compactions > 0
    assert_private_not_saved(root)
    await engine.close()
    return {"session": session, "requests": 3}


async def failure_recovery(root):
    draft = note("已知项目包含地面对照，服务中断前尚未完成进一步研究。")
    model = ScriptModel(
        [
            reply(tool_call("update_research", draft)),
            ModelError("local_simulated_timeout", category="timeout", request_sent=True),
        ]
    )
    engine = runtime(root, model)
    session = engine.create(
        SessionTask(role="researcher", objective="保存技术中断前的说明", application=APPLICATION)
    )
    interrupted = await engine.run(session)
    state = engine.load(session)
    assert interrupted.status in {"partial", "failed"}, interrupted
    assert state.note_ref and not state.submitted_ref
    assert engine.store.note(state.note_ref)["note"] == draft
    used_before = state.turns_used
    await engine.close()

    def resume(messages):
        require_content(messages, draft["body"]["paragraphs"][0]["parts"][0]["text"])
        return reply(tool_call("submit_research", {"note": None}))

    continued = runtime(root, ScriptModel([resume]))
    result = await continued.run(session, followup="按已明确的范围提交现有说明。")
    assert result.status == "submitted", result
    assert continued.load(session).turns_used == used_before + 1
    assert_private_not_saved(root)
    await continued.close()
    return {"session": session, "technical_failure_preserved_draft": True}


class ConcurrentModel(ScriptModel):
    def __init__(self, *, barrier=False):
        super().__init__([])
        self.active = 0
        self.max_active = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.barrier = barrier

    async def complete(self, messages, tools=None, *, response_schema=None):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.requests.append({"messages": deepcopy(messages), "tools": deepcopy(tools)})
        self.entered.set()
        if self.barrier and self.active == 2:
            self.release.set()
        try:
            await asyncio.wait_for(self.release.wait(), timeout=3)
            return reply(tool_call("submit_research", {"note": note("独立会话已直接提交。")}))
        finally:
            self.active -= 1


async def concurrency(root):
    serial_model = ConcurrentModel()
    serial = runtime(root / "same-topic", serial_model)
    session = serial.create(
        SessionTask(role="researcher", objective="同专题写入", application=APPLICATION)
    )
    first = asyncio.create_task(serial.run(session))
    await serial_model.entered.wait()
    second = asyncio.create_task(serial.run(session, followup="续问同一个专题。"))
    await asyncio.sleep(0)
    serial_model.release.set()
    results = await asyncio.gather(first, second)
    assert all(result.status == "submitted" for result in results), results
    assert serial_model.max_active == 1, "A logical session must have a single writer"
    assert len(serial_model.requests) == 2
    assert serial.load(session).turns_used == 2
    await serial.close()

    parallel_model = ConcurrentModel(barrier=True)
    parallel = runtime(root / "different-topics", parallel_model)
    topics = [
        parallel.create(
            SessionTask(role="researcher", objective=f"并行专题{i}", application=APPLICATION)
        )
        for i in range(2)
    ]
    results = await asyncio.gather(*(parallel.run(topic) for topic in topics))
    assert all(result.status == "submitted" for result in results), results
    assert parallel_model.max_active == 2, "Independent topics should execute concurrently"
    await parallel.close()
    return {"same_topic_max_writers": 1, "different_topics_max_writers": 2}


class MeteredModel(ScriptModel):
    def __init__(self, steps, budget, *, output_tokens=40):
        super().__init__(steps)
        self.budget = budget
        self.output_tokens = output_tokens
        self.config.max_tokens = 128

    async def complete(self, messages, tools=None, *, response_schema=None):
        # Match provider ownership: one reserve/settle per actual request, no runtime double count.
        ticket = self.budget.reserve(self.config, messages, tools, response_schema)
        response = await super().complete(messages, tools, response_schema=response_schema)
        response.completion_tokens = self.output_tokens
        self.budget.settle(
            ticket,
            SimpleNamespace(prompt_tokens=120, completion_tokens=self.output_tokens),
        )
        return response


async def budget_ownership(root):
    limits = BudgetLimits(
        max_model_calls=8,
        max_seconds=600,
        max_input_tokens=100_000,
        max_output_tokens=20_000,
    )
    ledger = Budget(root, limits)
    model = MeteredModel(
        [
            reply(tool_call("sort_fixture", {})),
            reply(tool_call("submit_research", {"note": note("专题一已完成局部研究。")})),
            reply(tool_call("submit_research", {"note": None})),
            reply(tool_call("update_research", note("专题二当前说明。"))),
            reply(tool_call("submit_research", {"note": None})),
            reply(tool_call("update_research", note("主审保留解释与提交资源。"))),
            reply(tool_call("submit_research", {"note": None})),
        ],
        ledger,
    )
    engine = runtime(root, model, budget=ledger)
    allowance = SessionAllowance(
        max_input_tokens=20_000,
        max_output_tokens=4000,
        completion_input_tokens=2000,
        completion_output_tokens=128,
    )
    chief, topic_a, topic_b = [
        engine.create(
            SessionTask(role=role, objective=objective, application=APPLICATION),
            allowance=allowance,
        )
        for role, objective in (
            ("chief", "保持项目判断"),
            ("researcher", "专题一"),
            ("researcher", "专题二"),
        )
    ]

    async def sort_fixture(args):
        ticket = ledger.reserve(model.config, [{"role": "user", "content": "给两个固定材料排序。"}])
        ledger.settle(ticket, SimpleNamespace(prompt_tokens=30, completion_tokens=10))
        return ToolResult(ok=True, content={"ordered": ["a", "b"]})

    tools = ToolRegistry([Tool("sort_fixture", "对固定材料排序。", SortFixture, sort_fixture)])
    first = await engine.run(topic_a, tools=tools)
    assert first.status == "submitted", first
    assert engine.load(topic_a).turns_used == 2
    assert ledger.usage(topic_a)["model_calls"] == 3, "Auxiliary usage must be counted once"
    assert ledger.usage(chief)["model_calls"] == engine.load(chief).turns_used == 0
    assert ledger.usage(topic_b)["model_calls"] == 0
    first_usage = ledger.usage(topic_a)
    await engine.close()

    restored_ledger = Budget(root, limits)
    model.budget = restored_ledger
    resumed = runtime(root, model, budget=restored_ledger)
    continued = await resumed.run(topic_a, followup="确认现有范围并提交。")
    assert continued.status == "submitted", continued
    assert resumed.load(topic_a).turns_used == 3
    assert restored_ledger.usage(topic_a)["model_calls"] == first_usage["model_calls"] + 1
    second = await resumed.run(topic_b)
    assert second.status == "submitted", second
    assert restored_ledger.usage(chief)["model_calls"] == resumed.load(chief).turns_used == 0
    final = await resumed.run(chief)
    assert final.status == "submitted", final
    assert resumed.load(chief).turns_used == 2
    assert restored_ledger.usage()["model_calls"] == 8
    assert len(model.requests) == 7 and not model.steps
    rows = restored_ledger.data["requests"]
    assert sum(row["purpose"] == "auxiliary" for row in rows) == 1
    assert rows[-1]["session_id"] == chief and rows[-1]["purpose"] == "completion"
    require_content(model.requests[-1]["messages"], "主审保留解释与提交资源。")
    assert restored_ledger.usage()["estimated_requests"] == 0
    await resumed.close()
    return {
        "actual_requests": 8,
        "scientific_requests": 7,
        "auxiliary_requests": 1,
        "chief_turns": 2,
        "topic_a_continued_turns": 3,
        "chief_completed_from_reserve": True,
    }


async def token_quota(root):
    ledger = Budget(
        root,
        BudgetLimits(
            max_model_calls=10,
            max_seconds=600,
            max_input_tokens=50_000,
            max_output_tokens=5000,
        ),
    )
    draft = note("当前材料支持有限结论，未知仍保留在本说明中。", unknowns=["尚无补充实验数据。"])

    def complete(messages):
        require_content(messages, draft["body"]["paragraphs"][0]["parts"][0]["text"])
        return reply(tool_call("submit_research", {"note": None}))

    model = MeteredModel(
        [reply(tool_call("update_research", draft)), complete], ledger, output_tokens=100
    )
    engine = runtime(root, model, budget=ledger)
    session = engine.create(
        SessionTask(role="researcher", objective="额度内保存实际研究范围", application=APPLICATION),
        allowance=SessionAllowance(
            max_input_tokens=20_000,
            max_output_tokens=350,
            completion_input_tokens=2000,
            completion_output_tokens=128,
        ),
    )
    result = await engine.run(session)
    assert result.status == "submitted", result
    assert engine.load(session).turns_used == 2
    assert len(model.requests) == ledger.usage()["model_calls"] == 2
    assert [row["purpose"] for row in ledger.data["requests"]] == ["research", "completion"]
    assert engine.store.note(result.note_ref)["note"] == draft
    await engine.close()
    return {"actual_requests": 2, "output_tokens": 200, "same_context_completion": True}


class ProbeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


async def time_reserve(root):
    results = {}
    for mode in ("elapsed_research", "external_tool_timeout", "model_time_budget"):
        clock = ProbeClock()
        ledger = Budget(
            root / mode,
            BudgetLimits(
                max_model_calls=5,
                max_seconds=0.06,
                max_input_tokens=50_000,
                max_output_tokens=5000,
            ),
            clock=clock,
        )
        draft = note("时间额度已到收束阶段，已完成的科学说明应继续直接提交。")

        def complete(messages):
            require_content(messages, draft["body"]["paragraphs"][0]["parts"][0]["text"])
            available = {row["function"]["name"] for row in model.requests[-1]["tools"]}
            assert "spend_research_time" not in available, "Completion started more research"
            return reply(tool_call("submit_research", {"note": None}))

        class TimedModel(MeteredModel):
            calls_started = 0

            async def complete(self, messages, tools=None, *, response_schema=None):
                self.calls_started += 1
                if mode == "model_time_budget" and self.calls_started == 2:
                    # The provider attempt started but does not finish before research time ends.
                    self.budget.reserve(self.config, messages, tools, response_schema)
                    clock.now = 0.025
                    await asyncio.sleep(0.2)
                    raise AssertionError("Research request outlived its time allowance")
                return await super().complete(messages, tools, response_schema=response_schema)

        first_calls = [tool_call("update_research", draft)]
        if mode != "model_time_budget":
            first_calls.append(tool_call("spend_research_time", {}))
        model = TimedModel([reply(*first_calls), complete], ledger)
        engine = runtime(root / mode, model, budget=ledger)
        session = engine.create(
            SessionTask(role="researcher", objective="时间预留直接提交", application=APPLICATION),
            allowance=SessionAllowance(
                max_input_tokens=20_000,
                max_output_tokens=1000,
                completion_input_tokens=2000,
                completion_output_tokens=128,
                completion_seconds=0.04,
            ),
        )

        async def spend_research_time(args):
            clock.now = 0.025
            if mode == "external_tool_timeout":
                await asyncio.sleep(0.2)
                raise AssertionError("External research tool outlived its time allowance")
            return ToolResult(ok=True, content={"research_time_consumed": True})

        tools = ToolRegistry(
            [Tool("spend_research_time", "固定本地时间探针。", SortFixture, spend_research_time)]
        )
        outcome = await engine.run(session, tools=tools)
        assert outcome.status == "submitted", (mode, outcome)
        state = engine.load(session)
        assert engine.store.note(state.submitted_ref)["note"] == draft
        assert len(model.requests) == 2 and not model.steps
        expected_calls = 3 if mode == "model_time_budget" else 2
        assert ledger.usage()["model_calls"] == expected_calls
        assert ledger.data["requests"][-1]["purpose"] == "completion"
        assert_private_not_saved(root / mode)
        await engine.close()
        results[mode] = {
            "actual_requests": expected_calls,
            "submitted_existing_note": True,
            "same_context_completion": True,
        }
    return results


async def route_compatibility(root):
    rejected = []
    for name, changes in (
        ("tools_disabled", {"tools": False}),
        ("messages_schema_only", {"request_protocol": "messages"}),
    ):
        model = ScriptModel([])
        model.config = model.config.model_copy(update=changes)
        engine = runtime(root / name, model)
        try:
            engine.create(
                SessionTask(role="chief", objective="路由协议验证", application=APPLICATION)
            )
        except ValueError as error:
            assert str(error) == "v5_session_requires_tool_capable_chat_route"
            rejected.append(name)
        else:
            raise AssertionError(f"Unsupported research route was accepted: {name}")
        assert not model.requests
        await engine.close()
    return {"unsupported_routes_rejected_before_request": rejected}


CASES = (
    continuity,
    compression,
    failure_recovery,
    concurrency,
    budget_ownership,
    token_quota,
    time_reserve,
    route_compatibility,
)


async def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = {}
    for case in CASES:
        if args.case and case.__name__ not in args.case:
            continue
        results[case.__name__] = await case(output / case.__name__)
    summary = {
        "engine_version": "v5",
        "mode": "offline",
        "network_requests": 0,
        "full_review_runs": 0,
        "cases": results,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "runs/v5-local/change09/offline-session"
    )
    parser.add_argument("--case", action="append", choices=[case.__name__ for case in CASES])
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
