"""Run only the admission role against real DashScope; no committee is started."""

import argparse
import asyncio
import json
import logging
from pathlib import Path

import httpx

from spacebio_review.admission import ProposalAdmission, load_admission_config
from spacebio_review.agents import AgentRuntime
from spacebio_review.context import load_application
from spacebio_review.evidence.store import SourceStore
from spacebio_review.models import load_config


async def run(args):
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty output directory")
    config = load_config(args.runtime_config)
    admission = load_admission_config(args.admission_config)
    runtime = AgentRuntime.from_config(config, env_file=args.env_file)
    try:
        async with httpx.AsyncClient() as client:
            store = SourceStore(output / "sources", client)
            bundle = await load_application(store, args.application, args.attachment)
            result = await ProposalAdmission(runtime, store, admission).check(bundle)
            (output / "admission.json").write_text(
                result.model_dump_json(indent=2), encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "status": result.status,
                        "decision": result.decision.decision,
                        "rationale": result.decision.rationale,
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
    parser.add_argument("--env-file", default=".env")
    asyncio.run(run(parser.parse_args()))
