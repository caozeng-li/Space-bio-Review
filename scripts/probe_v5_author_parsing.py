"""Local delivery protocol probes; no full review, retrieval, embedding, or real model calls."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_session import (  # noqa: E402
    APPLICATION,
    DELIVERY_FIXTURES,
    FixtureReader,
    MeteredModel,
    ParserModel,
    ReadFixture,
    ScriptModel,
    assert_private_not_saved,
    delivery,
    note,
    reply,
    require_content,
    runtime,
    tool_call,
)

from spacebio_review.models.provider import ModelError  # noqa: E402
from spacebio_review.v5 import SessionSettings, SessionTask  # noqa: E402
from spacebio_review.v5.authoring import ResearchMapping, research_note  # noqa: E402
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance  # noqa: E402
from spacebio_review.v5.records import ResearchNote  # noqa: E402


def create(engine, objective, **kwargs):
    return engine.create(
        SessionTask(role="researcher", objective=objective, application=APPLICATION), **kwargs
    )


def seed_note(engine, session):
    state = engine.load(session)
    return engine.store.save_note(
        state, ResearchNote.model_validate(note("开发者预置的既有说明。")), absorbed_through=0
    )


async def explicit_operation(root):
    """Discussion and a mixed response cannot submit an old note or execute its tools."""
    parser = ParserModel()
    reader = FixtureReader()
    final = note('结果中的"差异"需要结合实际比较对象解释。', ["E1"])
    mixed = delivery("submit_research", final)
    mixed.message["tool_calls"] = reply(
        tool_call("read_fixture", {"passage": "b"}), tool_call("submit_research", {})
    ).message["tool_calls"]
    mixed.finish_reason = "tool_calls"

    def after_discussion(messages):
        assert not parser.requests
        assert engine.load(session).note_ref == old_ref
        assert not engine.load(session).submitted_ref
        return reply(tool_call("read_fixture", {"passage": "a"}))

    def separate_delivery(messages):
        require_content(messages, "deliver_text_without_tool_calls")
        assert reader.visits == ["a"], "Mixed delivery must not execute the unrelated read"
        assert not parser.requests, "Mixed delivery must not be parsed"
        assert engine.load(session).note_ref == old_ref
        assert not engine.load(session).submitted_ref, "Mixed empty submission used an old note"
        return delivery("submit_research", final)

    author = ScriptModel(
        [
            reply(content="目前仍在讨论研究范围，尚未交付。"),
            after_discussion,
            mixed,
            separate_delivery,
        ]
    )
    engine = runtime(root, author, delivery_parser=parser)
    session = create(engine, "只把显式独立交付正文视为研究产物")
    old_ref = seed_note(engine, session)
    try:
        outcome = await engine.run(session, tools=reader.registry)
        assert outcome.status == "submitted", outcome
        assert len(parser.requests) == 1
        state = engine.load(session)
        assert state.revision == 2 and state.submitted_ref != old_ref
        assert len(list((root / "v5/author-texts").glob("*.json"))) == 2
        assert_private_not_saved(root)
        return {"ordinary_discussion_ignored": True, "mixed_tools_executed": 0, "parser_calls": 1}
    finally:
        await engine.close()


async def missing_fields(root):
    missing = "请作者明确尚未确定的事项，或明确说明无未知事项。"
    incomplete = "交付操作：update_research\n\n# 当前认识\n\n## 研究说明\n\n仅有现有材料。"
    parser = ParserModel(
        [
            {
                "title_paragraph_id": "p1",
                "body_paragraph_ids": ["p3"],
                "unknown_paragraph_ids": [],
                "missing_fields": [missing],
            }
        ]
    )
    complete_note = note("仅有现有材料，结论范围仍有限。", unknowns=["缺少比较实验数据。"])

    def supply_missing(messages):
        require_content(messages, missing)
        state = engine.load(session)
        assert state.note_ref == old_ref and not state.submitted_ref
        assert state.pending_delivery is None, "A completed missing-field result is not retried"
        assert len(parser.requests) == 1
        return delivery("update_research", complete_note)

    author = ScriptModel(
        [reply(content=incomplete), supply_missing, reply(tool_call("submit_research", {}))]
    )
    engine = runtime(root, author, delivery_parser=parser)
    session = create(engine, "缺项交回作者后明确补写")
    old_ref = seed_note(engine, session)
    try:
        outcome = await engine.run(session)
        assert outcome.status == "submitted", outcome
        state = engine.load(session)
        assert state.revision == 2 and not state.pending_delivery
        assert engine.store.note(state.note_ref)["note"] == complete_note
        assert len(parser.requests) == 2, "The final empty submission must not parse again"
        return {"missing_fields_returned_to_author": True, "parser_calls": 2, "new_revisions": 1}
    finally:
        await engine.close()


async def interrupted_parser(root):
    reader = FixtureReader()
    await reader.read(ReadFixture(passage="a"))
    await reader.read(ReadFixture(passage="b"))
    drafted = note('DCF记录"氧化相关变化"；尚不足以定位线粒体来源。', ["E1"])
    author_reply = delivery("submit_research", drafted)
    author = ScriptModel([author_reply])
    failed_parser = ParserModel(
        [ModelError("local_parser_timeout", category="timeout", request_sent=True)]
    )
    engine = runtime(root, author, delivery_parser=failed_parser)
    session = create(engine, "解析中断从原稿恢复", readings=reader.readings)
    old_ref = seed_note(engine, session)
    result = await engine.run(session)
    state = engine.load(session)
    assert result.status == "failed" and result.reason == "local_parser_timeout"
    assert state.note_ref == old_ref and not state.submitted_ref
    pending = dict(state.pending_delivery)
    assert pending["parse_ref"] is None
    raw = engine.store.author_text(pending["raw_ref"])
    assert raw["content"] == author_reply.message["content"]
    # The draft's citation catalogue remains authoritative even if the live catalogue changes.
    state.aliases.update(E1="evidence-b", E2="evidence-a")
    engine.store.save(state)
    await engine.close()

    resumed_author, resumed_parser = ScriptModel([]), ParserModel()
    resumed = runtime(root, resumed_author, delivery_parser=resumed_parser)
    try:
        outcome = await resumed.run(session)
        assert outcome.status == "submitted", outcome
        final = resumed.load(session)
        artifact = resumed.store.note(final.note_ref)
        assert final.turns_used == 1 and not resumed_author.requests
        assert len(resumed_parser.requests) == 1 and not final.pending_delivery
        assert artifact["author_text_ref"] == pending["raw_ref"]
        assert artifact["note"]["body"]["paragraphs"][0]["parts"][0]["cites"] == ["evidence-a"]
        assert raw["aliases"]["E1"] == "evidence-a"
        assert_private_not_saved(root)
        return {
            "resumed_from_same_manuscript": True,
            "new_author_calls": 0,
            "alias_snapshot_used": True,
        }
    finally:
        await resumed.close()


async def completed_mapping_resume(root):
    """A persisted mapping and an already saved artifact need neither model again."""
    author, parser = ScriptModel([]), ParserModel()
    engine = runtime(root, author, delivery_parser=parser)
    session = create(engine, "结构映射完成后的恢复不重复写作和解析")
    state = engine.load(session)
    content = delivery("submit_research", note("已完成解析的研究说明。")).message["content"]
    raw_ref = engine.store.save_author_text(
        state, content, "submit_research", absorbed_through=state.observation_index
    )
    raw = engine.store.author_text(raw_ref)
    mapping = ResearchMapping.model_validate(
        DELIVERY_FIXTURES[tuple(row["text"] for row in raw["paragraphs"])]
    )
    parse_ref = engine.store.save_delivery_parse(raw_ref, parser.config.model_dump(), mapping)
    # Simulate interruption after the note write and before clearing the pending delivery.
    saved_ref = engine.store.save_note(
        state, research_note(raw, mapping), absorbed_through=0, author_text_ref=raw_ref
    )
    state.turns_used = 1
    state.pending_delivery = {
        "raw_ref": raw_ref,
        "operation": "submit_research",
        "purpose": "auxiliary",
        "parse_ref": parse_ref,
    }
    state.status = "submitted"
    engine.store.save(state)
    try:
        result = await engine.run(session)
        assert result.status == "submitted", result
        after = engine.load(session)
        assert after.note_ref == saved_ref and after.revision == 1
        assert len(list((root / "v5/notes").glob("*.json"))) == 1
        assert after.submissions == [saved_ref] and not after.pending_delivery
        assert not author.requests and not parser.requests
        # The same interrupted commit must not consume a newly requested followup.
        after.pending_delivery = dict(state.pending_delivery)
        engine.store.save(after)
        author.steps.append(delivery("submit_research", note("续问后的新研究认识。")))
        continued = await engine.run(session, followup="继续解释新的测量条件。")
        latest = engine.load(session)
        assert continued.status == "submitted" and latest.revision == 2
        assert latest.note_ref != saved_ref and len(latest.submissions) == 2
        assert len(author.requests) == len(parser.requests) == 1
        assert not latest.pending_delivery
        return {
            "persisted_mapping_reused": True,
            "resume_requests": 0,
            "duplicate_versions": 0,
            "followup_after_pending_submission": True,
        }
    finally:
        await engine.close()


async def completion_parser_budget(root):
    ledger = Budget(root, BudgetLimits(max_model_calls=3, max_seconds=60, max_input_tokens=40_000))
    body = {
        "title": "原文与引用原位交付",
        "body": {
            "paragraphs": [
                {
                    "parts": [
                        {
                            "text": 'α信号"升高"约2×，阈值为10^-3，路径C:\\data。',
                            "cites": ["E1", "E2"],
                        },
                        {"text": "局部读出≠因果机制。", "cites": ["E2"]},
                    ]
                }
            ]
        },
        "unknowns": [],
    }
    parser = ParserModel(budget=ledger)
    author = MeteredModel(
        [reply(content="先整理公开研究范围。"), delivery("submit_research", body)], ledger
    )
    reader = FixtureReader()
    await reader.read(ReadFixture(passage="a"))
    await reader.read(ReadFixture(passage="b"))
    engine = runtime(
        root,
        author,
        delivery_parser=parser,
        budget=ledger,
        settings=SessionSettings(max_turns=2, completion_turns=1),
    )
    session = create(
        engine,
        "收束预留包含作者正文和专用解析",
        readings=reader.readings,
        allowance=SessionAllowance(
            completion_calls=2, completion_input_tokens=6000, completion_output_tokens=256
        ),
    )
    try:
        result = await engine.run(session)
        assert result.status == "submitted", result
        state = engine.load(session)
        assert state.turns_used == 2 and len(parser.requests) == 1
        assert ledger.usage(session)["model_calls"] == 3
        assert [row["purpose"] for row in ledger.data["requests"]] == [
            "research",
            "completion",
            "completion",
        ]
        artifact = engine.store.note(state.note_ref)
        parts = artifact["note"]["body"]["paragraphs"][0]["parts"]
        assert [part["text"] for part in parts] == [
            part["text"] for part in body["body"]["paragraphs"][0]["parts"]
        ]
        assert [part["cites"] for part in parts] == [["evidence-a", "evidence-b"], ["evidence-b"]]
        raw = engine.store.author_text(artifact["author_text_ref"])
        assert raw["model"]["model"] == author.config.model
        assert_private_not_saved(root)
        return {
            "actual_requests": 3,
            "author_turns": 2,
            "parser_uses_completion_reserve": True,
            "literal_text_and_local_citations_preserved": True,
        }
    finally:
        await engine.close()


CASES = (
    explicit_operation,
    missing_fields,
    interrupted_parser,
    completed_mapping_resume,
    completion_parser_budget,
)


async def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    cases = {}
    for case in CASES:
        if not args.case or case.__name__ in args.case:
            cases[case.__name__] = await case(output / case.__name__)
    summary = {
        "engine_version": "v5",
        "mode": "offline",
        "network_requests": 0,
        "full_review_runs": 0,
        "cases": cases,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "runs/v5-local/author-parsing-offline"
    )
    parser.add_argument("--case", action="append", choices=[case.__name__ for case in CASES])
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
