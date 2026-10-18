"""Run chief/expert coordination offline through the production runtime and tools."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_session import (  # noqa: E402
    ScriptModel,
    assert_private_not_saved,
    reply,
    require_content,
    tool_call,
)
from probe_v5_topic import (  # noqa: E402
    ABSTRACT,
    METHOD,
    RESULT,
    Fixture,
    aliases,
    objects,
    payloads,
    search,
)

from spacebio_review.agents.skills import SkillLibrary  # noqa: E402
from spacebio_review.models.config import ConcurrencyConfig  # noqa: E402
from spacebio_review.models.scheduling import RequestScheduler  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.review.expertise import ExpertiseCatalogue  # noqa: E402
from spacebio_review.v5 import ResearchRuntime, SessionSettings  # noqa: E402
from spacebio_review.v5.budget import Budget, BudgetLimits  # noqa: E402
from spacebio_review.v5.chief import ChiefReview  # noqa: E402
from spacebio_review.v5.topic import TopicResearch  # noqa: E402

A = "固定专家甲：培养条件如何影响细胞内 TAG 解释"
B = "固定专家乙：在轨与地面对照可产生什么认识"
INITIAL = "申请使用 Huh7，比较在轨与地面对照；培养条件与 TAG 读出需要专业研究。"
A_NOTE = "甲完整专业意见：原文比较 Huh7 与 HepG2，血清条件影响细胞内 TAG 的解释。"
B_NOTE = "乙完整专业意见：申请已有地面对照，可获得空间环境中的表型变化认识。"
A_FOLLOWUP = "甲续问意见：血清条件尚未给出，不能替申请假定条件；描述性比较仍有价值。"
FOLLOWUP = "请继续解释申请已有地面对照时，血清条件未知对知识产出的具体影响。"
FINAL = "综合两位专家：地面对照提供描述性认识，培养条件决定 TAG 变化的进一步解释。"


def body(text, citations=()):
    return reply(content=text + "".join(f"[@{key}]" for key in citations))


def names(tools):
    return {row["function"]["name"] for row in tools}


def topic_rows(messages):
    return {
        row["topic_id"]: row
        for payload in payloads(messages)
        for row in objects(payload)
        if {"topic_id", "status", "note_ref"}.issubset(row)
    }


class MeteredModel(ScriptModel):
    def __init__(self, name):
        super().__init__([], name=name)
        self.budget = None

    async def complete(self, messages, tools=None, *, response_schema=None):
        assert response_schema is None
        assert tools is not None
        self.requests.append({"messages": deepcopy(messages), "tools": deepcopy(tools)})
        ticket = self.budget.reserve(self.config, messages, tools, response_schema)
        try:
            return await self.respond(messages, names(tools))
        finally:
            self.budget.settle(ticket, SimpleNamespace(prompt_tokens=120, completion_tokens=40))


class ChiefModel(MeteredModel):
    def __init__(self, fixture, *, long_application=False):
        super().__init__("fixed-workflow-chief")
        self.fixture = fixture
        self.long_application = long_application
        self.stage = "read_application" if long_application else "initial"
        self.topic_ids = {}

    async def respond(self, messages, available):
        if self.stage == "read_application":
            assert {"browse_materials", "finish_research"} <= available
            assert not {"research_topics", "search_sources", "acquire_source"} & available
            self.stage = "finish_application"
            return reply(
                tool_call(
                    "browse_materials",
                    {
                        "reads": [{"source_id": self.fixture.application_id}],
                    },
                )
            )
        if self.stage == "finish_application":
            require_content(messages, "本项目使用 Huh7")
            self.stage = "initial"
            return reply(tool_call("finish_research", {"reason": "已阅读固定申请的研究安排。"}))
        if self.stage == "initial":
            assert available == set(), available
            require_content(messages, "本项目使用 Huh7")
            self.stage = "skills"
            return body(INITIAL)
        if self.stage in {"skills", "plan"}:
            assert {"research_topics", "read_skills", "read_materials"} <= available
            assert (
                not {"finish_research", "update_understanding", "search_sources", "acquire_source"}
                & available
            )
            require_content(messages, INITIAL)
            if self.stage == "skills":
                self.stage = "plan"
                return reply(tool_call("read_skills", {"names": ["measurement-analysis"]}))
            self.stage = "followup"
            return reply(
                tool_call(
                    "research_topics",
                    {
                        "requests": [
                            {
                                "task": {
                                    "objective": objective,
                                    "impact": "解释实际数据能够形成的认识。",
                                    "skills": ["measurement-analysis"],
                                }
                            }
                            for objective in (A, B)
                        ]
                    },
                )
            )
        assert {
            "research_topics",
            "finish_research",
            "search_sources",
            "acquire_source",
        } <= available or not available
        if self.stage == "followup":
            require_content(messages, A_NOTE, B_NOTE, METHOD, RESULT)
            rows = topic_rows(messages)
            assert len(rows) == 2, rows
            self.topic_ids = {row["objective"]: key for key, row in rows.items()}
            self.stage = "finish"
            return reply(
                tool_call(
                    "research_topics",
                    {
                        "requests": [
                            {
                                "topic_id": self.topic_ids[A],
                                "followup": FOLLOWUP,
                            }
                        ]
                    },
                )
            )
        if self.stage == "finish":
            require_content(messages, A_FOLLOWUP, B_NOTE, METHOD, RESULT)
            self.stage = "final"
            return reply(
                tool_call("finish_research", {"reason": "两位专家及续问已经回答本次局部问题。"})
            )
        assert self.stage == "final" and not available, (self.stage, available)
        require_content(messages, A_FOLLOWUP, B_NOTE, METHOD, RESULT)
        self.stage = "done"
        return body(FINAL, aliases(messages, METHOD, RESULT))


class Experts(MeteredModel):
    def __init__(self, fixture, *, pause=False):
        super().__init__("fixed-workflow-experts")
        self.fixture = fixture
        self.stages = Counter()
        self.pause = pause
        self.arrived = set()
        self.both_arrived = asyncio.Event()
        self.release = asyncio.Event()
        self.active = self.max_active = 0

    async def respond(self, messages, available):
        objective = next(p["task"]["objective"] for p in payloads(messages) if "task" in p)
        assert objective in {A, B}
        require_content(messages, "本项目使用 Huh7", "measurement-analysis")
        step = self.stages[objective]
        self.active += 1
        self.max_active = max(self.active, self.max_active)
        try:
            if step == 0:
                assert {
                    "search_sources",
                    "acquire_source",
                    "read_materials",
                    "read_source_for_question",
                } <= available
                assert "research_topics" not in available
                self.arrived.add(objective)
                if len(self.arrived) == 2:
                    self.both_arrived.set()
                await asyncio.wait_for(self.both_arrived.wait(), timeout=5)
                if self.pause:
                    await self.release.wait()
                answer = reply(search())
            elif step == 1:
                require_content(messages, ABSTRACT)
                answer = reply(
                    tool_call(
                        "acquire_source",
                        {
                            "source_id": self.fixture.source_id,
                            "reading_purpose": "Methods Results",
                        },
                    )
                )
            elif step in {2, 4}:
                require_content(messages, METHOD, RESULT)
                if step == 4:
                    assert objective == A
                    require_content(messages, FOLLOWUP, A_NOTE)
                answer = reply(
                    tool_call("finish_research", {"reason": "已取得固定研究问题的原文依据。"})
                )
            else:
                assert not available, available
                assert step in {3, 5}, step
                text = A_FOLLOWUP if step == 5 else A_NOTE if objective == A else B_NOTE
                answer = body(text, aliases(messages, METHOD, RESULT))
            self.stages[objective] += 1
            return answer
        finally:
            self.active -= 1


def services(root, fixture, chief_model, experts, *, turns=24):
    budget = Budget(root, BudgetLimits(max_model_calls=40, max_seconds=300))
    chief_model.budget = experts.budget = budget
    skills = SkillLibrary(ROOT / "skills")
    scheduler = RequestScheduler(ConcurrencyConfig(model_requests=2, tools=1))
    scheduler.request_budget = budget
    engine = ResearchRuntime(
        root,
        {"chief": chief_model, "expert": experts},
        {"chief": "chief", "researcher": "expert"},
        budget=budget,
        scheduler=scheduler,
        skills=skills,
        settings=SessionSettings(max_turns=turns, context_tokens=64000),
    )
    topics = TopicResearch(
        engine,
        fixture.store,
        fixture.discovery,
        application_ids=[fixture.application_id],
        catalogue=ExpertiseCatalogue(ROOT / "config/expertise.yaml", skills),
    )
    return (
        engine,
        topics,
        ChiefReview(
            topics,
            evaluation_system="gbt42048_2022",
            admission={"status": "skipped", "reason": "formal_review_disabled"},
        ),
    )


async def workflow(root, *, interrupted=False):
    fixture = await Fixture(root).setup()
    chief_model, experts = ChiefModel(fixture), Experts(fixture, pause=interrupted)
    engine, topics, chief = services(root, fixture, chief_model, experts)
    chief_id = chief.create("局部研究流程验证：从申请安排专家研究，综合后提交研究稿；不评分。")
    try:
        if interrupted:
            running = asyncio.create_task(chief.run(chief_id))
            await asyncio.wait_for(experts.both_arrived.wait(), timeout=10)
            running.cancel()
            try:
                await running
            except asyncio.CancelledError:
                pass
            saved = chief._manifest(chief_id)
            first_ids = list(saved["topic_ids"])
            assert len(first_ids) == 2 and saved["pending_batch"]
            assert engine.load(chief_id).coordination_phase == "planning"
            assert all(engine.load(key).status == "partial" for key in first_ids)
            await engine.close()
            experts.pause = False
            engine, topics, chief = services(root, fixture, chief_model, experts)
        result = await asyncio.wait_for(chief.run(chief_id), timeout=15)
        assert result["status"] == "submitted", result
        state = engine.load(chief_id)
        assert state.coordination_phase == "synthesis" and state.initial_understanding_ref
        assert FINAL in json.dumps(result["note"], ensure_ascii=False)
        manifest = chief._manifest(chief_id)
        assert len(manifest["topic_ids"]) == 2 and "pending_batch" not in manifest
        if interrupted:
            assert manifest["topic_ids"] == first_ids
        assert experts.stages == {A: 6, B: 4}
        assert experts.max_active == 2
        assert len(engine.load(chief_model.topic_ids[A]).submissions) == 2
        assert all(topics.result(key).status == "submitted" for key in manifest["topic_ids"])
        assert len(fixture.discovery.queries) == 2
        assert fixture.requests
        assert METHOD in json.dumps(result["readings"], ensure_ascii=False)
        assert not state.assessment_ref and not state.report_ref
        assert not list((root / "v5/delivery-parses").glob("*.json"))
        assert_private_not_saved(root)
        write_json(root / "result.json", result)
        return {
            "chief": chief_id,
            "topics": manifest["topic_ids"],
            "resumed_during_delegation": interrupted,
            "expert_model_responses": dict(experts.stages),
            "parallel_experts": experts.max_active,
            "searches": len(fixture.discovery.queries),
            "chief_model_requests": len(chief_model.requests),
            "complete_expert_notes_and_originals_received": True,
            "stopped_before_assessment": True,
        }
    finally:
        await engine.close()
        await fixture.close()


async def short_budget(root, *, long_application=False):
    fixture = await Fixture(root).setup()
    if long_application:
        path = root / "long-application.md"
        path.write_text(
            "# 固定长计划书\n\n本项目使用 Huh7，比较在轨与地面对照。\n\n"
            + "\n\n".join(
                f"实验安排 {i}：" + "culture comparison measurement " * 100 for i in range(160)
            ),
            encoding="utf-8",
        )
        fixture.application_id = await fixture.store.add_local(path)
    chief_model, experts = ChiefModel(fixture, long_application=long_application), Experts(fixture)
    engine, _, chief = services(
        root, fixture, chief_model, experts, turns=4 if long_application else 3
    )
    chief_id = chief.create("局部短预算验证，资源不足时保留当前研究规划。")
    try:
        state = engine.load(chief_id)
        assert state.task.application["full_text_in_context"] is not long_application
        result = await asyncio.wait_for(chief.run(chief_id), timeout=10)
        state = engine.load(chief_id)
        assert result["status"] == "partial", result
        assert state.coordination_phase == "planning" and not state.research_finished
        assert state.initial_understanding_ref
        assert not state.assessment_ref and not state.report_ref
        assert not chief._manifest(chief_id)["topic_ids"]
        assert not experts.requests
        assert not any(
            json.loads(path.read_text("utf-8")).get("phase") == "final_manuscript"
            for path in (root / "v5/author-texts").glob("*.json")
        )
        write_json(root / "result.json", result)
        return {
            "long_application": long_application,
            "status": state.status,
            "reason": state.reason,
            "turns": state.turns_used,
            "initial_understanding_saved": True,
            "remains_in_planning": True,
            "no_assessment_or_final_manuscript": True,
        }
    finally:
        await engine.close()
        await fixture.close()


async def main(output):
    output = output.resolve()
    if output.exists():
        raise ValueError("Use a fresh output directory for the offline probe")
    results = {}
    for name, interrupted in (("workflow", False), ("resume", True)):
        results[name] = await workflow(output / name, interrupted=interrupted)
    for name, long_application in (("short-budget", False), ("long-application", True)):
        results[name] = await short_budget(output / name, long_application=long_application)
    write_json(output / "probe.json", results)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args().output))
