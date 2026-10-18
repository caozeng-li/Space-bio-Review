"""Local author/delivery transitions; no providers, retrieval, or review orchestration."""

import json

import pytest
from pydantic import BaseModel, Field

from spacebio_review.models.config import ModelConfig
from spacebio_review.models.provider import ChatReply, ModelError
from spacebio_review.tools import ToolRegistry, ToolResult
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance
from spacebio_review.v5.records import ContributionSelection, ReportSelection, SessionTask
from spacebio_review.v5.session import ResearchRuntime


class LocalModel:
    def __init__(self, budget, name="author"):
        self.budget = budget
        self.config = ModelConfig(
            model=name, base_url="https://unused.invalid", max_tokens=128, retries=0
        )
        self.requests = []

    async def complete(self, messages, tools=None, *, response_schema=None):
        ticket = self.budget.reserve(
            self.config, messages=messages, tools=tools, response_schema=response_schema
        )
        self.requests.append((messages, tools, response_schema))
        if response_schema:
            message = {"role": "assistant", "content": '{"missing_fields": []}'}
        elif tools:
            message = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "finish",
                        "type": "function",
                        "function": {
                            "name": "finish_research",
                            "arguments": '{"reason": "已足够"}',
                        },
                    }
                ],
            }
        else:
            message = {
                "role": "assistant",
                "content": "对象与条件决定知识增量，现有材料支持开展研究。",
            }
        reply = ChatReply(
            message=message,
            prompt_tokens=100,
            completion_tokens=20,
            finish_reason="tool_calls" if tools else "stop",
        )
        self.budget.settle(ticket, reply)
        return reply


class Mapping(BaseModel):
    missing_fields: list[str] = Field(default_factory=list)


class LocalSubmission:
    instruction = "写出科学评价。"
    allow_research = False
    parsed_operations = {"submit_assessment"}

    def completed(self, state):
        return state.report_ref is not None

    def prepare(self, state, selection=None):
        if selection is not None:
            state.report_selection = selection

    def completion_operations(self, state):
        return [] if self.completed(state) else ["submit_assessment"]

    def completion_input(self, state, operation):
        return None

    def closing_instruction(self, state):
        return "以已有认识完成评价。"

    def authoring_contract(self, raw):
        return Mapping, "映射原稿。", {"system": "local"}

    def accept_authoring(self, state, raw_ref, raw, mapping, *, parse_ref):
        state.report_ref, state.status = raw_ref, "submitted"
        return ToolResult(ok=True, content={"report": raw_ref})


def runtime_at(root):
    budget = Budget(
        root,
        BudgetLimits(max_seconds=10000),
        token_counters={"author": lambda _: 92, "parser": lambda _: 92},
    )
    author, parser = LocalModel(budget), LocalModel(budget, "parser")
    allowance = SessionAllowance(
        max_input_tokens=10000,
        completion_input_tokens=1000,
        completion_output_tokens=256,
        completion_calls=2,
    )
    runtime = ResearchRuntime(
        root,
        {"author": author},
        {"researcher": "author", "chief": "author"},
        budget=budget,
        allowances={"researcher": allowance, "chief": allowance},
        delivery_parser=parser,
    )
    return runtime, author, parser


def create(runtime, *, role="researcher", budget_owner=None):
    session_id = runtime.create(
        SessionTask(role=role, objective="比较条件和读出", application="申请的实际安排"),
        budget_owner=budget_owner,
    )
    state = runtime.load(session_id)
    if state.inquiry_id:
        runtime.store.set_focus(state.inquiry_id, state.task.objective)
    state.research_finished = True
    runtime.store.save(state)
    return state


