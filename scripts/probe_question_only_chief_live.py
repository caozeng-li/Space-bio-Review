"""Current chief assessment consuming one expert's complete per-call archive."""

import argparse
import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from probe_expert_trajectory_live import ObservedRuntime, load, production_hashes
from spacebio_review.evidence.reading_render import render_originals
from spacebio_review.persistence import write_json
from spacebio_review.prose import prose_inputs
from spacebio_review.v5.assessment import AssessmentSubmission
from spacebio_review.v5.assessment_authoring import decision_type
from spacebio_review.v5.budget import SessionAllowance
from spacebio_review.v5.records import ResearchNote, SessionTask
from spacebio_review.v5.research_context import application_originals


class StepAssessment(AssessmentSubmission):
    def _review_input(self, state):
        view = super()._review_input(state)
        view["expert_opinions"].insert(self.chief.slot, self.chief.expert_delivery)
        view["research_records_instruction"] = (
            "专题内按调用顺序归档的研究记录来自同一位专家，不是不同专家的独立投票。"
            "各次研究成果直接交付，没有经过最终专家重写。结合原文综合其中的有效认识、"
            "支持和限制；出现相互矛盾的解释时依据申请和材料判断，不默认次数多或最后出现者正确。"
        )
        self.runtime._capture(state, self.chief.readings)
        present = application_originals(view["application"], state.evidence)
        view["originals"] = render_originals({
            handle: {**item, "citation": f"[{handle}](evidence:{handle})"}
            for handle, item in state.evidence.items() if handle not in present
        })
        return view


async def run_chief(root, spec):
    condition, topic, repeat = spec.split(":")
    run = root / condition / topic / f"run-{repeat}"
    result = load(run / "topic-result.json")
    if result["status"] != "submitted":
        raise ValueError("expert_not_submitted:" + spec)
    folder = root / "chief" / f"{condition}-{topic}-{repeat}"
    folder.mkdir(parents=True, exist_ok=False)
    design = load(root / "design.json")
    fixture = load("runs/selective_transfer_sample2/fixture.json")
    runtime = ObservedRuntime.from_config(root / "probe-config.yaml", folder)
    summary = {"case": spec, "status": "running", "full_review_runs": 0}

    def create_state(role):
        return runtime.load(runtime.create(SessionTask(
            role=role, objective="根据完整申请、专题研究成果及原文形成国标评价。",
            application=deepcopy(fixture["chief"]["task"]["application"]),
        ), allowance=SessionAllowance(completion_calls=0)))

    try:
        refs = {}
        slot = 2 if topic == "engineering" else 1
        for index, artifact in enumerate(fixture["artifacts"]):
            if index == slot:
                continue
            expert = create_state("researcher")
            runtime._capture(expert, artifact["evidence"])
            ref = runtime.store.save_note(expert, ResearchNote.model_validate(artifact["note"]),
                                          absorbed_through=0)
            refs[expert.inquiry_id] = [ref]
            runtime.budget.finish(expert.session_id)
        delivery = {"research_topic": design["assignments"][topic]["responsibility"]}
        if condition == "questions":
            steps = load(run / "research-steps.json")
            delivery["research_steps"] = [
                {k: row[k] for k in ("turn", "questions_received", "studied_questions", "opinion")}
                for row in steps if row.get("opinion")
            ]
            delivery["stop"] = {
                "expert_stopped": steps[-1]["stop_research"],
                "reason": steps[-1]["stop_reason"],
                "remaining_questions": steps[-1]["next_questions"],
            }
        else:
            delivery["opinion"] = prose_inputs(result["note"])
        chief = SimpleNamespace(
            runtime=runtime,
            topics=SimpleNamespace(contribution_refs=lambda topic_id: refs[topic_id]),
            _manifest=lambda _: {**fixture["manifest"], "topic_ids": list(refs)},
            slot=slot, expert_delivery=delivery, readings=result["readings"],
        )
        author = create_state("chief")
        runtime._capture(author, fixture["chief"]["evidence"])
        submission = StepAssessment(chief, author.session_id)
        messages, readings = submission.authoring_input(author, "submit_assessment")
        reply = await runtime._call_model(
            author, runtime.models[author.model_alias], messages, [],
            purpose="completion", stage="submit_assessment",
            response_schema=submission.response_schema(author, "submit_assessment"),
        )
        decision = decision_type(submission.system).model_validate_json(reply.message["content"]).model_dump()
        write_json(folder / "decision.json", decision)
        (folder / "chief.md").write_text(decision["overall_judgment"] + "\n\n" + "\n\n".join(
            "## " + criterion.label + "\n\n" + decision["dimensions"][criterion.id]["reason"]
            for criterion in submission.system.criteria
        ), "utf-8")
        summary.update(status="complete", input_readings=len(readings),
                       delivered_steps=len(delivery.get("research_steps", [delivery])))
    except BaseException as exc:
        summary.update(status="failed", error=repr(exc))
        raise
    finally:
        summary.update(usage=runtime.budget.usage(),
                       production_unchanged=production_hashes() == design["production_hashes"])
        write_json(folder / "verification.json", summary)
        write_json(folder / "scheduler.json", runtime.scheduler.records)
        await runtime.close()


async def main(args):
    if not args.live:
        raise ValueError("Use --live for real services")
    gate = asyncio.Semaphore(2)

    async def job(spec):
        async with gate:
            return await run_chief(Path(args.output).resolve(), spec)

    outcomes = await asyncio.gather(*(job(s) for s in args.jobs), return_exceptions=True)
    failures = [f"{s}: {r!r}" for s, r in zip(args.jobs, outcomes) if isinstance(r, BaseException)]
    if failures:
        raise RuntimeError("\n".join(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", default="runs/question_only_context_sample2")
    parser.add_argument("--jobs", nargs="+", required=True)
    asyncio.run(main(parser.parse_args()))
