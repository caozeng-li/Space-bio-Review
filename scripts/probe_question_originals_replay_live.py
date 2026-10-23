"""Paired final-composition replay: same questions/originals, omit intermediate judgments."""

import argparse
import asyncio
from copy import deepcopy
from pathlib import Path

from probe_expert_trajectory_live import ObservedRuntime, load, production_hashes

from spacebio_review.persistence import write_json
from spacebio_review.prose import prose_inputs
from spacebio_review.v5.budget import SessionAllowance
from spacebio_review.v5.records import SessionTask


async def run_case(output, repeat):
    source = output / "workspace" / f"run-{repeat}"
    row = load(source / "verification.json")
    original_state = load(source / "v5/sessions" / f"{row['session_id']}.json")
    records = load(source / "question-records.json")
    calls = [load(p) for p in sorted((source / "trace").glob("call-*-output.json"))]
    composition = [c for c in calls if c.get("stage") == "question_synthesis"][-1]
    actual = load(source / "trace" / f"{composition['call_id']}-input.json")
    messages = deepcopy(actual["messages"])
    removed = []
    for record in records:
        finding = record["finding"]
        count = messages[1]["content"].count(finding)
        if count != 1:
            raise ValueError("Cannot isolate exactly one recorded judgment in final input")
        messages[1]["content"] = messages[1]["content"].replace(finding, "", 1)
        removed.append(len(finding))
    messages[1]["content"] = messages[1]["content"].replace(
        "本专题独立问题记录", "本专题问题及原文",
    )
    group = output / "originals_only"
    config = group / "probe-config.yaml"
    folder = group / f"run-{repeat}"
    folder.mkdir(exist_ok=False)
    runtime = ObservedRuntime.from_config(config, folder)
    summary = {
        "status": "running", "parent": str(source), "parent_call": composition["call_id"],
        "removed_judgment_characters": removed, "full_review_runs": 0,
        "scope": "Paired final synthesis replay, not another autonomous full research session.",
    }
    try:
        state = runtime.load(runtime.create(
            SessionTask.model_validate(original_state["task"]),
            allowance=SessionAllowance(completion_calls=0),
        ))
        runtime._capture(state, original_state["evidence"])
        reply = await runtime._call_model(
            state, runtime.models[state.model_alias], messages, [],
            purpose="completion", stage="originals_only_synthesis",
        )
        raw = runtime._save_scientific_reply(
            state, reply.message["content"], title="地面—在轨—返回衔接评价",
            absorbed_through=0, operation="update_research", finish_reason=reply.finish_reason,
        )
        await runtime._deliver_manuscript(state, raw, None)
        artifact = runtime.store.note(state.note_ref)
        write_json(folder / "topic-result.json", {
            "note": artifact["note"], "readings": artifact["evidence"],
            "note_ref": state.note_ref,
        })
        prose = prose_inputs(artifact["note"])
        (folder / "expert.md").write_text("# " + prose["title"] + "\n\n" + prose["body"], "utf-8")
        summary.update(status="complete", finish_reason=reply.finish_reason)
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
    output = Path(args.output).resolve()
    group = output / "originals_only"
    group.mkdir(exist_ok=True)
    (group / "probe-config.yaml").write_text(
        (output / "workspace/probe-config.yaml").read_text("utf-8"), "utf-8",
    )
    gate = asyncio.Semaphore(2)

    async def job(repeat):
        async with gate:
            await run_case(output, repeat)

    results = await asyncio.gather(*(job(n) for n in args.repeats), return_exceptions=True)
    failures = [f"{n}: {r!r}" for n, r in zip(args.repeats, results, strict=True)
                if isinstance(r, BaseException)]
    if failures:
        raise RuntimeError("\n".join(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", default="runs/question_workspace_sample2")
    parser.add_argument("--repeats", type=int, nargs="+", default=[3, 4, 5])
    asyncio.run(main(parser.parse_args()))
