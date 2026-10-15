"""Opt-in, four-request protocol probe: local text, research note, direct submission."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from pydantic import BaseModel

from spacebio_review.models.provider import model_class
from spacebio_review.persistence import write_json
from spacebio_review.tools import Tool, ToolRegistry, ToolResult
from spacebio_review.v5 import ResearchRuntime, SessionSettings, SessionTask
from spacebio_review.v5.budget import Budget, BudgetLimits
from spacebio_review.v5.config import load_config

APPLICATION = (
    "这是一份协议验证用的虚构短材料，不是实际科研申请。"
    "计划在轨记录荧光，返回后实施分子检测，并设地面对照。生物学重复数尚未说明。"
)
ORIGINAL = (
    "固定方法示例：研究比较了两种肿瘤细胞系在不同培养条件下的荧光读数。"
    "没有比较正常原代细胞，也没有证明单一荧光信号可以识别唯一生物机制。"
)


class ReadLocal(BaseModel):
    pass


async def probe(args):
    output = Path(args.output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("probe_requires_empty_output_directory")
    configured = load_config(args.config)
    config = configured.models[configured.roles["researcher"]].model_copy(
        update={"max_tokens": 4096, "timeout_seconds": 180, "retries": 0}
    )
    budget = Budget(output, BudgetLimits(max_model_calls=4, max_embedding_calls=0, max_seconds=600))
    model = model_class(config)(config, env_file=args.env_file, budget=budget)
    runtime = ResearchRuntime(
        output,
        {"scientific": model},
        {"chief": "scientific", "researcher": "scientific"},
        budget=budget,
        settings=SessionSettings(max_turns=4),
    )
    readings = {}

    async def read_local(_args):
        readings["evidence-protocol-example"] = {
            "reference": {"source_id": "protocol-example", "passage_id": "p1", "visual_id": None},
            "title": "协议探针固定示例（非真实论文）",
            "kind": "application",
            "locator": "方法示例／第一段",
            "quote": ORIGINAL,
            "reading_scope": "text",
            "material_version": "protocol-example-1",
        }
        return ToolResult(ok=True, content={"text": ORIGINAL})

    tools = ToolRegistry([Tool("read_local", "读取固定示例全文，无网络。", ReadLocal, read_local)])
    tools.evidence_views.append(lambda: readings)
    session_id = runtime.create(
        SessionTask(
            role="researcher",
            application=APPLICATION,
            objective=(
                "这是一次局部工具协议探针：先调用 read_local 阅读方法示例，"
                "随后调用 update_research 保存一段简短研究说明及未知，"
                "再用 submit_research(note=null) 提交保存稿。"
                "说明实际实验阶段、示例的比较对象及其能够支持的范围。"
                "不检索网络、不评分、不生成报告。使用实际取得的原文引用。"
            ),
        )
    )
    try:
        outcome = await runtime.run(session_id, tools=tools)
        summary = {
            "scope": "Local tool/continuity/submission protocol only; no scientific quality claim",
            "outcome": outcome.model_dump(mode="json"),
            "actual_requests": budget.usage(),
            "tools": [row["name"] for row in runtime.scheduler.records if row["kind"] == "tool"],
            "model": config.model,
            "evaluation_pipeline_started": False,
            "external_retrieval_enabled": False,
        }
        write_json(output / "summary.json", summary)
        print(summary["outcome"])
        print(summary["actual_requests"])
        return 0 if outcome.status == "submitted" else 1
    finally:
        budget.save()
        await model.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", default="config/v5-runtime.yaml")
    parser.add_argument("--env-file", default=".env")
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
