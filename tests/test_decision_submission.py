"""Decision generation through the actual provider, persistence and report handoff."""

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI

from spacebio_review.models.provider import DashScopeModel, ModelError
from spacebio_review.review.evaluation import get_evaluation_system
from spacebio_review.v5.assessment import AssessmentSubmission
from spacebio_review.v5.budget import Budget, SessionAllowance
from spacebio_review.v5.config import load_config
from spacebio_review.v5.records import SessionTask
from spacebio_review.v5.session import ResearchRuntime


def fixture(tmp_path, identity="gbt42048_2022"):
    system = get_evaluation_system(identity)
    paragraphs = [
        '**项目价值。** 研究对象及安排中的“具体内容”。',
        "主要支持与限制：保留原文条件。",
        "整体取舍：现有计划值得探索。",
    ]
    payload = {
        # Deliberately use the provider's alphabetical order, not narrative order.
        "overall_judgment": {
            "overall_tradeoff": paragraphs[2],
            "project_and_value": paragraphs[0],
            "support_and_limits": paragraphs[1],
        },
        "dimensions": {
            c.id: {"reason": "本指标的独立依据。", **(
                {"score": c.max_score // 2} if system.score_grades
                else {"grade": "C", "confidence": None}
            )} for c in system.criteria
        },
    }
    if system.score_grades:
        payload["safety_veto"] = {"applies": False, "reason": "未见否决事实。"}
    else:
        payload.update(support_scope="探索性目标。", recommendation="support")
    sent = []

    def respond(request):
        body = json.loads(request.content)
        sent.append(body)
        content = json.dumps(payload, ensure_ascii=False) if "response_format" in body \
            else "本指标正文。"
        return httpx.Response(200, json={
            "id": "local", "object": "chat.completion", "created": 1, "model": "fixture",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
        })

    client = AsyncOpenAI(
        api_key="fixture", base_url="https://example.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)), max_retries=0,
    )
    budget = Budget(tmp_path)
    config = load_config("config/runtime.yaml").models["scientific"]
    model = DashScopeModel(config, client=client, budget=budget)
    runtime = ResearchRuntime(
        tmp_path, {"scientific": model}, {"chief": "scientific", "researcher": "scientific"},
        budget=budget,
    )
    state = runtime.load(runtime.create(
        SessionTask(role="chief", objective="提交评价", application={"text": "完整申请。"}),
        allowance=SessionAllowance(completion_calls=0),
    ))
    chief = SimpleNamespace(runtime=runtime, _manifest=lambda _: {
        "topic_ids": [], "review_context": {"evaluation": system.snapshot(), "admission": {}},
    })
    return runtime, model, AssessmentSubmission(chief, state.session_id), state, payload, sent


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["gbt42048_2022", "general"])
async def test_strict_decision_preserves_authored_text_and_report_contract(tmp_path, identity):
    runtime, model, submission, state, payload, sent = fixture(tmp_path, identity)
    try:
        await runtime._formal_submission(
            state, model, operation="submit_assessment", submission=submission,
        )
        assert len(sent) == 1  # No second generation or format repair.
        response_format = sent[0]["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["strict"] is True
        schema = response_format["json_schema"]["schema"]
        assert schema == submission.response_schema(state, "submit_assessment")
        assert set(schema["$defs"]["OverallJudgment"]["required"]) == set(
            payload["overall_judgment"]
        )
        assert sent[0]["enable_thinking"] is False
        artifact = submission._artifact(state.assessment_ref)
        assessment = artifact["assessment"]
        expected = "\n\n".join(payload["overall_judgment"][key] for key in (
            "project_and_value", "support_and_limits", "overall_tradeoff",
        ))
        assert assessment["overall_judgment"] == expected
        assert assessment["dimensions"] == payload["dimensions"]
        if identity == "gbt42048_2022":
            assert assessment["safety_veto"] == payload["safety_veto"]
            assert assessment["total_score"] == sum(
                row["score"] for row in payload["dimensions"].values()
            )
        else:
            assert assessment["recommendation"] == payload["recommendation"]
        raw = runtime.store.author_text(artifact["author_sources"]["decision"]["author_text_ref"])
        assert json.loads(raw["content"]) == payload

        operation = "write_section:" + submission.system.criteria[0].id
        await runtime._formal_submission(state, model, operation=operation, submission=submission)
        common = json.loads(sent[1]["messages"][1]["content"])
        assert common["decision"]["overall_judgment"] == expected
        assert "response_format" not in sent[1]
        assert len(runtime.budget.data["requests"]) == 2

        tool = {"type": "function", "function": {
            "name": "read_materials", "parameters": {"type": "object", "properties": {}},
        }}
        with runtime.budget.scope(state.session_id, purpose="research"):
            await model.complete([{"role": "user", "content": "继续研究。"}], [tool])
        assert sent[2]["tools"] == [tool]
        assert "response_format" not in sent[2]
    finally:
        await model.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["support_and_limits", "dimensions"])
async def test_missing_content_is_not_filled_or_retried(tmp_path, missing):
    runtime, model, submission, state, payload, sent = fixture(tmp_path)
    if missing == "dimensions":
        del payload[missing]
    else:
        del payload["overall_judgment"][missing]
    try:
        with pytest.raises(ModelError, match="submit_assessment_invalid:ValidationError"):
            await runtime._formal_submission(
                state, model, operation="submit_assessment", submission=submission,
            )
        assert len(sent) == 1
        assert state.assessment_ref is None
    finally:
        await model.close()
