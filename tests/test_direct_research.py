"""Real evidence tools and final assessment input, without external services."""

import json
from types import SimpleNamespace

import httpx
import pytest

from spacebio_review.evidence.store import SourceStore
from spacebio_review.models.config import ModelConfig
from spacebio_review.models.provider import ChatReply
from spacebio_review.prose import compile_authored_body
from spacebio_review.review.evaluation import get_evaluation_system
from spacebio_review.v5.assessment import AssessmentSubmission
from spacebio_review.v5.records import ResearchNote, SessionTask
from spacebio_review.v5.session import ResearchRuntime
from spacebio_review.v5.topic import TopicResearch, TopicTask


class ReaderModel:
    config = ModelConfig(model="fixture", base_url="https://example.invalid/v1")

    def __init__(self):
        self.requests = []

    async def complete(self, messages, tools=None, **kwargs):
        self.requests.append(messages)
        if kwargs.get("response_schema"):
            content = '{"annotations": {}}'
        else:
            content = "原文记载设备已有自动换液能力。"
        return ChatReply({"role": "assistant", "content": content})


@pytest.mark.asyncio
async def test_direct_acquisition_and_explicit_reader_use_separate_paths(tmp_path):
    model = ReaderModel()
    runtime = ResearchRuntime(
        tmp_path / "run", {"scientific": model, "assembly": model},
        {"researcher": "scientific", "source_reader": "scientific"},
    )
    async with httpx.AsyncClient() as client:
        store = SourceStore(tmp_path / "sources", client)
        path = tmp_path / "device.md"
        path.write_text(
            "# 装置\n\n设备已有自动换液能力。\n\n## 使用\n\n培养过程持续十天。", "utf-8",
        )
        sid = await store.add_local(path, kind="literature")
        topics = TopicResearch(runtime, store, None, application_ids=[])
        topic_id = topics.create(TopicTask(responsibility="研究设备功能", source_ids=[sid]))
        registry = topics.tools(topic_id)
        result = await registry.execute("acquire_source", json.dumps({
            "source_id": sid, "reading_purpose": "自动换液能力",
        }))
        assert result.ok
        assert "设备已有自动换液能力" in json.dumps(result.content, ensure_ascii=False)
        assert topics.access(topic_id).reading_records()[sid].passage_ids
        assert not model.requests
        assert not topics._task(topic_id)["reading_ids"]
        assert registry.manuscript_tokens("acquire_source", "{}") == 0

        # Continuing the expert's reading also keeps the original source identity.
        doc = store.retained(sid)
        last = doc.passages[-1]
        continued = await registry.execute("read_materials", json.dumps({
            "source_id": sid, "passage_ids": [last.passage_id],
        }))
        assert continued.ok
        assert last.text in json.dumps(continued.content, ensure_ascii=False)

        delegated = await registry.execute("read_source_for_question", json.dumps({
            "source_id": sid, "question": "解释装置的自动换液能力",
            "passage_ids": [doc.passages[1].passage_id],
        }))
        assert delegated.ok, delegated.error
        assert delegated.content["note"]
        assert delegated.content["originals"]
        assert len(topics._task(topic_id)["reading_ids"]) == 1
        assert model.requests
        await topics.close()


@pytest.mark.parametrize("with_chief_draft", [False, True])
def test_assessment_uses_expert_opinion_and_originals_without_chief_restatement(
    tmp_path, with_chief_draft,
):
    model = ReaderModel()
    runtime = ResearchRuntime(
        tmp_path, {"scientific": model},
        {"chief": "scientific", "researcher": "scientific"},
    )
    system = get_evaluation_system("gbt42048_2022")
    expert_id = runtime.create(SessionTask(
        role="researcher", objective="设备能力", application={"text": "完整申请"},
    ))
    expert = runtime.load(expert_id)
    chief_id = runtime.create(SessionTask(
        role="chief", objective="综合判断", application={"text": "完整申请"},
    ))
    chief_state = runtime.load(chief_id)

    def save(state, key, text, original):
        state.evidence[key] = {
            "reference": {"source_id": key, "passage_id": "p1"},
            "title": "原始资料", "quote": original, "kind": "external",
        }
        runtime.store.save_note(state, ResearchNote(
            title="研究意见", body=compile_authored_body(f"{text} [原文](evidence:{key})"),
        ), absorbed_through=0)
        return state.note_ref

    expert_ref = save(expert, "evidence-1111111111111111", "专家最终意见", "专家实际阅读的原文")
    if with_chief_draft:
        save(chief_state, "evidence-2222222222222222", "过早形成的整体判断", "主审独立研究的原文")
    chief = SimpleNamespace(
        runtime=runtime,
        topics=SimpleNamespace(contribution_refs=lambda _: [expert_ref]),
        _manifest=lambda _: {
            "topic_ids": [expert.inquiry_id],
            "review_context": {"evaluation": system.snapshot()},
        },
    )
    submission = AssessmentSubmission(chief, chief_id)
    messages, references = submission.authoring_input(chief_state, "submit_assessment")
    view = json.loads(messages[1]["content"])
    text = json.dumps(view, ensure_ascii=False)
    assert "chief_opinion" not in view
    assert "过早形成的整体判断" not in text
    assert "专家最终意见" in text and "完整申请" in text
    assert "专家实际阅读的原文" in text
    assert "evidence-1111111111111111" in references
    if with_chief_draft:
        assert "主审独立研究的原文" in text
        assert "evidence-2222222222222222" in references
