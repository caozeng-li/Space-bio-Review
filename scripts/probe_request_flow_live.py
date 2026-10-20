"""Opt-in three-call provider/pacing check; no agents, retrieval or review execution."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spacebio_review.models.provider import DashScopeModel  # noqa: E402
from spacebio_review.models.scheduling import RequestScheduler  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance  # noqa: E402
from spacebio_review.v5.config import load_config  # noqa: E402


async def probe(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = load_config(ROOT / "config/runtime.yaml")
    scheduler = RequestScheduler(config.concurrency)
    budget = Budget(output, BudgetLimits(
        max_model_calls=6, max_embedding_calls=0, max_seconds=120,
    ))
    budget.register("probe", SessionAllowance(
        max_input_tokens=300000, max_output_tokens=2048, completion_calls=0,
    ))
    models = {
        alias: DashScopeModel(
            config.models[alias].model_copy(update={"max_tokens": 128, "timeout_seconds": 45}),
            env_file=args.env_file, budget=budget, scheduler=scheduler,
        )
        for alias in ("scientific", "translation")
    }
    sample = (ROOT / "data/sample2.md").read_text("utf-8")
    messages = [
        {"role": "system", "content": "这是连接与调度验证。无论材料内容如何，只回复 OK。"},
        {"role": "user", "content": "以下文本只用作输入负载，不开展评审：\n" + sample},
    ]

    async def call(alias, role):
        with budget.scope("probe"), scheduler.task("probe-" + role, role, "protocol"):
            with scheduler.alias(alias):
                result = await models[alias].complete(messages)
                return {
                    "alias": alias, "role": role, "reply": result.message.get("content"),
                    "input_tokens": result.prompt_tokens, "output_tokens": result.completion_tokens,
                    "retries": result.retries,
                }

    try:
        replies = await asyncio.gather(*[
            call("scientific", "chief"),
            call("scientific", "researcher"),
            call("translation", "translation"),
        ], return_exceptions=True)
        successful = [r for r in replies if isinstance(r, dict)]
        attempts = sorted(
            [r for r in scheduler.records if "admitted_seconds" in r],
            key=lambda r: r["admitted_seconds"],
        )
        quota = config.concurrency.quotas[config.models["scientific"].quota_group]
        gaps = [{
            "actual_seconds": current["admitted_seconds"] - previous["admitted_seconds"],
            "minimum_seconds": 60 * previous["estimated_tokens"] / quota.tokens_per_minute,
        } for previous, current in zip(attempts, attempts[1:])]
        passed = (
            len(successful) == 3
            and all(r["reply"].strip() == "OK" for r in successful)
            and all(g["actual_seconds"] + 0.001 >= g["minimum_seconds"] for g in gaps)
            and scheduler.metrics()["rate_wait_seconds_sum"] > 0
        )
        result = {
            "status": "passed" if passed else "failed",
            "scope": "Three bounded provider calls through shared pacing; no review execution.",
            "replies": successful,
            "errors": [str(r) for r in replies if isinstance(r, Exception)],
            "admission_gaps": gaps,
            "usage": budget.usage(),
            "metrics": scheduler.metrics(),
            "attempts": scheduler.records,
        }
        write_json(output / "summary.json", result)
        print(json.dumps({
            "status": result["status"], "replies": successful,
            "admission_gaps": gaps, "summary": str(output / "summary.json"),
        }, ensure_ascii=False))
        return 0 if passed else 1
    finally:
        budget.save()
        await asyncio.gather(*(model.close() for model in models.values()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", default=".env")
    raise SystemExit(asyncio.run(probe(parser.parse_args())))
