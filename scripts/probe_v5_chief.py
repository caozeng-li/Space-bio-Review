"""Exercise persistent chief coordination with fixed models and retained sources only."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_session import (  # noqa: E402
    ParserModel,
    ScriptModel,
    assert_private_not_saved,
    delivery,
    note,
    require_content,
    tool_call,
)
from probe_v5_session import (  # noqa: E402
    reply as provider_reply,
)
from probe_v5_topic import METHOD, RESULT, Fixture, aliases, objects, payloads  # noqa: E402

from spacebio_review.agents.skills import SkillLibrary  # noqa: E402
from spacebio_review.models.config import ConcurrencyConfig  # noqa: E402
from spacebio_review.models.provider import ModelError  # noqa: E402
from spacebio_review.models.scheduling import RequestScheduler  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.prose import cited_ids  # noqa: E402
from spacebio_review.review.expertise import ExpertiseCatalogue  # noqa: E402
from spacebio_review.v5 import ResearchRuntime, SessionSettings, SessionTask  # noqa: E402
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance  # noqa: E402
from spacebio_review.v5.chief import ChiefReview  # noqa: E402
from spacebio_review.v5.records import ResearchNote  # noqa: E402
from spacebio_review.v5.topic import ApplicationRef, TopicResearch, TopicTask  # noqa: E402

A = "固定专题甲：培养条件与检测含义"
B = "固定专题乙：比较条件的已有资料"
A_NOTE = "固定论文比较 Huh7 与 HepG2，血清条件限定细胞内 TAG 结果的解释。"
A_REVISED = A_NOTE + "本项目已有地面对照；血清类型尚未给出，不能替申请假定培养条件。"
B_DRAFT = "中断前确认计划书已有地面对照，尚未完成比较条件的进一步资料研究。"
SCIENTIFIC_UNKNOWN = "申请尚未给出血清类型，培养条件对读数的影响仍未确定。"
TECHNICAL_FAILURE = "fixed_topic_transport_timeout"
WORKING = "已确认研究对象与地面对照，当前关注血清条件和读数解释，不把服务中断当作设计缺陷。"
CHIEF_NOTE = "项目确有地面对照。已有固定原文帮助界定培养条件与 TAG 读数含义；血清信息缺口仍需保留。"
ADMISSION = {"status": "skipped", "reason": "formal_review_disabled"}


def reply(*calls, content=None):
    """Keep canonical fixture notes while emitting the current ordinary-text protocol."""
    if len(calls) == 1:
        function = calls[0]["function"]
        operation = function["name"]
        args = json.loads(function["arguments"])
        if operation == "update_research":
            return delivery(operation, args)
        if operation == "submit_research":
            if args.get("note") is not None:
                return delivery(operation, args["note"])
            return provider_reply(tool_call(operation, {}))
    return provider_reply(*calls, content=content)


def agenda(text=WORKING, *, topic_ids=()):
    return {
        "working_understanding": text,
        "items": [
            {
                "topic": "培养条件与读数解释",
                "why": "决定细胞模型结果可以支持何种科学解释。",
                "topic_ids": list(topic_ids),
                "unknowns": [SCIENTIFIC_UNKNOWN],
                "disposition": "active",
            }
        ],
    }


def task(objective, source_id):
    return {
        "task": {
            "objective": objective,
            "impact": "解释申请的实际比较与终点。",
            "source_ids": [source_id],
        },
        "topic_id": None,
        "followup": None,
    }


class MeteredScript(ScriptModel):
    async def complete(self, messages, tools=None, *, response_schema=None):
        ticket = self.budget.reserve(self.config, messages, tools, response_schema)
        try:
            return await super().complete(messages, tools, response_schema=response_schema)
        finally:
            self.budget.settle(ticket, SimpleNamespace(prompt_tokens=120, completion_tokens=40))


class Researchers(ScriptModel):
    """One fake route serving independently scripted persistent authors."""

    def __init__(self, source_id):
        super().__init__([], name="fixed-researcher")
        self.source_id = source_id
        self.counts = {A: 0, B: 0}
        self.active = self.max_active = 0
        self.first_arrivals = 0
        self.both_arrived = asyncio.Event()

    async def complete(self, messages, tools=None, *, response_schema=None):
        objective = next(p["task"]["objective"] for p in payloads(messages) if "task" in p)
        assert objective in self.counts
        assert "research_topics" not in {t["function"]["name"] for t in tools}
        require_content(messages, "本项目使用 Huh7", "比较在轨与地面对照")
        number = self.counts[objective] = self.counts[objective] + 1
        self.requests.append({"objective": objective, "messages": deepcopy(messages)})
        ticket = self.budget.reserve(self.config, messages, tools, response_schema)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if number == 1:
                self.first_arrivals += 1
                if self.first_arrivals == 2:
                    self.both_arrived.set()
                # Both slots must belong to children while the chief awaits delegation.
                await asyncio.wait_for(self.both_arrived.wait(), timeout=3)
                if objective == A:
                    return reply(
                        tool_call(
                            "acquire_source",
                            {"source_id": self.source_id, "reading_purpose": "Methods Results"},
                        )
                    )
                return reply(
                    tool_call("update_research", note(B_DRAFT, unknowns=[SCIENTIFIC_UNKNOWN]))
                )
            if objective == B:
                assert number == 2
                raise ModelError(TECHNICAL_FAILURE, category="timeout", request_sent=True)
            require_content(messages, METHOD, RESULT)
            if number == 2:
                return reply(
                    tool_call(
                        "submit_research",
                        {"note": note(A_NOTE, aliases(messages, METHOD, RESULT))},
                    )
                )
            assert number == 3
            require_content(messages, "请结合实际地面对照继续解释", A_NOTE)
            return reply(
                tool_call(
                    "submit_research",
                    {
                        "note": note(
                            A_REVISED,
                            aliases(messages, METHOD, RESULT),
                            unknowns=[SCIENTIFIC_UNKNOWN],
                        )
                    },
                )
            )
        finally:
            self.active -= 1
            self.budget.settle(ticket, SimpleNamespace(prompt_tokens=120, completion_tokens=40))


def service(
    root,
    fixture,
    chief_model,
    researchers,
    *,
    context_tokens=64_000,
    cutoff=None,
    prepared_application=True,
):
    budget = Budget(root, BudgetLimits(max_model_calls=30, max_seconds=300))
    chief_model.budget = researchers.budget = budget
    skills = SkillLibrary(ROOT / "skills")
    engine = ResearchRuntime(
        root,
        {"chief": chief_model, "researcher": researchers},
        {"chief": "chief", "researcher": "researcher"},
        scheduler=RequestScheduler(ConcurrencyConfig(model_requests=2, tools=1)),
        budget=budget,
        settings=SessionSettings(max_turns=12, context_tokens=context_tokens),
        skills=skills,
        delivery_parser=ParserModel(),
    )
    engine.scheduler.request_budget = budget
    topics = TopicResearch(
        engine,
        fixture.store,
        fixture.discovery,
        application_ids=[fixture.application_id],
        cutoff=cutoff,
        catalogue=ExpertiseCatalogue(ROOT / "config/expertise.yaml", skills),
    )
    chief = ChiefReview(topics, evaluation_system="gbt42048_2022", admission=ADMISSION)
    if prepared_application:
        original_create = chief.create

        def create(objective, **kwargs):
            chief_id = original_create(objective, **kwargs)
            state = engine.load(chief_id)
            # Coordination cases start after a fixed application-reading stage. The
            # separate application_grounding case exercises that stage from no note.
            initial = ResearchNote.model_validate(
                note("固定前置研究稿：申请使用 Huh7，已有在轨与地面对照。", list(state.aliases))
            )
            engine.store.save_note(state, initial, absorbed_through=0)
            return chief_id

        chief.create = create
    return engine, topics, chief


def delivered_topics(messages):
    return {
        row["topic_id"]: row
        for payload in payloads(messages)
        for row in objects(payload)
        if isinstance(row, dict) and "topic_id" in row and "status" in row and "note" in row
    }


async def coordination(root, *, compact=False):
    fixture = await Fixture(root).setup()
    researchers = Researchers(fixture.source_id)
    identities = {}

    def begin(messages):
        require_content(messages, "本项目使用 Huh7", "GB/T 42048", "skipped", "methods")
        assert not list((root / "v5/topics").glob("*.json")), "Chief must create topics itself"
        return reply(
            tool_call("update_agenda", agenda("初读计划书：需要弄清模型比较和检测条件。")),
            tool_call("read_skills", {"names": ["measurement-analysis"]}),
            tool_call(
                "research_topics",
                {"requests": [task(A, fixture.source_id), task(B, fixture.source_id)]},
            ),
        )

    def continue_topic(messages):
        require_content(messages, A_NOTE, B_DRAFT, TECHNICAL_FAILURE, METHOD, RESULT)
        rows = delivered_topics(messages)
        assert len(rows) == 2, rows
        identities.update(
            a=next(key for key, row in rows.items() if row["status"] == "submitted"),
            b=next(key for key, row in rows.items() if row["status"] in {"failed", "partial"}),
        )
        assert rows[identities["b"]]["reason"] == TECHNICAL_FAILURE
        assert SCIENTIFIC_UNKNOWN in json.dumps(
            rows[identities["b"]]["note"]["body"], ensure_ascii=False
        )
        return reply(
            tool_call("update_agenda", agenda(topic_ids=identities.values())),
            tool_call(
                "research_topics",
                {
                    "requests": [
                        {
                            "task": None,
                            "topic_id": identities["a"],
                            "followup": "请结合实际地面对照继续解释血清信息缺口。",
                        }
                    ]
                },
            ),
        )

    def absorb(messages):
        require_content(messages, A_REVISED, WORKING, METHOD, RESULT)
        return reply(
            tool_call(
                "update_research",
                note(CHIEF_NOTE, aliases(messages, METHOD, RESULT), unknowns=[SCIENTIFIC_UNKNOWN]),
            ),
        )

    def submit(messages):
        require_content(messages, CHIEF_NOTE)
        return reply(tool_call("submit_research", {"note": None}))

    chief_model = MeteredScript([begin, continue_topic, absorb, submit], name="fixed-chief")
    engine, topics, chief = service(
        root, fixture, chief_model, researchers, context_tokens=1600 if compact else 64_000
    )
    chief_id = chief.create("以完整计划书组织有限研究，不评分、不生成报告。")
    try:
        result = await asyncio.wait_for(chief.run(chief_id), timeout=12)
        assert result["status"] == "submitted", result
        assert len(list((root / "v5/topics").glob("*.json"))) == 2
        assert researchers.counts == {A: 3, B: 2}
        assert researchers.max_active == 2
        assert engine.load(identities["a"]).turns_used == 3
        assert engine.load(chief_id).turns_used == 4
        assert engine.budget.usage(chief_id)["model_calls"] == 4
        assert engine.budget.usage()["model_calls"] == 9
        assert len(engine.load(identities["a"]).submissions) == 2
        assert fixture.source_id in result["reading_records"]
        assert METHOD in json.dumps(result["readings"], ensure_ascii=False)
        assert TECHNICAL_FAILURE not in json.dumps(result["note"]["body"], ensure_ascii=False)
        if compact:
            assert engine.load(chief_id).context_compactions > 0
        delegations = [r for r in engine.scheduler.records if r["name"] == "research_topics"]
        assert len(delegations) == 2 and all(r["kind"] == "delegation" for r in delegations)
        write_json(root / "first.json", result)
        assert_private_not_saved(root)
        return {
            "chief_id": chief_id,
            "topics": identities,
            "actual_model_requests": engine.budget.usage()["model_calls"],
            "chief_requests": engine.load(chief_id).turns_used,
            "researcher_requests": 5,
            "parallel_researcher_requests": researchers.max_active,
            "technical_failure_retained_draft": True,
            "same_topic_followup": True,
            "compactions": engine.load(chief_id).context_compactions,
            "external_network_requests": 0,
        }
    finally:
        await engine.close()
        await fixture.close()


async def research_reuse(root):
    fixture = await Fixture(root).setup()
    identities = {}

    def submit_original(messages):
        return reply(
            tool_call("submit_research", {"note": note(A_NOTE, aliases(messages, METHOD, RESULT))})
        )

    def reuse_context(messages):
        context = next(payload for payload in payloads(messages) if "task" in payload)
        existing = json.loads(context["task"]["evaluation"])["existing_research"]
        assert len(existing) == 1 and existing[0]["topic_id"] == identities["a"]
        assert "readings" not in existing[0], "Originals must be supplied through the common scope"
        refs = set(cited_ids(existing[0]["body"]))
        assert refs and refs <= set(context["available_readings"])
        assert all(ref.startswith("E") and ref[1:].isdigit() for ref in refs)
        require_content(messages, A_NOTE, METHOD, RESULT)
        return reply(tool_call("submit_research", {"note": note(A_REVISED, sorted(refs))}))

    researchers = MeteredScript(
        [
            reply(
                tool_call(
                    "acquire_source",
                    {"source_id": fixture.source_id, "reading_purpose": "Methods Results"},
                )
            ),
            submit_original,
            reuse_context,
        ],
        name="fixed-researcher",
    )

    def new_topic(messages):
        rows = delivered_topics(messages)
        identities["a"] = next(iter(rows))
        request = task(B, fixture.source_id)
        request["task"]["research_ids"] = [identities["a"]]
        return reply(tool_call("research_topics", {"requests": [request]}))

    def complete(messages):
        require_content(messages, A_NOTE, A_REVISED, METHOD, RESULT)
        return reply(
            tool_call("submit_research", {"note": note(CHIEF_NOTE, aliases(messages, RESULT))})
        )

    chief_model = MeteredScript(
        [
            reply(tool_call("research_topics", {"requests": [task(A, fixture.source_id)]})),
            new_topic,
            complete,
        ],
        name="fixed-chief",
    )
    engine, topics, chief = service(root, fixture, chief_model, researchers)
    try:
        chief_id = chief.create("局部验证已有专题知识传给新专题，原文与引用保持可读。")
        result = await chief.run(chief_id)
        assert result["status"] == "submitted", result
        assert len(list((root / "v5/topics").glob("*.json"))) == 2
        first = topics.result(identities["a"])
        assert not any(ref.startswith("E") for ref in cited_ids(first.note))
        assert engine.budget.usage()["model_calls"] == 6
        write_json(root / "result.json", result)
        assert_private_not_saved(root)
        return {
            "actual_model_requests": 6,
            "new_topic_citations_are_local_aliases": True,
            "originals_supplied_once": True,
        }
    finally:
        await engine.close()
        await fixture.close()


async def live_probe_limits(root):
    from probe_v5_chief_live import limit_actual_requests

    budget = Budget(
        root, BudgetLimits(max_model_calls=18, max_embedding_calls=18, max_seconds=1500)
    )
    model = ScriptModel([])
    engine = ResearchRuntime(
        root, {"fixed": model}, {"chief": "fixed", "researcher": "fixed"}, budget=budget
    )
    chief_id, first, second = [
        engine.create(SessionTask(role=role, objective="固定配额验证", application="固定材料"))
        for role in ("chief", "researcher", "researcher")
    ]
    limit_actual_requests(engine)

    def request(session_id, *, purpose="research", embedding=False):
        with budget.scope(session_id, purpose=purpose):
            ticket = budget.reserve(
                model.config,
                messages=None if embedding else [{"role": "user", "content": "固定请求"}],
                texts=["固定向量请求"] if embedding else None,
            )
            budget.settle(ticket, SimpleNamespace(prompt_tokens=20, completion_tokens=10))

    def denied(session_id, *, embedding=False):
        before = len(budget.data["requests"])
        try:
            request(session_id, embedding=embedding)
        except ModelError as exc:
            assert exc.category == "budget" and exc.request_sent is False
        else:
            raise AssertionError("Probe sent a request reserved for completion")
        assert len(budget.data["requests"]) == before

    try:
        for _ in range(5):
            request(chief_id)
        denied(chief_id)
        for topic_id in (first, second):
            for _ in range(4):
                request(topic_id)
            request(topic_id, purpose="auxiliary", embedding=True)
        denied(first)
        denied(second, embedding=True)
        for topic_id in (first, second):
            request(topic_id, purpose="completion")
            budget.finish(topic_id)
        request(chief_id, purpose="completion")
        denied(chief_id)
        assert len(budget.data["requests"]) == 18
        assert budget.usage(chief_id)["model_calls"] == 6
        assert budget.usage()["model_calls"] == 16
        assert budget.usage()["embedding_calls"] == 2
        return {
            "chief_actual_requests": 6,
            "researcher_actual_requests": 12,
            "total_actual_requests": 18,
            "each_active_researcher_completion_protected": True,
            "auxiliary_requests_counted": True,
            "unsent_denials_not_billed": True,
        }
    finally:
        await engine.close()




async def uncited_source_directory(root, *, failed, draft=True):
    """A source directory grants retrieval, never a record of having read its text."""
    fixture = await Fixture(root).setup()
    initial_note = "已读取固定材料，但当前说明尚未采用其内容；仍需研究。"
    identities = {}

    def acquire():
        return reply(
            tool_call(
                "acquire_source",
                {"source_id": fixture.source_id, "reading_purpose": "Methods Results"},
            )
        )

    def inspect_new_topic(messages):
        scope = topics.access(engine.budget._owner.get())
        assert fixture.source_id in scope.visible
        assert fixture.source_id not in scope.read
        assert METHOD not in json.dumps(messages, ensure_ascii=False)
        return acquire()

    def cited_submission(messages):
        require_content(messages, METHOD, RESULT)
        return reply(
            tool_call("submit_research", {"note": note(A_NOTE, aliases(messages, METHOD, RESULT))})
        )

    researcher_steps = [acquire()]
    if failed:
        if draft:
            researcher_steps.append(reply(tool_call("update_research", note(initial_note))))
        researcher_steps.append(
            ModelError(TECHNICAL_FAILURE, category="timeout", request_sent=True)
        )
    else:
        researcher_steps.extend(
            [
                reply(tool_call("submit_research", {"note": note(initial_note)})),
                inspect_new_topic,
                cited_submission,
            ]
        )
    researchers = MeteredScript(researcher_steps, name="fixed-researcher")

    def use_directory(messages):
        rows = delivered_topics(messages)
        identities["a"] = next(iter(rows))
        first_result = topics.result(identities["a"])
        assert first_result.readings == {}, "Uncited text must not be delivered as used evidence"
        assert fixture.source_id in first_result.reading_records
        assert any(m["source_id"] == fixture.source_id for m in first_result.materials)
        scope = chief.access(chief_id)
        assert fixture.source_id in scope.visible
        assert fixture.source_id not in scope.read
        assert METHOD not in json.dumps(messages, ensure_ascii=False)
        if failed:
            assert first_result.status == "failed"
            assert first_result.reason == TECHNICAL_FAILURE
            assert (first_result.note is not None) == draft
            return acquire()
        request = {
            "task": {"objective": B, "research_ids": [identities["a"]]},
            "topic_id": None,
            "followup": None,
        }
        # No explicit source_ids: the existing research directory is the only entry.
        return reply(tool_call("research_topics", {"requests": [request]}))

    chief_model = MeteredScript(
        [
            reply(tool_call("research_topics", {"requests": [task(A, fixture.source_id)]})),
            use_directory,
            cited_submission,
        ],
        name="fixed-chief",
    )
    engine, topics, chief = service(root, fixture, chief_model, researchers)
    chief_id = chief.create("依据专题返回的资料目录继续原文阅读，区分材料可取得与已经读过。")
    try:
        result = await chief.run(chief_id)
        assert result["status"] == "submitted", result
        assert fixture.source_id in result["reading_records"]
        assert METHOD in json.dumps(result["readings"], ensure_ascii=False)
        assert_private_not_saved(root)
        write_json(root / "result.json", result)
        return {
            "actual_model_requests": engine.budget.usage()["model_calls"],
            "failed_topic_directory_handoff": failed,
            "failed_topic_had_draft": failed and draft,
            "uncited_completed_research_directory_reuse": not failed,
            "directory_did_not_mark_original_as_read": True,
            "direct_acquisition_succeeded": True,
        }
    finally:
        await engine.close()
        await fixture.close()


async def closing_submission(root):
    fixture = await Fixture(root).setup()
    draft = "已理解计划书的 Huh7 对象与地面对照，现有材料可直接形成有限说明。"
    final = draft + "血清类型仍未给出，其影响保留为未知。"

    def save(messages):
        require_content(messages, "本项目使用 Huh7", "比较在轨与地面对照")
        refs = list(engine.load(chief_id).aliases)
        return reply(tool_call("update_research", note(draft, refs)))

    def submit(messages):
        names = {tool["function"]["name"] for tool in chief_model.requests[-1]["tools"]}
        assert names == {"submit_research"}, names
        require_content(messages, draft, "本项目使用 Huh7", "比较在轨与地面对照")
        refs = list(engine.load(chief_id).aliases)
        return reply(
            tool_call("submit_research", {"note": note(final, refs, unknowns=[SCIENTIFIC_UNKNOWN])})
        )

    chief_model = MeteredScript([save, submit], name="fixed-chief")
    researchers = MeteredScript([], name="fixed-researcher")
    engine, _, chief = service(root, fixture, chief_model, researchers, context_tokens=1600)
    engine.settings.max_turns = 2
    chief_id = chief.create("以已读计划书保存研究稿后直接提交，不再安排下轮阅读。")
    try:
        result = await chief.run(chief_id)
        assert result["status"] == "submitted", result
        assert engine.load(chief_id).turns_used == 2
        assert engine.load(chief_id).context_compactions > 0
        assert result["readings"] and cited_ids(result["note"])
        assert final in json.dumps(result["note"], ensure_ascii=False)
        assert [row["purpose"] for row in engine.budget.data["requests"]] == [
            "research",
            "completion",
        ]
        assert engine.resources(chief_id)["session_model_turns_left"] == 0
        assert not researchers.requests
        write_json(root / "result.json", result)
        return {
            "actual_model_requests": 2,
            "closing_tools": ["submit_research"],
            "originals_available_without_another_read": True,
            "saved_note_revised_and_submitted_in_final_request": True,
        }
    finally:
        await engine.close()
        await fixture.close()


async def closing_unsent_rejection(root):
    budget = Budget(root, BudgetLimits(max_model_calls=4, max_seconds=300))
    draft = note("已保存的固定长篇说明，收束时仍保留于研究语境。" * 2000)
    model = MeteredScript([reply(tool_call("update_research", draft))])
    model.budget = budget
    engine = ResearchRuntime(
        root,
        {"fixed": model},
        {"chief": "fixed", "researcher": "fixed"},
        budget=budget,
        settings=SessionSettings(max_turns=2),
        delivery_parser=ParserModel(),
    )
    session_id = engine.create(
        SessionTask(
            role="chief", objective="保存稿后收束请求因输入额度不足被拒绝。", application="固定申请"
        ),
        allowance=SessionAllowance(max_input_tokens=10_000, completion_input_tokens=1000),
    )
    try:
        result = await engine.run(session_id)
        state = engine.load(session_id)
        assert result.status == "partial"
        assert result.reason == "session_input_token_budget_exhausted", result
        assert state.turns_used == budget.usage()["model_calls"] == len(model.requests) == 1
        assert state.note_ref and not state.submitted_ref
        assert engine.store.note(state.note_ref)["note"] == draft
        summary = {
            "actual_model_requests": 1,
            "turns_used": 1,
            "unsent_closing_request_not_counted": True,
            "saved_draft_preserved": True,
        }
        write_json(root / "summary.json", summary)
        return summary
    finally:
        await engine.close()


async def application_grounding(root):
    """Exercise the actual note gate and stable positions without semantic assertions."""
    fixture = await Fixture(root).setup()
    application = root / "grounding-application.md"
    first_text = "本项目使用 Huh7，比较在轨与地面对照。"
    second_text = "返回后检测细胞内甘油三酯；血清类型尚未给出。"
    application.write_text(f"# 固定申请\n\n{first_text}\n\n{second_text}\n", "utf-8")
    fixture.application_id = await fixture.store.add_local(application)
    originals = fixture.store.retained(fixture.application_id)
    first, second = [
        next(p for p in originals.passages if p.text == text) for text in (first_text, second_text)
    ]
    first_ref = ApplicationRef(source_id=fixture.application_id, passage_id=first.passage_id)
    second_ref = ApplicationRef(source_id=fixture.application_id, passage_id=second.passage_id)
    identities = {}

    def position_aliases(messages, refs):
        context = next(p for p in payloads(messages) if "task" in p)
        wanted = {(r.source_id, r.passage_id) for r in refs}
        return [
            alias
            for alias, row in context["available_readings"].items()
            if (row["reference"]["source_id"], row["reference"]["passage_id"]) in wanted
        ]

    def understand(messages):
        names = {t["function"]["name"] for t in chief_model.requests[-1]["tools"]}
        assert "research_topics" not in names
        return reply(
            tool_call("update_research", note(first_text, position_aliases(messages, [first_ref])))
        )

    def delegate(messages):
        names = {t["function"]["name"] for t in chief_model.requests[-1]["tools"]}
        assert "research_topics" in names
        assert engine.load(chief_id).note_ref
        return reply(
            tool_call(
                "research_topics",
                {
                    "requests": [
                        {
                            "task": {
                                "objective": "固定验证：解释比较条件与终点。",
                                "application_refs": [first_ref.model_dump()],
                            }
                        }
                    ]
                },
            )
        )

    def read_basis(messages):
        context = next(p for p in payloads(messages) if "task" in p)
        basis = json.loads(context["task"]["evaluation"])["application_basis"]
        assert basis[0]["source_id"] == fixture.application_id
        assert basis[0]["title"] == originals.candidate.title
        assert basis[0]["passages"] == [first.model_dump(mode="json")]
        local = position_aliases(messages, [first_ref])
        assert local and not set(local) & identities["chief_aliases"]
        return reply(tool_call("submit_research", {"note": note(first_text, local)}))

    def read_followup(messages):
        context = next(p for p in payloads(messages) if "task" in p)
        basis = json.loads(context["task"]["evaluation"])["application_basis"]
        assert [p["passage_id"] for row in basis for p in row["passages"]] == [
            first.passage_id,
            second.passage_id,
        ]
        return reply(
            tool_call(
                "submit_research",
                {
                    "note": note(
                        first_text + second_text,
                        position_aliases(messages, [first_ref, second_ref]),
                    )
                },
            )
        )

    chief_model = MeteredScript(
        [understand, delegate, reply(tool_call("submit_research", {}))], name="fixed-chief"
    )
    researchers = MeteredScript([read_basis, read_followup], name="fixed-researcher")
    engine, topics, chief = service(
        root, fixture, chief_model, researchers, prepared_application=False
    )
    chief_id = chief.create("固定验证申请理解后派题；原文位置由代码传递。")
    state = engine.load(chief_id)
    # Force different local aliases so a copied chief E id cannot accidentally pass.
    state.aliases = {f"E{i + 101}": key for i, key in enumerate(state.aliases.values())}
    identities["chief_aliases"] = set(state.aliases)
    engine.store.save(state)
    try:
        result = await chief.run(chief_id)
        assert result["status"] == "submitted", result
        topic_id = result["topics"][0]["topic_id"]
        assert topics._task(topic_id)["task"]["application_refs"] == [first_ref.model_dump()]
        followup = await chief.tools(chief_id).execute(
            "research_topics",
            json.dumps(
                {
                    "requests": [
                        {
                            "topic_id": topic_id,
                            "followup": "继续解释返回后的读出。",
                            "application_refs": [second_ref.model_dump(), first_ref.model_dump()],
                        }
                    ]
                }
            ),
        )
        assert followup.ok, followup
        assert followup.content["topics"][0]["status"] == "submitted", followup
        saved = topics._task(topic_id)["task"]["application_refs"]
        assert saved == [first_ref.model_dump(), second_ref.model_dump()]
        assert TopicTask.model_validate({"objective": "历史无锚点任务"}).application_refs == []
        for ref, error in (
            (
                ApplicationRef(source_id=fixture.source_id, passage_id=first.passage_id),
                "application_reference_requires_current_application",
            ),
            (
                ApplicationRef(source_id=fixture.application_id, passage_id="missing"),
                "unknown_application_passage",
            ),
        ):
            try:
                topics.application_basis([ref], topics.access(topic_id))
            except ValueError as exc:
                assert str(exc) == error
            else:
                raise AssertionError("Invalid application identity was accepted")
        write_json(root / "result.json", {"chief": result, "followup": followup})
        return {
            "initial_note_before_delegation": True,
            "exact_original_title_and_locator": True,
            "cross_session_aliases_differ": True,
            "followup_keeps_and_supplements_anchors": True,
            "historical_task_readable": True,
            "only_identity_checks": True,
            "scripted_author_requests": engine.budget.usage()["model_calls"],
            "real_provider_requests": 0,
        }
    finally:
        await engine.close()
        await fixture.close()


async def probe(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    cases = {}
    cases["application_grounding"] = await application_grounding(output / "application_grounding")
    for compact in (False, True):
        name = "coordination" if not compact else "compressed_coordination"
        cases[name] = await coordination(output / name, compact=compact)
    cases["research_reuse"] = await research_reuse(output / "research_reuse")
    cases["live_probe_limits"] = await live_probe_limits(output / "live_probe_limits")
    for name, failed, draft in (
        ("failed_topic_directory", True, True),
        ("no_note_topic_directory", True, False),
        ("uncited_research_directory", False, True),
    ):
        cases[name] = await uncited_source_directory(output / name, failed=failed, draft=draft)
    cases["closing_submission"] = await closing_submission(output / "closing_submission")
    cases["closing_unsent_rejection"] = await closing_unsent_rejection(
        output / "closing_unsent_rejection"
    )
    summary = {"mode": "offline", "cases": cases, "full_review_runs": 0}
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    main()
