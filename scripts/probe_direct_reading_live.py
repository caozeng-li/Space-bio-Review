"""Replay one saved topic's materials, then one chief decision; no search or report."""

import argparse
import asyncio
import json
import shutil
from pathlib import Path

import httpx
import yaml

from spacebio_review.evidence.research import load_evidence_config
from spacebio_review.evidence.store import SourceStore
from spacebio_review.models.scheduling import ScheduledModel
from spacebio_review.persistence import write_json
from spacebio_review.prose import prose_inputs
from spacebio_review.review.expertise import ExpertiseCatalogue
from spacebio_review.v5.assessment import AssessmentSubmission
from spacebio_review.v5.budget import SessionAllowance
from spacebio_review.v5.chief import ChiefReview
from spacebio_review.v5.config import load_config
from spacebio_review.v5.records import ResearchNote
from spacebio_review.v5.session import ResearchRuntime
from spacebio_review.v5.sources import VisualReader
from spacebio_review.v5.topic import TopicResearch, TopicTask


def load(path):
    return json.loads(path.read_text("utf-8"))


class DecisionOnly(AssessmentSubmission):
    def completion_operations(self, state):
        return [] if state.assessment_ref else ["submit_assessment"]

    @staticmethod
    def completed(state):
        return state.assessment_ref is not None