@pytest.mark.parametrize("role", ["researcher", "source_reader", "chief"])
async def test_final_author_uses_the_exact_messages_priced(tmp_path, monkeypatch, role):
    runtime, author, _ = runtime_at(tmp_path)
    owner = create(runtime).session_id if role == "source_reader" else None
    state = create(runtime, role=role, budget_owner=owner)
    submission = LocalSubmission() if role == "chief" else None
    measured = []
    measure = runtime.budget.measure

    def capture(config, messages, tools=None, **kwargs):
        measured.append((config.model, messages, tools))
        return measure(config, messages, tools, **kwargs)

    monkeypatch.setattr(runtime.budget, "measure", capture)
    context_calls = []

    def context():
        context_calls.append(1)
        return {"scientific_explanation": "完整新发现", "assessment": {"criteria": "科学价值"}}

    await runtime._write_phase(
        state,
        author,
        phase="final_manuscript",
        operation="submit_assessment" if submission else "submit_research",
        context=context,
        submission=submission,
    )
    actual = author.requests[0][0]
    assert measured[0][0] == "author"
    assert measured[0][1] is actual
    assert measured[0][2] == []
    assert len(context_calls) == 1
    payload = json.loads(actual[1]["content"])
    assert payload["working_context"]["scientific_explanation"] == "完整新发现"
    assert "resources" not in payload


def test_intermediate_manuscript_prices_remaining_final_input(tmp_path, monkeypatch):
    runtime, author, _ = runtime_at(tmp_path)
    state = create(runtime)
    research_messages = [{"role": "user", "content": "检索过程和工具历史"}]
    captured = []
    measure = runtime.budget.measure

    def capture(config, messages, tools=None, **kwargs):
        captured.append(messages)
        return measure(config, messages, tools, **kwargs)

    monkeypatch.setattr(runtime.budget, "measure", capture)

    def context():
        return {"new_result": "决定判断的科学结果"}

    runtime._completion_plan(
        state, author, research_messages, [{"function": "large_schema"}], context=context
    )
    assert captured[0] == runtime._writing_messages(
        state, "final_manuscript", "submit_research", context, None
    )
    assert "检索过程和工具历史" not in json.dumps(captured[0], ensure_ascii=False)




async def test_research_to_assessment_starts_new_cycle(tmp_path):
    runtime, author, parser = runtime_at(tmp_path)
    state = create(runtime)
    await runtime.run(state.session_id)
    old_count = runtime.budget.usage()["model_calls"]
    result = await runtime.run(state.session_id, submission=LocalSubmission())
    assert result.status == "submitted"
    assert runtime.budget.data["sessions"][state.session_id]["reserve_start"] == old_count
    assert len(author.requests) == 2
    assert len(parser.requests) == 1


async def test_assessment_followup_writes_and_maps_a_new_report(tmp_path):
    runtime, author, parser = runtime_at(tmp_path)
    state = create(runtime)
    submission = LocalSubmission()
    await runtime.run(state.session_id, submission=submission)
    old_report = runtime.load(state.session_id).report_ref
    outcome = await runtime.run(
        state.session_id, submission=submission, followup="结合新增认识重新评价"
    )
    assert outcome.status == "submitted"
    assert runtime.load(state.session_id).report_ref != old_report
    assert len(author.requests) == len(parser.requests) == 2
    assert runtime.budget.data["sessions"][state.session_id]["reserve_start"] == 2


async def test_chief_closes_dispatch_before_authoring_and_mapper_keeps_fixed_raw(tmp_path):
    runtime, author, parser = runtime_at(tmp_path)
    state = create(runtime, role="chief")
    events = []

    class Hook:
        consulting = False

        async def before_model(self, state):
            assert state.authoring_phase == "final_manuscript"
            events.append("author_boundary")
            runtime._observe(state, {"kind": "scientific_contribution", "text": "已选作者解释"})
            return True

        async def after_contribution(self, state, note_ref):
            raise AssertionError("Assessment does not create a research contribution.")

        async def finish(self, state, reason, wait_for):
            events.append("closed")
            assert author.requests == parser.requests == []
            return "finish"

    result = await runtime.run(
        state.session_id, submission=LocalSubmission(), collaboration=Hook()
    )
    assert result.status == "submitted"
    assert events == ["closed", "author_boundary"]
    assert "已选作者解释" in json.dumps(author.requests[0][0], ensure_ascii=False)
    assert len(author.requests) == len(parser.requests) == 1


