"""Offline manuscript preservation checks using fixed saved responses."""

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

from probe_v5_delivery import fixture as publication_fixture  # noqa: E402
from probe_v5_session import ParserModel, ScriptModel, reply, runtime  # noqa: E402

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
    engine.store.set_focus(state.inquiry_id, "固定历史研究稿：" + name, "保存历史科学原稿")
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
    return (
        engine,
        artifact,
        {
            "blocks": len(raw["paragraphs"]),
            "bound_evidence": len(artifact["evidence"]),
            "scripted_author_responses": 1,
            "scripted_parser_responses": 0,
        },
    )


def handoff(root, child):
    """Exercise citation capture, tool payload and the recipient's working context."""
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
    view = engine._context(state)
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


def current_appendix(root):
    assessment, evidence, contributions = publication_fixture(GBT42048)
    original = deepcopy(contributions[0]["note"])
    unknown = "跨对象迁移的范围仍未知。"
    assert "unknowns" not in original
    result = TopicResult(
        topic_id=contributions[0]["inquiry_id"],
        status="submitted",
        note_ref=contributions[0]["contribution_ref"],
        note=original,
    )
    assert result.note == original
    assert SourceReading.view({"note": original})["note"] == original
    document, audit = compile_publication(
        assessment, evidence=evidence, contributions=contributions,
        report_selection={"main_sections": [], "appendix_sections": [{
            key: contributions[0][key] for key in ("inquiry_id", "contribution_ref")
        }]},
    )
    text = "\n".join(node.text for node in walk_nodes(document.appendix[0].blocks) if node.text)
    assert text.count(unknown) == 1
    assert "原科学稿完整进入报告" in text
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
