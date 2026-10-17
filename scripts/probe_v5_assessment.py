"""Offline single-manuscript assessment and publication checks; no complete review."""

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
    note,
    reply,
)
from probe_v5_topic import Fixture  # noqa: E402

from spacebio_review.models.provider import ModelError  # noqa: E402
from spacebio_review.models.scheduling import ScheduledModel  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.review.evaluation import GBT42048, GENERAL  # noqa: E402
from spacebio_review.v5.assessment import AssessmentSubmission, bind_citations  # noqa: E402
from spacebio_review.v5.authoring import DeliveryParser  # noqa: E402
from spacebio_review.v5.chief import ChiefReview  # noqa: E402
from spacebio_review.v5.delivery import deliver_assessment  # noqa: E402
from spacebio_review.v5.records import ResearchNote  # noqa: E402


def service(root, fixed, model, researchers, *, parser_steps=(), prepared_application=True):
    engine, topics, chief = chief_service(
        root, fixed, model, researchers, prepared_application=prepared_application
    )
    parser = ParserModel(parser_steps, budget=engine.budget)
    engine.delivery_parser = DeliveryParser(
        ScheduledModel(parser, engine.scheduler, "delivery_parser")
    )
    return engine, topics, chief


def assessment_text(system, operation, values, *, missing_fields=()):
    """A manually authored fixture; summary reuses existing manuscript paragraphs."""
    assert operation == "submit_assessment"
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
        else {
            "recommendation": None,
            "support_scope": [],
        }
    )
    mapping.update(overall=[], summary=[])
    originals = []

    def block(text):
        blocks.append(text)
        return f"p{len(blocks)}"

    def paragraphs(value):
        ids = []
        for paragraph in value["paragraphs"]:
            identity = block(
                "".join(
                    part["text"] + "".join(f"[@{ref}]" for ref in part["cites"])
                    for part in paragraph["parts"]
                )
            )
            originals.append((paragraph, identity))
            ids.append(identity)
        return ids

    for key, value in values.items():
        if key == "summary":
            continue
        block("## " + key)
        if key in {"project", "scope", "support_scope", "overall"}:
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
    mapping["summary"] = [
        next(identity for paragraph, identity in originals if paragraph == selected)
        for selected in values["summary"]["paragraphs"]
    ]
    DELIVERY_FIXTURES[tuple(blocks)] = mapping
    return reply(content="\n\n".join(blocks))


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


def manuscript_draft(system, *, veto=False, selected=()):
    values = assessment_draft(system, veto=veto, selected=selected)
    core = (
        "本项目拟在轨观测小肠类器官，返回后检测独立分子终点。申请未提供前期数据；"
        '当前方案可支持比较范围内的描述性认识，不能把细胞内"ROS"直接限定为线粒体来源。'
    )
    values["overall"] = note(core, ["E1"])["body"]
    values["summary"] = deepcopy(values["overall"])
    return values


async def complete_case(root, system, *, veto=False, interrupt=False):
    fixed = await Fixture(root).setup()
    draft = manuscript_draft(system, veto=veto)
    model = MeteredScript([assessment_text(system, "submit_assessment", draft)], name="fixed-chief")
    researchers = MeteredScript([], name="unused-researcher")
    interruption = (
        [ModelError("fixture_parser_interruption", category="timeout", request_sent=True)]
        if interrupt
        else ()
    )
    engine, topics, chief = service(root, fixed, model, researchers, parser_steps=interruption)
    if system.id == "general":
        chief = ChiefReview(topics, evaluation_system=system.id, admission=ADMISSION)
    chief_id = chief.create("已有研究的完整评价局部验收，不检索不派题。")
    seed_research(engine, chief_id)
    try:
        result = await chief.assess(chief_id, allow_research=False)
        if interrupt:
            state = engine.load(chief_id)
            assert state.pending_delivery and not state.report_ref
            original_ref = state.pending_delivery["raw_ref"]
            original_content = engine.store.author_text(original_ref)["content"]
            assert len(model.requests) == 1
            result = await chief.assess(chief_id, allow_research=False)
            assert result["report_author_source"]["author_text_ref"] == original_ref
            assert engine.store.author_text(original_ref)["content"] == original_content
        assert result["status"] == "submitted", result.get("reason")
        assert engine.load(chief_id).turns_used == len(model.requests) == 1
        assert all(not request["tools"] for request in model.requests)
        assert engine.budget.usage()["model_calls"] == 2 + int(interrupt)
        assessment = result["assessment"]
        assert assessment["summary"] == assessment["overall"]
        assert assessment["summary"] == bind_citations(
            draft["summary"], engine.load(chief_id).aliases
        )
        if system.score_grades:
            assert assessment["total_score"] == 85 and assessment["score_grade"] == "A"
            assert assessment["recommendation"] == (
                "not_support" if veto else system.grade_for(85).recommendation
            )
        else:
            assert "total_score" not in assessment
            assert assessment["recommendation"] == "support"
            assert assessment["dimensions"]["scientific_value"]["grade"] is None
        raw_ref = result["report_author_source"]["author_text_ref"]
        assert result["last_author_text_ref"] == raw_ref
        assert (
            result["author_sources"]["summary"]["selection"]
            == result["author_sources"]["overall"]["selection"]
        )
        assert "## summary" not in engine.store.author_text(raw_ref)["content"]
        before = engine.budget.usage()
        again = await chief.assess(chief_id, allow_research=False)
        assert again["report_ref"] == result["report_ref"]
        assert engine.budget.usage() == before
        delivery = await deliver_assessment(
            engine, assessment, evidence=result["evidence"], english=False
        )
        assert delivery["status"] == "completed"
        assert not fixed.requests and not fixed.discovery.queries
        write_json(root / "result.json", result)
        return {
            "status": "passed",
            "author_turns": 1,
            "requests": before["model_calls"],
            "same_original_summary": True,
            "repeated_resume_no_requests": True,
        }
    finally:
        await engine.close()
        await fixed.close()


