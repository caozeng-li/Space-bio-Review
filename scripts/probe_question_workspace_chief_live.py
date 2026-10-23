"""Pass each autonomous expert result through the current real chief assessment input.

Four other mytest49 opinions are held fixed. No report or full review is run.
"""

import argparse
import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from probe_expert_trajectory_live import ObservedRuntime, load, production_hashes

from spacebio_review.persistence import write_json
from spacebio_review.v5.assessment import AssessmentSubmission
from spacebio_review.v5.assessment_authoring import decision_type
from spacebio_review.v5.budget import SessionAllowance
from spacebio_review.v5.records import ResearchNote, SessionTask


async def run_case(output, spec):
    group, repeat = spec.split(":")
    source = output / group / f"run-{repeat}"
    result = load(source / "topic-result.json")
    fixture = load("runs/selective_transfer_sample2/fixture.json")
    folder = output / "chief" / f"{group}-{repeat}"
    folder.mkdir(parents=True, exist_ok=False)
    runtime = ObservedRuntime.from_config(output / group / "probe-config.yaml", folder)
    summary = {"expert": str(source), "full_review_runs": 0, "status": "running"}

    def state(role):
        return runtime.load(runtime.create(SessionTask(
            role=role, objective="根据完整申请、专家意见和原文形成评价决定。",
            application=deepcopy(fixture["chief"]["task"]["application"]),
        ), allowance=SessionAllowance(completion_calls=0)))

    try:
        artifacts = deepcopy(fixture["artifacts"])
        artifacts[2] = {"note": result["note"], "evidence": result["readings"]}
        refs = {}
        for artifact in artifacts:
            expert = state("researcher")
            runtime._capture(expert, artifact["evidence"])
            ref = runtime.store.save_note(
                expert, ResearchNote.model_validate(artifact["note"]), absorbed_through=0,
            )
            refs[expert.inquiry_id] = [ref]
            runtime.budget.finish(expert.session_id)
        chief = SimpleNamespace(
            runtime=runtime,
            topics=SimpleNamespace(contribution_refs=lambda topic_id: refs[topic_id]),
            _manifest=lambda _: {**fixture["manifest"], "topic_ids": list(refs)},
        )
        author = state("chief")
        runtime._capture(author, fixture["chief"]["evidence"])
        submission = AssessmentSubmission(chief, author.session_id)
        messages, readings = submission.authoring_input(author, "submit_assessment")
        reply = await runtime._call_model(
            author, runtime.models[author.model_alias], messages, [],
            purpose="completion", stage="submit_assessment",
            response_schema=submission.response_schema(author, "submit_assessment"),
        )
        decision = decision_type(submission.system).model_validate_json(
            reply.message["content"],
        ).model_dump()
        write_json(folder / "decision.json", decision)
        (folder / "chief.md").write_text(
            decision["overall_judgment"] + "\n\n" + "\n\n".join(
                "## " + criterion.label + "\n\n"
                + decision["dimensions"][criterion.id]["reason"]
                for criterion in submission.system.criteria
            ), "utf-8",
        )
        summary.update(status="complete", finish_reason=reply.finish_reason,
                       input_readings=readings)
    except BaseException as exc:
        summary.update(status="failed", error=repr(exc))
        raise
    finally:
        summary.update(
            usage=runtime.budget.usage(),
            production_unchanged=production_hashes()
            == load(output / "design.json")["production_hashes"],
        )
        write_json(folder / "verification.json", summary)
        write_json(folder / "scheduler.json", runtime.scheduler.records)
        await runtime.close()


async def main(args):
    if not args.live:
        raise ValueError("Real model calls require --live")
    gate = asyncio.Semaphore(2)

    async def job(spec):
        async with gate:
            await run_case(Path(args.output).resolve(), spec)

    results = await asyncio.gather(*(job(spec) for spec in args.jobs), return_exceptions=True)
    failures = [f"{spec}: {result!r}" for spec, result in zip(args.jobs, results, strict=True)
                if isinstance(result, BaseException)]
    if failures:
        raise RuntimeError("\n".join(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", default="runs/question_workspace_sample2")
    parser.add_argument("--jobs", nargs="+", required=True)
    asyncio.run(main(parser.parse_args()))
