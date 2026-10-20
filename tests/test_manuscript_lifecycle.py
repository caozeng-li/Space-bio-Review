"""Exercise the model/tool loop that previously replaced manuscripts with goodbye text."""

import pytest
from pydantic import BaseModel

from scripts.fixtures.session import ScriptModel, reply, runtime, tool_call
from spacebio_review.authored_markdown import compile_blocks
from spacebio_review.prose import prose_inputs
from spacebio_review.publication.document import PublicationDocument, Section
from spacebio_review.publication.render import Renderer
from spacebio_review.tools import Tool, ToolRegistry, ToolResult
from spacebio_review.v5.records import SessionSettings, SessionTask


class Empty(BaseModel):
    pass


@pytest.mark.parametrize("intervening_read", [False, True])
async def test_only_explicit_publication_replaces_manuscript(tmp_path, intervening_read):
    original = "完整科学稿：形态读出提供描述性比较；平台适配仍是计划工作。"
    steps = [reply(tool_call(
        "publish_manuscript", {"title": "形态读出的评价", "content": original},
    ))]
    if intervening_read:
        steps.append(reply(tool_call("read_local", {})))
    steps.extend(
        [
            reply(content="稿件已发布且研究已完成。无需重新发布。"),
            reply(tool_call("finish_research", {"reason": "已有正式稿充分回答当前问题。"})),
        ]
    )
    model = ScriptModel(steps)
    rt = runtime(tmp_path, model, settings=SessionSettings(max_turns=8))
    readings = {}

    async def read_local(args):
        readings["evidence-extra"] = {
            "reference": {"source_id": "extra", "passage_id": "p1"},
            "title": "补充材料",
            "quote": "这条信息不改变已发布的判断。",
            "kind": "external",
            "reading_scope": "text",
            "material_version": "one",
        }
        return ToolResult(ok=True, content={"text": readings["evidence-extra"]["quote"]})

    registry = ToolRegistry([Tool("read_local", "读取补充材料", Empty, read_local)])
    registry.evidence_views.append(lambda: readings)
    sid = rt.create(SessionTask(role="researcher", application={}, objective="提交局部科学稿"))
    outcome = await rt.run(sid, tools=registry)
    assert outcome.status == "submitted", outcome
    note = rt.store.note(outcome.note_ref)
    assert note["note"]["title"] == "形态读出的评价"
    assert prose_inputs(note["note"])["body"] == original
    assert note["revision"] == 1
    assert not model.steps  # finishing is an explicit action, never an inferred manuscript


async def test_unavailable_acquisition_is_a_readable_tool_outcome(tmp_path):
    model = ScriptModel(
        [
            reply(tool_call("acquire_source", {})),
            reply(content="本次未取得正文；无法由当前材料回答具体参数。"),
        ]
    )
    rt = runtime(tmp_path, model)

    async def unavailable(args):
        return ToolResult(
            ok=True,
            content={
                "availability": "unavailable",
                "limitations": ["HTTP_403"],
                "reading": None,
            },
        )

    registry = ToolRegistry([Tool("acquire_source", "获取正文", Empty, unavailable)])
    sid = rt.create(
        SessionTask(role="source_reader", application={}, objective="解释资料的具体参数")
    )
    outcome = await rt.run(sid, tools=registry)
    assert outcome.status == "submitted", outcome
    assert "未取得正文" in prose_inputs(rt.store.note(outcome.note_ref)["note"])["body"]


def test_report_headings_are_authored_by_chief_without_added_nesting():
    section = Section(
        section_id="body", title="",
        blocks=compile_blocks("## 综合评价\n\n评审正文。\n\n## 科学目标\n\n具体论据。"),
    )
    before = section.model_dump()
    renderer = Renderer(PublicationDocument(main=[section], recommendation="support"))
    rendered = renderer.section(section, "zh")
    assert rendered.startswith("## 综合评价")
    assert "## 科学目标" in rendered
    assert "###" not in rendered
    assert section.model_dump() == before
