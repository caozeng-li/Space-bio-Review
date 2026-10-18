"""Offline checks of shared metering and remaining delivery reserves, with no review run."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spacebio_review.agents.context import TokenEstimator  # noqa: E402
from spacebio_review.models.config import ModelConfig  # noqa: E402
from spacebio_review.models.provider import ModelError  # noqa: E402
from spacebio_review.models.scheduling import RequestScheduler  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.v5.budget import (  # noqa: E402
    Budget,
    BudgetLimits,
    CompletionReserve,
    SessionAllowance,
)


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


def config(**updates):
    return ModelConfig(
        model="offline-science", base_url="https://example.invalid/v1", max_tokens=100,
        retries=0,
    ).model_copy(update=updates)


def actual(tokens, output=10):
    return SimpleNamespace(prompt_tokens=tokens, completion_tokens=output)


def calibration(root):
    ledger = Budget(root)
    ledger.register("chief", SessionAllowance(completion_calls=0))
    model = config()
    application = (ROOT / "data/sample2.md").read_text("utf-8")
    messages = [{"role": "user", "content": application}]
    tools = [{"type": "function", "function": {"name": "read_sources"}}]
    first = ledger.measure(model, messages, tools)
    with ledger.scope("chief"):
        first_ticket = ledger.reserve(model, messages, tools)
        second_ticket = ledger.reserve(model, messages[:], [])
    assert ledger.data["requests"][first_ticket]["estimated_input"] == first["input_tokens"]
    second = ledger.data["requests"][second_ticket]["estimate"]
    assert second["route_key"] == first["route_key"]
    # Requests can return in either order. Each observation uses its own raw base.
    ledger.settle(second_ticket, actual(round(second["text_base_tokens"] * 0.7)))
    ledger.settle(first_ticket, actual(round(first["text_base_tokens"] * 0.8)))
    down = ledger.measure(model, messages, tools)
    assert 0.87 < down["scale"] < 0.89
    with ledger.scope("chief"):
        up_ticket = ledger.reserve(model, messages, tools)
    ledger.settle(up_ticket, actual(round(down["text_base_tokens"] * 1.15)))
    up = ledger.measure(model, messages, tools)
    assert 1.26 < up["scale"] < 1.27
    with ledger.scope("chief"):
        lower_ticket = ledger.reserve(model, messages, tools)
    ledger.settle(lower_ticket, actual(round(up["text_base_tokens"] * 0.6)))
    lowered = ledger.measure(model, messages, tools)
    assert 0.65 < lowered["scale"] < 0.67

    parser = config(max_tokens=50, tools=False, structured_output="json_object")
    rerank = parser.model_copy(update={"max_tokens": 25})
    parser_measure = ledger.measure(parser, messages)
    rerank_measure = ledger.measure(rerank, messages)
    assert len({first["route_key"], parser_measure["route_key"], rerank_measure["route_key"]}) == 3
    visual_messages = [{"role": "user", "content": [
        {"type": "text", "text": "解释曲线的条件"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,fixture"}},
    ]}]
    assert ledger.measure(model, visual_messages)["route_key"] != first["route_key"]
    ledger.save()
    usage = ledger.usage()
    # A tokenizer and v4's default behavior remain independent of v5 calibration.
    legacy = TokenEstimator()
    legacy.observe(first, round(first["text_base_tokens"] * 0.6))
    assert legacy.scale == 1.0
    counted = Budget(root / "tokenizer", token_counters={model.model: lambda text: len(text)})
    counted.register("chief", SessionAllowance(completion_calls=0))
    with counted.scope("chief"):
        ticket = counted.reserve(model, messages)
    counted.settle(ticket, actual(100))
    assert counted.measure(model, messages)["method"] == "tokenizer"
    assert counted.measure(model, messages)["scale"] == 1.0
    return {"initial": first, "downward": lowered, "upward": up, "usage": usage}


def delivery(root):
    clock = Clock()
    ledger = Budget(root, BudgetLimits(max_model_calls=3, max_seconds=1000), clock=clock)
    ledger.register("chief", SessionAllowance(
        max_input_tokens=20000, max_output_tokens=2000, completion_calls=0,
    ))
    steps = ["assessment_author", "assessment_parser"]
    plan = CompletionReserve(
        input_tokens=12000, output_tokens=400, calls=2, seconds=200, steps=steps,
    )
    assert ledger.update_completion("chief", plan) is None
    model = config()
    message = [{"role": "user", "content": "按现有六项评价形成报告。"}]
    with ledger.scope("chief"):
        ticket = ledger.reserve(model, message)
        ledger.settle(ticket, actual(1000, 100))
        try:
            ledger.reserve(model, message)
        except ModelError as exc:
            assert exc.category == "budget" and exc.request_sent is False
        else:
            raise AssertionError("research_consumed_completion_calls")
    before = ledger.usage()
    with ledger.scope("chief", purpose="completion"):
        ticket = ledger.reserve(model, message)
        clock.value += 10
        ledger.settle(ticket, actual(2000, 80))
    assert ledger._reserve_left("chief", "calls") == 1
    assert ledger._reserve_left("chief", "input") == 10000
    assert ledger._reserve_left("chief", "output") == 320
    assert ledger._reserve_left("chief", "seconds") == 190
    # The author has completed; price the mapper within this same execution.
    author_done = ledger.usage()
    assert author_done["model_calls"] == before["model_calls"] + 1
    assert ledger.update_completion("chief", CompletionReserve(
        input_tokens=7000, output_tokens=300, calls=1, seconds=150, steps=steps[1:],
    )) is None
    assert ledger.usage() == author_done
    assert ledger.data["sessions"]["chief"]["allowance"]["max_input_tokens"] == 20000
    for stage in steps[1:]:
        with ledger.scope("chief", purpose="completion"):
            ticket = ledger.reserve(model, message)
            clock.value += 10
            ledger.settle(ticket, actual(1000, 50))
    assert ledger._reserve_left("chief", "calls") == 0
    assert ledger.usage()["model_calls"] == 3
    consumed = ledger.usage()
    ledger.finish("chief")
    ledger.activate("chief")
    assert ledger.usage() == consumed
    assert "completion_plan" not in ledger.data["sessions"]["chief"]
    assert ledger.update_completion("chief", CompletionReserve(calls=2)) is not None
    assert ledger.usage() == consumed
    return {"steps": steps, "usage": consumed, "scientific_author_turns": 1}


def overlapping_time(root):
    clock = Clock()
    ledger = Budget(root, clock=clock)
    ledger.register("chief", SessionAllowance(completion_calls=0))
    ledger.update_completion("chief", CompletionReserve(calls=2, seconds=100))
    with ledger.scope("chief", purpose="completion"):
        first = ledger.reserve(config(), [{"role": "user", "content": "first"}])
        clock.value = 5
        second = ledger.reserve(config(), [{"role": "user", "content": "second"}])
        clock.value = 10
        ledger.settle(first, actual(10))
        clock.value = 15
        ledger.settle(second, actual(10))
    assert ledger._reserve_left("chief", "seconds") == 85
    return {"reserved_seconds_left": 85, "completion_wall_seconds": 15}


async def metrics():
    scheduler = RequestScheduler()

    async def refuse():
        raise ModelError("local_budget_exhausted", category="budget", request_sent=False)

    async def fail():
        raise ModelError("local_provider_timeout", category="timeout", request_sent=True)

    for operation in (refuse, fail):
        try:
            await scheduler.call(operation, name="offline", model_id="fixture")
        except ModelError:
            pass
    report = scheduler.metrics()
    group = report["groups"][0]
    assert group["requests"] == 1
    assert group["budget_blocked"] == 1
    assert group["failed"] == 1
    assert group["usage_unavailable"] == 1
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "runs/v5-local/change18-budget-alignment")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Use a new output directory; existing budget history is not overwritten.")
    result = {
        "calibration": calibration(args.output / "calibration"),
        "two_request_delivery": delivery(args.output / "delivery"),
        "overlapping_completion_time": overlapping_time(args.output / "overlapping-time"),
        "scheduler": asyncio.run(metrics()),
        "real_model_requests": 0,
        "status": "passed",
    }
    write_json(args.output / "result.json", result)
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
