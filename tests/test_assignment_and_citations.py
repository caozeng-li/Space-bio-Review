"""Application premises and source attribution across the actual handoff interfaces."""

import json
from types import SimpleNamespace

import httpx
import pytest
from test_direct_research import ReaderModel

from spacebio_review.authored_markdown import Node, plain_text
from spacebio_review.evidence.store import SourceStore
from spacebio_review.prose import cited_ids, compile_authored_body, render_authored_body
from spacebio_review.v5.assembly import compile_manuscript
from spacebio_review.v5.chief import topic_batch_type
from spacebio_review.v5.citation_writing import author_messages
from spacebio_review.v5.records import SessionTask
from spacebio_review.v5.session import ResearchRuntime
from spacebio_review.v5.topic import ApplicationRef, TopicResearch


def test_assignment_schema_offers_catalogue_ids_and_keeps_task_contract():
    batch = topic_batch_type(SimpleNamespace(entries={"space-operations": None, "organoids": None}))
    schema = batch.model_json_schema()
    assert schema["$defs"]["TopicAssignment"]["properties"]["expertise"]["items"]["enum"] == [
        "space-operations", "organoids",
    ]
    selected = batch.model_validate({"requests": [{
        "task": {
            "responsibility": "在轨培养与地面制备怎样衔接？", "expertise": ["space-operations"],
        },
    }]})
    assert selected.requests[0].task.expertise == ["space-operations"]


@pytest.mark.asyncio
async def test_application_anchor_carries_original_stage_and_deduplicates_section(tmp_path):
    model = ReaderModel()
    runtime = ResearchRuntime(tmp_path / "run", {"scientific": model}, {"researcher": "scientific"})
    async with httpx.AsyncClient() as client:
        store = SourceStore(tmp_path / "sources", client)
        application = tmp_path / "application.md"
        application.write_text(
            "# 研究方案\n\n（1）地面环境下，分离隐窝，\n\n"
            "转移到预先铺好Matrigel的培养皿中。\n\n"
            "（2）在轨培养并成像。\n\n# 组织\n\n人员安排。", "utf-8",
        )
        sid = await store.add_local(application)
        topics = TopicResearch(runtime, store, None, application_ids=[sid])
        doc = store.retained(sid)
        anchors = [p for p in doc.passages if "Matrigel" in p.text or "在轨" in p.text]
        access = topics.source_access("评价在轨实施条件")
        basis = topics.application_basis(
            [ApplicationRef(source_id=sid, passage_id=p.passage_id) for p in anchors], access,
        )
        assert len(basis) == 1
        assert basis[0]["selected_passage_ids"] == [p.passage_id for p in anchors]
        text = "\n".join(p["text"] for p in basis[0]["passages"])
        assert "地面环境下" in text and "在轨培养" in text
        assert "人员安排" not in text
        assert basis[0]["passages"] == [
            p.model_dump(mode="json") for p in doc.passages if p.section == "研究方案"
        ]


@pytest.mark.asyncio
async def test_author_attributions_bind_without_hash_generation_or_prose_rewrite(tmp_path):
    model = ReaderModel()
    runtime = ResearchRuntime(tmp_path, {"scientific": model}, {"researcher": "scientific"})
    sid = runtime.create(SessionTask(role="researcher", objective="评价依据", application={}))
    state = runtime.load(sid)
    handle = "evidence-0123456789abcdef"
    state.evidence = {handle: {
        "reference": {"source_id": "source-one", "passage_id": "p9"},
        "title": '研究 "A"', "locator": "表2 / p9", "quote": "地面培养持续十天。",
    }}
    messages = [{"role": "user", "content": json.dumps({
        "previous": f"原有判断。[{handle}](evidence:{handle})",
        "originals": {handle: {
            "evidence_handle": handle, "citation": f"[{handle}]",
            "text": "地面培养持续十天。",
        }},
    }, ensure_ascii=False)}]
    view = json.loads(author_messages(messages, state.evidence)[0]["content"])
    assert view["previous"] == "原有判断。[证据1]"
    assert "evidence:" not in view["previous"]
    assert view["originals"][handle]["evidence_handle"] == handle
    assert view["originals"][handle]["text"] == "地面培养持续十天。"
    assert json.loads(messages[0]["content"])["previous"].endswith(f"(evidence:{handle})")

    raw_ref = runtime.store.save_author_text(
        state, '研究 "A" 表2记载地面培养持续十天。[证据 1]\n\n| 环节 | 判断 |\n| --- | --- |\n'
        '| 地面培养 | 已有实践依据 |', "submit_research", absorbed_through=0,
        readings=[handle],
    )
    raw = runtime.store.author_text(raw_ref)
    body = compile_manuscript(raw)
    assert set(cited_ids(body)) == {handle}
    original = compile_authored_body(raw["content"].replace("[证据 1]", ""))
    assert plain_text([Node.model_validate(n) for n in body["blocks"]]) == plain_text(
        [Node.model_validate(n) for n in original["blocks"]]
    )
    assert runtime.store.author_text(raw_ref) == raw
    assert not model.requests

    # A later reading and a save/load cycle cannot renumber an earlier choice.
    runtime._capture(state, {"evidence-fedcba9876543210": {
        **state.evidence[handle], "quote": "另一原文。",
    }})
    runtime.store.save(state)
    state = runtime.load(sid)
    assert "[证据1]" in author_messages(messages, state.evidence)[0]["content"]
    assert set(cited_ids(compile_manuscript(raw))) == {handle}

    # The receiving author uses a different local order, through permanent AST identity.
    received = dict(reversed(list(state.evidence.items())))
    next_view = author_messages(
        [{"role": "user", "content": render_authored_body(body)}], received,
    )
    assert "[证据2]" in next_view[0]["content"]
    assert "[证据1]" not in next_view[0]["content"]
