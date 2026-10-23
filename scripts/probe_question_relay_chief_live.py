"""Deliver every relay round directly to the current chief decision interface."""

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


class ChainAssessment(AssessmentSubmission):
    def _review_input(self, state):
        view = super()._review_input(state)
        records = self.chief.round_records
        view["expert_opinions"].insert(
            self.chief.slot,
            {
                "research_topic": self.chief.responsibility,
                "research_rounds": records,
                "chain_stop": self.chief.stop,
            },
        )
        view["research_records_instruction"] = (
            "同一专题下的研究轮次按时间保存，各轮成果直接交付，没有再经专家综合改写。"
            "它们属于同一研究链；重复说法不是独立证据。结合各轮实际论据与原文形成评价。"
        )
        for readings in self.chief.round_readings:
            self.runtime._capture(state, readings)
        present = application_originals(view["application"], state.evidence)
        # The same raw-source delivery rule applies to baseline and relay.
        view["originals"] = render_originals(
            {
                handle: {**item, "citation": f"[{handle}](evidence:{handle})"}
                for handle, item in state.evidence.items()
                if handle not in present
            }
        )
        return view


async def run_chief(root, spec):
    condition, topic, repeat = spec.split(":")
    chain = load(root / condition / f"{topic}-{repeat}" / "chain.json")
    folder = root / "chief" / f"{condition}-{topic}-{repeat}"
    folder.mkdir(parents=True, exist_ok=False)
    design = load(root / "design.json")
    fixture = load("runs/selective_transfer_sample2/fixture.json")
    runtime = ObservedRuntime.from_config(root / "probe-config.yaml", folder)
    summary = {"case": spec, "status": "running", "full_review_runs": 0}

    def state(role):
        return runtime.load(
            runtime.create(
                SessionTask(
                    role=role,
                    objective="根据完整申请、专题研究成果及原文形成国标评价。",
                    application=deepcopy(fixture["chief"]["task"]["application"]),
                ),
                allowance=SessionAllowance(completion_calls=0),
            )
        )

    try:
        refs = {}
        slot = 2 if topic == "engineering" else 1
        for index, artifact in enumerate(fixture["artifacts"]):
            if index == slot:
                continue
            expert = state("researcher")
            runtime._capture(expert, artifact["evidence"])
            ref = runtime.store.save_note(
                expert,
                ResearchNote.model_validate(artifact["note"]),
                absorbed_through=0,
            )
            refs[expert.inquiry_id] = [ref]
            runtime.budget.finish(expert.session_id)
        records, readings = [], []
        for row in chain["rounds"]:
            run = Path(row["run"])
            result = load(run / "topic-result.json")
            packet = (
                load(run / "question-handoff.json")
                if (run / "question-handoff.json").exists()
                else {}
            )
            record = {"round": row["round"], "status": row["status"], **packet}
            if result.get("note"):
                record["opinion"] = prose_inputs(result["note"])
            records.append(record)
            readings.append(result["readings"])
        chief = SimpleNamespace(
            runtime=runtime,
            topics=SimpleNamespace(contribution_refs=lambda topic_id: refs[topic_id]),
            _manifest=lambda _: {**fixture["manifest"], "topic_ids": list(refs)},
            round_records=records,
            round_readings=readings,
            slot=slot,
            responsibility=design["assignments"][topic]["responsibility"],
            stop=chain.get("stop", chain.get("error")),
        )
        author = state("chief")
        runtime._capture(author, fixture["chief"]["evidence"])
        submission = ChainAssessment(chief, author.session_id)
        messages, seen = submission.authoring_input(author, "submit_assessment")
        reply = await runtime._call_model(
            author,
            runtime.models[author.model_alias],
            messages,
            [],
            purpose="completion",
            stage="submit_assessment",
            response_schema=submission.response_schema(author, "submit_assessment"),
        )
        decision = (
            decision_type(submission.system)
            .model_validate_json(
                reply.message["content"],
            )
            .model_dump()
        )
        write_json(folder / "decision.json", decision)
        (folder / "chief.md").write_text(
            decision["overall_judgment"]
            + "\n\n"
            + "\n\n".join(
                "## " + criterion.label + "\n\n" + decision["dimensions"][criterion.id]["reason"]
                for criterion in submission.system.criteria
            ),
            "utf-8",
        )
        summary.update(status="complete", delivered_rounds=len(records), input_readings=seen)
    except BaseException as exc:
        summary.update(status="failed", error=repr(exc))
        raise
    finally:
        summary.update(
            usage=runtime.budget.usage(),
            production_unchanged=production_hashes() == design["production_hashes"],
        )
        write_json(folder / "verification.json", summary)
        write_json(folder / "scheduler.json", runtime.scheduler.records)
        await runtime.close()


async def main(args):
    if not args.live:
        raise ValueError("Use --live for real services")
    gate = asyncio.Semaphore(args.concurrency)

    async def job(spec):
        root = Path(args.output).resolve()
        condition, topic, repeat = spec.split(":")
        chain_path = root / condition / f"{topic}-{repeat}" / "chain.json"
        if args.watch:
            while not chain_path.exists():
                await asyncio.sleep(15)
            if (root / "chief" / f"{condition}-{topic}-{repeat}").exists():
                return
        async with gate:
            await run_chief(root, spec)

    results = await asyncio.gather(*(job(s) for s in args.jobs), return_exceptions=True)
    errors = [
        f"{s}: {r!r}"
        for s, r in zip(args.jobs, results, strict=True)
        if isinstance(r, BaseException)
    ]
    if errors:
        raise RuntimeError("\n".join(errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", default="runs/question_relay_sample2")
    parser.add_argument("--jobs", nargs="+", required=True)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--concurrency", type=int, default=2)
    asyncio.run(main(parser.parse_args()))
