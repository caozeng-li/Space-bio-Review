"""Local report production using saved readings; never searches or runs a full review."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from spacebio_review.evidence.store import SourceStore
from spacebio_review.persistence import write_json
from spacebio_review.prose import prose_inputs
from spacebio_review.v5.chief import ChiefReview
from spacebio_review.v5.delivery import deliver_assessment
from spacebio_review.v5.records import ResearchNote, SessionTask
from spacebio_review.v5.session import ResearchRuntime
from spacebio_review.v5.topic import TopicResearch, TopicTask


def load(path):
    return json.loads(path.read_text("utf-8"))


async def probe(run, output, *, english=False, opinions_from=None, report_only=False):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose a fresh output directory")
    saved = run / "v5"
    manifest = load(next((saved / "chiefs").glob("*.json")))
    old_chief = load(saved / "sessions" / (manifest["chief_id"] + ".json"))
    runtime = ResearchRuntime.from_config("config/runtime.yaml", output)

    def no_acquisition(request):
        raise AssertionError(f"Report probe must not acquire materials: {request.url}")

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(no_acquisition)) as client:
            topics = TopicResearch(
                runtime,
                SourceStore(output / "sources", client),
                None,
                application_ids=[],
                external_research_enabled=False,
            )
            admission = manifest["review_context"]["admission"]
            chief = ChiefReview(
                topics, evaluation_system=manifest["evaluation_system"], admission=admission
            )
            chief_id = chief.create(old_chief["task"]["objective"])
            chief_state = runtime.load(chief_id)
            chief_state.task = SessionTask.model_validate(old_chief["task"])
            runtime._capture(chief_state, old_chief["evidence"])
            runtime.store.save(chief_state)
            fresh = chief._manifest(chief_id)
            fresh["review_context"] = manifest["review_context"]
            opinions = []
            reused = load(opinions_from / "opinions.json") if opinions_from else None
            for index, old_id in enumerate([] if report_only else manifest["topic_ids"]):
                inquiry = load(saved / "inquiries" / (old_id + ".json"))
                old = load(saved / "sessions" / (inquiry["current_owner_session_id"] + ".json"))
                topic_id = topics.create(TopicTask(responsibility=inquiry["responsibility"]))
                state = topics.state(topic_id)
                state.task = SessionTask.model_validate(old["task"])
                state.task.evaluation = {}
                runtime._capture(state, old["evidence"])
                runtime.store.save(state)
                if reused:
                    runtime.store.save_note(
                        state,
                        ResearchNote.model_validate(reused[index]["note"]),
                        absorbed_through=0,
                    )
                else:
                    readings = []
                    for path in (saved / "source-readings").glob("*.json"):
                        reading = load(path)
                        if reading["inquiry_id"] != old_id:
                            continue
                        reader = load(saved / "sessions" / (reading["reading_id"] + ".json"))
                        if reader["note_ref"]:
                            note = load(saved / "notes" / (reader["note_ref"] + ".json"))
                            readings.append(
                                {
                                    "source_id": reading["source_id"],
                                    "question": reading["question"],
                                    "interpretation": prose_inputs(note["note"]),
                                }
                            )
                    # All reading was done in the source run; enter the real delivery phase.
                    state.research_finished = True
                    runtime.store.save(state)
                    outcome = await runtime.run(
                        state.session_id,
                        context=lambda: {"source_readings": readings},
                    )
                    if outcome.status != "submitted":
                        raise RuntimeError(str(outcome))
                state = runtime.load(state.session_id)
                note = runtime.store.note(state.note_ref)
                opinions.append({"note": note["note"], "evidence": note["evidence"]})
                view = prose_inputs(note["note"])
                (output / f"expert-{index + 1}.md").write_text(
                    "# " + view["title"] + "\n\n" + view["body"] + "\n", "utf-8"
                )
                fresh["topic_ids"].append(topic_id)
                write_json(output / "opinions.json", opinions)
                print(
                    json.dumps({"expert": index + 1, "characters": len(view["body"])}), flush=True
                )
            write_json(chief._path(chief_id), fresh)
            if report_only:
                # Controlled writing comparison: identical opinions, originals and decision.
                fixture = load(saved / "assessments" / (old_chief["assessment_ref"] + ".json"))
                fixture["chief_id"] = chief_id
                chief_state.assessment_ref = "assessment-fixture"
                chief_state.research_finished = True
                write_json(
                    runtime.store._path("assessments", chief_state.assessment_ref), fixture
                )
                runtime.store.save(chief_state)
            result = await chief.assess(chief_id, allow_research=False)
            write_json(output / "chief-result.json", result)
            if not result.get("report_ref"):
                raise RuntimeError(result.get("reason"))
            if report_only:
                decision = {
                    key: value for key, value in result["assessment"].items()
                    if key not in {"body", "sections"}
                }
                assert decision == {
                    key: value for key, value in fixture["assessment"].items()
                    if key not in {"body", "sections"}
                }, "Report-only authoring changed the fixed evaluation"
            print(json.dumps({"chief": "completed"}), flush=True)
            delivered = await deliver_assessment(
                runtime,
                result["assessment"],
                evidence=result["evidence"],
                author_sources=result["author_sources"],
                admission=admission,
                english=english,
                translation_model=runtime.models["translation"],
                root=output,
            )
            report = (output / "report.zh.md").read_text("utf-8")
            summary = {
                "source_run": str(run),
                "report_only": report_only,
                "delivery": delivered["status"],
                "characters": len(report),
                "headings": [line for line in report.splitlines() if line.startswith("## ")],
                "internal_markers": [
                    value
                    for value in [
                        "note-", "evidence-", "safety_veto", "applies:", "当前科学问题", "直接回答"
                    ]
                    if value in report
                ],
                "usage": runtime.budget.usage(),
                "sections": [
                    {
                        "title": section["title"],
                        "characters": len(prose_inputs(section["body"])),
                    }
                    for section in result["assessment"].get("sections", [])
                ],
                "authoring": [
                    {
                        "operation": raw["operation"],
                        "characters": len(raw["content"]),
                        "finish_reason": raw["finish_reason"],
                    }
                    for path in sorted((output / "v5" / "author-texts").glob("*.json"))
                    if (raw := load(path))["operation"] not in {
                        "submit_research", "update_research",
                    }
                ],
            }
            write_json(output / "probe-summary.json", summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
    finally:
        write_json(output / "metrics.json", runtime.scheduler.metrics())
        await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=Path("runs/mytest44_sample2"))
    parser.add_argument("--output", type=Path, required=True)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--opinions-from", type=Path)
    inputs.add_argument(
        "--report-only", action="store_true",
        help="Use the saved writing input and decision; call only report writing and translation.",
    )
    parser.add_argument("--english", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        probe(
            args.run, args.output, english=args.english,
            opinions_from=args.opinions_from, report_only=args.report_only,
        )
    )
