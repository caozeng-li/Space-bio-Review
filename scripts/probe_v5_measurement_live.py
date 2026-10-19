"""Opt-in independent sample2 measurement research; no chief or project assessment.

One topic, at most eight scientific turns, twelve chat requests including auxiliary
models, four embedding requests, and fifteen minutes. No seeded scientific findings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_chief_live import record_public_replies  # noqa: E402

from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.v5.config import load_config  # noqa: E402
from spacebio_review.v5.topic import TopicResearch, TopicTask  # noqa: E402

OBJECTIVE = (
    "研究完整 sample2 计划书中 ROS、钙信号和 mtDNA 类核成像的测量含义："
    "具体方法直接读出什么，这些读出能支持哪些有关本项目的科学认识？"
    "从计划书实际使用的具体方法自主检索并获取有用原文，选择最影响判断的一至两项"
    "深入研究，联系原文方法、结果与申请的对象、操作及观察阶段。"
    "形成有来源依据的专题说明，保留现有设计确能取得的认识和仍未知之处。"
    "本次只有一个研究专题，最多八轮科学调用、十二次聊天请求（含辅助模型）、"
    "四次 embedding 请求和十五分钟。证据足以形成当前认识时直接提交专题，"
    "不做六项评分、主审综合评价、摘要、翻译或完整报告。"
)


async def probe(args):
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("probe_requires_empty_output_directory")
    output.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config).model_dump(mode="json")
    researcher = config["roles"]["researcher"]
    auxiliary = {
        key: value for key, value in config["auxiliary"].items() if key in {"rerank", "visual"}
    }
    aliases = {researcher, *auxiliary.values()}
    config.update(
        roles={"chief": researcher, "researcher": researcher},
        models={alias: value for alias, value in config["models"].items() if alias in aliases},
        auxiliary=auxiliary,
        budget={"max_model_calls": 12, "max_embedding_calls": 4, "max_seconds": 900},
        allowances={
            "researcher": {
                "max_input_tokens": 500000,
                "max_output_tokens": 65536,
                "completion_input_tokens": 64000,
                "completion_output_tokens": 8192,
                "completion_calls": 1,
                "completion_seconds": 120,
            }
        },
    )
    config["session"].update(max_turns=8, context_tokens=64000, completion_turns=1)
    for model in config["models"].values():
        model.update(
            max_tokens=min(model["max_tokens"], 8192),
            timeout_seconds=min(model["timeout_seconds"], 180),
            retries=0,
        )
    if config["embedding"]:
        config["embedding"].update(retries=0, timeout_seconds=45)
    config_path = output / "probe-config.yaml"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), "utf-8")
    service = await TopicResearch.from_config(
        config_path,
        output,
        args.application,
        env_file=args.env_file,
        evaluation="只研究测量与科学解释，供国标体系后续评价使用；本专题不评分。",
        cutoff=date(2026, 10, 15),
    )
    try:
        record_public_replies(service.runtime, output)
        task = TopicTask(
            responsibility=OBJECTIVE,
            impact="解释计划书主要观察可以支持的科学认识，为后续评价提供专业依据。",
            expertise=["mitochondrial-metabolism"],
            skills=["measurement-analysis"],
        )
        topic_id = service.create(task)
        write_json(
            output / "input.json",
            {
                "topic_id": topic_id,
                "task": task,
                "application": str(args.application.resolve()),
                "limits": config["budget"],
                "max_scientific_turns": 8,
                "seeded_findings": False,
                "full_review_runs": 0,
            },
        )
        print(json.dumps({"phase": "started", "topic_id": topic_id}), flush=True)
        result = await service.run(topic_id)
        write_json(output / "result.json", result)
        budget = service.runtime.budget
        summary = {
            "topic_id": topic_id,
            "status": result.status,
            "reason": result.reason,
            "note_ref": result.note_ref,
            "turns_used": result.turns_used,
            "usage": budget.usage(),
            "elapsed_seconds": 900 - budget.remaining_seconds(),
            "tools": [
                row["name"] for row in service.runtime.scheduler.records if row["kind"] == "tool"
            ],
            "sources": service.store.catalogue(),
            "scientific_quality": "requires_manual_reading",
            "full_review_runs": 0,
            "scope": "One independent measurement topic; no continuation or chief assessment.",
        }
        write_json(output / "summary.json", summary)
        print(
            json.dumps(
                {
                    key: summary[key]
                    for key in ("status", "reason", "turns_used", "usage", "elapsed_seconds")
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0 if result.status == "submitted" else 1
    finally:
        await service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/runtime.yaml")
    parser.add_argument("--application", type=Path, default=ROOT / "data/sample2.md")
    parser.add_argument("--env-file", default=".env")
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
