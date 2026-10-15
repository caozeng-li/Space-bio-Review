"""Bounded national-standard stage probes; never starts the review coordinator."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from spacebio_review.admission import FORMAL_REVIEW_VERSION
from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.diagnostics import SubmissionRecorder
from spacebio_review.agents.skills import SkillLibrary
from spacebio_review.context import load_application
from spacebio_review.evidence.store import SourceStore
from spacebio_review.models.config import load_config
from spacebio_review.models.provider import model_class
from spacebio_review.review.evaluation import GBT42048, configure_evaluation
from spacebio_review.review.project import load_preparation_config
from spacebio_review.v4.assessment import (
    assessment_contract,
    assessment_request,
    assessment_system,
    synthesize,
)
from spacebio_review.v4.assessment_input import build_evaluation_context, freeze_review
from spacebio_review.v4.budget import Budget
from spacebio_review.v4.config import ReportingConfig, V4Config
from spacebio_review.v4.execution import Execution
from spacebio_review.v4.manuscript_call import ManuscriptCall, ManuscriptConfig
from spacebio_review.v4.preparation import Preparation
from spacebio_review.v4.records import Records, write_json
from spacebio_review.v4.report_authoring import PROMPT as AUTHOR_PROMPT
from spacebio_review.v4.report_authoring import author_request, writing_plan


def expert_fixture():
    return {
        "project": {},
        "experts": {
            "expert-1": {
                "purpose": "评价根系定向研究的科学意义、创新、实验设计、前期基础及成果解释。",
                "profile_labels": ["植物生理与实验设计"],
                "method_skill_ids": ["causal-experimental-design", "observation-statistics"],
            },
            "expert-2": {
                "purpose": "评价已有植物培养载荷上的工程实施、安全可靠性和资源需求。",
                "profile_labels": ["空间实验工程与安全"],
                "method_skill_ids": ["spaceflight-operations", "space-experiment-safety"],
            },
        },
    }


async def run_assessment(args, output, records, budget, runtime_config, prep_config):
    skill = SkillLibrary(runtime_config.skills_dir).get(runtime_config.evaluation_skill)
    evaluation = build_evaluation_context(
        skill, runtime_config.evaluation_core, prep_config.evaluation, system=GBT42048
    )
    caller = ManuscriptCall(
        records, ManuscriptConfig.load(args.assessment_config), budget=budget, env_file=".env"
    )
    coverage = {
        aspect: {
            "status": "unassessed",
            "question_details": [],
            "result_refs": [],
            "analysis_limits": ["局部综合评价探针未执行问题生成或专业研究。"],
        }
        for aspect in GBT42048.aspects
    }
    admission = {
        "kind": "proposal_content",
        "rule_version": FORMAL_REVIEW_VERSION,
        "status": "skipped",
        "reason": "本局部探针直接验证综合评分，未执行计划书内容形式审查。",
    }
    try:
        async with httpx.AsyncClient() as client:
            store = SourceStore(output / "sources", client)
            bundle = await load_application(store, Path(args.plan), [])
            application = [
                store.retained(source.source_id).model_dump(mode="json")
                for source in bundle.sources
            ]
            write_json(
                output / "fixture.json",
                {
                    "stage": "assessment",
                    "origin": "local assessment probe; all upstream stages intentionally skipped",
                    "application": bundle,
                    "admission": admission,
                    "research_coverage": coverage,
                    "professional_opinions": [],
                },
            )
            frozen = freeze_review(
                records, [], application, evaluation, coverage=coverage, admission=admission
            )
            frozen_ref = records.put("frozen-review", frozen)
            write_json(output / "frozen.json", frozen)
            prompt, materials = assessment_request(frozen)
            request = caller.request(
                prompt, materials, assessment_contract(frozen), caller.config.assessment_tokens
            )
            capacity = caller.preview(request)
            write_json(output / "request-input.json", {"request": request, "capacity": capacity})
            if not capacity["fits"]:
                raise ValueError("Probe input exceeds the assessment model capacity")
            assessment_ref = await synthesize(caller, frozen_ref)
            result = records.value(assessment_ref)
            write_json(output / "assessment.json", result)
            print(
                json.dumps(
                    {
                        "stage": "assessment",
                        "total_score": result["total_score"],
                        "score_grade": result["score_grade"],
                        "safety_veto": result["safety_veto"]["applies"],
                        "recommendation": result["recommendation"],
                        "output": str(output),
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        budget.save()
        write_json(output / "metrics.json", caller.scheduler.metrics())


async def run_summary(args, output, records, budget):
    source = Path(args.from_case).resolve()
    frozen = json.loads((source / "frozen.json").read_text("utf-8"))
    assessment = json.loads((source / "assessment.json").read_text("utf-8"))
    # Construct the new writer fixture from old probe output; never edit the source archive.
    adapted = assessment_system(assessment).id == "gbt42048_2022" and "overall" in assessment
    if adapted:
        assessment.pop("overall")
    keys = writing_plan(assessment)[0]
    caller = ManuscriptCall(
        records, ManuscriptConfig.load(args.assessment_config), budget=budget, env_file=".env"
    )
    try:
        write_json(
            output / "fixture.json",
            {
                "stage": "summary",
                "origin": "existing assessment; no assessment or upstream stages rerun",
                "from_case": str(source),
                "units": keys,
                "fixture_adaptation": "removed obsolete national overall from writer input"
                if adapted
                else None,
            },
        )
        write_json(output / "writer-assessment.json", assessment)
        materials, contract, tokens = author_request(caller, frozen, assessment, keys)
        request = caller.request(AUTHOR_PROMPT, materials, contract, tokens)
        capacity = caller.preview(request)
        write_json(output / "request-input.json", {"request": request, "capacity": capacity})
        if not capacity["fits"]:
            raise ValueError("Probe input exceeds the summary model capacity")
        result = await caller.submit(
            "writer/authorship/" + "+".join(keys),
            AUTHOR_PROMPT,
            materials,
            contract,
            max_tokens=tokens,
        )
        write_json(output / "summary.json", result)
        print(
            json.dumps(
                {"stage": "summary", "from_case": str(source), "output": str(output)},
                ensure_ascii=False,
            )
        )
    finally:
        budget.save()
        write_json(output / "metrics.json", caller.scheduler.metrics())


async def run(args):
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty output directory for a new probe")
    records = Records(output)
    config = V4Config(
        evaluation_system=GBT42048.id,
        max_model_calls=1,
        max_embedding_calls=0,
        completion_reserve_calls=0,
        completion_reserve_seconds=0,
        max_seconds=600,
    )
    reporting = ReportingConfig()
    budget = Budget(records, config, reporting)
    if args.stage == "summary":
        return await run_summary(args, output, records, budget)
    runtime_config, prep_config = configure_evaluation(
        GBT42048,
        load_config("config/runtime.yaml"),
        load_preparation_config("config/preparation.yaml"),
    )
    if args.stage == "assessment":
        return await run_assessment(args, output, records, budget, runtime_config, prep_config)
    runtime = AgentRuntime.from_config(
        runtime_config,
        model_factory=lambda model: model_class(model)(model, env_file=".env", budget=budget),
    )
    runtime.scheduler.request_budget = budget
    runtime.recorder = SubmissionRecorder(output)
    evaluation = build_evaluation_context(
        runtime.evaluation,
        runtime_config.evaluation_core,
        prep_config.evaluation,
        system=GBT42048,
    )
    try:
        async with httpx.AsyncClient() as client:
            store = SourceStore(output / "sources", client)
            bundle = await load_application(store, Path(args.plan), [])
            ex = Execution(runtime, records, config, evaluation, reporting)
            prep = Preparation(ex, store, bundle, prep_config)
            package = expert_fixture()
            write_json(
                output / "fixture.json",
                {
                    "origin": "constructed expert capabilities; no preparation or expert run",
                    "package": package,
                    "application": bundle,
                },
            )
            generation = prep.questions(package)
            capacity = generation.preview()
            write_json(
                output / "request-input.json",
                {
                    "objective": generation.objective,
                    "evaluation": evaluation,
                    "materials": [m.data for m in generation.materials()],
                    "capacity": capacity,
                },
            )
            if not capacity["fits"]:
                raise ValueError("Probe input exceeds the question model capacity")
            result = await generation.run()
            write_json(output / "questions.json", result)
            print(
                json.dumps(
                    {
                        "stage": "questions",
                        "questions": len(result["questions"]),
                        "aspects": sorted({a for q in result["questions"] for a in q["aspects"]}),
                        "output": str(output),
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        budget.save()
        write_json(output / "metrics.json", runtime.scheduler.metrics())
        await runtime.close()


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage", choices=("questions", "assessment", "summary"), default="questions"
    )
    parser.add_argument("--plan", default="tests/fixtures/gbt-plan-complete.md")
    parser.add_argument("--from-case", help="Existing assessment probe directory for summary")
    parser.add_argument("--output", required=True)
    parser.add_argument("--assessment-config", default="config/assessment.yaml")
    args = parser.parse_args()
    if args.stage == "summary" and not args.from_case:
        parser.error("--from-case is required for --stage summary")
    asyncio.run(run(args))