async def test_chief_finish_freezes_explicit_selection_before_closing_dispatch(tmp_path):
    runtime, _, _ = runtime_at(tmp_path)
    state = create(runtime, role="chief")
    events = []
    selected = ReportSelection(main_sections=[ContributionSelection(
        inquiry_id="inquiry-selected", contribution_ref="note-original"
    )])

    class Submission(LocalSubmission):
        def prepare(self, state, selection=None):
            events.append("selection")
            assert selection == selected
            state.report_selection = selection

    class Hook:
        async def finish(self, state, reason, wait_for):
            assert state.report_selection == selected
            events.append("closed")
            return "finish"

    registry = runtime._tools(
        state, ToolRegistry(), submission=Submission(), collaboration=Hook()
    )
    result = await registry.execute("finish_research", json.dumps({
        "reason": "采用已形成的专业认识。", **selected.model_dump(mode="json"),
    }))
    assert result.ok
    assert events == ["selection", "closed"]
    assert runtime.load(state.session_id).report_selection == selected


async def test_waiting_does_not_prepare_report_and_expert_schema_stays_scientific(tmp_path):
    runtime, _, _ = runtime_at(tmp_path)
    state = create(runtime, role="chief")

    class Submission(LocalSubmission):
        def prepare(self, state, selection=None):
            raise AssertionError("Waiting has no report selection.")

    class Hook:
        async def finish(self, state, reason, wait_for):
            assert wait_for == ["request-science"]
            return "waiting"

    registry = runtime._tools(
        state, ToolRegistry(), submission=Submission(), collaboration=Hook()
    )
    result = await registry.execute("finish_research", json.dumps({
        "reason": "等待方法作者解释。", "wait_for": ["request-science"],
    }))
    assert result.ok and state.status == "waiting"
    assert state.report_selection is None and state.report_candidates == []
    expert = create(runtime)
    schema = next(row["function"]["parameters"] for row in runtime._tools(
        expert, ToolRegistry()
    ).schemas() if row["function"]["name"] == "finish_research")
    assert set(schema["properties"]) == {"reason", "wait_for"}


async def test_forced_report_prepares_candidates_before_close_and_uses_two_requests(tmp_path):
    runtime, author, parser = runtime_at(tmp_path)
    state = create(runtime, role="chief")
    events = []

    class Submission(LocalSubmission):
        def prepare(self, state, selection=None):
            assert selection is None
            state.report_candidates = [{"candidate_id": "S1", "contribution_ref": "note-old"}]
            events.append("prepared")

        def completion_input(self, state, operation):
            assert state.report_candidates[0]["contribution_ref"] == "note-old"
            return [
                {"role": "system", "content": "据固定候选写作并明确选择。"},
                {"role": "user", "content": json.dumps(state.report_candidates)},
            ]

    class Hook:
        consulting = False

        async def finish(self, state, reason, wait_for):
            assert events == ["prepared"]
            events.append("closed")
            return "finish"

        async def before_model(self, state):
            assert events == ["prepared", "closed"]
            return False

    result = await runtime.run(
        state.session_id, submission=Submission(), collaboration=Hook()
    )
    assert result.status == "submitted"
    assert len(author.requests) == len(parser.requests) == 1
    assert runtime.load(state.session_id).report_candidates[0]["contribution_ref"] == "note-old"


