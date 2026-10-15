"""Run only the configured admission role; no expert review is started."""

import argparse
import asyncio
import json
import logging
from pathlib import Path

import httpx

from spacebio_review.admission import (
    FormalReviewDecision,
    ProposalAdmission,
    admission_report,
    load_admission_config,
)
from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.diagnostics import SubmissionRecorder
from spacebio_review.context import load_application
from spacebio_review.evidence.store import SourceStore
from spacebio_review.models import load_config
from spacebio_review.review.evaluation import configure_evaluation, get_evaluation_system
from spacebio_review.review.project import PreparationConfig


async def run(args):
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty output directory")
    output.mkdir(parents=True, exist_ok=True)
    config = load_config(args.runtime_config)
    admission = load_admission_config(args.admission_config)
    system = get_evaluation_system(args.evaluation_system)
    config, _ = configure_evaluation(system, config, PreparationConfig())
    runtime = AgentRuntime.from_config(config, env_file=args.env_file)
    runtime.recorder = SubmissionRecorder(output, config.submission_max_bytes)
    try:
        async with httpx.AsyncClient() as client:
            store = SourceStore(output / "sources", client)
            bundle = await load_application(store, args.application, args.attachment)
            result = await ProposalAdmission(
                runtime, store, admission, evaluation_system=system.id
            ).check(bundle)
            (output / "admission.json").write_text(
                result.model_dump_json(indent=2), encoding="utf-8"
            )
            (output / "admission.md").write_text(admission_report(result), encoding="utf-8")
            (output / "metrics.json").write_text(
                json.dumps(runtime.scheduler.metrics(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            decision = result.decision
            formal = isinstance(decision, FormalReviewDecision)
            print(
                json.dumps(
                    {
                        "status": result.status,
                        "kind": result.kind,
                        "can_proceed": result.can_proceed,
                        "decision": decision.conclusion
                        if formal
                        else decision.decision
                        if decision
                        else None,
                        "missing_items": [
                            key
                            for key, check in decision.checks.model_dump().items()
                            if not check["covered"]
                        ]
                        if formal and decision.checks
                        else [],
                        "rationale": decision.rationale if decision else result.reason,
                        "actions": result.task_result.actions if result.task_result else 0,
                        "model_turns": result.task_result.model_turns if result.task_result else 0,
                        "output": str(output),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        await runtime.close()


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application", required=True)
    parser.add_argument("--attachment", action="append", default=[])
    parser.add_argument("--output", required=True)
    parser.add_argument("--runtime-config", default="config/runtime.yaml")
    parser.add_argument("--admission-config", default="config/admission.yaml")
    parser.add_argument(
        "--evaluation-system", choices=["general", "gbt42048_2022"], default="general"
    )
    parser.add_argument("--env-file", default=".env")
    asyncio.run(run(parser.parse_args()))
