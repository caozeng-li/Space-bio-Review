"""Persistent scientific authors through real runtime transitions, without providers."""

import asyncio
import json
from collections import deque
from copy import deepcopy

import pytest
from pydantic import BaseModel

from spacebio_review.models.config import ModelConfig
from spacebio_review.models.provider import ChatReply
from spacebio_review.prose import cited_ids, prose_parts
from spacebio_review.tools import Tool, ToolRegistry, ToolResult
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance
from spacebio_review.v5.records import SessionTask
from spacebio_review.v5.session import ResearchRuntime


def calls(*actions):
    return True, {
        "role": "assistant",
        "tool_calls": [
            {
                "id": f"call-{index}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            }
            for index, (name, args) in enumerate(actions)
        ],
    }


def contribute(mode="write", **kwargs):
    return calls(("contribute", {"mode": mode, **kwargs}))


def manuscript(text):
    return False, {"role": "assistant", "content": text}


def finish():
    return calls(("finish_research", {"reason": "当前材料足以形成科学判断。"}))


class ScriptedModel:
    def __init__(self, budget, script):
        self.budget, self.script = budget, deque(script)
        self.config = ModelConfig(
            model="scripted", base_url="https://unused.invalid", max_tokens=512, retries=0
        )
        self.requests = []

    async def complete(self, messages, tools=None, *, response_schema=None):
        assert response_schema is None, "Scientific contributions need no semantic parser."
        assert self.script, "Unexpected extra model request."
        expected_tools, message = self.script.popleft()
        assert bool(tools) == expected_tools, "Unexpected research/authoring transition."
        ticket = self.budget.reserve(self.config, messages=messages, tools=tools)
        self.requests.append({"messages": deepcopy(messages), "tools": deepcopy(tools)})
        reply = ChatReply(
            message=message, prompt_tokens=100, completion_tokens=20,
            finish_reason="tool_calls" if expected_tools else "stop",
        )
        self.budget.settle(ticket, reply)
        return reply

    @property
    def authors(self):
        return [request for request in self.requests if not request["tools"]]


def runtime_at(root, script):
    budget = Budget(
        root, BudgetLimits(max_seconds=10000), token_counters={"scripted": lambda _: 92}
    )
    model = ScriptedModel(budget, script)
    allowance = SessionAllowance(
        max_input_tokens=100000, completion_input_tokens=1000,
        completion_output_tokens=1024, completion_calls=1,
    )
    runtime = ResearchRuntime(
        root, {"scripted": model}, {"researcher": "scripted", "chief": "scripted"},
        budget=budget, allowances={"researcher": allowance, "chief": allowance},
    )
    return runtime, model


def snapshot(source="live-assay", quote="活细胞在轨连续成像，返回后不再测量探针信号。"):
    return {
        "reference": {"source_id": source, "passage_id": "p1", "visual_id": None},
        "title": source, "quote": quote, "material_version": "v1",
    }


def create(runtime, *, role="researcher", readings=None, inquiry_id=None):
    return runtime.create(
        SessionTask(
            role=role, objective="原始职责待独立理解", application="申请安排在轨活细胞成像。"
        ),
        readings=readings, inquiry_id=inquiry_id,
        inquiry_assignment={"objective": "OBSOLETE_FIXED_SAMPLE_PREMISE", "impact": "先理解原文"},
    )


def payload(request):
    return json.loads(request["messages"][1]["content"])


def current(runtime, session_id):
    state = runtime.load(session_id)
    return runtime.store.contribution(state.inquiry_id, state.note_ref)


class NoArgs(BaseModel):
    pass


async def test_focus_replaces_assignment_stops_old_batch_and_finish_reuses_manuscript(tmp_path):
    focus = "在轨活细胞信号如何区分真实生物学变化与成像漂移？"
    runtime, model = runtime_at(tmp_path, [
        calls(
            ("contribute", {"mode": "focus_only", "focus": focus, "reason": "按申请重新定题"}),
            ("external_read", {}),
        ),
        contribute(), manuscript("时间序列与对照可识别漂移，具有直接方法学价值。[@E1]"),
        finish(),
    ])
    external_calls = []

    async def external_read(args):
        external_calls.append(args)
        return ToolResult(ok=True, content="不应执行旧问题的检索")

    registry = ToolRegistry([Tool("external_read", "继续读取", NoArgs, external_read)])
    session_id = create(runtime, readings={"original-1": snapshot()})
    result = await runtime.run(session_id, tools=registry)

    assert result.status == "submitted"
    assert not external_calls
    assert not model.script
    assert len(model.authors) == 1
    assert "OBSOLETE_FIXED_SAMPLE_PREMISE" in json.dumps(model.requests[0], ensure_ascii=False)
    for request in model.requests[1:]:
        assert "OBSOLETE_FIXED_SAMPLE_PREMISE" not in json.dumps(request, ensure_ascii=False)
        assert payload(request)["task"]["objective"] == focus
        assert "initial_assignment" not in payload(request)["task"]
    state = runtime.load(session_id)
    assert state.submitted_ref == state.note_ref
    assert current(runtime, session_id)["revision"] == 1
    assert runtime.store.inquiry(state.inquiry_id).assignment["objective"] == (
        "OBSOLETE_FIXED_SAMPLE_PREMISE"
    )


async def test_reframed_previous_draft_keeps_version_chain_symbols_and_citations(tmp_path):
    first_text = "浓度为 10 μg·mL⁻¹，ΔF/F₀ = 0.2；活细胞读出支持可行性。[@E1]"
    second_text = "ΔF/F₀ ≠ ROS 浓度；需用时间对照识别光漂白。[@E1]\n\n这一方法问题值得验证。"
    runtime, model = runtime_at(tmp_path, [
        contribute("focus_only", focus="判断活细胞读出可行性"),
        contribute(), manuscript(first_text),
        contribute("focus_only", focus="区分生物学效应和光漂白"),
        contribute(), manuscript(second_text), finish(),
    ])
    session_id = create(runtime, readings={"stable-evidence": snapshot()})
    assert (await runtime.run(session_id)).status == "submitted"

    second_context = payload(model.authors[1])
    previous = second_context["current_research"][0]
    assert previous["relation"] == "previous_focus"
    assert previous["focus"] == "判断活细胞读出可行性"
    assert second_context["task"]["objective"] == "区分生物学效应和光漂白"
    latest = current(runtime, session_id)
    earlier = runtime.store.note(latest["previous_ref"])
    assert latest["previous_ref"] == previous["note_ref"]
    assert (earlier["revision"], latest["revision"]) == (1, 2)
    assert earlier["previous_ref"] is None
    assert latest["author_session_id"] == earlier["author_session_id"] == session_id
    assert runtime.store.author_text(earlier["author_text_ref"])["content"] == first_text
    assert runtime.store.author_text(latest["author_text_ref"])["content"] == second_text
    parts = prose_parts(latest["note"]["body"])
    assert "".join(part["text"] for part in parts) == second_text.replace("[@E1]", "")
    assert list(cited_ids(latest["note"])) == ["stable-evidence"]
    assert not model.script






async def test_chief_research_starts_with_tools_and_uses_the_same_contribution_path(tmp_path):
    runtime, model = runtime_at(tmp_path, [
        contribute(focus="该项目可获得何种在轨生物学认识"),
        manuscript("初始理解：该项目研究在轨活细胞变化。"),
        contribute(focus="项目能揭示何种动态生物学机制", reason="从技术可行性推进到科学价值"),
        manuscript("持续理解：时间对照使动态机制具有可辨识性。"), finish(),
    ])
    session_id = create(runtime, role="chief")
    assert (await runtime.run(session_id)).status == "submitted"
    previous_ref = current(runtime, session_id)["previous_ref"]
    assert runtime.store.note(previous_ref)["previous_ref"] is None
    assert model.requests[0]["tools"]
    assert len(model.authors) == 2
    for request in model.requests:
        if request["tools"]:
            names = {tool["function"]["name"] for tool in request["tools"]}
            assert "contribute" in names
            assert "update_understanding" not in names
    assert not model.script


class CollaborationHook:
    consulting = False

    def __init__(self, runtime, actions=()):
        self.runtime = runtime
        self.actions = deque(actions)
        self.published = []
        self.finished = []
        self.deliver = None

    async def before_model(self, state):
        if self.deliver:
            self.runtime._observe(state, self.deliver)
            self.deliver = None
            return True
        return False

    async def after_contribution(self, state, note_ref):
        if note_ref:
            assert self.runtime.store.inquiry(state.inquiry_id).current_contribution_ref == note_ref
            assert self.runtime.store.note(note_ref)["inquiry_id"] == state.inquiry_id
        self.published.append(note_ref)

    async def finish(self, state, reason, wait_for):
        self.finished.append((reason, wait_for, state.note_ref))
        return self.actions.popleft() if self.actions else "finish"


async def test_waiting_keeps_budget_cycle_and_wakes_without_rewriting_current_manuscript(tmp_path):
    runtime, model = runtime_at(tmp_path, [
        contribute(focus="解释采样安排"), manuscript("现有时间点支持比较早晚变化。"),
        calls(("finish_research", {"reason": "等待方法解释", "wait_for": ["request-method"]})),
    ])
    session_id = create(runtime)
    hook = CollaborationHook(runtime, ["waiting", "finish"])
    result = await runtime.run(session_id, collaboration=hook)
    account = runtime.budget.data["sessions"][session_id]
    assert result.status == "waiting"
    assert account["active"] and account["reserve_start"] == 0
    assert hook.finished[0][1] == ["request-method"]
    assert len(hook.published) == len(model.authors) == 1
    note_ref = runtime.load(session_id).note_ref

    model.script.append(finish())
    assert (await runtime.run(session_id, collaboration=hook)).status == "submitted"
    assert runtime.load(session_id).note_ref == note_ref
    assert len(model.authors) == 1
    assert account["reserve_start"] == 0 and not account["active"]


async def test_consultation_short_answer_uses_existing_version_without_new_cycle(
    tmp_path, monkeypatch
):
    runtime, model = runtime_at(tmp_path, [
        contribute(focus="时间对照的用途"), manuscript("时间对照区分漂移与生物效应。"), finish(),
    ])
    session_id = create(runtime)
    await runtime.run(session_id)
    state = runtime.load(session_id)
    account = runtime.budget.data["sessions"][session_id]
    start = account["reserve_start"]
    hook = CollaborationHook(runtime, ["waiting"])
    hook.consulting = True
    hook.deliver = {
        "kind": "consultation", "request_id": "request-control", "question": "对照用途？"
    }
    def no_final_plan(*args, **kwargs):
        raise AssertionError("A consultation has no automatic final manuscript to reserve.")

    monkeypatch.setattr(runtime, "_completion_plan", no_final_plan)
    model.script.append(finish())

    result = await runtime.run(session_id, collaboration=hook)
    assert result.status == "waiting"
    assert hook.finished[0][2] == state.note_ref
    assert runtime.load(session_id).submitted_ref == state.submitted_ref
    assert len(model.authors) == 1 and hook.published == []
    assert account["reserve_start"] == start and not account["active"]
    assert runtime.budget.data["requests"][-1]["purpose"] == "research"


async def test_consultation_update_is_research_without_replanning_a_final_delivery(
    tmp_path, monkeypatch
):
    runtime, model = runtime_at(tmp_path, [
        contribute(focus="解释动态成像"), manuscript("现稿解释时间对照。"), finish(),
    ])
    session_id = create(runtime)
    await runtime.run(session_id)
    old_ref = runtime.load(session_id).note_ref
    first_count = len(runtime.budget.data["requests"])
    hook = CollaborationHook(runtime, ["waiting"])
    hook.consulting = True
    model.script.extend([contribute(), manuscript("新稿连接时间对照与轨道成像条件。"), finish()])

    def no_final_plan(*args, **kwargs):
        raise AssertionError("Consultation writing consumes research budget directly.")

    monkeypatch.setattr(runtime, "_completion_plan", no_final_plan)
    assert (await runtime.run(session_id, collaboration=hook)).status == "waiting"
    assert runtime.load(session_id).note_ref != old_ref
    assert hook.finished[0][2] == runtime.load(session_id).note_ref
    assert all(row["purpose"] == "research"
               for row in runtime.budget.data["requests"][first_count:])
    assert not runtime.budget.data["sessions"][session_id]["active"]


async def test_consultation_budget_stop_does_not_fabricate_a_finish_answer(tmp_path):
    runtime, model = runtime_at(tmp_path, [
        contribute(focus="解释成像"), manuscript("当前稿解释观测条件。"), finish(),
    ])
    session_id = create(runtime)
    await runtime.run(session_id)
    runtime.budget.limits = runtime.budget.limits.model_copy(
        update={"max_model_calls": runtime.budget.usage()["model_calls"]}
    )
    hook = CollaborationHook(runtime)
    hook.consulting = True
    model.script.append(finish())
    result = await runtime.run(session_id, collaboration=hook)
    assert result.status == "partial" and "budget_exhausted" in result.reason
    assert hook.finished == []


async def test_new_delivery_before_authorship_is_included_and_published_immediately(tmp_path):
    runtime, model = runtime_at(tmp_path, [
        contribute("focus_only", focus="成像变化的解释"),
        contribute(), manuscript("新解释连接了成像对照和时间变化。"), finish(),
    ])
    session_id = create(runtime)
    hook = CollaborationHook(runtime)
    before_model = hook.before_model

    async def deliver_before_writing(state):
        if len(model.requests) == 2:
            assert state.authoring_phase == "contribution"
            hook.deliver = {"kind": "scientific_contribution", "explanation": "对照可识别仪器漂移"}
        return await before_model(state)

    hook.before_model = deliver_before_writing
    assert (await runtime.run(session_id, collaboration=hook)).status == "submitted"
    assert "对照可识别仪器漂移" in json.dumps(payload(model.authors[0]), ensure_ascii=False)
    assert hook.published == [None, runtime.load(session_id).note_ref]
    assert len(model.authors) == 1


async def test_chief_reads_long_application_then_plans_without_an_initial_manuscript(tmp_path):
    runtime, model = runtime_at(tmp_path, [
        finish(), calls(("research_topics", {})),
        calls(("finish_research", {"reason": "先看研究进展", "wait_for": ["request-team"]})),
    ])
    session_id = runtime.create(SessionTask(
        role="chief", objective="安排开放科学职责",
        application={"full_text_in_context": False, "sources": []},
    ))
    state = runtime.load(session_id)
    state.coordination_phase = "planning"
    runtime.store.save(state)

    def tools(state):
        async def research(args):
            state.coordination_phase = "synthesis"
            runtime.store.save(state)
            return ToolResult(ok=True, content={"request_id": "request-team"})

        return ToolRegistry([Tool("research_topics", "安排研究职责", NoArgs, research)])

    hook = CollaborationHook(runtime, ["waiting"])
    result = await runtime.run(session_id, tools=tools, collaboration=hook)
    assert result.status == "waiting"
    assert not model.authors and not hook.published
    assert all(request["tools"] for request in model.requests)
    first_names = {tool["function"]["name"] for tool in model.requests[0]["tools"]}
    second_names = {tool["function"]["name"] for tool in model.requests[1]["tools"]}
    assert "research_topics" not in first_names and "research_topics" in second_names
    assert hook.finished[0][1] == ["request-team"]


def test_model_role_changes_route_without_changing_scientific_role_or_budget(tmp_path):
    runtime, _ = runtime_at(tmp_path, [])
    original = create(runtime)
    original_state = runtime.load(original)
    runtime.models["chief-model"] = runtime.models["scripted"]
    runtime.routes["chief"] = "chief-model"
    next_owner = runtime.create(
        SessionTask(role="researcher", objective="主审接手方法问题", application="原申请"),
        inquiry_id=original_state.inquiry_id, model_role="chief",
    )
    state = runtime.load(next_owner)
    assert state.task.role == "researcher" and state.model_alias == "chief-model"
    assert runtime.budget_account(state) == original
    assert set(runtime.budget.data["sessions"]) == {original}


async def test_handoff_cancellation_keeps_the_original_delivery_cycle(tmp_path):
    runtime, model = runtime_at(tmp_path, [])
    session_id = create(runtime)
    hook = CollaborationHook(runtime)
    reached_boundary = asyncio.Event()

    async def blocked_boundary(state):
        reached_boundary.set()
        await asyncio.Event().wait()

    hook.before_model = blocked_boundary
    running = asyncio.create_task(runtime.run(session_id, collaboration=hook))
    await reached_boundary.wait()
    hook.handoff = True
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    account = runtime.budget.data["sessions"][session_id]
    assert account["active"] and account["reserve_start"] == 0
    assert not model.requests


async def test_new_owner_keeps_budget_and_rebinds_local_evidence_for_final_completion(tmp_path):
    runtime, model = runtime_at(tmp_path, [
        contribute(focus="连续成像的科学价值"),
        manuscript("旧稿据连续成像提出判断。[@E1]"), finish(),
    ])
    original_owner = create(runtime, readings={"old-evidence": snapshot()})
    assert (await runtime.run(original_owner)).status == "submitted"
    old_state = runtime.load(original_owner)
    original_usage = runtime.budget.usage(original_owner)["model_calls"]
    model.script.extend([
        finish(), manuscript("新对照支持识别漂移。[@E1]原有连续成像仍保留动态信息。[@E2]"),
    ])
    new_owner = create(
        runtime, inquiry_id=old_state.inquiry_id,
        readings={"new-evidence": snapshot("control", "时间对照可估计光漂白。")},
    )
    assert runtime.load(new_owner).budget_owner == original_owner
    assert (await runtime.run(new_owner)).status == "submitted"

    new_state = runtime.load(new_owner)
    inquiry = runtime.store.inquiry(old_state.inquiry_id)
    assert inquiry.current_owner_session_id == new_owner
    assert inquiry.budget_account_id == original_owner
    assert set(runtime.budget.data["sessions"]) == {original_owner}
    assert runtime.budget.usage(original_owner)["model_calls"] == original_usage + 2
    assert new_state.aliases == {"E1": "new-evidence", "E2": "old-evidence"}
    new_context = payload(model.authors[-1])
    assert new_context["current_research"][0]["note"]["body"][0][0]["cites"] == ["E2"]
    assert new_context["originals"]["E2"]["quote"] == snapshot()["quote"]
    latest = current(runtime, new_owner)
    assert latest["previous_ref"] == old_state.note_ref
    assert latest["author_session_id"] == new_owner
    assert list(cited_ids(latest["note"])) == ["new-evidence", "old-evidence"]
    assert runtime.budget.data["requests"][-1]["purpose"] == "completion"
    assert {row["session_id"] for row in runtime.budget.data["requests"]} == {original_owner}
    assert not model.script


async def test_new_followup_requires_new_manuscript_even_after_finish(tmp_path):
    runtime, model = runtime_at(tmp_path, [
        contribute(focus="评价连续成像"), manuscript("连续成像具有方法学价值。"), finish(),
    ])
    session_id = create(runtime)
    assert (await runtime.run(session_id)).status == "submitted"
    previous_ref = runtime.load(session_id).note_ref
    followup = "补充解释时间采样密度如何影响可辨识性。"
    model.script.extend([finish(), manuscript("采样密度决定快慢过程能否被区分。")])
    assert (await runtime.run(session_id, followup=followup)).status == "submitted"

    assert len(model.authors) == 2
    assert followup in json.dumps(payload(model.authors[-1]), ensure_ascii=False)
    assert current(runtime, session_id)["previous_ref"] == previous_ref
    assert runtime.load(session_id).note_ref != previous_ref
    assert not model.script


async def test_new_original_after_contribution_requires_new_final_manuscript(tmp_path):
    runtime, model = runtime_at(tmp_path, [
        contribute(focus="评价连续成像"), manuscript("当前读出支持可行性。"),
        calls(("read_more", {})), finish(),
        manuscript("新增时间对照使漂移和生物学变化可以区分。[@E1]"),
    ])
    readings = {}

    async def read_more(args):
        readings["new-original"] = snapshot("control", "时间对照识别漂移。")
        return ToolResult(ok=True, content={
            "source_id": "control",
            "passages": [{"passage_id": "p1", "text": "时间对照识别漂移。"}],
        })

    registry = ToolRegistry([Tool("read_more", "读取原文", NoArgs, read_more)])
    registry.evidence_views.append(lambda: readings)
    session_id = create(runtime)
    assert (await runtime.run(session_id, tools=registry)).status == "submitted"

    assert len(model.authors) == 2
    assert "时间对照识别漂移。" in json.dumps(payload(model.authors[-1]), ensure_ascii=False)
    latest = current(runtime, session_id)
    assert latest["revision"] == 2
    assert list(cited_ids(latest["note"])) == ["new-original"]
    assert runtime.budget.data["requests"][-1]["purpose"] == "completion"
    assert not model.script
