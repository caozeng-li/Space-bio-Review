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

from probe_v5_chief import ADMISSION, CHIEF_NOTE, MeteredScript  # noqa: E402
from probe_v5_chief import service as chief_service  # noqa: E402
from probe_v5_delivery import fixture as fixed_assessment  # noqa: E402
from probe_v5_session import (  # noqa: E402
    DELIVERY_FIXTURES,
    ParserModel,
    assert_private_not_saved,
    note,
    reply,
    require_content,
    tool_call,
)
from probe_v5_topic import Fixture, payloads  # noqa: E402

from spacebio_review.models.provider import ModelError  # noqa: E402
from spacebio_review.models.scheduling import ScheduledModel  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.prose import prose_inputs  # noqa: E402
from spacebio_review.review.evaluation import GBT42048, GENERAL  # noqa: E402
from spacebio_review.tools import ToolResult  # noqa: E402
from spacebio_review.v5.assessment import AssessmentSubmission  # noqa: E402
from spacebio_review.v5.assessment_authoring import assessment_mapping_type  # noqa: E402
from spacebio_review.v5.authoring import DeliveryParser  # noqa: E402
from spacebio_review.v5.chief import ChiefReview  # noqa: E402
from spacebio_review.v5.delivery import deliver_assessment  # noqa: E402
from spacebio_review.v5.records import ResearchNote  # noqa: E402
from spacebio_review.v5.topic import TopicTask  # noqa: E402


def service(root, fixed, model, researchers, *, parser_steps=()):
    engine, topics, chief = chief_service(root, fixed, model, researchers)
    parser = ParserModel(parser_steps, budget=engine.budget)
    engine.delivery_parser = DeliveryParser(
        ScheduledModel(parser, engine.scheduler, "delivery_parser")
    )
    return engine, topics, chief


def assessment_text(system, operation, values, *, missing_fields=()):
    """Fixed ordinary author text and independently explicit expected field mapping."""
    blocks = []
    mapping = {
        "provided_fields": list(values),
        "project": [],
        "scope": [],
        "dimensions": {criterion.id: None for criterion in system.criteria},
        "unknowns": [],
        "appendix_topics": [],
        "missing_fields": list(missing_fields),
    }
    mapping.update(
        {"safety_veto": None}
        if system.score_grades
        else {"recommendation": None, "support_scope": []}
    )

    def block(text):
        blocks.append(text)
        return f"p{len(blocks)}"

    def paragraphs(value):
        return [
            block(
                "".join(
                    part["text"] + "".join(f"[@{ref}]" for ref in part["cites"])
                    for part in paragraph["parts"]
                )
            )
            for paragraph in value["paragraphs"]
        ]

    for key, value in values.items():
        block("## " + key)
        if key in {"project", "scope", "support_scope"}:
            mapping[key] = paragraphs(value)
        elif key == "dimensions":
            for criterion, dimension in value.items():
                block("### " + criterion)
                entry = {"paragraph_ids": paragraphs(dimension["body"])}
                for scalar in ("score",) if system.score_grades else ("grade", "confidence"):
                    entry[scalar] = dimension.get(scalar)
                    if scalar in dimension:
                        block(
                            scalar
                            + "："
                            + str(dimension[scalar] if dimension[scalar] is not None else "null")
                        )
                mapping[key][criterion] = entry
        elif key == "unknowns":
            if not value:
                block("未知事项：无")
            for item in value:
                mapping[key].append(
                    {
                        "title_paragraph_id": block("### " + item["title"]),
                        "paragraph_ids": paragraphs(item["text"]),
                    }
                )
        elif key == "safety_veto":
            block("applies：" + str(value["applies"]).lower())
            mapping[key] = {
                "applies": value["applies"],
                "paragraph_ids": paragraphs(value["reason"]),
            }
        elif key == "appendix_topics":
            if not value:
                block("专题附录：无")
            for item in value:
                block("topic_id：" + item["topic_id"] + "；note_ref：" + item["note_ref"])
            mapping[key] = deepcopy(value)
        else:
            block(key + "：" + str(value))
            mapping[key] = value
    DELIVERY_FIXTURES[tuple(blocks)] = mapping
    return reply(content=f"交付操作：{operation}\n\n" + "\n\n".join(blocks))


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
    allowed = {"finalize_assessment", "submit_report"}
    for request in model.requests:
        exposed = {tool["function"]["name"] for tool in request["tools"]}
        assert exposed and exposed <= allowed, exposed
        for tool in request["tools"]:
            function = tool["function"]
            properties = function["parameters"]["properties"]
            if function["name"] == "finalize_assessment":
                assert not properties
            if function["name"] == "submit_report":
                assert set(properties) == {"overall", "summary"}


