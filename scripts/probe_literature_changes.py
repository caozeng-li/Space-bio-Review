"""Run one bounded literature task; never start the review runner or coordinator."""

import argparse
import asyncio
import json
from pathlib import Path

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.diagnostics import SubmissionRecorder
from spacebio_review.evidence import EvidenceResources, LiteratureResearcher, load_evidence_config
from spacebio_review.evidence.knowledge import KnowledgeQuestion
from spacebio_review.models.config import load_config
from spacebio_review.models.provider import model_class
from spacebio_review.v4.budget import Budget
from spacebio_review.v4.config import ReportingConfig, V4Config
from spacebio_review.v4.records import Records, write_json

ROOT = Path(__file__).resolve().parents[1]
MODEL_CALLS = 8
TASK_ACTIONS = 6
MAX_SECONDS = 900


def change05_question():
    return KnowledgeQuestion(
        question=(
            "TMRM 荧光读出与 mtDNA 类核成像分别提供什么测量信息？"
            "二者在什么实验条件下能够回答不同的线粒体科学问题？"
        ),
        purpose="method",
        background="这是独立的方法知识咨询，不假定某份申请已提供具体染料浓度或设备配置。",
        assessment_focus="理解测量对象与实验条件，形成可用于判断方法适用性的外部科学认识。",
        needed_information=[
            "TMRM 信号所反映的对象、相关实验条件与解释范围。",
            "mtDNA 类核成像如何识别和测量类核，其读出与 TMRM 的关系。",
            "原始方法研究和厂家技术说明分别能为上述问题提供哪些有用依据。",
        ],
    )


def probe_configs(runtime_path, evidence_path, output):
    runtime = load_config(runtime_path)
    # These are probe-only overrides; production configuration files stay unchanged.
    for model in runtime.models.values():
        model.retries = 0
    for route in runtime.task_routes.values():
        route.retries = 0
    if runtime.embedding:
        runtime.embedding.retries = 0
        runtime.embedding.cache_dir = output / "embedding-cache"
    evidence = load_evidence_config(evidence_path)
    evidence.literature.max_model_turns = MODEL_CALLS
    # Bound all task actions, including reading; exploration is therefore also bounded.
    evidence.literature.max_actions = TASK_ACTIONS
    return runtime, evidence


def recorded_models(output, env_file, budget):
    """Keep actual public tool choices without recording credentials or reasoning fields."""
    calls = []

    class Model:
        def __init__(self, config):
            self.config = config
            self.inner = model_class(config)(config, env_file=env_file, budget=budget)

        async def complete(self, messages, tools=None, *, response_schema=None):
            call = {"request": len(calls) + 1, "model": self.config.model}
            calls.append(call)
            try:
                reply = await self.inner.complete(messages, tools, response_schema=response_schema)
                call.update(
                    status="completed",
                    content=reply.message.get("content"),
                    tool_calls=reply.message.get("tool_calls", []),
                )
                return reply
            except BaseException as exc:
                call.update(status="failed", error_type=type(exc).__name__)
                raise
            finally:
                write_json(output / "model-calls.json", calls)

        async def close(self):
            await self.inner.close()

    return Model


async def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    runtime_config, evidence_config = probe_configs(
        args.runtime_config, args.evidence_config, output
    )
    question = change05_question()
    write_json(
        output / "input.json",
        {
            "change": args.change,
            "case": "tmrm-and-mtdna-nucleoids",
            "question": question.model_dump(mode="json"),
            "input_origin": "standalone knowledge question; no proposal or prior run required",
            "limits": {
                "model_calls": MODEL_CALLS,
                "task_actions": TASK_ACTIONS,
                "max_seconds": MAX_SECONDS,
                "automatic_resampling": False,
            },
            "runtime_config": runtime_config.model_dump(mode="json"),
            "evidence_config": evidence_config.model_dump(mode="json"),
        },
    )
    budget = Budget(
        Records(output),
        V4Config(
            max_model_calls=MODEL_CALLS,
            max_embedding_calls=64,
            max_seconds=MAX_SECONDS,
            completion_reserve_calls=0,
            completion_reserve_seconds=0,
        ),
        ReportingConfig(),
    )
    runtime = resources = literature = None
    try:
        runtime = AgentRuntime.from_config(
            runtime_config,
            model_factory=recorded_models(output, args.env_file, budget),
        )
        runtime.scheduler.request_budget = budget
        runtime.recorder = SubmissionRecorder(output, runtime_config.submission_max_bytes)
        resources = EvidenceResources.create(
            runtime, output, evidence_config, env_file=args.env_file
        )
        literature = LiteratureResearcher.from_resources(resources, env_file=args.env_file)
        async with asyncio.timeout(budget.remaining_seconds()):
            result = await literature.investigate(question, single_submission=True)
        # The researcher also writes its normal knowledge/<id>/answer.json and source records.
        write_json(output / "answer.json", result)
        summary = {
            "change": args.change,
            "research_id": result.research_id,
            "status": result.task_result.status,
            "reason": result.task_result.reason,
            "model_calls": MODEL_CALLS - budget.remaining_calls,
            "output": str(output),
        }
        write_json(output / "result.json", summary)
        print(json.dumps(summary, ensure_ascii=False))
    except BaseException as exc:
        write_json(
            output / "result.json",
            {"status": "interrupted", "error_type": type(exc).__name__},
        )
        raise
    finally:
        budget.save()
        if runtime:
            write_json(output / "metrics.json", runtime.scheduler.metrics())
        if literature:
            await literature.close()
        if resources:
            await resources.close()
        if runtime:
            await runtime.close()


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--change", choices=("change05",), default="change05")
    cli.add_argument(
        "--output", type=Path, default=ROOT / "runs/literature-local-20261014/change05"
    )
    cli.add_argument("--runtime-config", type=Path, default=ROOT / "config/runtime.yaml")
    cli.add_argument("--evidence-config", type=Path, default=ROOT / "config/evidence.yaml")
    cli.add_argument("--env-file", type=Path, default=ROOT / ".env")
    return cli


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(parser().parse_args()))
