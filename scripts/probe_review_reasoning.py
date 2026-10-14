"""Fixed-material model comparison; no tools, expert workflow or formal submission."""

import argparse
import asyncio
import json
from pathlib import Path

from probe_review_quality import ProbeBudget, fixture, read

from spacebio_review.agents.context import TokenEstimator
from spacebio_review.agents.diagnostics import digest
from spacebio_review.models.config import ModelConfig, load_config
from spacebio_review.models.provider import model_class
from spacebio_review.models.scheduling import RequestScheduler, ScheduledModel
from spacebio_review.v4.assessment_input import select_materials
from spacebio_review.v4.records import write_json

CASES = tuple(
    f"C-{topic}-{model}" for topic in ("time", "method") for model in ("specialist", "strong")
)
QUESTIONS = {
    "time": "按照计划的观测时点和实际比较，目前的设计能得到什么认识、仍有哪些解释边界？",
    "method": (
        "给定资料能支持对返回样品的 mtDNA 分析作出什么判断？请结合方法、数值及其适用条件说明。"
    ),
}
PROMPT = """围绕给定的局部专业问题，比较申请原文、外部原文与专业意见，形成约400-600字的专业分析。
说明当前设计能获得什么认识、关键依据与适用范围、真正未决的条件。专业意见是待审议的解释，
申请事实由申请原文说明，外部研究的对象和方法条件随其结论一起使用。
直接给出面向读者的连贯分析，以材料的实际内容说明理由。"""


def request(case):
    _, topic, model = case.split("-")
    frozen, files = fixture("A24-1")
    materials = select_materials(
        frozen,
        ["O1", "O2"] if topic == "time" else ["O12", "O13", "O14"],
        evidence_ids=["E6", "E12", "E20"] if topic == "time" else ["E13", "E20", "E51", "E52"],
    )
    config = (
        load_config("config/runtime.yaml")
        .models[model]
        .model_copy(
            update={
                "max_tokens": 6000,
                "retries": 0,
                "tools": False,
                "extra_body": {"enable_thinking": True},
                "parameters": {},
            }
        )
    )
    messages = [
        {"role": "system", "content": PROMPT + "\n" + frozen["evaluation"]["prompt"]},
        {
            "role": "user",
            "content": json.dumps(
                {"question": QUESTIONS[topic], "materials": materials}, ensure_ascii=False
            ),
        },
    ]
    return {
        "case": case,
        "messages": messages,
        "model": config.model_dump(mode="json"),
        "source_hashes": files,
        "input_estimate": TokenEstimator().measure(messages),
    }


def preview(output):
    target = output / "reasoning-preview.json"
    if target.exists():
        raise ValueError("preserve_existing_reasoning_preview")
    value = {case: request(case) for case in CASES}
    write_json(target, value)
    return {case: row["input_estimate"]["input_tokens"] for case, row in value.items()}


async def live(output, root, case, reason):
    frozen = read(output / "reasoning-preview.json")[case]
    if digest(request(case)) != digest(frozen):
        raise ValueError("reasoning_request_changed_after_preview")
    path = output / "cases" / case / "result.json"
    if path.exists():
        return read(path)
    budget = ProbeBudget(root, "C", case)
    config = ModelConfig.model_validate(frozen["model"])
    result = {"status": "started", "request": frozen, "diagnostic_reason": reason}
    write_json(path, result)
    model = model_class(config)(config, env_file=".env", budget=budget)
    scheduler = RequestScheduler()
    try:
        with scheduler.task(case, "specialist", "local_reasoning"):
            reply = await ScheduledModel(model, scheduler, "probe").complete(frozen["messages"])
        result.update(
            status="received",
            response=reply.message,
            finish_reason=reply.finish_reason,
            usage={"input": reply.prompt_tokens, "output": reply.completion_tokens},
        )
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        await model.close()
        write_json(path, result)
        write_json(root / "usage.json", budget.report())
    return {"case": case, "status": result["status"], "usage": result["usage"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("preview", "live"))
    parser.add_argument("--output", type=Path, default=Path("runs/probe-review-quality-20261013"))
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--reason", help="Observed fixed-evidence misunderstanding triggering C.")
    args = parser.parse_args()
    root = args.output.resolve()
    output = root / "revisions" / "initial"
    if args.stage == "preview":
        result = preview(output)
    else:
        if not args.case or not args.reason:
            parser.error("live requires a fixed case and diagnostic reason")
        result = asyncio.run(live(output, root, args.case, args.reason))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