async def probe(args):
    run, output = Path(args.run), Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose an empty output directory")
    saved = run / "v5"
    old_manifest = load(next((saved / "chiefs").glob("*.json")))
    old_inquiry = load(saved / "inquiries" / f"{args.topic}.json")
    old_topic = load(saved / "topics" / f"{args.topic}.json")
    source_ids = list(dict.fromkeys(
        load(saved / "source-readings" / f"{rid}.json")["source_id"]
        for rid in old_topic["reading_ids"]
    ))
    output.mkdir(parents=True, exist_ok=True)
    for sid in [*old_topic["application_ids"], *source_ids]:
        shutil.copytree(run / "sources" / sid, output / "sources" / sid)
    config = load_config(args.config)
    data = config.model_dump(mode="json")
    data["budget"] = {"max_model_calls": 40, "max_embedding_calls": 0, "max_seconds": 1800}
    data["session"]["max_turns"] = 14
    for model in data["models"].values():
        model["retries"] = 0
    config_path = output / "probe-config.yaml"
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    runtime = ResearchRuntime.from_config(config_path, output)

    def no_acquisition(request):
        raise AssertionError(f"Saved-material probe must not fetch: {request.url}")

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(no_acquisition)) as client:
            store = SourceStore(output / "sources", client, browser_fallback=False)
            store.restore()
            auxiliary = {
                kind: ScheduledModel(runtime.models[alias], runtime.scheduler, alias)
                for kind, alias in config.auxiliary.items()
            }
            topics = TopicResearch(
                runtime, store, None, application_ids=old_topic["application_ids"],
                rerank_model=auxiliary["rerank"],
                visual_reader=VisualReader(auxiliary["visual"], store),
                catalogue=ExpertiseCatalogue(config.expertise_config, runtime.skills),
                evidence_config=load_evidence_config(config.evidence_config),
            )
            normal_tools = topics.tools

            def reading_tools(tid):
                registry = normal_tools(tid)
                return registry.select([
                    row["function"]["name"] for row in registry.schemas()
                    if row["function"]["name"] not in {"search_sources", "related_papers"}
                ])

            topics.tools = reading_tools
            task = TopicTask.model_validate({
                **old_inquiry["assignment"], "source_ids": source_ids, "research_ids": [],
            })
            topic_id = topics.create(
                task, allowance=SessionAllowance(completion_calls=0) if args.expert_from else None,
            )
            write_json(output / "input.json", {
                "source_run": str(run), "old_topic": args.topic, "task": task,
                "scope": "Same assignment and previously read sources; no old interpretations",
            })
            if args.expert_from:
                previous = load(Path(args.expert_from) / "topic-result.json")
                state = topics.state(topic_id)
                runtime._capture(state, previous["readings"])
                runtime.store.save_note(
                    state, ResearchNote.model_validate(previous["note"]), absorbed_through=0,
                )
                state.status, state.research_finished = "submitted", True
                runtime.store.save(state)
                runtime.budget.finish(state.session_id)
                result = topics.result(topic_id)
            else:
                result = await topics.run(topic_id)
            write_json(output / "topic-result.json", result)
            print({"phase": "expert", "status": result.status,
                   "usage": runtime.budget.usage()}, flush=True)
            if result.status != "submitted":
                raise RuntimeError(str(result.reason))
            text = prose_inputs(result.note)
            (output / "expert.md").write_text(
                "# " + text["title"] + "\n\n" + text["body"] + "\n", "utf-8",
            )

            # Hold the other expert opinions fixed; evaluate once without a chief draft.
            chief = ChiefReview(
                topics, evaluation_system=old_manifest["evaluation_system"],
                admission=old_manifest["review_context"]["admission"],
            )
            chief_id = chief.create(
                "综合已完成的专家意见形成正式评价。",
                allowance=SessionAllowance(completion_calls=1, completion_seconds=120),
            )
            manifest = chief._manifest(chief_id)
            for old_id in old_manifest["topic_ids"]:
                if old_id == args.topic:
                    manifest["topic_ids"].append(topic_id)
                    continue
                inquiry = load(saved / "inquiries" / f"{old_id}.json")
                old = load(saved / "sessions" / f"{inquiry['current_owner_session_id']}.json")
                artifact = load(saved / "notes" / f"{old['note_ref']}.json")
                tid = topics.create(
                    TopicTask(responsibility=inquiry["responsibility"]),
                    allowance=SessionAllowance(completion_calls=0),
                )
                state = topics.state(tid)
                runtime._capture(state, artifact["evidence"])
                runtime.store.save_note(
                    state, ResearchNote.model_validate(artifact["note"]), absorbed_through=0,
                )
                runtime.budget.finish(state.session_id)
                manifest["topic_ids"].append(tid)
            write_json(chief._path(chief_id), manifest)
            state = runtime.load(chief_id)
            state.research_finished = True
            runtime.store.save(state)
            submission = DecisionOnly(chief, chief_id)
            messages, _ = submission.authoring_input(state, "submit_assessment")
            write_json(output / "decision-input.json", json.loads(messages[1]["content"]))
            outcome = await runtime.run(chief_id, submission=submission)
            state = runtime.load(chief_id)
            if not state.assessment_ref:
                raise RuntimeError(str(outcome))
            artifact = submission._artifact(state.assessment_ref)
            write_json(output / "assessment.json", artifact["assessment"])
            decision = artifact["assessment"]
            body = "# 综合评价\n\n" + decision["overall_judgment"] + "\n\n"
            for item in submission.system.criteria:
                dimension = decision["dimensions"][item.id]
                body += f"## {item.label}：{dimension['score']}/{item.max_score}\n\n"
                body += dimension["reason"] + "\n\n"
            (output / "decision.md").write_text(body, "utf-8")
            write_json(output / "verification.json", {
                "usage": runtime.budget.usage(),
                "reader_sessions": topics._task(topic_id)["reading_ids"],
                "tools": [row["name"] for row in runtime.scheduler.records
                          if row["kind"] in {"tool", "delegation"}],
                "chief_note_ref": state.note_ref,
                "expert_from": args.expert_from,
                "expert_status": result.status, "decision_status": outcome.status,
                "full_review_runs": 0, "reports_generated": 0,
            })
            print({"phase": "decision", "status": outcome.status,
                   "usage": runtime.budget.usage()}, flush=True)
            await topics.close()
    except Exception as exc:
        write_json(output / "verification.json", {
            "status": "failed", "error": str(exc), "usage": runtime.budget.usage(),
            "full_review_runs": 0, "reports_generated": 0,
        })
        raise
    finally:
        await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", default="config/runtime.yaml")
    parser.add_argument(
        "--expert-from", help="Reuse a completed local expert probe for decision only",
    )
    asyncio.run(probe(parser.parse_args()))
