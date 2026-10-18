"""Offline multi-author assessment and publication checks; no complete review."""

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

from spacebio_review.models.scheduling import ScheduledModel  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.review.evaluation import GBT42048, GENERAL  # noqa: E402
from spacebio_review.v5.assessment import AssessmentSubmission, bind_citations  # noqa: E402
from spacebio_review.v5.authoring import DeliveryParser  # noqa: E402
from spacebio_review.v5.chief import ChiefReview  # noqa: E402
from spacebio_review.v5.delivery import deliver_assessment  # noqa: E402
from spacebio_review.v5.records import (  # noqa: E402
    ContributionSelection,
    ReportSelection,
    ResearchNote,
)
from spacebio_review.v5.topic import TopicTask  # noqa: E402


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
        else:
            block(key + "：" + str(value))
            mapping[key] = value
    mapping["summary"] = [
        next(identity for paragraph, identity in originals if paragraph == selected)
        for selected in values["summary"]["paragraphs"]
    ]
    DELIVERY_FIXTURES[tuple(blocks)] = mapping
    return reply(content="\n\n".join(blocks))


def seed_contribution(chief, chief_id, artifact):
    """Import one reviewed scientific manuscript into its own local inquiry."""
    engine, topics = chief.runtime, chief.topics
    focus = artifact.get("focus") or artifact["note"]["title"]
    inquiry_id = topics.create(TopicTask(
        objective=focus, impact="保留已研究科学认识，供主审选文与综合。"
    ))
    state = topics.state(inquiry_id)
    engine.store.set_focus(inquiry_id, focus)
    engine._capture(state, artifact["evidence"])
    aliases = {handle: alias for alias, handle in state.aliases.items()}
    body = artifact["note"]["body"]["paragraphs"]
    content = "\n\n".join(
        "".join(part["text"] + "".join(f"[@{aliases[ref]}]" for ref in part["cites"])
                for part in paragraph["parts"])
        for paragraph in body
    )
    raw_ref = engine.store.save_author_text(
        state, content, "submit_research", absorbed_through=state.observation_index,
        phase="contribution", title=artifact["note"]["title"], focus=focus,
    )
    ref = engine.store.save_note(
        state, ResearchNote.model_validate(artifact["note"]), author_text_ref=raw_ref,
        absorbed_through=state.observation_index,
        aliases={key: key for key in artifact["evidence"]},
    )
    state.submitted_ref = ref
    state.submissions.append(ref)
    state.status = "submitted"
    engine.store.save(state)
    engine.budget.finish(engine.budget_account(state))
    manifest = chief._manifest(chief_id)
    manifest["topic_ids"].append(inquiry_id)
    write_json(chief._path(chief_id), manifest)
    return ContributionSelection(inquiry_id=inquiry_id, contribution_ref=ref)


def seed_research(chief, chief_id):
    state = chief.runtime.load(chief_id)
    evidence = next(iter(state.aliases.values()))
    selected = seed_contribution(chief, chief_id, {
        "focus": "实际比较与测量含义",
        "note": note(CHIEF_NOTE, [evidence]),
        "evidence": {evidence: state.evidence[evidence]},
    })
    AssessmentSubmission(chief, chief_id).prepare(
        state, ReportSelection(main_sections=[selected])
    )
    return selected


def assessment_draft(system, *, veto=False):
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


def manuscript_draft(system, *, veto=False):
    values = assessment_draft(system, veto=veto)
    core = (
        "本项目拟在轨观测小肠类器官，返回后检测独立分子终点。申请未提供前期数据；"
        '当前方案可支持比较范围内的描述性认识，不能把细胞内"ROS"直接限定为线粒体来源。'
    )
    values["overall"] = note(core, ["E1"])["body"]
    values["summary"] = deepcopy(values["overall"])
    return values


async def complete_case(root, system, *, veto=False):
    fixed = await Fixture(root).setup()
    draft = manuscript_draft(system, veto=veto)
    model = MeteredScript([assessment_text(system, "submit_assessment", draft)], name="fixed-chief")
    researchers = MeteredScript([], name="unused-researcher")
    engine, topics, chief = service(root, fixed, model, researchers)
    if system.id == "general":
        chief = ChiefReview(topics, evaluation_system=system.id, admission=ADMISSION)
    chief_id = chief.create("已有研究的完整评价局部验收，不检索不派题。")
    selected = seed_research(chief, chief_id)
    try:
        result = await chief.assess(chief_id, allow_research=False)
        assert result["status"] == "submitted", result.get("reason")
        assert engine.load(chief_id).turns_used == len(model.requests) == 1
        assert all(not request["tools"] for request in model.requests)
        assert engine.budget.usage()["model_calls"] == 2
        assessment = result["assessment"]
        assert assessment["summary"] == assessment["overall"]
        assert assessment["summary"] == bind_citations(
            draft["summary"], engine.load(chief_id).aliases
        )
        assert result["report_selection"]["main_sections"] == [selected.model_dump(mode="json")]
        assert result["contributions"][0]["note"]["body"]["paragraphs"][0]["parts"][0]["text"] == (
            CHIEF_NOTE
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
        delivery = await deliver_assessment(
            engine, assessment, evidence=result["evidence"],
            report_selection=result["report_selection"], contributions=result["contributions"],
            author_sources=result["author_sources"], english=False,
        )
        assert delivery["status"] == "completed"
        assert not fixed.requests and not fixed.discovery.queries
        write_json(root / "result.json", result)
        return {
            "status": "passed",
            "author_turns": 1,
            "requests": engine.budget.usage()["model_calls"],
            "same_original_summary": True,
        }
    finally:
        await engine.close()
        await fixed.close()


async def assembly_fixed(root):
    """Map and publish one supplied manuscript without model calls."""
    fixed = await Fixture(root).setup()
    model = MeteredScript([], name="unused-author")
    engine, _, chief = service(root, fixed, model, MeteredScript([], name="unused-researcher"))
    chief_id = chief.create("固定已解析原稿的单次代码装配")
    seed_research(chief, chief_id)
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
    try:
        assert submission.accept_authoring(
            state, raw_ref, raw, mapping, parse_ref=parse_ref
        ).ok
        assert state.assessment_ref and state.report_ref
        assert not model.requests and engine.budget.usage()["model_calls"] == 0
        write_json(root / "result.json", submission.result())
        return {"status": "passed", "requests": 0, "explicit_parse_ref": parse_ref}
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
        "assembly_fixed": await assembly_fixed(output / "assembly-fixed"),
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
