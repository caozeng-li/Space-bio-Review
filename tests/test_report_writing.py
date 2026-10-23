"""Report-only integration: real runtime and publication, scripted model responses."""

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI

from spacebio_review.evidence.reading_render import render_originals
from spacebio_review.models.config import ModelConfig
from spacebio_review.models.provider import ChatReply, DashScopeModel, ModelError
from spacebio_review.models.scheduling import RequestScheduler, ScheduledModel
from spacebio_review.models.session import RunModel
from spacebio_review.persistence import write_json
from spacebio_review.publication import Renderer, compile_publication
from spacebio_review.review.evaluation import get_evaluation_system
from spacebio_review.review.scoring import finalize_assessment
from spacebio_review.v5.assembly import compile_manuscript
from spacebio_review.v5.assessment import AssessmentSubmission
from spacebio_review.v5.budget import Budget, SessionAllowance
from spacebio_review.v5.records import SessionTask
from spacebio_review.v5.session import ResearchRuntime

SYSTEM = get_evaluation_system("gbt42048_2022")
HANDLE = "evidence-0123456789abcdef"
LINK = f"[原文](evidence:{HANDLE})"


class ReportModel:
    def __init__(self):
        self.config = ModelConfig(
            model="report-fixture", base_url="https://example.invalid/v1",
            max_tokens=16384, context_window=262144, structured_output="json_object",
        )
        self.requests = []
        self.interrupt_at = None
        self.report_fault = None

    async def complete(self, messages, tools=None, *, response_schema=None, max_tokens=None):
        task = json.loads(messages[-2]["content"])
        operation = task["operation"]
        if operation == self.interrupt_at:
            raise ModelError("interrupted_report_probe", category="exploration")
        self.requests.append((operation, messages, max_tokens))
        if operation == "synthesize_report":
            common = json.loads(messages[1]["content"])
            content = "## 综合评价\n\n" + common["decision"]["overall_judgment"] + "\n\n"
            criteria = SYSTEM.criteria[:-1] if self.report_fault == "missing_chapter" \
                else SYSTEM.criteria
            for index, item in enumerate(criteria):
                content += "## " + item.label + "\n\n"
                content += "**统稿后的判断。** 来源《来源》支持该判断。[证据1]\n\n"
                if index == 0:
                    content += "实验单元应区分小鼠、隐窝与培养孔。\n\n"
                    content += "| 对象 | 判断 |\n| --- | --- |\n| 本项目 | 有价值 |\n\n"
            if self.report_fault == "unknown_citation":
                content += "[evidence-fedcba9876543210]"
        else:
            content = "**初稿。** 这是本指标的论证，依据《来源》。[证据1]"
            if operation == "write_section:experiment_feasibility":
                content += "\n\n实验单元应区分小鼠、隐窝与培养孔。"
        return ChatReply({"role": "assistant", "content": content})


def fixture(tmp_path):
    model = ReportModel()
    budget = Budget(tmp_path)
    runtime = ResearchRuntime(
        tmp_path, {"scientific": model, "assembly": model},
        {"chief": "scientific", "researcher": "scientific"}, budget=budget,
    )
    chief_id = runtime.create(
        SessionTask(role="chief", objective="只验证报告", application={"text": "完整申请。"}),
        allowance=SessionAllowance(completion_calls=0),
    )
    state = runtime.load(chief_id)
    state.settings.max_turns = 2
    state.turns_used = 2
    state.research_finished = True
    state.assessment_ref = "assessment-fixture"
    state.evidence = {HANDLE: {
        "reference": {"source_id": "source-one", "passage_id": "p1"},
        "title": "来源", "quote": "相关的完整原文。", "kind": "external",
    }}
    runtime.store.save(state)
    assessment = finalize_assessment({
        "overall_judgment": "已有依据支持有价值的探索。",
        "dimensions": {
            item.id: {"score": item.max_score // 2, "reason": "有依据的中等评价。"}
            for item in SYSTEM.criteria
        },
        "safety_veto": {"applies": False, "reason": "没有否决事实。"},
    }, SYSTEM)
    review_input = {
        "application": {"text": "完整申请。"},
        "expert_opinions": [{"body": "专家最终判断。" + LINK}],
        "evaluation": SYSTEM.snapshot(), "originals": render_originals(state.evidence),
        "evaluation_instructions": {"legacy_delivery": "一次写完整报告正文。"},
    }
    write_json(runtime.store._path("assessments", state.assessment_ref), {
        "assessment": assessment, "review_input": review_input, "author_sources": {},
    })
    chief = SimpleNamespace(
        runtime=runtime, _manifest=lambda _: {"review_context": {"evaluation": SYSTEM.snapshot()}},
    )
    return runtime, model, AssessmentSubmission(chief, chief_id), state, assessment


@pytest.mark.asyncio
@pytest.mark.parametrize("research_finished", [True, False])
async def test_report_resumes_sections_and_publishes_one_coherent_structure(
    tmp_path, research_finished,
):
    runtime, model, submission, state, decision = fixture(tmp_path)
    if not research_finished:
        state.research_finished = False
        state.turns_used = state.settings.max_turns - 1
        runtime.store.save(state)
    third = "write_section:" + SYSTEM.criteria[2].id
    model.interrupt_at = third
    outcome = await runtime.run(state.session_id, submission=submission)
    assert outcome.status == "partial"
    assert len(submission.writing.load(runtime.load(state.session_id))["drafts"]) == 2
    # Recreate the authoring object from persisted progress; completed units stay intact.
    state = runtime.load(state.session_id)
    state.status = "ready"
    runtime.store.save(state)
    model.interrupt_at = None
    submission = AssessmentSubmission(submission.chief, state.session_id)
    outcome = await runtime.run(state.session_id, submission=submission)
    assert outcome.status == "submitted"
    operations = [row[0] for row in model.requests]
    assert operations == [
        *["write_section:" + item.id for item in SYSTEM.criteria], "synthesize_report",
    ]
    assert len({row[1][1]["content"] for row in model.requests}) == 1
    assert all("](evidence:" not in row[1][1]["content"] for row in model.requests)
    assert [row[2] for row in model.requests] == [4096] * 6 + [16384]
    first = json.loads(model.requests[0][1][-2]["content"])
    second = json.loads(model.requests[1][1][-2]["content"])
    assert "一次写完整报告" not in model.requests[-1][1][1]["content"]
    assert "一次完成全文统稿" in model.requests[-1][1][-1]["content"]
    assert json.loads(model.requests[-1][1][1]["content"])["originals"]
    assert not first["drafts"]
    assert len(second["drafts"]) == 1
    result = submission.result()
    assert {
        key: value for key, value in result["assessment"].items() if key != "sections"
    } == decision
    document, audit = compile_publication(
        result["assessment"], evidence=result["evidence"], author_sources=result["author_sources"],
    )
    report = Renderer(document).render("zh")
    assert [s.section_id for s in document.main] == [
        "summary", "grades", *[item.id for item in SYSTEM.criteria],
    ]
    assert report.count("## 综合评价") == 1
    assert "**统稿后的判断。**" in report and "| 本项目 | 有价值 |" in report
    assert "**初稿。**" not in report
    assert report.count("实验单元应区分小鼠、隐窝与培养孔。") == 1
    assert "实验单元" in report.split("## 创新性")[0]
    assert "evidence-" not in report
    assert len(document.numbering) == 1
    assert all(row.get("author_text_ref") for row in audit["paragraphs"].values()
               if row.get("author_paragraph_id"))
    work = submission.writing.load(runtime.load(state.session_id))
    assert set(work) == {"drafts", "report"}
    assert {row["author_text_ref"] for row in audit["paragraphs"].values()
            if row.get("author_paragraph_id")} == {work["report"]}
    assert "evidence-" not in runtime.store.author_text(work["report"])["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing_chapter", "unknown_citation"])
