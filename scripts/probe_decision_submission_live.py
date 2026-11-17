"""Replay full saved review inputs through the current production decision stage."""

import argparse
import asyncio
import json
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from spacebio_review.persistence import write_json
from spacebio_review.v5.assessment import AssessmentSubmission
from spacebio_review.v5.budget import SessionAllowance
from spacebio_review.v5.citation_writing import author_messages
from spacebio_review.v5.records import SessionTask
from spacebio_review.v5.session import ResearchRuntime


class SavedInputSubmission(AssessmentSubmission):
    """Replace only completed research input and the remaining-work list."""

    def __init__(self, runtime, state, view):
        self.view = view
        chief = SimpleNamespace(runtime=runtime, _manifest=lambda _: {
            "review_context": {"evaluation": view["evaluation"], "admission": {}},
        })
        super().__init__(chief, state.session_id)

    def _review_input(self, state):
        return deepcopy(self.view)

    def completion_operations(self, state):
        return [] if state.assessment_ref else ["submit_assessment"]


class RecordingRuntime(ResearchRuntime):
    async def _call_model(self, state, *args, **kwargs):
        reply = await super()._call_model(state, *args, **kwargs)
        self.replies[state.session_id] = reply
        return reply


async def run(root, assessments, repeats):
    views = {}
    for index, path in enumerate(assessments, 1):
        artifact = json.loads(path.read_text("utf-8"))
        views[f"input-{index}"] = artifact["review_input"]
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / "inputs.json", {
        case: str(path.resolve()) for case, path in zip(views, assessments)
    })
    runtime = RecordingRuntime.from_config("config/runtime.yaml", root / "runtime")
    runtime.replies = {}
    model = runtime.models[runtime.routes["chief"]]
    write_json(root / "model-config.json", model.config.model_dump(mode="json"))
    gate = asyncio.Semaphore(2)

    async def job(case, repeat):
        async with gate:
            folder = root / case / str(repeat)
            folder.mkdir(parents=True)
            view = views[case]
            state = runtime.load(runtime.create(
                SessionTask(role="chief", objective="依据完整材料形成国标评价决定。",
                            application=view["application"]),
                allowance=SessionAllowance(
                    completion_calls=1, completion_seconds=600,
                    completion_input_tokens=250000, completion_output_tokens=16384,
                ),
            ))
            state.evidence = {
                handle: {} for handle in dict.fromkeys(
                    re.findall(r"evidence-[0-9a-f]{16}", json.dumps(view))
                )
            }
            submission = SavedInputSubmission(runtime, state, view)
            messages, _ = submission.authoring_input(state, "submit_assessment")
            write_json(folder / "request.json", {
                "messages": author_messages(messages, state.evidence),
                "schema": submission.response_schema(state, "submit_assessment"),
            })
            row = {"case": case, "repeat": repeat, "session_id": state.session_id}
            print(json.dumps({**row, "status": "started"}), flush=True)
            try:
                await runtime._formal_submission(
                    state, model, operation="submit_assessment", submission=submission,
                )
                artifact = submission._artifact(state.assessment_ref)
                write_json(folder / "assessment.json", artifact)
                decision = artifact["assessment"]
                raw = runtime.store.author_text(
                    artifact["author_sources"]["decision"]["author_text_ref"]
                )
                write_json(folder / "author-text.json", raw)
                parts = json.loads(raw["content"])["overall_judgment"]
                expected = "\n\n".join(parts[key] for key in (
                    "project_and_value", "support_and_limits", "overall_tradeoff",
                ))
                assert decision["overall_judgment"] == expected
                operation = "write_section:" + submission.system.criteria[0].id
                downstream, _ = submission.authoring_input(state, operation)
                write_json(folder / "report-input.json", downstream)
                received = json.loads(downstream[1]["content"])["decision"]["overall_judgment"]
                assert received == expected
                (folder / "review.md").write_text(
                    "## 综合评价\n\n" + expected + "\n\n" + "\n\n".join(
                        f"## {c.label}：{decision['dimensions'][c.id]['score']}/{c.max_score}\n\n"
                        + decision["dimensions"][c.id]["reason"]
                        for c in submission.system.criteria
                    ), "utf-8",
                )
                row.update(status="complete", total_score=decision["total_score"],
                           paragraph_chars={key: len(value) for key, value in parts.items()},
                           lossless_handoff=True)
            except Exception as exc:
                row.update(status="failed", error=type(exc).__name__, detail=str(exc))
            reply = runtime.replies.get(state.session_id)
            if reply:
                write_json(folder / "response.json", {
                    "message": reply.message, "request": reply.effective_request,
                    "finish_reason": reply.finish_reason, "retries": reply.retries,
                    "prompt_tokens": reply.prompt_tokens,
                    "completion_tokens": reply.completion_tokens,
                })
                row.update(finish_reason=reply.finish_reason, retries=reply.retries,
                           input_tokens=reply.prompt_tokens,
                           output_tokens=reply.completion_tokens,
                           request=reply.effective_request)
            write_json(folder / "result.json", row)
            print(json.dumps({k: v for k, v in row.items() if k != "request"}), flush=True)
            return row

    try:
        rows = await asyncio.gather(*(
            job(case, repeat) for repeat in range(1, repeats + 1)
            for case in views
        ))
        write_json(root / "summary.json", rows)
        write_json(root / "scheduler.json", runtime.scheduler.records)
    finally:
        await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--assessment", type=Path, nargs="+", required=True,
        help="Saved v5/assessments/assessment-*.json files containing the full review_input.",
    )
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(run(args.output, args.assessment, args.repeats))
