"""A bounded initial-understanding and delegation case for the shared scientific probe."""

from __future__ import annotations

from pathlib import Path

import httpx

from spacebio_review.evidence.store import SourceStore
from spacebio_review.models.provider import ModelError
from spacebio_review.persistence import write_json
from spacebio_review.review.expertise import ExpertiseCatalogue
from spacebio_review.tools import ToolResult
from spacebio_review.v5.budget import SessionAllowance
from spacebio_review.v5.chief import ChiefReview
from spacebio_review.v5.topic import TopicResearch

OBJECTIVE = (
    "本次局部工作覆盖项目初始理解和第一批专家研究安排。输入为完整计划书，"
    "尚未提供外部文献。局部程序在首批研究安排提交后记录并停止，"
    "不执行专家子任务、外部检索、评分、摘要或报告。"
)


class NoDiscovery:
    async def search(self, *args, **kwargs):
        raise AssertionError("planning_probe_has_no_retrieval")


class CapturedPlanningChief(ChiefReview):
    """Use production tools and capture the submitted arrangement before execution."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.captured = []

    async def _research(self, chief_id, args):
        self.captured = [
            {
                "request": request.model_dump(mode="json"),
                "application_basis": self.topics.application_basis(
                    request.task.application_refs, self.access(chief_id)
                )
                if request.task
                else [],
            }
            for request in args.requests
        ]
        return ToolResult(ok=True, content={"local_probe_delegation_captured": True})


async def run_case(engine, config, output, name, application):
    """Use the caller's models and ledger; never create a separate live-call budget."""
    if engine.budget is None:
        raise ValueError("scientific_planning_requires_shared_budget")
    case_root = Path(output) / name
    case_root.mkdir(parents=True, exist_ok=False)
    application = Path(application).resolve()
    author = engine.models[engine.routes["chief"]]
    original_complete = author.complete
    requests, chief_id, chief, error = [], None, None, None

    def no_http(request):
        raise AssertionError("planning_probe_does_not_fetch_external_material")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_http)) as client:
        store = SourceStore(case_root / "sources", client, browser_fallback=False)
        application_id = await store.add_local(application)
        topics = TopicResearch(
            engine,
            store,
            NoDiscovery(),
            application_ids=[application_id],
            catalogue=ExpertiseCatalogue(config.expertise_config, engine.skills),
            external_research_enabled=False,
        )
        chief = CapturedPlanningChief(
            topics,
            evaluation_system="gbt42048_2022",
            admission={"status": "skipped", "reason": "local_initial_planning"},
        )
        chief_id = chief.create(OBJECTIVE, allowance=SessionAllowance(completion_calls=0))
        state = engine.load(chief_id)
        state.settings.max_turns = 5
        engine.store.save(state)
        write_json(
            case_root / "input.json",
            {
                "case": name,
                "chief_id": chief_id,
                "application": application.as_posix(),
                "application_id": application_id,
                "objective": OBJECTIVE,
                "author_model": author.config,
                "shared_budget": engine.budget.path.as_posix(),
                "planned_author_requests": 3,
                "external_materials": [],
                "children_executed": 0,
            },
        )

        async def bounded_complete(messages, tools=None, *, response_schema=None):
            if chief.captured:
                raise ModelError(
                    "local_probe_delegation_captured", category="exploration", request_sent=False
                )
            if len(requests) >= 3:
                raise ModelError(
                    "local_probe_planning_author_limit", category="exploration", request_sent=False
                )
            number = len(requests) + 1
            request = {
                "session_id": chief_id,
                "messages": messages,
                "tools": tools,
                "response_schema": response_schema,
            }
            requests.append(number)
            write_json(case_root / f"request-{number:02d}.json", request)
            response = await original_complete(messages, tools, response_schema=response_schema)
            write_json(
                case_root / f"response-{number:02d}.json",
                {
                    "finish_reason": response.finish_reason,
                    "message": {
                        key: response.message.get(key) for key in ("role", "content", "tool_calls")
                    },
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                },
            )
            return response

        author.complete = bounded_complete
        try:
            result = await chief.run(chief_id)
            write_json(case_root / "result.json", result)
        except Exception as exc:
            error = {"type": type(exc).__name__, "reason": str(exc)}
        finally:
            author.complete = original_complete
            state = engine.load(chief_id)
            write_json(case_root / "delegation.json", chief.captured)
            if state.note_ref:
                write_json(
                    case_root / "application-understanding.json", engine.store.note(state.note_ref)
                )
            delegation_errors = [
                row["output"]["result"]
                for row in state.observations
                if row.get("name") == "research_topics" and not row["output"]["result"]["ok"]
            ]
            if delegation_errors:
                write_json(case_root / "delegation-errors.json", delegation_errors)
            engine.budget.finish(chief_id)
            summary = {
                "case": name,
                "chief_id": chief_id,
                "status": "captured" if chief.captured else "incomplete",
                "session_reason": state.reason,
                "note_ref": state.note_ref,
                "captured_tasks": len(chief.captured),
                "author_attempts": len(requests),
                "usage": engine.budget.usage(chief_id),
                "error": error,
                "children_executed": 0,
                "retrieval_requests": 0,
                "scientific_quality": "Requires reading the saved understanding and delegation.",
            }
            write_json(case_root / "summary.json", summary)
    return summary
