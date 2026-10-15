"""Opt-in assessment only: sample2 + fixed authentic abstracts, four calls / 15 minutes.

No admission, discovery, source acquisition, topic delegation, publication or translation.
The fixed public research note is prepared by the developer, not a successful autonomous
research output. Both external inputs are explicitly abstracts, not full-text readings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_chief_live import record_public_replies  # noqa: E402

from spacebio_review.evidence.types import SourceCandidate  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.prose import prose_parts, render_parts  # noqa: E402
from spacebio_review.v5.chief import ChiefReview  # noqa: E402
from spacebio_review.v5.config import load_config  # noqa: E402
from spacebio_review.v5.records import ResearchNote  # noqa: E402
from spacebio_review.v5.topic import TopicResearch  # noqa: E402

SOURCE_IDS = ("src-e25f529f1fb2", "src-2c6b1b494e71")
OBJECTIVE = (
    "本次仅做国标最终评价：依据完整 sample2 计划书、当前公开研究说明和所附真实摘要，"
    "形成六项评价及同主审摘要。研究工具关闭，appendix_topics 为空；总共最多4次实际"
    "请求，15分钟。可以分次保存评价，也可以一次提交全部；收到代码计算结果后提交摘要。"
    "固定研究说明由开发者整理供本次局部测试，不是已完成的自主研究；"
    "外部资料只有摘要，按实际材料覆盖面作判断，不能宣称已研究原文方法和图表。"
)


def paragraph(text, refs=()):
    return {"parts": [{"text": text, "cites": list(refs)}]}


def readable(assessment):
    """Inspection transcript only; deliberately does not call the report compiler."""
    rows = ["# 局部评价原文（未装配交付报告）"]
    for key in ("project", "scope", "overall", "summary"):
        if assessment.get(key):
            rows.extend(["## " + key, render_parts(prose_parts(assessment[key]))])
    for key, value in assessment.get("dimensions", {}).items():
        rows.extend(
            [
                "## " + key + " / " + str(value.get("score")),
                render_parts(prose_parts(value["body"])),
            ]
        )
    rows.extend(
        [
            "## 代码结果",
            json.dumps(
                {
                    key: assessment.get(key)
                    for key in (
                        "total_score",
                        "score_grade",
                        "recommendation",
                        "safety_veto",
                        "unknowns",
                    )
                },
                ensure_ascii=False,
                indent=2,
            ),
        ]
    )
    return "\n\n".join(rows)


async def probe(args):
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("probe_requires_empty_output_directory")
    output.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config).model_dump(mode="json")
    alias = config["roles"]["chief"]
    config.update(
        evaluation_system="gbt42048_2022",
        models={alias: config["models"][alias]},
        roles={"chief": alias, "researcher": alias},
        auxiliary={},
        embedding=None,
        budget={"max_model_calls": 4, "max_embedding_calls": 0, "max_seconds": 900},
        allowances={
            "chief": {
                "completion_calls": 3,
                "completion_input_tokens": 192000,
                "completion_output_tokens": 49152,
                "completion_seconds": 480,
            }
        },
    )
    config["models"][alias].update(retries=0, timeout_seconds=480)
    config["session"].update(max_turns=4, context_tokens=64000, completion_turns=1)
    path = output / "probe-config.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), "utf-8")
    topics = await TopicResearch.from_config(path, output, args.application, env_file=args.env_file)
    try:
        chief = ChiefReview(
            topics,
            evaluation_system="gbt42048_2022",
            admission={"status": "skipped", "reason": "assessment_only_probe"},
        )
        chief_id = chief.create(OBJECTIVE)
        access = chief.access(chief_id)
        inputs = []
        for sid in SOURCE_IDS:
            source = args.research_root / "sources" / sid / "candidate.json"
            candidate = SourceCandidate.model_validate_json(source.read_text("utf-8"))
            retained_id = topics.store.register(candidate)
            access.visible.add(retained_id)
            access._read_abstract(retained_id, candidate, candidate.abstract)
            inputs.append({"source_file": str(source.resolve()), "scope": "metadata/abstract"})
        state = topics.runtime.load(chief_id)
        topics.runtime._capture(state, access.references.snapshot_choices(selected=False))
        refs = [
            alias
            for alias, handle in state.aliases.items()
            if state.evidence[handle]["kind"] == "external"
        ]
        note = ResearchNote.model_validate(
            {
                "title": "固定研究材料：实验阶段与外部先例的适用范围",
                "body": {
                    "paragraphs": [
                        paragraph(
                            "申请正文安排地面培养与地面镜像装置调试，并以地面实验作比较；"
                            "在轨第3、6、9天进行明场及荧光成像，样品返回地面后做mtDNA PCR和测序。"
                            "这段说明是对所附完整计划书的阅读记录，评价仍须结合全文。"
                        ),
                        paragraph(
                            "Marotta等2023年预印本摘要描述人iPSC来源神经类器官在ISS静态培养30天，"
                            "活样本返回地球后进行组织学、转录组和分泌组分析，并比较轨道与地面样本。"
                            "它提供类器官空间培养及返回后分析的先例，但对象、培养方式和读出不同；"
                            "摘要没有交付本项目小鼠肠类器官培养、在轨荧光或mtDNA测序的验证。",
                            refs[:1],
                        ),
                        paragraph(
                            "Mair等2024年摘要描述自动心脏芯片平台一个月空间飞行：在轨实时监测收缩，"
                            "返回后进行超微结构及RNA测序，报告收缩和线粒体相关改变。"
                            "在轨与返回后检测结合是一种已有研究路径；这是人源工程心脏组织证据，"
                            "不能据此认定小鼠小肠体系的具体机制或硬件适配已经成立。",
                            refs[1:],
                        ),
                    ]
                },
                "unknowns": [
                    "两篇外部资料只有真实摘要，正文方法、具体统计比较和图表未读。",
                    "固定输入未覆盖DCF、Fluo8与类核成像的测量特异性文献。",
                ],
            }
        )
        topics.runtime.store.save_note(state, note, absorbed_through=0)
        access.save()
        write_json(
            output / "input.json",
            {
                "chief_id": chief_id,
                "application": str(args.application.resolve()),
                "fixed_research": inputs,
                "note_origin": "developer_prepared",
                "limits": {"actual_requests": 4, "seconds": 900},
                "stop": "assessment_and_summary_only",
                "full_review_runs": 0,
            },
        )
        record_public_replies(topics.runtime, output)
        result = await chief.assess(chief_id, allow_research=False)
        write_json(output / "result.json", result)
        if result.get("assessment"):
            (output / "assessment-inspection.md").write_text(
                readable(result["assessment"]), "utf-8"
            )
        summary = {
            "status": result["status"],
            "reason": result["reason"],
            "assessment_ref": result["assessment_ref"],
            "report_ref": result["report_ref"],
            "actual_requests": len(topics.runtime.budget.data["requests"]),
            "usage": topics.runtime.budget.usage(),
            "tools": [r["name"] for r in topics.runtime.scheduler.records if r["kind"] == "tool"],
            "elapsed_seconds": 900 - topics.runtime.budget.remaining_seconds(),
            "scientific_quality": "requires_manual_reading",
            "full_review_runs": 0,
        }
        write_json(output / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 0 if result["report_ref"] else 1
    finally:
        await topics.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/runtime.yaml")
    parser.add_argument("--application", type=Path, default=ROOT / "data/sample2.md")
    parser.add_argument(
        "--research-root", type=Path, default=ROOT / "runs/v5-local/change11/live-sample2-01"
    )
    parser.add_argument("--env-file", default=".env")
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
