"""One forced-close expert, then frozen research -> synthesis -> delivery. No full review."""

import argparse
import asyncio
import json
from copy import deepcopy
from pathlib import Path

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.diagnostics import SubmissionRecorder
from spacebio_review.application import ApplicationBundle
from spacebio_review.evidence.literature import LiteratureResearcher
from spacebio_review.evidence.store import SourceStore
from spacebio_review.models import DashScopeModel
from spacebio_review.models import load_config as load_runtime
from spacebio_review.review.project import load_preparation_config
from spacebio_review.v4.budget import Budget
from spacebio_review.v4.config import load_config, load_reporting
from spacebio_review.v4.coordinator import Coordinator
from spacebio_review.v4.delivery import Delivery
from spacebio_review.v4.execution import Execution
from spacebio_review.v4.preparation import Preparation
from spacebio_review.v4.records import Records, write_json
from spacebio_review.v4.research import Research
from spacebio_review.v4.synthesis import Synthesis


async def main(resume=False):
    root = Path(__file__).resolve().parents[1]
    source = root / "runs/mytest16"
    output = root / "runs/local-probes/mytest16-completion"
    if output.exists() and not resume:
        raise ValueError("Probe output already exists; do not silently repeat charged requests")
    original, records = Records(source), Records(output)
    state = original.load_state()
    inputs = state["tasks"]["synthesis/current"]["inputs"]

    def copy_record(ref):
        if (records.root / (ref + ".json")).exists():
            return
        data = original.get(ref)
        for dependency in data["dependencies"]:
            copy_record(dependency)
        assert records.put(data["kind"], data["value"], data["dependencies"]) == ref

    for ref in inputs["results"]:
        copy_record(ref)
    cfg = load_runtime(root / "config/runtime.yaml")
    config = load_config(root / "config/review.yaml").model_copy(
        update={
            "max_model_calls": 80,
            "max_embedding_calls": 0,
            "max_seconds": 1800,
            "completion_reserve_calls": 40,
            "completion_reserve_seconds": 600,
        }
    )
    reporting = load_reporting(root / "config/reporting.yaml")
    budget = Budget(records, config, reporting)
    runtime = AgentRuntime.from_config(
        cfg, model_factory=lambda c: DashScopeModel(c, env_file=root / ".env", budget=budget)
    )
    runtime.scheduler.request_budget = budget
    runtime.recorder = SubmissionRecorder(output, cfg.submission_max_bytes)
    prep_cfg = load_preparation_config(config.preparation_config)
    ex = Execution(runtime, records, config, {"prompt": prep_cfg.evaluation.prompt()}, reporting)
    store = SourceStore(source / "sources", None)
    store.restore()
    prep = Preparation(
        ex, store, ApplicationBundle.model_validate(state["metadata"]["application"]), prep_cfg
    )
    literature = LiteratureResearcher(runtime, store, None, index=None)
    status = {"status": "running", "scope": "forced-close-and-frozen-completion"}
    write_json(output / "status.json", status)
    execute = runtime._execute

    async def force_closure(*args, **kwargs):
        # Inject the global stop after the actual runtime registered this task's
        # formal allowance. All subsequent model generation is real.
        budget._closed_reason = "completion_call_reserve"
        return await execute(*args, **kwargs)

    try:
        async with asyncio.timeout(budget.remaining_seconds()):
            runtime._execute = force_closure
            expert_input = state["tasks"]["research/q3/expert-3/r0"]["inputs"]
            expert = await Research(prep, state["package"], literature).run(
                expert_input["task"],
                related=expert_input["related"],
                originals=expert_input["originals"],
            )
            runtime._execute = execute
            status["forced_close_artifact"] = expert
            status["forced_close_calls"] = len(budget.data["requests"])
            write_json(output / "status.json", status)
            print(json.dumps(status), flush=True)

            requests = deepcopy(state["requests"])
            for request in requests:
                if request["status"] == "pending":
                    request.update(status="deferred", reason="completion_call_reserve")
            assessment = await Synthesis(ex, prep).run(
                inputs["results"], inputs["coverage"], inputs["project"], open_requests=requests
            )
            # Use the same freezing and request retention as production, without routing.
            coordinator = Coordinator(ex, budget)
            coordinator._state = deepcopy(state)
            coordinator._state["requests"] = requests
            coordinator._state["assessment"] = assessment
            coordinator.requests(records.value(assessment).get("followups", []), assessment)
            coordinator.defer_requests("completion_call_reserve")
            frozen = coordinator.freeze_delivery(assessment)
            delivery = Delivery(ex, output)
            manifest = delivery.assemble(assessment, delivery_input=frozen)
            status.update(assessment=assessment, manifest=manifest, stage="delivery")
            write_json(output / "status.json", status)
            print(json.dumps(status), flush=True)
            result = await delivery.run(manifest)
            status.update(status=records.value(result)["status"], delivery=result)
    except Exception as exc:
        status.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        runtime._execute = execute
        budget.save()
        status.update(calls=len(budget.data["requests"]), elapsed=budget.data["elapsed_seconds"])
        write_json(output / "status.json", status)
        write_json(output / "metrics.json", runtime.scheduler.metrics())
        await runtime.close()
        print(json.dumps(status), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true", help="Reuse the same capped probe ledger")
    asyncio.run(main(parser.parse_args().resume))
