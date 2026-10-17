"""Offline question-reader integration; synthetic papers, real runtime and source tools."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from probe_v5_session import ScriptModel, delivery, note, reply, tool_call
from probe_v5_topic import METHOD, RESULT, SUPPLEMENT, VISUAL, Fixture, aliases

from spacebio_review.models.provider import ModelError
from spacebio_review.persistence import write_json
from spacebio_review.prose import cited_ids
from spacebio_review.tools import ToolResult
from spacebio_review.v5.chief import ChiefReview
from spacebio_review.v5.records import ResearchNote
from spacebio_review.v5.source_reading import SourceQuestion
from spacebio_review.v5.topic import TopicTask


def plain(text, cites=()):
    return delivery("submit_research", note(text, cites))


async def run(root):
    fixture = await Fixture(root).setup()

    def supplement(_):
        material = next(
            m for m in fixture.store.retained(fixture.source_id).materials if m.role == "supplement"
        )
        return reply(
            tool_call(
                "acquire_source",
                {"material_id": material.material_id, "reading_purpose": "calibration standards"},
            )
        )

    def figure(_):
        visual_id = fixture.store.retained(fixture.source_id).visuals[0].visual_id
        return reply(
            tool_call(
                "read_literature_visual",
                {"visual_id": visual_id, "question": "Which objects are compared?"},
            )
        )

    def author(messages):
        return plain(
            "固定材料的细胞系与血清条件，以及校准附件和图示支持当前比较。",
            aliases(messages, METHOD, RESULT, SUPPLEMENT, VISUAL),
        )

    model = ScriptModel(
        [
            reply(tool_call("acquire_source", {"reading_purpose": "Methods Results"})),
            supplement,
            figure,
            reply(tool_call("finish_research", {"reason": "固定原文和图像已读"})),
            author,
            ModelError("fixed_parent_connection_failure", category="transport"),
            reply(tool_call("finish_research", {"reason": "已有原文可回答续问"})),
            plain("续问说明保留原始比较条件。"),
        ]
    )
    engine, topics = fixture.topics(model)
    try:
        topic = topics.create(
            TopicTask(
                objective="解释比较对象及校准依据",
                source_ids=[fixture.source_id],
                skills=["gbt42048-experiment-review"],
            )
        )
        assert "gbt42048-experiment-review" not in engine.load(topic).task.skills
        names = set(topics.tools(topic)._tools)
        assert {"read_source_for_question", "read_source_notes", "read_application"} <= names
        assert {
            "read_materials",
            "browse_materials",
            "retrieve_knowledge",
            "read_literature_visual",
        } <= names
        question = SourceQuestion(source_id=fixture.source_id, question="这份论文实际比较了什么？")
        result = await topics.reader.research(topic, question)
        assert result.ok, result
        rid = result.content["reading_id"]
        assert engine.load(rid).budget_owner == topic
        assert engine.load(rid).model_alias == engine.load(topic).model_alias
        assert not set(topics.reader.tools(rid)._tools) & {
            "search_sources",
            "related_papers",
            "read_source_for_question",
        }
        before = len(model.requests)
        question.reading_id = rid
        cached = await topics.reader.research(topic, question)
        assert cached.ok and len(model.requests) == before
        failed = await topics.run(topic)
        assert failed.status == "failed" and failed.note is None
        assert failed.source_readings[0]["note"] and failed.readings
        assert failed.model_view()["source_readings"][0]["note"]
        assert any(SUPPLEMENT in row["quote"] for row in failed.readings.values())
        assert len(fixture.visual_requests) == 1
        chief = ChiefReview(topics, admission={"status": "skipped"})
        chief_id = chief.create("读取固定专题执行目录")
        manifest = chief._manifest(chief_id)
        manifest["topic_ids"].append(topic)
        write_json(chief._path(chief_id), manifest)
        coverage = chief._context(chief_id)["topics"][0]
        assert coverage["source_readings"][0]["reading_id"] == rid
        assert coverage["source_readings"][0]["note_ref"]
        assert coverage["source_readings"][0]["note"]
        # Resume the actual chief entry with a fixed provider boundary: it must
        # attach completed child originals before constructing any author input.
        model.steps.insert(0, ModelError("fixed_chief_input_boundary", category="exploration"))
        boundary = await chief.run(chief_id)
        assert boundary["status"] == "partial"
        chief_input = json.loads(model.requests[-1]["messages"][-1]["content"])
        reader_note = chief_input["working_context"]["topics"][0]["source_readings"][0]["note"]
        assert set(cited_ids(reader_note)) <= set(chief_input["available_readings"])
        assert any(SUPPLEMENT in row["quote"] for row in chief_input["originals"].values())
        # A restored reader uses the existing original identities and no model requests.
        topics.reader._access.clear()
        namespaced = await topics.tools(topic).execute(
            "read_source_notes", '{"reading_ids":["' + rid + '"]}'
        )
        assert namespaced.ok and namespaced.content["source_readings"][0]["note"]
        question.question = "补充解释既有比较条件"
        followup = await topics.reader.research(topic, question)
        assert followup.ok, followup
        count = len(model.requests)
        again = await topics.reader.research(topic, question)
        assert again.ok and len(model.requests) == count
        direct = await direct_expert(root / "direct-expert")
        write_json(
            root / "summary.json",
            {
                "status": "passed",
                "fictional_science": True,
                "source_acquisition_attachment_visual": True,
                "failed_parent_keeps_reader_note": True,
                "evidence_survives_restore": True,
                "chief_directory_exposes_failed_topic_reading": True,
                "chief_resume_attaches_child_originals_before_author": True,
                "completed_read_and_followup_reuse_requests": 0,
                "topic": topic,
                "reader": rid,
                "author_requests": count,
                "direct_expert": direct,
            },
        )
    finally:
        await engine.close()
        await fixture.close()


async def direct_expert(root):
    """A topic reads an external original itself through the real registered tools."""
    fixture = await Fixture(root).setup()

    def select_passages(_):
        passages = [
            next(
                p.passage_id
                for p in fixture.store.retained(fixture.source_id).passages
                if text in p.text
            )
            for text in (METHOD, RESULT)
        ]
        assert len(passages) == 2
        return reply(
            tool_call(
                "read_materials",
                {"reads": [{"source_id": fixture.source_id, "passage_ids": passages}]},
            )
        )

    def complete(messages):
        return plain(
            "直接读到的模型条件和比较结果支持当前问题的答案。", aliases(messages, METHOD, RESULT)
        )

    model = ScriptModel(
        [
            reply(tool_call("acquire_source", {"source_id": fixture.source_id})),
            select_passages,
            reply(tool_call("finish_research", {"reason": "当前原文已足以回答，无须独立阅读任务"})),
            complete,
        ]
    )
    engine, topics = fixture.topics(model)
    try:
        topic = topics.create(
            TopicTask(objective="直接解释实际模型比较", source_ids=[fixture.source_id])
        )
        result = await topics.run(topic)
        assert result.status == "submitted", result.reason
        assert result.note and not result.source_readings
        assert all(
            any(text in row["quote"] for row in result.readings.values())
            for text in (METHOD, RESULT)
        )
        assert not list((engine.store.root / "source-readings").glob("*.json"))
        assert len(model.requests) == 4

        chief = ChiefReview(topics, admission={"status": "skipped"})
        chief_id = chief.create("只验证成果交接与恢复，不作评分")
        manifest = chief._manifest(chief_id)
        manifest["topic_ids"].append(topic)
        write_json(chief._path(chief_id), manifest)
        state = engine.load(chief_id)
        readings = engine._capture(state, result.readings)
        engine._observe(
            state,
            {
                "kind": "tool",
                "name": "research_topics",
                "output": {
                    "result": ToolResult(
                        ok=True, content={"topics": [result.model_view()]}
                    ).model_dump(),
                    "readings": readings,
                },
            },
        )
        context = engine._context(state, lambda: chief._context(chief_id))
        assert "note" not in context["working_context"]["topics"][0]
        assert context["unabsorbed_observations"][0]["output"]["result"]["content"]["topics"][0][
            "note"
        ]
        # Updating working understanding removes tool observations, not independent expert work.
        engine.store.save_note(
            state,
            ResearchNote.model_validate(
                {
                    "title": "当前理解",
                    "body": {
                        "paragraphs": [{"parts": [{"text": "当前理解保留原安排。", "cites": []}]}]
                    },
                }
            ),
            absorbed_through=state.observation_index,
        )
        restored = engine._context(engine.load(chief_id), lambda: chief._context(chief_id))
        supplied = restored["working_context"]["topics"][0]["note"]
        assert supplied and not restored["unabsorbed_observations"]
        assert set(cited_ids(supplied)) <= set(restored["available_readings"])
        assert all(
            any(text in row["quote"] for row in restored["originals"].values())
            for text in (METHOD, RESULT)
        )
        return {
            "direct_original_reading": True,
            "reader_tasks": 0,
            "independent_note_survives_working_update": True,
            "duplicate_note_omitted": True,
        }
    finally:
        await engine.close()
        await fixture.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args().output.resolve()))