async def test_incomplete_synthesis_preserves_drafts_for_retry(tmp_path, failure):
    runtime, model, submission, state, _ = fixture(tmp_path)
    model.report_fault = failure
    outcome = await runtime.run(state.session_id, submission=submission)
    assert outcome.status == "partial"
    state = runtime.load(state.session_id)
    assert state.report_ref is None
    assert len(submission.writing.load(state)["drafts"]) == 6
    state.status = "ready"
    runtime.store.save(state)
    model.report_fault = None
    outcome = await runtime.run(state.session_id, submission=submission)
    assert outcome.status == "submitted"
    operations = [row[0] for row in model.requests]
    assert len([op for op in operations if op.startswith("write_section:")]) == 6
    assert operations.count("synthesize_report") == 2


def test_report_can_select_displayed_original_not_cited_by_previous_expert(tmp_path):
    runtime, _, submission, state, _ = fixture(tmp_path)
    other = "evidence-2222222222222222"
    state.evidence[other] = {
        **state.evidence[HANDLE], "reference": {"source_id": "source-one", "passage_id": "p2"},
        "quote": "同一阅读窗口内此前未引用的另一段原文。",
    }
    artifact = submission._artifact(state.assessment_ref)
    artifact["review_input"]["originals"] = render_originals(state.evidence)
    write_json(runtime.store._path("assessments", state.assessment_ref), artifact)
    _, readings = submission.authoring_input(state, "write_section:scientific_objectives")
    assert other in readings
    ref = runtime.store.save_author_text(
        state, "作者采用了另一段原文。[证据2]", "submit_research",
        absorbed_through=0, readings=readings,
    )
    body = compile_manuscript(runtime.store.author_text(ref))
    assert body["blocks"][0]["children"][-1]["target"] == other


@pytest.mark.asyncio
async def test_per_request_output_limit_matches_provider_and_budget(tmp_path):
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "local", "object": "chat.completion", "created": 1, "model": "fixture",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "正文"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        })

    client = AsyncOpenAI(
        api_key="fixture", base_url="https://example.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)), max_retries=0,
    )
    budget = Budget(tmp_path)
    budget.register("report", SessionAllowance(completion_calls=0))
    scheduler = RequestScheduler()
    model = DashScopeModel(ReportModel().config, client=client, budget=budget, scheduler=scheduler)
    route = ScheduledModel(RunModel(model, {}), scheduler, "chief")
    try:
        with budget.scope("report", purpose="completion"):
            await route.complete([{"role": "user", "content": "正文"}], max_tokens=2048)
            await route.complete([{"role": "user", "content": "正文"}])
        assert [row["max_tokens"] for row in sent] == [2048, 16384]
        assert [row["output_reserve"] for row in budget.data["requests"]] == [2048, 16384]
        assert [row["output_reserve"] for row in scheduler.records] == [2048, 16384]
        assert model.config.max_tokens == 16384
    finally:
        await model.close()