async def assembly_resume(root):
    """Interrupt after scored artifact persistence; resume uses saved mapping, zero models."""
    fixed = await Fixture(root).setup()
    model = MeteredScript([], name="unused-author")
    engine, _, chief = service(root, fixed, model, MeteredScript([], name="unused-researcher"))
    chief_id = chief.create("固定已解析原稿的代码装配恢复")
    state = engine.load(chief_id)
    response = assessment_text(GBT42048, "submit_assessment", manuscript_draft(GBT42048))
    raw_ref = engine.store.save_author_text(
        state,
        response.message["content"],
        "submit_assessment",
        absorbed_through=state.observation_index,
    )
    raw = engine.store.author_text(raw_ref)
    submission = AssessmentSubmission(chief, chief_id)
    schema, _, _ = submission.authoring_contract(raw)
    mapping = schema.model_validate(
        DELIVERY_FIXTURES[tuple(row["text"] for row in raw["paragraphs"])]
    )
    parse_ref = engine.store.save_delivery_parse(raw_ref, {}, mapping)
    state.pending_delivery = {
        "raw_ref": raw_ref,
        "operation": "submit_assessment",
        "purpose": "completion",
        "parse_ref": parse_ref,
    }
    engine.store.save(state)
    publish = submission._publish

    def interrupted(*args):
        raise OSError("fixed_assembly_interruption")

    submission._publish = interrupted
    try:
        try:
            submission.accept_authoring(state, raw_ref, raw, mapping)
            raise AssertionError("Expected assembly interruption")
        except OSError:
            pass
        persisted = engine.load(chief_id)
        assert persisted.assessment_ref and not persisted.report_ref
        old_bytes = engine.store._path("assessments", persisted.assessment_ref).read_bytes()
        submission._publish = publish
        assert submission.accept_authoring(persisted, raw_ref, raw, mapping).ok
        report_ref = persisted.report_ref
        assert submission.accept_authoring(persisted, raw_ref, raw, mapping).ok
        assert persisted.report_ref == report_ref
        assert engine.store._path("assessments", persisted.assessment_ref).read_bytes() == old_bytes
        assert not model.requests and engine.budget.usage()["model_calls"] == 0
        write_json(root / "result.json", submission.result())
        return {"status": "passed", "requests": 0, "immutable_assessment": True}
    finally:
        await engine.close()
        await fixed.close()


async def run(output):
    if output.exists() and any(output.iterdir()):
        raise ValueError("probe_requires_empty_output_directory")
    results = {
        "gbt85": await complete_case(output / "gbt85", GBT42048),
        "gbt85_veto": await complete_case(output / "gbt85-veto", GBT42048, veto=True),
        "general": await complete_case(output / "general", GENERAL),
        "parser_resume": await complete_case(output / "parser-resume", GBT42048, interrupt=True),
        "assembly_resume": await assembly_resume(output / "assembly-resume"),
    }
    summary = {
        "status": "passed",
        "results": results,
        "real_model_requests": 0,
        "full_review_runs": 0,
        "science": "Fixed manual fixtures; mechanical acceptance only.",
    }
    write_json(output / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.output)), ensure_ascii=False, indent=2))
