"""Opt-in formal-output replay: no search, tools, repairs or full review.

Expert cases reconstruct the retained final source catalogue, not the historical
tool conversation. The original run is read-only. Every sample calls the real API.
"""

import argparse
import asyncio
import json
from pathlib import Path

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.contracts import SubmissionContract
from spacebio_review.agents.diagnostics import SubmissionRecorder
from spacebio_review.agents.types import AgentTask, ContextView, RoleProfile
from spacebio_review.evidence.submissions import record
from spacebio_review.models.config import load_config
from spacebio_review.tools import ToolRegistry
from spacebio_review.v4.contracts import (
    DraftBlock,
    grounded_contract,
    professional_contract,
    proposal_type,
)
from spacebio_review.v4.delivery import TextDraft, TranslationDraft
from spacebio_review.v4.records import write_json

TASKS = {
    "summary": (
        "writer/summary/manifest-d8b5541b2568022d776a5e0c9963a28555ed93d67fb5545fd6289f8af833f697"
    ),
    "orientation": "prepare/orient/expert-2",
    "research": "research/q22/expert-6/r0",
    "translation": "writer/en/d594090e3c13271dee12f94e7dfc5275abbb28063dd55263ffe6367f39b91f51",
}


def retained_case(root, case):
    task_id = TASKS[case]
    for path in (root / "v4/inputs").glob("*.json"):
        saved = json.loads(path.read_text("utf-8"))
        if saved["task"]["task_id"] == task_id:
            break
    else:
        raise ValueError("retained_input_missing: " + case)
    task = AgentTask.model_validate(saved["task"]).model_copy(
        update={
            "mode": "direct",
            "read_actions": 0,
            "max_repairs": 0,
            "max_truncation_recoveries": 0,
        }
    )
    view = ContextView.model_validate(saved["context"])
    if case in {"summary", "translation"}:
        model = TextDraft if case == "summary" else TranslationDraft
        return task, view, SubmissionContract(model, model), []

    for path in (root / "v4").glob("receipt-*.json"):
        receipt = json.loads(path.read_text("utf-8"))["value"]
        if receipt.get("identity", {}).get("task") == task_id and receipt.get("mapping"):
            break
    else:
        raise ValueError("retained_reading_missing: " + case)
    evidence = {
        item["identity"]: item["value"]
        for alias, item in receipt["mapping"].items()
        if alias.startswith("E")
    }
    results = {
        item["identity"]: item["value"]
        for alias, item in receipt["mapping"].items()
        if alias.startswith("R")
    }
    if case == "orientation":
        contract = grounded_contract(
            "orientation",
            lambda ev, rs: record(
                "ProfessionalOrientation",
                understanding=(str, ...),
                corrections=(list[DraftBlock], ...),
                proposals=(list[proposal_type(ev)], ...),
            ),
            evidence=evidence,
            results=results,
        )
        skills = next(m.data["skills"] for m in view.materials if m.source_id == "expert-purpose")
    else:
        members = next(
            m.data["members"] for m in view.materials if m.source_id == "professional-work"
        )
        contract = professional_contract(None, results, members, evidence=evidence)
        state = json.loads((root / "v4/state.json").read_text("utf-8"))
        skills = state["package"]["experts"]["expert-6"]["skills"]
    contract.task_context.collect([m.data for m in view.materials])
    contract.refresh(contract)
    return task, view, contract, skills


async def main(args):
    if not 1 <= args.repeats <= 5:
        raise ValueError("repeats must be between 1 and 5")
    output = Path(args.output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("use_an_empty_probe_output")
    output.mkdir(parents=True, exist_ok=True)
    config = load_config("config/runtime.yaml")
    for model in config.models.values():
        model.retries = 0
        model.timeout_seconds = 180
    if args.format == "json_object":
        for kind in ("specialist", "writer"):
            config.formal_routes[kind].structured_output = "json_object"
    for case in args.cases:
        for repeat in range(args.repeats):
            task, view, contract, skills = retained_case(Path(args.source), case)
            target = output / f"{case}-{args.format}-{repeat + 1}.json"
            write_json(
                output / f"{case}-{repeat + 1}-input.json",
                {
                    "task": task,
                    "context": view,
                    "contract": contract.metadata(),
                    "mapping": contract.task_context.mapping() if contract.task_context else {},
                    "output_format": config.routed_model(task.kind, formal=True).structured_output,
                },
            )
            runtime = AgentRuntime.from_config(config, env_file=".env")
            runtime.recorder = SubmissionRecorder(output / f"{case}-{repeat + 1}")
            print(f"START {case} {args.format} {repeat + 1}", flush=True)
            try:
                result = await runtime.run_task(
                    task,
                    RoleProfile(
                        agent_id=task.task_id, responsibility=task.objective, required_skills=skills
                    ),
                    view,
                    ToolRegistry(),
                    contract,
                )
                write_json(target, result)
                print(
                    json.dumps(
                        {
                            "case": case,
                            "repeat": repeat + 1,
                            "status": result.status,
                            "reason": result.reason,
                            "turns": result.model_turns,
                            "tokens": [r["actual_output_tokens"] for r in result.context_usage],
                        }
                    ),
                    flush=True,
                )
            finally:
                await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="runs/mytest17")
    parser.add_argument("--output", required=True)
    parser.add_argument("--cases", nargs="+", choices=list(TASKS), default=["summary"])
    parser.add_argument("--format", choices=["current", "json_object"], default="current")
    parser.add_argument("--repeats", type=int, default=1)
    asyncio.run(main(parser.parse_args()))
