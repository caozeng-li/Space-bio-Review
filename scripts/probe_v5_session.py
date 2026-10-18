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
from spacebio_review.models.provider import ChatReply  # noqa: E402
from spacebio_review.prose import prose_inputs  # noqa: E402
from spacebio_review.tools import Tool, ToolRegistry, ToolResult  # noqa: E402
from spacebio_review.v5 import ResearchRuntime, SessionSettings, SessionTask  # noqa: E402
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance  # noqa: E402

PRIVATE_SENTINEL = "PRIVATE_REASONING_MUST_NOT_BE_PERSISTED"
APPLICATION = "项目包含地面对照；在轨记录荧光，返回后独立实施分子检测。"
SOURCE_A = "原文A：实验设有地面对照，检测对象为两种肿瘤细胞系。"
SOURCE_B = "原文B：培养条件会改变读数；本文没有直接比较正常原代肝细胞。"
DELIVERY_FIXTURES = {}


def note(text, cites=(), *, unknowns=()):
    paragraphs = [{"parts": [{"text": text, "cites": list(cites)}]}]
    paragraphs.extend({"parts": [{"text": text, "cites": []}]} for text in unknowns)
    return {"title": "细胞模型研究说明", "body": {"paragraphs": paragraphs}}


def delivery(operation, value):
    """Prepare ordinary public research prose without a second parser contract."""
    blocks = ["# " + value["title"], "## 研究说明"]
    body = value["body"]
    paragraphs = body["paragraphs"] if isinstance(body, dict) else body
    for paragraph in paragraphs:
        parts = paragraph["parts"] if isinstance(paragraph, dict) else paragraph
        blocks.append(
            "".join(
                part["text"] + "".join(f"[@{cite}]" for cite in part["cites"]) for part in parts
            )
        )
    return reply(content="\n\n".join(blocks))


def tool_call(name, arguments):
    # Fixtures retain canonical artifacts; simulated authors use the public tool shape.
    if name in {
        "update_research",
        "submit_research",
        "submit_assessment",
    }:
        arguments = prose_inputs(arguments)
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
        assert response_schema is None, "Scientific authors do not encode delivery mappings"
        assert tools is not None, "The caller supplies the current tool list, including empty"
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


