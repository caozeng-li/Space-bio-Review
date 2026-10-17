"""Offline manuscript preservation and recovery checks using fixed saved responses."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_assessment import (  # noqa: E402
    assessment_text,
    manuscript_draft,
)
from probe_v5_assessment import (  # noqa: E402
    service as assessment_service,
)
from probe_v5_chief import MeteredScript  # noqa: E402
from probe_v5_delivery import fixture as publication_fixture  # noqa: E402
from probe_v5_session import ParserModel, ScriptModel, reply, runtime  # noqa: E402
from probe_v5_topic import Fixture  # noqa: E402

from spacebio_review.models.provider import ModelError  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.prose import cited_ids  # noqa: E402
from spacebio_review.publication import compile_publication  # noqa: E402
from spacebio_review.publication.document import walk_nodes  # noqa: E402
from spacebio_review.review.evaluation import GBT42048  # noqa: E402
from spacebio_review.tools import ToolResult  # noqa: E402
from spacebio_review.v5 import SessionTask  # noqa: E402
from spacebio_review.v5.authoring import citation_parts  # noqa: E402
from spacebio_review.v5.source_reading import SourceReading  # noqa: E402
from spacebio_review.v5.topic import TopicResult  # noqa: E402

HISTORY = ROOT / "runs/mytest35_sample2/v5"
ORIGINALS = {
    "initial": "author-7cd9257ee89b041ec33c410aa4d6bf5818d3317dcb8a41ee577662ae29d072be",
    "batch": "author-5e31c6486ef94b78f78360fbafea5c13216e6c31b13c5f94a7385337051166bc",
}


def historical(name):
    raw = json.loads((HISTORY / "author-texts" / (ORIGINALS[name] + ".json")).read_text("utf-8"))
    state = json.loads((HISTORY / "sessions" / (raw["session_id"] + ".json")).read_text("utf-8"))
    return raw, state


def text_blocks(note):
    return ["".join(part["text"] for part in row["parts"]) for row in note["body"]["paragraphs"]]


def without_markers(text):
    return re.sub(r"(?<!\\)[\[［]@?E\d+(?:\s*[,，;；]\s*@?E\d+)*[\]］]", "", text)


async def save_original(root, name):
    """The real writing/persistence path receives one historical public manuscript."""
    raw, old = historical(name)
    author, parser = ScriptModel([reply(content=raw["content"])]), ParserModel()
    engine = runtime(root, author, delivery_parser=parser)
    sid = engine.create(
        SessionTask(
            role="researcher",
            objective="固定历史研究稿：" + name,
            application="只验证保存，不重新评审",
        )
    )
    state = engine.load(sid)
    state.aliases, state.evidence = deepcopy(raw["aliases"]), deepcopy(old["evidence"])
    state.research_finished = True
    engine.store.save(state)
    outcome = await engine.run(sid)
    assert outcome.status == "submitted", outcome
    state = engine.load(sid)
    artifact = engine.store.note(state.note_ref)
    accepted = engine.store.author_text(artifact["author_text_ref"])
    assert accepted["content"] == raw["content"]
    assert accepted["paragraphs"] == raw["paragraphs"]
    assert text_blocks(artifact["note"]) == [
        without_markers(row["text"]) for row in raw["paragraphs"]
    ]
    assert "unknowns" not in artifact["note"]
    assert len(author.requests) == 1 and not parser.requests
    assert set(cited_ids(artifact["note"])) == set(artifact["evidence"])
    assert artifact["evidence"], "Historical explicit references must bind to their originals"
    if name == "initial":
        assert len(artifact["note"]["body"]["paragraphs"]) == 13
        assert "全长PCR" in text_blocks(artifact["note"])[-1]
        assert "辐射" in text_blocks(artifact["note"])[-1]
    else:
        assert "批次吸收" in text_blocks(artifact["note"])[0]
    before = state.note_ref
    await engine.run(sid)
    assert engine.load(sid).note_ref == before and len(author.requests) == 1
    return (
        engine,
        artifact,
        {
            "blocks": len(raw["paragraphs"]),
            "bound_evidence": len(artifact["evidence"]),
            "scripted_author_responses": 1,
            "scripted_parser_responses": 0,
            "repeat_resume_requests": 0,
        },
    )


def handoff(root, child):
    """Exercise actual citation capture, tool payload and persisted context restoration."""
    author, parser = ScriptModel([]), ParserModel()
    engine = runtime(root, author, delivery_parser=parser)
    parent = engine.create(
        SessionTask(role="chief", objective="接收固定研究稿", application="局部身份交接"),
        readings={
            "evidence-parent-only": {
                "title": "父会话已有材料",
                "reference": {"source_id": "source-parent", "passage_id": "p1"},
                "quote": "父会话 E1 已由不同原文占用。",
            }
        },
    )
    state = engine.load(parent)
    readings = engine._capture(state, child["evidence"])
    topic = TopicResult(
        topic_id=child["session_id"],
        status="submitted",
        note_ref="note-fixed-handoff",
        note=child["note"],
        readings=child["evidence"],
    )
    result = ToolResult(ok=True, content={"topics": [topic.model_view()]})
    engine._observe(
        state,
        {
            "kind": "tool",
            "name": "research_topics",
            "output": {"result": result.model_dump(mode="json"), "readings": readings},
        },
    )
    restored = runtime(root, ScriptModel([]), delivery_parser=ParserModel())
    view = restored._context(restored.load(parent))
    received = view["unabsorbed_observations"][0]["output"]["result"]["content"]["topics"][0]
    body = received["note"]["body"]
    assert len(body) == len(child["note"]["body"]["paragraphs"])
    for original, delivered in zip(child["note"]["body"]["paragraphs"], body, strict=True):
        assert [part["text"] for part in delivered] == [part["text"] for part in original["parts"]]
        for before, after in zip(original["parts"], delivered, strict=True):
            assert [state.aliases[alias] for alias in after["cites"]] == before["cites"]
            assert "E1" not in after["cites"]
    assert not author.requests and not parser.requests
    return {"all_blocks_preserved": True, "parent_aliases_rebound": True, "model_requests": 0}


async def resume_research(root):
    text = "# 完整研究原稿\n\n正文与“引号”原样保存。\n\n后续问题仍属于研究正文。"
    author, parser = ScriptModel([reply(content=text)]), ParserModel()
    engine = runtime(root, author, delivery_parser=parser)
    sid = engine.create(
        SessionTask(role="researcher", objective="中断恢复", application="固定输入")
    )
    state = engine.load(sid)
    state.research_finished = True
    engine.store.save(state)

    def interrupted(*args, **kwargs):
        raise ModelError("fixed_save_interruption", category="exploration", request_sent=False)

    engine.store.save_note = interrupted
    first = await engine.run(sid)
    assert first.status == "partial", first
    pending = engine.load(sid).pending_delivery
    assert pending and pending["raw_ref"] and not pending.get("parse_ref")
    raw_ref = pending["raw_ref"]
    raw_bytes = engine.store._path("author-texts", raw_ref).read_bytes()
    resumed_author, resumed_parser = ScriptModel([]), ParserModel()
    resumed = runtime(root, resumed_author, delivery_parser=resumed_parser)
    second = await resumed.run(sid)
    assert second.status == "submitted", second
    state = resumed.load(sid)
    assert state.revision == 1 and state.pending_delivery is None
    assert resumed.store.note(state.note_ref)["author_text_ref"] == raw_ref
    assert resumed.store._path("author-texts", raw_ref).read_bytes() == raw_bytes
    assert len(author.requests) == 1 and not parser.requests
    assert not resumed_author.requests and not resumed_parser.requests
    await resumed.run(sid)
    assert resumed.load(sid).revision == 1
    return {"original_retained": True, "resume_model_requests": 0, "saved_revisions": 1}


async def resume_assessment(root):
    """Predetermined assessment text tests pending parsing, not scientific evaluation."""
    fixed = await Fixture(root).setup()
    response = assessment_text(GBT42048, "submit_assessment", manuscript_draft(GBT42048))
    author = MeteredScript([response], name="fixed-chief")

    def false_missing(payload):
        from probe_v5_session import DELIVERY_FIXTURES

        value = deepcopy(DELIVERY_FIXTURES[tuple(row["text"] for row in payload["paragraphs"])])
        value["missing_fields"] = ["project"]
        return value

    engine, _, chief = assessment_service(
        root,
        fixed,
        author,
        MeteredScript([], name="unused-researcher"),
        parser_steps=[false_missing],
        prepared_application=False,
    )
    sid = chief.create("固定评价原稿解析恢复；不进行科学研究或报告交付")
    try:
        first = await chief.assess(sid, allow_research=False)
        assert first["status"] == "partial" and first["reason"] == "delivery_mapping_incomplete"
        state = engine.load(sid)
        assert state.pending_delivery and not state.pending_delivery.get("parse_ref")
        raw_ref = state.pending_delivery["raw_ref"]
        raw_bytes = engine.store._path("author-texts", raw_ref).read_bytes()
        saved_failures = list((engine.store.root / "delivery-parses").glob("*.json"))
        assert len(saved_failures) == 1
        assert len(author.requests) == 1
        second = await chief.assess(sid, allow_research=False)
        assert second["status"] == "submitted", second.get("reason")
        assert second["report_author_source"]["author_text_ref"] == raw_ref
        assert engine.store._path("author-texts", raw_ref).read_bytes() == raw_bytes
        assert len(author.requests) == 1 and engine.budget.usage()["model_calls"] == 3
        assert engine.load(sid).pending_delivery is None
        assert len(list((engine.store.root / "delivery-parses").glob("*.json"))) == 2
        assert not fixed.requests and not fixed.discovery.queries
        return {
            "scripted_author_responses": 1,
            "scripted_parser_responses": 2,
            "same_original": True,
        }
    finally:
        await engine.close()
        await fixed.close()


def current_appendix(root):
    assessment, evidence, topics = publication_fixture(GBT42048)
    original = deepcopy(topics[0]["note"])
    unknown = "跨对象迁移的范围仍未知。"
    assert "unknowns" not in original
    result = TopicResult(
        topic_id=topics[0]["topic_id"],
        status="submitted",
        note_ref=topics[0]["note_ref"],
        note=original,
    )
    assert result.note == original
    assert SourceReading.view({"note": original})["note"] == original
    document, audit = compile_publication(assessment, evidence=evidence, topics=topics)
    text = "\n".join(node.text for node in walk_nodes(document.appendix[0].blocks) if node.text)
    assert text.count(unknown) == 1
    assert "原专题科学正文原样进入附录" in text
    assert not any(
        node.kind == "label_heading" and node.attrs.get("label") == "unknowns"
        for node in walk_nodes(document.appendix[0].blocks)
    )
    write_json(root / "publication.json", document)
    write_json(root / "audit.json", audit)
    return {"complete_manuscript_preserved": True, "uncertainty_in_body_once": True}


def citation_forms():
    aliases = {"E1": "first", "E2": "second"}
    for marker in ("[E1]", "[@E1]", "［E1］", "［@E1］"):
        assert citation_parts("正文" + marker, aliases) == [{"text": "正文", "cites": ["E1"]}]
    assert citation_parts("组合[E1, @E2]", aliases) == [{"text": "组合", "cites": ["E1", "E2"]}]
    assert citation_parts(r"字面\[E1]与\[@E2]", aliases) == [
        {"text": r"字面\[E1]与\[@E2]", "cites": []}
    ]
    return {"explicit_forms_bound": True, "escaped_markers_literal": True}


async def run(output):
    if output.exists() and any(output.iterdir()):
        raise ValueError("probe_requires_empty_output_directory")
    initial_engine, initial, initial_result = await save_original(output / "initial", "initial")
    batch_engine, _, batch_result = await save_original(output / "batch", "batch")
    results = {
        "actual_model_requests": 0,
        "historical_initial": initial_result,
        "historical_batch": batch_result,
        "citation_forms": citation_forms(),
        "child_parent_handoff": handoff(output / "handoff", initial),
        "research_resume": await resume_research(output / "research-resume"),
        "assessment_parse_resume": await resume_assessment(output / "assessment-resume"),
        "research_appendix": current_appendix(output / "research-appendix"),
    }
    await initial_engine.close()
    await batch_engine.close()
    write_json(output / "results.json", results)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.output.resolve())), ensure_ascii=False, indent=2))
