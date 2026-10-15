"""Offline same-chief assessment checks, stopping before delivery and without run_review."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_chief import ADMISSION, CHIEF_NOTE, MeteredScript, service  # noqa: E402
from probe_v5_delivery import fixture as fixed_assessment  # noqa: E402
from probe_v5_session import (  # noqa: E402
    assert_private_not_saved,
    note,
    reply,
    require_content,
    tool_call,
)
from probe_v5_topic import Fixture, payloads  # noqa: E402

from spacebio_review.models.provider import ModelError  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.review.evaluation import GBT42048, GENERAL  # noqa: E402
from spacebio_review.tools import ToolResult  # noqa: E402
from spacebio_review.v5.chief import ChiefReview  # noqa: E402
from spacebio_review.v5.records import ResearchNote  # noqa: E402
from spacebio_review.v5.topic import TopicTask  # noqa: E402


def seed_research(engine, chief_id):
    state = engine.load(chief_id)
    ref = engine.store.save_note(
        state,
        ResearchNote.model_validate(note(CHIEF_NOTE, [next(iter(state.aliases))])),
        absorbed_through=state.observation_index,
    )
    state.submitted_ref = ref
    state.submissions.append(ref)
    state.status = "submitted"
    engine.store.save(state)
    engine.budget.finish(chief_id)
    return ref


def assessment_draft(system, *, veto=False, selected=()):
    result = deepcopy(fixed_assessment(system)[0])
    for key in (
        "schema_version",
        "evaluation_system",
        "total_score",
        "score_grade",
        "summary",
        "overall",
    ):
        result.pop(key, None)
    if system.score_grades:
        result.pop("recommendation")
        result["safety_veto"]["applies"] = veto
    result["appendix_topics"] = list(selected)

    def rebind(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "cites":
                    value[key] = ["E1"] if item else []
                else:
                    rebind(item)
        elif isinstance(value, list):
            for item in value:
                rebind(item)

    rebind(result)
    return result


def calculated(messages):
    for value in reversed(list(payloads(messages))):
        result = value.get("result", {})
        if result.get("ok") and isinstance(result.get("content"), dict):
            if "assessment_ref" in result["content"]:
                return result["content"]["assessment"]
    raise AssertionError("Chief did not receive the calculated assessment")


def summary_step(system, veto=False):
    def submit(messages):
        value = calculated(messages)
        assert "overall" not in value and "summary" not in value
        if system.score_grades:
            assert value["total_score"] == 85 and value["score_grade"] == "A"
            assert value["recommendation"] == (
                "not_support" if veto else system.grade_for(85).recommendation
            )
        else:
            assert not {"total_score", "score_grade", "safety_veto"} & value.keys()
            assert value["dimensions"]["scientific_value"]["grade"] is None
            assert value["dimensions"]["rigor"]["grade"] == "B"
        return reply(
            tool_call(
                "submit_report",
                {
                    "overall": note(
                        "同一主审收到确定结果后形成整体判断：" + value["recommendation"] + "。",
                        ["E1"],
                    )["body"],
                    "summary": note("同一主审已读到代码结论后，概括地面对照及测量范围。", ["E1"])[
                        "body"
                    ],
                },
            )
        )

    return submit


def assert_only_assessment(model):
    allowed = {"save_assessment", "submit_assessment", "finalize_assessment", "submit_report"}
    for request in model.requests:
        exposed = {tool["function"]["name"] for tool in request["tools"]}
        assert exposed and exposed <= allowed, exposed
        for tool in request["tools"]:
            function = tool["function"]
            properties = function["parameters"]["properties"]
            if function["name"] in {"save_assessment", "submit_assessment"}:
                assert "overall" not in properties and "summary" not in properties
            if function["name"] == "submit_report":
                assert set(properties) == {"overall", "summary"}


async def complete_case(root, system, *, veto=False):
    fixed = await Fixture(root).setup()
    draft = assessment_draft(system, veto=veto)

    def submit(messages):
        require_content(messages, CHIEF_NOTE, "地面对照", system.label)
        return reply(tool_call("submit_assessment", draft))

    model = MeteredScript([submit, summary_step(system, veto)], name="fixed-chief")
    researchers = MeteredScript([], name="fixed-researcher")
    engine, topics, chief = service(root, fixed, model, researchers)
    if system.id == "general":
        chief = ChiefReview(topics, evaluation_system=system.id, admission=ADMISSION)
    chief_id = chief.create("固定研究状态后的局部最终评价，不开展检索或派题。")
    original = seed_research(engine, chief_id)
    try:
        result = await chief.assess(chief_id, allow_research=False)
        assert result["status"] == "submitted" and result["report_ref"]
        assert result["note_ref"] == original and result["chief_id"] == chief_id
        assert result["assessment"]["recommendation"] in json.dumps(
            result["assessment"]["overall"], ensure_ascii=False
        )
        assert engine.load(chief_id).turns_used == 2
        assert engine.budget.usage()["model_calls"] == 2
        assert_only_assessment(model)
        again = await chief.assess(chief_id, allow_research=False)
        assert again["report_ref"] == result["report_ref"] and len(model.requests) == 2
        assert not fixed.requests and not researchers.requests
        assert len(list((root / "v5/sessions").glob("*.json"))) == 1
        write_json(root / "result.json", result)
        assert_private_not_saved(root)
        return {
            "same_chief": chief_id,
            "requests": 2,
            "reentry_requests": 0,
            "assessment_only_tools": True,
            "recommendation": result["assessment"]["recommendation"],
        }
    finally:
        await engine.close()
        await fixed.close()


async def saved_parts_resume(root):
    fixed = await Fixture(root).setup()
    draft = assessment_draft(GBT42048)
    first = {
        "project": draft["project"],
        "dimensions": {"scientific_objectives": draft["dimensions"]["scientific_objectives"]},
    }
    source_text = "Fixed draft-only original: measurement phases require separate interpretation."

    def save_first(messages):
        require_content(messages, source_text)
        return reply(tool_call("save_assessment", first))

    model = MeteredScript([save_first, ModelError("fixture_interruption")], name="fixed-chief")
    researchers = MeteredScript([], name="fixed-researcher")
    engine, _, chief = service(root, fixed, model, researchers)
    chief_id = chief.create("分次保存评价稿，重开后沿用当前主审。")
    seed_research(engine, chief_id)
    state = engine.load(chief_id)
    extra = {
        "reference": {"source_id": fixed.source_id, "passage_id": "draft-only", "visual_id": None},
        "title": "Fixed draft-only reading",
        "kind": "external",
        "locator": "Results",
        "quote": source_text,
        "reading_scope": "text",
    }
    added = engine._capture(state, {"evidence-draft-only": extra})
    extra_alias = next(iter(added))
    first["project"]["paragraphs"][0]["parts"][0]["cites"] = [extra_alias]
    engine._observe(
        state,
        {
            "kind": "tool",
            "name": "fixed_previous_reading",
            "output": {"result": ToolResult(ok=True, content={}).model_dump(), "readings": added},
        },
    )
    try:
        interrupted = await chief.assess(chief_id)
        assert interrupted["status"] == "failed" and not interrupted["assessment"]
        consumed = engine.budget.usage()
        assert consumed["model_calls"] == 2
        await engine.close()
        remainder = deepcopy(draft)
        remainder.pop("project")
        remainder["dimensions"].pop("scientific_objectives")

        def resume(messages):
            require_content(messages, "scientific_objectives", "地面对照", source_text)
            context = next(value for value in payloads(messages) if "working_context" in value)
            saved = context["working_context"]["assessment"]["draft"]
            assert saved["project"] == first["project"]
            assert set(saved["dimensions"]) == {"scientific_objectives"}
            assert context["originals"][extra_alias]["quote"] == source_text
            return reply(
                tool_call("save_assessment", remainder), tool_call("finalize_assessment", {})
            )

        reopened = MeteredScript([resume, summary_step(GBT42048)], name="fixed-chief")
        second, _, resumed_chief = service(root, fixed, reopened, researchers)
        try:
            assert second.budget.usage() == consumed
            result = await resumed_chief.assess(chief_id)
            assert result["status"] == "submitted", result
            assert result["chief_id"] == chief_id
            assert second.load(chief_id).turns_used == 4
            assert second.budget.usage()["model_calls"] == 4
            assert result["assessment"]["total_score"] == 85
            assert_only_assessment(model)
            assert_only_assessment(reopened)
            write_json(root / "result.json", result)
            assert_private_not_saved(root)
            return {
                "chief_id": chief_id,
                "requests_before": 2,
                "requests_after": 4,
                "draft_preserved": True,
            }
        finally:
            await second.close()
    finally:
        await engine.close()
        await fixed.close()


async def selected_topic_version(root):
    fixed = await Fixture(root).setup()
    selected = []

    def submit(messages):
        return reply(tool_call("submit_assessment", assessment_draft(GBT42048, selected=selected)))

    model = MeteredScript([submit, summary_step(GBT42048)], name="fixed-chief")
    researchers = MeteredScript(
        [
            reply(tool_call("submit_research", {"note": note("选定的专题第一版正文。", ["E1"])})),
            reply(tool_call("submit_research", {"note": note("后续专题第二版正文。", ["E1"])})),
        ],
        name="fixed-researcher",
    )
    engine, topics, chief = service(root, fixed, model, researchers)
    chief_id = chief.create("固定专题版本后的局部评价。")
    seed_research(engine, chief_id)
    try:
        topic_id = topics.create(TopicTask(objective="本地固定专题说明"))
        manifest = chief._manifest(chief_id)
        manifest["topic_ids"] = [topic_id]
        write_json(chief._path(chief_id), manifest)
        original = await topics.run(topic_id)
        revised = await topics.run(topic_id, followup="保留第一版并提交第二版。")
        assert original.note_ref != revised.note_ref
        selected.append({"topic_id": topic_id, "note_ref": original.note_ref})
        result = await chief.assess(chief_id)
        assert result["status"] == "submitted", result
        assert result["assessment"]["appendix_topics"] == selected
        assert result["selected_topics"][0]["note"] == original.note
        assert result["selected_topics"][0]["note"] != revised.note
        assert_only_assessment(model)
        write_json(root / "result.json", result)
        return {
            "topic_id": topic_id,
            "selected_ref": original.note_ref,
            "latest_ref": revised.note_ref,
            "requests": 4,
        }
    finally:
        await engine.close()
        await fixed.close()


async def run(output):
    results = {
        "gbt85": await complete_case(output / "gbt85", GBT42048),
        "gbt85_veto": await complete_case(output / "gbt85-veto", GBT42048, veto=True),
        "general": await complete_case(output / "general", GENERAL),
        "saved_parts_resume": await saved_parts_resume(output / "saved-parts-resume"),
        "selected_topic_version": await selected_topic_version(output / "selected-topic-version"),
    }
    summary = {
        "scope": "Fixed public research state to same-chief assessment only; offline.",
        "results": results,
    }
    write_json(output / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.output)), ensure_ascii=False, indent=2))