class ParserModel:
    """A fixed mapping oracle, never a second scientific author or a semantic validator."""

    def __init__(self, steps=(), *, budget=None):
        self.config = ModelConfig(
            model="local-delivery-parser",
            base_url="https://local.invalid/v1",
            tools=False,
            structured_output="json_object",
            max_tokens=128,
            retries=0,
        )
        self.steps, self.requests, self.budget = list(steps), [], budget

    async def complete(self, messages, tools=None, *, response_schema=None):
        assert not tools and response_schema, "The parser only returns a short structure"
        self.requests.append({"messages": deepcopy(messages), "schema": response_schema})
        ticket = (
            self.budget.reserve(self.config, messages, tools, response_schema)
            if self.budget
            else None
        )
        payload = json.loads(messages[-1]["content"])
        if self.steps:
            mapping = self.steps.pop(0)
            if isinstance(mapping, Exception):
                raise mapping
            if callable(mapping):
                mapping = mapping(payload)
        else:
            key = tuple(row["text"] for row in payload["paragraphs"])
            assert key in DELIVERY_FIXTURES, "No predetermined parser answer for this draft"
            mapping = DELIVERY_FIXTURES[key]
        if ticket is not None:
            self.budget.settle(ticket, SimpleNamespace(prompt_tokens=80, completion_tokens=30))
        return ChatReply(
            {"role": "assistant", "content": json.dumps(mapping, ensure_ascii=False)},
            prompt_tokens=80,
            completion_tokens=30,
        )

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
    parser = kwargs.pop("delivery_parser", None) or ParserModel(budget=kwargs.get("budget"))
    return ResearchRuntime(
        root,
        {"local": model},
        {"chief": "local", "researcher": "local"},
        delivery_parser=parser,
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
        return delivery("update_research", note(first_text, ["E1"]))

    def revised_update(messages):
        require_content(messages, first_text, SOURCE_A, SOURCE_B)
        return delivery("update_research", note(revised_text, ["E1", "E2"]))

    model = ScriptModel(
        [
            reply(tool_call("read_fixture", {"passage": "a"})),
            first_update,
            reply(tool_call("read_fixture", {"passage": "b"})),
            revised_update,
            reply(tool_call("submit_research", {})),
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
    def followup_read(messages):
        require_content(messages, APPLICATION, revised_text, "原代细胞")
        return reply(tool_call("read_references", {"references": ["E1", "E2"]}))

    def followup_submit(messages):
        require_content(messages, SOURCE_A, SOURCE_B, revised_text)
        return delivery("submit_research", note(final_text, ["E1", "E2"]))

    model.steps.extend([followup_read, followup_submit])
    followup = await engine.run(session, followup="请结合已有地面对照解释外推范围。")
    after = engine.load(session)
    assert followup.status == "submitted", followup
    assert after.turns_used == 7
    assert after.note_ref != previous_ref
    assert engine.store.note(previous_ref) == artifact
    final_artifact = engine.store.note(after.note_ref)
    assert final_artifact["note"]["body"]["paragraphs"][0]["parts"][0]["text"] == final_text
    assert final_artifact["paragraph_ids"] != artifact["paragraph_ids"]
    assert set(after.evidence) == {"evidence-a", "evidence-b"}
    assert_private_not_saved(root)
    await engine.close()
    return {"session": session, "requests": 7, "saved_revisions": after.revision}


async def compression(root):
    current_text = "当前认识只吸收了细胞系的比较对象。"

    def update(messages):
        require_content(messages, SOURCE_A)
        return delivery("update_research", note(current_text, ["E1"]))

    def complete(messages):
        require_content(messages, current_text, SOURCE_B)
        # A reading after the saved draft has not yet been absorbed by that draft.
        assert any(SOURCE_B in str(row.get("content", "")) for row in messages), (
            "Compaction dropped the unabsorbed reading"
        )
        return delivery("submit_research", note(current_text + SOURCE_B, ["E1", "E2"]))

    model = ScriptModel(
        [
            reply(tool_call("read_fixture", {"passage": "a"})),
            update,
            reply(tool_call("read_fixture", {"passage": "b"})),
            complete,
        ]
    )
    engine = runtime(root, model, settings=SessionSettings(context_tokens=1400))
    reader = FixtureReader(padding="定位上下文。" * 2000)
    session = engine.create(
        SessionTask(role="researcher", objective="压缩后继续阅读", application=APPLICATION)
    )
    result = await engine.run(session, tools=reader.registry)
    assert result.status == "submitted", result
    assert len(model.requests) == 4
    assert engine.load(session).context_compactions > 0
    assert_private_not_saved(root)
    await engine.close()
    return {"session": session, "requests": 4}




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
            return delivery("submit_research", note("独立会话已直接提交。"))
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
        max_model_calls=12,
        max_seconds=600,
        max_input_tokens=100_000,
        max_output_tokens=20_000,
    )
    ledger = Budget(root, limits)
    model = MeteredModel(
        [
            reply(tool_call("sort_fixture", {})),
            delivery("submit_research", note("专题一已完成局部研究。")),
            reply(tool_call("submit_research", {})),
            delivery("update_research", note("专题二当前说明。")),
            reply(tool_call("submit_research", {})),
            delivery("update_research", note("主审保留解释与提交资源。")),
            reply(tool_call("submit_research", {})),
        ],
        ledger,
    )
    engine = runtime(root, model, budget=ledger)
    allowance = SessionAllowance(
        max_input_tokens=20_000,
        max_output_tokens=4000,
        completion_input_tokens=5000,
        completion_output_tokens=256,
        completion_calls=2,
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
    assert ledger.usage(topic_a)["model_calls"] == 4, "Both auxiliary requests count once"
    assert ledger.usage(chief)["model_calls"] == engine.load(chief).turns_used == 0
    assert ledger.usage(topic_b)["model_calls"] == 0
    first_usage = ledger.usage(topic_a)
    continued = await engine.run(topic_a, followup="确认现有范围并提交。")
    assert continued.status == "submitted", continued
    assert engine.load(topic_a).turns_used == 3
    assert ledger.usage(topic_a)["model_calls"] == first_usage["model_calls"] + 1
    second = await engine.run(topic_b)
    assert second.status == "submitted", second
    assert ledger.usage(chief)["model_calls"] == engine.load(chief).turns_used == 0
    final = await engine.run(chief)
    assert final.status == "submitted", final
    assert engine.load(chief).turns_used == 2
    assert ledger.usage()["model_calls"] == 11
    assert len(model.requests) == 7 and not model.steps
    rows = ledger.data["requests"]
    assert sum(row["purpose"] == "auxiliary" for row in rows) == 3
    assert sum(row["model"] == "local-delivery-parser" for row in rows) == 3
    assert rows[-1]["session_id"] == chief and rows[-1]["purpose"] == "completion"
    require_content(model.requests[-1]["messages"], "主审保留解释与提交资源。")
    assert ledger.usage()["estimated_requests"] == 0
    await engine.close()
    return {
        "actual_requests": 11,
        "scientific_requests": 7,
        "parser_requests": 3,
        "other_auxiliary_requests": 1,
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
        return reply(tool_call("submit_research", {}))

    model = MeteredModel([delivery("update_research", draft), complete], ledger, output_tokens=100)
    engine = runtime(root, model, budget=ledger)
    session = engine.create(
        SessionTask(role="researcher", objective="额度内保存实际研究范围", application=APPLICATION),
        allowance=SessionAllowance(
            max_input_tokens=20_000,
            max_output_tokens=380,
            completion_input_tokens=2000,
            completion_output_tokens=128,
        ),
    )
    result = await engine.run(session)
    assert result.status == "submitted", result
    assert engine.load(session).turns_used == 2
    assert len(model.requests) == 2 and ledger.usage()["model_calls"] == 3
    assert [row["purpose"] for row in ledger.data["requests"]] == [
        "research",
        "auxiliary",
        "completion",
    ]
    assert engine.store.note(result.note_ref)["note"] == draft
    await engine.close()
    return {"actual_requests": 3, "output_tokens": 230, "same_context_completion": True}


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
            return reply(tool_call("submit_research", {}))

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

        steps = [delivery("update_research", draft)]
        if mode != "model_time_budget":
            steps.append(reply(tool_call("spend_research_time", {})))
        steps.append(complete)
        model = TimedModel(steps, ledger)
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
        assert len(model.requests) == (2 if mode == "model_time_budget" else 3)
        assert not model.steps
        expected_calls = 4
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


async def prose_authoring(root):
    """Citation spans and paragraph boundaries survive both authoring operations."""
    wire_body = [
        [
            {"text": 'α信号中的"差异"属于观测。', "cites": ["E1"]},
            {"text": "机制解释还取决于比较条件。", "cites": ["E2"]},
        ],
        [{"text": "第二段保留未知；不改变前段的引用位置。", "cites": []}],
    ]
    wire_body.append([{"text": "机制尚未区分。", "cites": []}])
    draft = {"title": "多段引用", "body": wire_body}
    model = ScriptModel(
        [
            delivery("update_research", draft),
            delivery("submit_research", draft),
        ]
    )
    reader = FixtureReader()
    await reader.read(ReadFixture(passage="a"))
    await reader.read(ReadFixture(passage="b"))
    engine = runtime(root, model)
    session = engine.create(
        SessionTask(role="researcher", objective="局部正文装配验证", application=APPLICATION),
        readings=reader.readings,
    )
    try:
        outcome = await engine.run(session)
        assert outcome.status == "submitted", outcome
        state = engine.load(session)
        saved = engine.store.note(state.submitted_ref)["note"]
        paragraphs = saved["body"]["paragraphs"]
        assert len(paragraphs) == 2 and [len(p["parts"]) for p in paragraphs] == [2, 1]
        assert [[part["text"] for part in p["parts"]] for p in paragraphs] == [
            [part["text"] for part in p] for p in wire_body
        ]
        assert paragraphs[0]["parts"][0]["cites"] == ["evidence-a"]
        assert paragraphs[0]["parts"][1]["cites"] == ["evidence-b"]
        assert engine._context(state)["current_research"]["body"] == wire_body
        for request in model.requests:
            tools = {tool["function"]["name"]: tool["function"] for tool in request["tools"]}
            assert "update_research" not in tools
            assert not tools["submit_research"]["parameters"].get("properties")
        return {"paragraphs": 2, "citation_spans": 3, "text_and_citation_positions_preserved": True}
    finally:
        await engine.close()


CASES = (
    continuity,
    compression,
    concurrency,
    budget_ownership,
    token_quota,
    time_reserve,
    route_compatibility,
    prose_authoring,
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
        "--output", type=Path, default=ROOT / "runs/v5-local/author-parsing-session"
    )
    parser.add_argument("--case", action="append", choices=[case.__name__ for case in CASES])
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
