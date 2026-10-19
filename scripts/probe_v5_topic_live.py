"""Opt-in scientific topic probe: one author, one continuation, no project assessment.

At most 14 chat requests plus 2 embedding requests and 20 minutes, shared by both
turns. Source HTTP requests use the normal bounded evidence tools. No model retries.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path

import yaml

from spacebio_review.evidence.types import SourceCandidate
from spacebio_review.persistence import write_json
from spacebio_review.prose import render_authored_body
from spacebio_review.v5.config import load_config
from spacebio_review.v5.topic import TopicResearch, TopicTask


async def probe(args):
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("probe_requires_empty_output_directory")
    output.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config).model_dump(mode="json")
    config["budget"] = {
        "max_model_calls": 14,
        "max_embedding_calls": 2,
        "max_seconds": 1200,
    }
    config["session"].update(max_turns=8, completion_turns=1)
    for model in config["models"].values():
        model.update(max_tokens=min(model["max_tokens"], 6144), timeout_seconds=150, retries=0)
    if config["embedding"]:
        config["embedding"].update(retries=0, timeout_seconds=45)
    config["allowances"]["researcher"].update(
        completion_output_tokens=6144,
        completion_seconds=120,
    )
    config_path = output / "probe-config.yaml"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), "utf-8")
    service = await TopicResearch.from_config(
        config_path,
        output,
        args.application,
        env_file=args.env_file,
        evaluation="仅研究模型与培养条件的科学含义，供国标体系主审后续理解；本探针不评分。",
        cutoff=date(2026, 10, 15),
    )
    source_id = service.store.register(
        SourceCandidate(
            title=(
                "In vitro cellular models of human hepatic fatty acid metabolism: differences "
                "between Huh7 and HepG2 cell lines in human and fetal bovine culturing serum"
            ),
            identifiers={"doi": "10.14814/phy2.13532", "pmcid": "PMC5742701"},
            publication_date=date(2017, 12, 21),
            url="https://doi.org/10.14814/phy2.13532",
            fulltext_urls=[
                "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC5742701/fullTextXML"
            ],
        )
    )
    task = TopicTask(
        responsibility=(
            "研究原计划书中 Huh7 模型所能支持的脂质代谢认识及培养条件的影响。"
            "给定 Gunn 等 2017 论文作为可核查起点，直接获取方法与结果，"
            "弄清实际比较对象、培养条件及关键终点；围绕尚需解释的内容自主检索一项"
            "互补信息。选择该论文一幅直接相关的结果图，调用图像阅读理解组别与差异，"
            "并与图注、方法联系。结合完整申请解释对本项目研究意义与外推范围的影响。"
            "这是单专题研究，材料已足以形成认识就直接提交，不做六项评分或完整报告。"
        ),
        impact="影响对脂质代谢模型可解释范围与实验条件的理解；不预设模型无效或必须更换。",
        skills=["model-comparison", "causal-experimental-design"],
        source_ids=[source_id],
    )
    topic_id = service.create(task)
    write_json(
        output / "input.json",
        {
            "task": task,
            "topic_id": topic_id,
            "application": str(Path(args.application).resolve()),
            "limits": config["budget"],
            "scientific_quality_requires_reading_actual_output": True,
        },
    )
    outcomes = []
    try:
        result = await service.run(topic_id)
        outcomes.append(result.model_dump(mode="json"))
        write_json(output / "first.json", result)
        print(
            {"phase": "first", "status": result.status, "usage": service.runtime.budget.usage()},
            flush=True,
        )
        if result.status == "submitted" and service.runtime.budget.remaining_calls >= 2:
            service.runtime.extend(service.state(topic_id).session_id, turns=2)
            result = await service.run(
                topic_id,
                followup=(
                    "继续同一专题：如果申请后续确定采用与原论文不同的血清条件，"
                    "此前哪些模型比较认识仍可保留，哪些条件相关结果需要重新界定？"
                    "先区分计划书已经写明的安排与这个假设，再据已读原文更新同一说明；"
                    "确有必要时再读一个方法位置。"
                ),
            )
            outcomes.append(result.model_dump(mode="json"))
            write_json(output / "followup.json", result)
        if result.note:
            (output / "research.md").write_text(
                "# "
                + result.note["title"]
                + "\n\n"
                + render_authored_body(result.note["body"]),
                "utf-8",
            )
        usage = service.runtime.budget.usage()
        summary = {
            "topic_id": topic_id,
            "outcomes": [
                {key: row[key] for key in ("status", "note_ref", "turns_used", "reason")}
                for row in outcomes
            ],
            "actual_requests": usage,
            "total_model_and_embedding_requests": usage["model_calls"] + usage["embedding_calls"],
            "tools": [
                r["name"]
                for r in service.runtime.scheduler.records
                if r["kind"] in {"tool", "delegation"}
            ],
            "sources": service.store.catalogue(),
            "full_review_runs": 0,
            "scope": (
                "One topic and at most one followup; no scoring/report; "
                "inspect scientific output manually"
            ),
        }
        write_json(output / "summary.json", summary)
        print({"phase": "final", "status": result.status, "usage": usage}, flush=True)
        return 0 if result.status == "submitted" else 1
    finally:
        await service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", default="config/runtime.yaml")
    parser.add_argument("--application", default="data/sample1.md")
    parser.add_argument("--env-file", default=".env")
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
