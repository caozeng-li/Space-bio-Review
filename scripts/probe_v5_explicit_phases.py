"""Local phase, persistence and shared reading budget checks; no live requests."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from probe_v5_session import (
    APPLICATION,
    MeteredModel,
    ParserModel,
    ScriptModel,
    delivery,
    note,
    reply,
    runtime,
    tool_call,
)
from pydantic import BaseModel

from spacebio_review.models.provider import ModelError
from spacebio_review.persistence import write_json
from spacebio_review.tools import Tool, ToolRegistry, ToolResult
from spacebio_review.v5 import SessionTask
from spacebio_review.v5.authoring import citation_parts
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance

ROOT = Path(__file__).resolve().parents[1]


def plain(text):
    return delivery("submit_research", note(text))


async def original33(root):
    historical = ROOT / "runs/mytest33_sample2/v5"
    raw = json.loads(
        (
            historical
            / "author-texts"
            / "author-f31e5c948b47ed2e54c1a587e946a90267f07e27934e22d5de0363686453eefa.json"
        ).read_text("utf-8")
    )
    saved = json.loads((historical / "sessions" / (raw["session_id"] + ".json")).read_text("utf-8"))
    author = ScriptModel(
        [
            reply(content=raw["content"]),
            ModelError("local_stop_after_understanding", category="exploration"),
        ]
    )
    parser = ParserModel()
    engine = runtime(root, author, delivery_parser=parser)
    sid = engine.create(
        SessionTask(role="chief", objective="保存实际项目理解原稿", application=APPLICATION)
    )
    state = engine.load(sid)
    state.aliases, state.evidence = raw["aliases"], saved["evidence"]
    engine.store.save(state)
    await engine.run(sid)
    state = engine.load(sid)
    assert state.initial_understanding_ref and state.revision == 1
    artifact = engine.store.note(state.initial_understanding_ref)
    accepted = engine.store.author_text(artifact["author_text_ref"])
    assert accepted["operation"] == "update_research"
    assert accepted["content"] == raw["content"]
    assert not author.requests[0]["tools"] and not parser.requests
    assert len(artifact["note"]["body"]["paragraphs"]) == len(raw["paragraphs"])
    return {"historical_operation_ignored": True, "original_preserved": True}


async def batches(root):
    class Empty(BaseModel):
        pass

    visits = []

    async def batch(_):
        visits.append(len(visits) + 1)
        return ToolResult(
            ok=True, content={"batch": visits[-1], "findings": "已完成的固定研究成果"}
        )

    author = ScriptModel(
        [
            plain("初始理解保持申请的实际条件。"),
            reply(tool_call("research_topics", {})),
            reply(tool_call("research_topics", {})),
            reply(tool_call("finish_research", {"reason": "当前研究足以成稿"})),
            plain("最终说明保留两批认识。"),
        ]
    )
    engine = runtime(root, author)
    sid = engine.create(SessionTask(role="chief", objective="批次吸收", application=APPLICATION))
    outcome = await engine.run(
        sid, tools=ToolRegistry([Tool("research_topics", "固定研究批次", Empty, batch)])
    )
    assert outcome.status == "submitted", outcome
    state = engine.load(sid)
    assert visits == [1, 2] and state.initial_understanding_ref
    assert state.revision == 2  # Initial understanding and final manuscript, no forced batch draft.
    current = json.dumps(engine._context(state), ensure_ascii=False)
    assert "最终说明保留两批认识" in current and "初始理解保持" not in current
    assert not author.requests[0]["tools"] and not author.requests[4]["tools"]
    assert all(author.requests[index]["tools"] for index in (1, 2, 3))
    assert engine.store.note(state.initial_understanding_ref)
    return {
        "batches": 2,
        "forced_absorptions": 0,
        "initial_history_retained": True,
        "tools_available_immediately_after_batch": True,
    }


async def long_application(root):
    class Empty(BaseModel):
        pass

    async def browse(_):
        return ToolResult(ok=True, content={"text": APPLICATION})

    author = ScriptModel(
        [
            reply(tool_call("browse_materials", {})),
            reply(tool_call("finish_research", {"reason": "申请正文已读"})),
            plain("基于已读申请形成理解。"),
            reply(tool_call("finish_research", {"reason": "当前研究结束"})),
            plain("依据已有材料形成研究说明。"),
        ]
    )
    engine = runtime(root, author)
    sid = engine.create(
        SessionTask(
            role="chief",
            objective="先阅读长申请",
            application={"full_text_in_context": False, "sources": []},
        )
    )
    result = await engine.run(
        sid,
        tools=ToolRegistry(
            [
                Tool("browse_materials", "申请浏览", Empty, browse),
                Tool("research_topics", "本阶段不可委派", Empty, browse),
            ]
        ),
    )
    assert result.status == "submitted", result
    assert "research_topics" not in {row["function"]["name"] for row in author.requests[0]["tools"]}
    assert not author.requests[2]["tools"]
    assert "research_topics" in {row["function"]["name"] for row in author.requests[3]["tools"]}
    return {"application_read_before_understanding": True}


async def bounded_closing(root):
    budget = Budget(root, BudgetLimits(max_model_calls=2, max_seconds=600))
    author = MeteredModel([plain("初始理解。"), plain("现有材料范围内的最终说明。")], budget)
    engine = runtime(root, author, budget=budget)
    sid = engine.create(SessionTask(role="chief", objective="资源收束", application=APPLICATION))
    result = await engine.run(sid)
    assert result.status == "submitted", result
    assert budget.usage()["model_calls"] == 2
    assert all(not request["tools"] for request in author.requests)
    return {"scripted_requests": 2, "research_skipped_at_delivery_reserve": True}


async def delegated_visual_budget(root):
    class Empty(BaseModel):
        pass

    budget = Budget(root, BudgetLimits(max_model_calls=12, max_seconds=600))
    author = MeteredModel(
        [
            reply(tool_call("read_literature_visual", {})),
            reply(tool_call("finish_research", {"reason": "图像已读"})),
            plain("图像解释已获得。"),
        ],
        budget,
    )
    visual = MeteredModel([reply(content="固定视觉说明")], budget)

    async def inspect(_):
        response = await visual.complete([{"role": "user", "content": "固定图像材料"}], [])
        return ToolResult(ok=True, content=response.message["content"])

    engine = runtime(root, author, budget=budget)
    parent = engine.create(
        SessionTask(
            role="researcher",
            objective="视觉父专题",
            application=APPLICATION,
        ),
        allowance=SessionAllowance(completion_calls=2),
    )
    child = engine.create(
        SessionTask(
            role="source_reader",
            objective="阅读单篇图像",
            application=APPLICATION,
        ),
        budget_owner=parent,
    )
    result = await engine.run(
        child,
        tools=ToolRegistry(
            [
                Tool("read_literature_visual", "委派视觉解读", Empty, inspect, delegates=True),
            ]
        ),
    )
    assert result.status == "submitted", result
    rows = budget.data["requests"]
    assert len(rows) == 4 and all(row["session_id"] == parent for row in rows)
    assert rows[1]["purpose"] == "auxiliary"
    return {"visual_charged_to_parent": True, "requests": 4}


async def reading_budget(root):
    budget = Budget(root, BudgetLimits(max_model_calls=12, max_seconds=600))
    author = MeteredModel(
        [
            reply(tool_call("finish_research", {"reason": "同篇原文已足够回答"})),
            plain("同篇阅读已经形成有依据的说明。"),
        ],
        budget,
    )
    engine = runtime(root, author, budget=budget)
    parent = engine.create(
        SessionTask(role="researcher", objective="父专题", application=APPLICATION),
        allowance=SessionAllowance(completion_calls=2),
    )
    child = engine.create(
        SessionTask(role="source_reader", objective="阅读单篇", application=APPLICATION),
        budget_owner=parent,
    )
    outcome = await engine.run(child)
    assert outcome.status == "submitted", outcome
    assert set(budget.data["sessions"]) == {parent}
    assert budget.data["sessions"][parent]["active"]
    assert len(budget.data["requests"]) == 2
    assert all(
        row["session_id"] == parent and row["purpose"] != "completion"
        for row in budget.data["requests"]
    )
    return {
        "registered_allowances": 1,
        "reading_requests_charged_to_parent": 2,
        "parent_reserve_retained": True,
    }


def reading_context(root):
    engine = runtime(root, ScriptModel([]))
    snapshot = {
        "reference": {"source_id": "fixture-paper", "passage_id": "p1"},
        "quote": "可回读的具体原文。",
        "title": "固定原文",
    }
    sid = engine.create(
        SessionTask(role="researcher", objective="读取已保存科学说明", application=APPLICATION),
        readings={"evidence-persistent": snapshot},
    )
    context = engine._context(
        engine.load(sid),
        lambda: {
            "completed_reading": note("有原文支持的解释。", ["evidence-persistent"]),
        },
    )
    parts = context["working_context"]["completed_reading"]["body"][0]
    assert parts[0]["cites"] == ["E1"], parts
    assert context["originals"]["E1"]["quote"] == snapshot["quote"]
    return {"saved_reading_citations_rebound_to_local_alias": True}


def citations():
    parsed = citation_parts(
        "第一句[@E1, @E2]第二句[@E3][@E2]。\\[@E1]", {"E1": "a", "E2": "b", "E3": "c"}
    )
    assert parsed == [
        {"text": "第一句", "cites": ["E1", "E2"]},
        {"text": "第二句", "cites": ["E3", "E2"]},
        {"text": "。\\[@E1]", "cites": []},
    ]
    assert citation_parts("正文［@E1， @E2］后文", {"E1": "a", "E2": "b"}) == [
        {"text": "正文", "cites": ["E1", "E2"]},
        {"text": "后文", "cites": []},
    ]
    return {
        "group_and_adjacent_citations": True,
        "full_width_citations": True,
        "escaped_text_preserved": True,
    }


async def main(root):
    root.mkdir(parents=True, exist_ok=False)
    results = {
        "historical33": await original33(root / "historical33"),
        "batches": await batches(root / "batches"),
        "long_application": await long_application(root / "long-application"),
        "bounded_closing": await bounded_closing(root / "bounded-closing"),
        "delegated_visual_budget": await delegated_visual_budget(root / "delegated-visual"),
        "reading_budget": await reading_budget(root / "reading-budget"),
        "reading_context": reading_context(root / "reading-context"),
        "citations": citations(),
    }
    write_json(root / "results.json", results)
    print(json.dumps(results, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--output", type=Path, required=True)
    asyncio.run(main(cli.parse_args().output))