async def test_no_scientific_contribution_returns_partial_without_authoring(tmp_path):
    runtime, author, parser = runtime_at(tmp_path)
    state = create(runtime, role="chief")

    class Submission(LocalSubmission):
        def prepare(self, state, selection=None):
            raise ModelError("scientific_contribution_required", category="exploration")

    result = await runtime.run(state.session_id, submission=Submission())
    assert result.status == "partial" and result.reason == "scientific_contribution_required"
    assert author.requests == parser.requests == []
    assert runtime.load(state.session_id).report_ref is None


async def test_explicit_research_followup_clears_selection_before_new_preparation(tmp_path):
    runtime, author, parser = runtime_at(tmp_path)
    state = create(runtime, role="chief")
    submission = LocalSubmission()
    await runtime.run(state.session_id, submission=submission)
    old_report = runtime.load(state.session_id).report_ref
    old_raw = runtime.store.author_text(old_report)
    state = runtime.load(state.session_id)
    state.report_selection = ReportSelection(main_sections=[ContributionSelection(
        inquiry_id="inquiry-science", contribution_ref="note-old"
    )])
    state.report_candidates = [{"contribution_ref": "note-old"}]
    runtime.store.save(state)
    selected = ReportSelection(main_sections=[ContributionSelection(
        inquiry_id="inquiry-science", contribution_ref="note-new"
    )])

    class RevisedSubmission(LocalSubmission):
        allow_research = True

        def prepare(self, state, selection=None):
            if selection is not None:
                assert state.report_selection is None and state.report_candidates == []
                assert state.assessment_ref is None and state.report_ref is None
                state.report_selection = selection

    complete = author.complete

    async def choose(messages, tools=None, *, response_schema=None):
        reply = await complete(messages, tools, response_schema=response_schema)
        if tools:
            reply.message["tool_calls"][0]["function"]["arguments"] = json.dumps({
                "reason": "新认识改变选文。", **selected.model_dump(mode="json"),
            })
        return reply

    author.complete = choose
    outcome = await runtime.run(
        state.session_id, submission=RevisedSubmission(), followup="继续研究后重新选文。"
    )
    assert outcome.status == "submitted"
    revised = runtime.load(state.session_id)
    assert revised.report_selection == selected and revised.report_ref != old_report
    assert runtime.store.author_text(old_report) == old_raw
    assert len(parser.requests) == 2


async def test_author_and_parser_finish_with_actual_remaining_allowance(tmp_path):
    runtime, author, parser = runtime_at(tmp_path)
    state = create(runtime)
    with runtime.budget.scope(state.session_id, purpose="research"):
        ticket = runtime.budget.reserve(
            author.config, messages=[{"role": "user", "content": "已有研究"}]
        )
    # Actual provider usage leaves 500 tokens, below the 1000-token fixed reserve.
    runtime.budget.settle(ticket, ChatReply({}, prompt_tokens=9500, completion_tokens=20))
    result = await runtime.run(state.session_id, submission=LocalSubmission())
    assert result.status == "submitted"
    assert len(author.requests) == len(parser.requests) == 1
    assert runtime.budget.usage(state.session_id)["input_tokens"] == 9700
    mapped = json.loads(parser.requests[0][0][1]["content"])
    raw = runtime.store.author_text(runtime.load(state.session_id).report_ref)
    assert mapped["paragraphs"] == raw["paragraphs"]
    assert "task" not in mapped










@pytest.mark.parametrize("limit", ["window", "budget"])
async def test_completion_still_obeys_window_and_actual_allowance(tmp_path, limit):
    runtime, author, _ = runtime_at(tmp_path)
    if limit == "window":
        author.config.context_window = 150
    state = create(runtime)
    if limit == "budget":
        runtime.budget.data["sessions"][state.session_id]["allowance"]["max_input_tokens"] = 50
    outcome = await runtime.run(state.session_id)
    assert outcome.status == "partial"
    assert outcome.reason == (
        "context_window_exceeded" if limit == "window" else "session_input_token_budget_exhausted"
    )
    assert author.requests == []
    assert (await runtime.run(state.session_id)).status == "partial"
    assert author.requests == []






