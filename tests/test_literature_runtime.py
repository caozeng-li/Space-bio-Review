"""Literature ends research naturally and submits once through the native contract."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from spacebio_review.agents.context import LITERATURE_INSTRUCTION, TokenEstimator, build_messages
from spacebio_review.agents.contracts import ProductBindingError, SubmissionContract
from spacebio_review.agents.runtime import AgentRuntime
from spacebio_review.agents.skills import SkillLibrary
from spacebio_review.agents.types import (
    AgentTask,
    ContextView,
    Material,
    RoleProfile,
    WorkingMemory,
)
from spacebio_review.models.config import load_config
from spacebio_review.models.provider import ChatReply
from spacebio_review.tools import Tool, ToolRegistry, ToolResult
from spacebio_review.v4.budget import Budget
from spacebio_review.v4.config import ReportingConfig, V4Config
from spacebio_review.v4.context import TaskContext
from spacebio_review.v4.records import Records


class Answer(BaseModel):
    answer: str


class Empty(BaseModel):
    pass


class ObservedBudget(Budget):
    def __init__(self, root):
        super().__init__(
            Records(root),
            V4Config(
                max_model_calls=20,
                max_input_tokens=200000,
                max_output_tokens=200000,
                completion_reserve_calls=0,
                completion_reserve_seconds=0,
            ),
            ReportingConfig(),
        )
        self.previews = []

    def refresh_completion(self, **kwargs):
        self.previews.append(dict(kwargs))
        super().refresh_completion(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("formal_result", ["valid", "missing_field", "binding_error"])
async def test_natural_literature_completion_is_native_without_structured_memory(
    tmp_path,
    monkeypatch,
    formal_result,
):
    captured = []
    budget = ObservedBudget(tmp_path)
    context = TaskContext(compact_sources=True, structured_refs=False, formal_cites=True)
    config = load_config("config/runtime.yaml")
    research_config = config.models["research"].model_copy(update={"retries": 0})
    formal_config = config.models["question_generator"].model_copy(update={"retries": 0})
    original = "NEW_READ_ORIGINAL " * 1500

    async def read(args):
        context.add(
            {
                "exact": {
                    "reference": {"source_id": "paper", "passage_id": "p1"},
                    "title": "Paper",
                    "kind": "external",
                    "quote": original,
                    "locator": "p1",
                    "material_version": "v1",
                    "reading_scope": "text",
                }
            },
            "E",
        )
        return ToolResult(
            ok=True,
            content={
                "source_id": "paper",
                "passages": [
                    {"passage_id": "p1", "text": original},
                ],
            },
        )

    def assemble(draft):
        if formal_result == "binding_error":
            raise ProductBindingError(reason="inline_reference_not_bound:E99")
        return draft.model_dump()

    contract = SubmissionContract(
        Answer,
        Answer,
        assemble=assemble,
        task_context=context,
        refresh=lambda _: None,
        version="literature-test",
        completion="natural",
        native_prompt="NATIVE_LITERATURE_OUTPUT",
    )

    def forbidden_detach(*args, **kwargs):
        raise AssertionError("Natural literature must not detach citations")

    monkeypatch.setattr("spacebio_review.agents.runtime.detach_citations", forbidden_detach)

    class Model:
        def __init__(self, model_config, formal=False):
            self.config, self.formal = model_config, formal

        async def complete(self, messages, tools=None, response_schema=None):
            ticket = budget.reserve(self.config, messages, tools, response_schema)
            captured.append(
                {
                    "formal": self.formal,
                    "messages": deepcopy(messages),
                    "tools": deepcopy(tools),
                    "schema": deepcopy(response_schema),
                }
            )
            if self.formal:
                content = {} if formal_result == "missing_field" else {"answer": "Scoped finding"}
                reply = ChatReply(message={"role": "assistant", "content": json.dumps(content)})
            elif sum(not call["formal"] for call in captured) == 1:
                reply = ChatReply(
                    message={
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "read-1",
                                "type": "function",
                                "function": {"name": "read", "arguments": "{}"},
                            }
                        ],
                    },
                    finish_reason="tool_calls",
                )
            else:
                reply = ChatReply(message={"role": "assistant", "content": "RESEARCH_FINISHED"})
            budget.settle(ticket, reply)
            return reply

    runtime = AgentRuntime(
        {
            "research": Model(research_config),
            "formal/literature/normal": Model(formal_config, formal=True),
        },
        SkillLibrary(Path("skills")),
    )
    runtime.scheduler.request_budget = budget
    task = AgentTask(
        task_id="literature",
        kind="literature",
        phase="local",
        objective="Read",
        expected_product="Answer",
        mode="research",
        max_model_turns=4,
        max_actions=4,
        max_repairs=0,
        max_truncation_recoveries=0,
    )
    profile = RoleProfile(
        agent_id="reader", responsibility="Research", model="research", allowed_tools=["read"]
    )
    result = await runtime.run_task(
        task,
        profile,
        ContextView(agent_id="reader", phase="local"),
        ToolRegistry([Tool("read", "Read", Empty, read)]),
        contract,
    )
    assert result.status == ("completed" if formal_result == "valid" else "failed"), result.reason
    assert len(captured) == 3
    assert len(budget.data["requests"]) == 3
    assert sum(call["formal"] for call in captured) == 1
    assert all("submit_result" not in str(call) for call in captured)
    for call in captured:
        assert LITERATURE_INSTRUCTION in call["messages"][0]["content"]
        assert runtime.evaluation.body not in str(call["messages"])
    assert not context.structured_refs
    formal = captured[-1]
    assert formal["tools"] is None
    assert formal["schema"] == contract.schema()
    assert "NATIVE_LITERATURE_OUTPUT" in str(formal["messages"])
    assert "所有输出文本引用使用" not in str(formal["messages"])
    assert "NEW_READ_ORIGINAL" in str(formal["messages"])
    assert "RESEARCH_FINISHED" in str(formal["messages"])
    assert (
        max(p["input_tokens"] for p in budget.previews) > budget.previews[0]["input_tokens"] + 1000
    )
    assert (
        budget.previews[-1]["input_tokens"]
        == TokenEstimator(None, formal_config.image_tokens).measure(
            formal["messages"],
            response_schema=formal["schema"],
        )["input_tokens"]
    )
    if formal_result != "valid":
        assert result.reason == "submission_repair_exhausted"
        assert result.product is None


def test_refresh_completion_updates_only_current_owner(tmp_path):
    budget = ObservedBudget(tmp_path)
    with budget.research_task("parent", calls=1, input_tokens=100, output_tokens=20):
        with budget.research_task("child", calls=2, input_tokens=200, output_tokens=40):
            budget.refresh_completion(input_tokens=750, output_tokens=30)
            assert budget.active["child"] == {"calls": 2, "input": 1500, "output": 60}
            assert budget.active["parent"] == {"calls": 1, "input": 100, "output": 20}
        assert set(budget.active) == {"parent"}


async def test_natural_answer_after_action_limit_keeps_actual_research_stop_reason(tmp_path):
    from spacebio_review.evidence.knowledge import (
        KnowledgeAnswer,
        KnowledgeQuestion,
        LiteratureConfig,
    )
    from spacebio_review.evidence.literature import KnowledgeSession
    from spacebio_review.evidence.store import SourceStore
    from spacebio_review.evidence.submissions import scientific_contract

    calls = []
    config = load_config("config/runtime.yaml")
    research_config = config.models["research"].model_copy(update={"retries": 0})
    formal_config = config.models["question_generator"].model_copy(update={"retries": 0})

    class Model:
        def __init__(self, model_config, *, formal=False):
            self.config, self.formal = model_config, formal

        async def complete(self, messages, tools=None, response_schema=None):
            calls.append(self.formal)
            if self.formal:
                assert "Calibration was measured at 37 C." in str(messages)
                return ChatReply(
                    message={
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "findings": [
                                    {
                                        "paragraphs": [
                                            {
                                                "parts": [
                                                    {
                                                "text": "Calibration at 37 C.",
                                                        "cites": ["E1"],
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ],
                                "scope": None,
                                "unknowns": [],
                            }
                        ),
                    }
                )
            return ChatReply(
                message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "read-calibration",
                            "type": "function",
                            "function": {
                                "name": "acquire_source",
                                "arguments": json.dumps(
                                    {
                                        "source_id": sid,
                                        "reading_purpose": "Read the calibration temperature.",
                                    }
                                ),
                            },
                        }
                    ],
                },
                finish_reason="tool_calls",
            )

    runtime = AgentRuntime(
        {
            "research": Model(research_config),
            "formal/literature/normal": Model(formal_config, formal=True),
        },
        SkillLibrary(Path("skills")),
    )

    def no_network(request):
        raise AssertionError("The fixed local original requires no network")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as client:
        source = tmp_path / "paper.md"
        source.write_text("# Methods\n\nCalibration was measured at 37 C.", encoding="utf-8")
        store = SourceStore(tmp_path / "sources", client, browser_fallback=False)
        sid = await store.add_local(source, kind="literature")
        runtime.models["task/rerank/normal"] = runtime.models["research"]
        session = KnowledgeSession(
            SimpleNamespace(
                runtime=runtime,
                store=store,
                discovery=None,
                index=None,
                rerank=False,
                config=LiteratureConfig(max_actions=3),
            ),
            KnowledgeQuestion(
                question="What calibration temperature was used?", existing_source_ids=[sid]
            ),
        )
        contract = scientific_contract(session.references, KnowledgeAnswer, session=session)
        task = AgentTask(
            task_id="literature-action-limit",
            kind="literature",
            phase="local",
            objective="Read the calibration conditions",
            expected_product="KnowledgeAnswer",
            mode="research",
            max_model_turns=4,
            max_actions=1,
            reserve_actions=0,
            reserve_model_turns=0,
            max_repairs=0,
            max_truncation_recoveries=0,
        )
        try:
            result = await runtime.run_task(
                task,
                RoleProfile(
                    agent_id="reader",
                    responsibility="Read",
                    model="research",
                    allowed_tools=["acquire_source"],
                ),
                ContextView(agent_id="reader", phase="local"),
                session.tools(),
                contract,
            )
        finally:
            await runtime.close()
    assert result.status == "completed", result.reason
    assert calls == [False, True]
    assert result.product["stop_reason"] == "action_budget_exhausted"
    assert result.product["findings"]
    assert len(result.product["evidence_resolution"]) == 1


@pytest.mark.parametrize(
    "kind,phase",
    [("literature", "local"), ("query", "knowledge_research"), ("visual", "literature_visual")],
)
def test_knowledge_context_keeps_consultation_goal_without_review_instructions(kind, phase):
    skills = SkillLibrary(Path("skills"))
    evaluation = skills.get("exploration-review")
    task = AgentTask(
        task_id="knowledge",
        kind=kind,
        phase=phase,
        objective="Read sources",
        expected_product="Knowledge",
    )
    messages = build_messages(
        task,
        RoleProfile(agent_id="knowledge", responsibility="Explain external research"),
        ContextView(
            agent_id="knowledge",
            phase=phase,
            materials=[
                Material(
                    source_id="knowledge-question",
                    essential=True,
                    data={
                        "assessment_focus": "Which preservation conditions were measured?",
                        "application_statements": {"original": "ORIGINAL_APPLICATION_STATEMENT"},
                    },
                )
            ],
        ),
        evaluation,
        {
            evaluation.name: evaluation.body,
            evaluation.name + "/notes": "REVIEW_REFERENCE",
            "literature-understanding": skills.get("literature-understanding").body,
        },
        skills.catalog([evaluation.name, "literature-understanding"]),
        WorkingMemory(),
        evaluation_core="SUPPORT_CATEGORY_INSTRUCTIONS",
    )
    body = json.dumps(messages, ensure_ascii=False)
    assert "Which preservation conditions were measured?" in body
    assert "ORIGINAL_APPLICATION_STATEMENT" in body
    assert "SUPPORT_CATEGORY_INSTRUCTIONS" not in body
    assert "REVIEW_REFERENCE" not in body
    assert evaluation.name not in body
    assert "findings" in body and "scope" in body