async def complete_case(root, system, *, veto=False):
    fixed = await Fixture(root).setup()
    draft = assessment_draft(system, veto=veto)

    def submit(messages):
        require_content(messages, CHIEF_NOTE, "地面对照", system.label)
        return assessment_text(system, "submit_assessment", draft)

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
        assert engine.budget.usage()["model_calls"] == 3
        assert_only_assessment(model)
        again = await chief.assess(chief_id, allow_research=False)
        assert again["report_ref"] == result["report_ref"] and len(model.requests) == 2
        assert not fixed.requests and not researchers.requests
        assert len(list((root / "v5/sessions").glob("*.json"))) == 1
        origins = result["author_sources"]
        assert set(origins["dimensions"]) == set(draft["dimensions"])
        for origin in origins["dimensions"].values():
            assert (
                engine.store.author_text(origin["author_text_ref"])["operation"]
                == "submit_assessment"
            )
            assert (
                engine.store.delivery_parse(origin["parse_ref"])["author_text_ref"]
                == origin["author_text_ref"]
            )
        before_publication = engine.budget.usage()
        publication = await deliver_assessment(
            engine,
            result["assessment"],
            evidence=result["evidence"],
            topics=result["selected_topics"],
            english=False,
        )
        assert publication["status"] == "completed" and publication["en"]["status"] == "disabled"
        assert engine.budget.usage() == before_publication
        assert (root / "report.zh.md").exists() and not (root / "report.en.md").exists()
        write_json(root / "result.json", result)
        assert_private_not_saved(root)
        return {
            "same_chief": chief_id,
            "requests": 3,
            "author_turns": 2,
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
        return assessment_text(GBT42048, "save_assessment", first)

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
        assert consumed["model_calls"] == 3
        await engine.close()
        remainder = deepcopy(draft)
        remainder.pop("project")
        remainder["dimensions"].pop("scientific_objectives")

        def resume(messages):
            require_content(messages, "scientific_objectives", "地面对照", source_text)
            context = next(value for value in payloads(messages) if "working_context" in value)
            saved = context["working_context"]["assessment"]["draft"]
            assert saved["project"] == prose_inputs(first["project"])
            assert set(saved["dimensions"]) == {"scientific_objectives"}
            assert context["originals"][extra_alias]["quote"] == source_text
            return assessment_text(GBT42048, "save_assessment", remainder)

        reopened = MeteredScript(
            [resume, reply(tool_call("finalize_assessment", {})), summary_step(GBT42048)],
            name="fixed-chief",
        )
        second, _, resumed_chief = service(root, fixed, reopened, researchers)
        try:
            assert second.budget.usage() == consumed
            result = await resumed_chief.assess(chief_id)
            assert result["status"] == "submitted", result
            assert result["chief_id"] == chief_id
            assert second.load(chief_id).turns_used == 5
            assert second.budget.usage()["model_calls"] == 7
            assert result["assessment"]["total_score"] == 85
            assert_only_assessment(model)
            assert_only_assessment(reopened)
            write_json(root / "result.json", result)
            assert_private_not_saved(root)
            return {
                "chief_id": chief_id,
                "requests_before": 3,
                "requests_after": 7,
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
        return assessment_text(
            GBT42048, "submit_assessment", assessment_draft(GBT42048, selected=selected)
        )

    model = MeteredScript([submit, summary_step(GBT42048)], name="fixed-chief")
    researchers = MeteredScript([], name="fixed-researcher")
    engine, topics, chief = service(root, fixed, model, researchers)
    chief_id = chief.create("固定专题版本后的局部评价。")
    seed_research(engine, chief_id)
    try:
        topic_id = topics.create(TopicTask(objective="本地固定专题说明"))
        manifest = chief._manifest(chief_id)
        manifest["topic_ids"] = [topic_id]
        write_json(chief._path(chief_id), manifest)
        state = engine.load(topic_id)
        for text in ("选定的专题第一版正文。", "后续专题第二版正文。"):
            ref = engine.store.save_note(
                state, ResearchNote.model_validate(note(text, ["E1"])), absorbed_through=0
            )
            state.submitted_ref = ref
            state.submissions.append(ref)
            state.status = "submitted"
            engine.store.save(state)
        engine.budget.finish(topic_id)
        original = topics.result(topic_id, note_ref=state.submissions[0])
        revised = topics.result(topic_id, note_ref=state.submissions[1])
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
            "requests": 3,
            "topic_versions_developer_prepared": True,
        }
    finally:
        await engine.close()
        await fixed.close()


async def partial_clear_and_origins(root):
    fixed = await Fixture(root).setup()
    initial = assessment_draft(GBT42048)
    revised = {
        "project": {
            "paragraphs": [
                {
                    "parts": [
                        {"text": '在轨"α信号"约2×，记录路径C:\\data。', "cites": ["E1"]},
                        {"text": "返回后测量≠同阶段机制验证；保留其比较价值。", "cites": ["E2"]},
                    ]
                }
            ]
        },
        "unknowns": [],
        "appendix_topics": [],
    }

    def first(messages):
        return assessment_text(GBT42048, "save_assessment", initial)

    def clear(messages):
        saved = json.loads(
            (root / "v5/assessment-drafts" / (chief_id + ".json")).read_text("utf-8")
        )
        assert saved["unknowns"] and saved["appendix_topics"]
        return assessment_text(GBT42048, "save_assessment", revised)

    model = MeteredScript(
        [first, clear, reply(tool_call("finalize_assessment", {})), summary_step(GBT42048)],
        name="fixed-chief",
    )
    researchers = MeteredScript([], name="fixed-researcher")
    engine, topics, chief = service(root, fixed, model, researchers)
    chief_id = chief.create("明确清空与未提供字段的含义及各部分原稿来源")
    seed_research(engine, chief_id)
    topic_id = topics.create(TopicTask(objective="固定附录待选版本"))
    topic_state = engine.load(topic_id)
    selected_ref = engine.store.save_note(
        topic_state,
        ResearchNote.model_validate(note("预先保存的专题说明。", ["E1"])),
        absorbed_through=0,
    )
    topic_state.submitted_ref = selected_ref
    topic_state.submissions.append(selected_ref)
    topic_state.status = "submitted"
    engine.store.save(topic_state)
    engine.budget.finish(topic_id)
    manifest = chief._manifest(chief_id)
    manifest["topic_ids"] = [topic_id]
    write_json(chief._path(chief_id), manifest)
    initial["appendix_topics"] = [{"topic_id": topic_id, "note_ref": selected_ref}]
    state = engine.load(chief_id)
    added = engine._capture(
        state,
        {
            "evidence-quote-fixture": {
                "reference": {
                    "source_id": fixed.source_id,
                    "passage_id": "fixed-result",
                    "visual_id": None,
                },
                "title": "固定局部读出资料",
                "kind": "external",
                "locator": "Results",
                "quote": "This synthetic fixture separates observation phases.",
                "reading_scope": "text",
            }
        },
    )
    external_alias = next(iter(added))
    revised["project"]["paragraphs"][0]["parts"][1]["cites"] = [external_alias]
    engine.store.save(state)
    try:
        result = await chief.assess(chief_id, allow_research=False)
        assert result["status"] == "submitted", result
        assessment = result["assessment"]
        assert assessment["unknowns"] == assessment["appendix_topics"] == []
        assert not result["selected_topics"] and assessment["total_score"] == 85
        parts = assessment["project"]["paragraphs"][0]["parts"]
        expected_parts = revised["project"]["paragraphs"][0]["parts"]
        assert [part["text"] for part in parts] == [part["text"] for part in expected_parts]
        assert [part["cites"] for part in parts] == [
            [state.aliases["E1"]],
            ["evidence-quote-fixture"],
        ]
        sources = result["author_sources"]
        original_ref = sources["scope"]["author_text_ref"]
        revised_ref = sources["project"]["author_text_ref"]
        assert revised_ref != original_ref
        assert all(row["author_text_ref"] == original_ref for row in sources["dimensions"].values())
        assert (
            sources["unknowns"]["author_text_ref"]
            == sources["appendix_topics"]["author_text_ref"]
            == revised_ref
        )
        assert engine.store.author_text(revised_ref)["content"].count('在轨"α信号"') == 1
        assert engine.budget.usage()["model_calls"] == 6 and engine.load(chief_id).turns_used == 4
        assert_only_assessment(model)
        write_json(root / "result.json", result)
        return {
            "requests": 6,
            "explicit_empty_lists_clear": True,
            "unprovided_fields_preserved": True,
            "per_part_origins": True,
            "literal_prose_and_local_citations_preserved": True,
        }
    finally:
        await engine.close()
        await fixed.close()


async def missing_fields_and_parse_resume(root):
    fixed = await Fixture(root).setup()
    draft = assessment_draft(GBT42048)
    changed = {
        "dimensions": {
            "scientific_objectives": deepcopy(draft["dimensions"]["scientific_objectives"])
        }
    }
    changed["dimensions"]["scientific_objectives"].pop("score")
    required = "scientific_objectives.score：需要作者明确给出分数。"

    def mapping_of(payload):
        return DELIVERY_FIXTURES[tuple(row["text"] for row in payload["paragraphs"])]

    model = MeteredScript(
        [
            assessment_text(GBT42048, "save_assessment", draft),
            assessment_text(GBT42048, "save_assessment", changed, missing_fields=[required]),
        ],
        name="fixed-chief",
    )
    researchers = MeteredScript([], name="fixed-researcher")
    engine, _, chief = service(
        root,
        fixed,
        model,
        researchers,
        parser_steps=[
            mapping_of,
            ModelError("fixture_parser_interruption", category="timeout", request_sent=True),
        ],
    )
    chief_id = chief.create("解析中断与缺少明示分值时保留有效评价稿")
    seed_research(engine, chief_id)
    draft_path = root / "v5/assessment-drafts" / (chief_id + ".json")
    sources_path = root / "v5/assessment-draft-sources" / (chief_id + ".json")
    try:
        interrupted = await chief.assess(chief_id, allow_research=False)
        assert (
            interrupted["status"] == "failed"
            and interrupted["reason"] == "fixture_parser_interruption"
        )
        saved_draft, saved_sources = draft_path.read_bytes(), sources_path.read_bytes()
        assert json.loads(saved_draft)["dimensions"]["scientific_objectives"]["score"] == 22
        pending_raw = engine.load(chief_id).pending_delivery["raw_ref"]
        assert engine.budget.usage()["model_calls"] == 4
        await engine.close()

        def provide_score(messages):
            require_content(messages, required)
            assert (
                draft_path.read_bytes() == saved_draft
                and sources_path.read_bytes() == saved_sources
            )
            assert resumed.load(chief_id).pending_delivery is None
            assert resumed.load(chief_id).turns_used == 3, (
                "Pending parsing must not add an author turn"
            )
            return assessment_text(
                GBT42048,
                "save_assessment",
                {
                    "dimensions": {
                        "scientific_objectives": draft["dimensions"]["scientific_objectives"]
                    }
                },
            )

        continued = MeteredScript(
            [provide_score, reply(tool_call("finalize_assessment", {})), summary_step(GBT42048)],
            name="fixed-chief",
        )
        resumed, _, resumed_chief = service(root, fixed, continued, researchers)
        try:
            result = await resumed_chief.assess(chief_id, allow_research=False)
            assert result["status"] == "submitted" and result["assessment"]["total_score"] == 85
            assert resumed.budget.usage()["model_calls"] == 9
            assert resumed.load(chief_id).turns_used == 5
            assert resumed.store.author_text(pending_raw)["operation"] == "save_assessment"
            assert len(list((root / "v5/author-texts").glob("*.json"))) == 3
            write_json(root / "result.json", result)
            assert_private_not_saved(root)
            return {
                "requests": 9,
                "parser_resume_keeps_author_turns": True,
                "missing_score_returned_to_author": True,
                "successful_draft_unchanged_on_failure": True,
            }
        finally:
            await resumed.close()
    finally:
        await engine.close()
        await fixed.close()


async def untitled_unknown_list(root):
    """A complete unknown list does not need a separately authored display heading."""
    fixed = await Fixture(root).setup()
    model = MeteredScript([], name="fixed-chief")
    researchers = MeteredScript([], name="fixed-researcher")
    engine, _, chief = service(root, fixed, model, researchers)
    chief_id = chief.create("未知事项直接成列表，不因缺少独立展示标题而拒绝保存")
    state = engine.load(chief_id)
    first = '- "2×荧光差异"的适用范围尚未确定。'
    second = "\n- 返回后10^-3阈值与在轨测量的对应关系仍未知，路径C:\\data。"
    content = "交付操作：save_assessment\n\n## unknowns\n\n" + first + "[@E1]" + second + "[@E1]"
    raw_ref = engine.store.save_author_text(
        state, content, "save_assessment", absorbed_through=state.observation_index
    )
    raw = engine.store.author_text(raw_ref)
    mapping = assessment_mapping_type(GBT42048).model_validate(
        {
            "provided_fields": ["unknowns"],
            "project": [],
            "scope": [],
            "dimensions": {criterion.id: None for criterion in GBT42048.criteria},
            "unknowns": [{"title_paragraph_id": None, "paragraph_ids": ["p2"]}],
            "appendix_topics": [],
            "safety_veto": None,
            "missing_fields": [],
        }
    )
    parse_ref = engine.store.save_delivery_parse(
        raw_ref, engine.delivery_parser.config.model_dump(mode="json"), mapping
    )
    state.pending_delivery = {
        "raw_ref": raw_ref,
        "operation": "save_assessment",
        "purpose": "auxiliary",
        "parse_ref": parse_ref,
    }
    try:
        submission = AssessmentSubmission(chief, chief_id)
        accepted = submission.accept_authoring(state, raw_ref, raw, mapping)
        assert accepted.ok and accepted.content == {"saved_parts": ["unknowns"]}
        saved = submission._draft()
        assert set(saved) == {"unknowns"} and len(saved["unknowns"]) == 1
        unknown = saved["unknowns"][0]
        assert unknown["title"] == "未确定事项"
        parts = unknown["text"]["paragraphs"][0]["parts"]
        assert [part["text"] for part in parts] == [first, second]
        assert [part["cites"] for part in parts] == [[raw["aliases"]["E1"]]] * 2
        assert engine.store.author_text(raw_ref)["content"] == content
        assert submission._sources()["unknowns"] == {
            "author_text_ref": raw_ref,
            "parse_ref": parse_ref,
        }
        assert not model.requests and not researchers.requests
        assert engine.budget.usage()["model_calls"] == 0
        write_json(
            root / "result.json",
            {
                "saved_draft": saved,
                "author_text_ref": raw_ref,
                "parse_ref": parse_ref,
                "model_calls": 0,
            },
        )
        return {
            "untitled_complete_list_saved": True,
            "literal_text_and_citations_preserved": True,
            "neutral_display_title": "未确定事项",
            "model_calls": 0,
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
        "partial_clear_and_origins": await partial_clear_and_origins(
            output / "partial-clear-and-origins"
        ),
        "missing_fields_and_parse_resume": await missing_fields_and_parse_resume(
            output / "missing-fields-and-parse-resume"
        ),
        "untitled_unknown_list": await untitled_unknown_list(output / "untitled-unknown-list"),
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
