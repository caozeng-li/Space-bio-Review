"""Fixed-draft publication checks only: no review engine and no network requests."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spacebio_review.models.scheduling import RequestScheduler  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.publication import compile_publication  # noqa: E402
from spacebio_review.publication.document import walk_nodes  # noqa: E402
from spacebio_review.review.evaluation import GBT42048, GENERAL  # noqa: E402
from spacebio_review.v5.budget import Budget, BudgetLimits  # noqa: E402
from spacebio_review.v5.delivery import deliver_assessment  # noqa: E402


def prose(*texts, cites=("evidence-application",)):
    return {"paragraphs": [{"parts": [{"text": text, "cites": list(cites)}]} for text in texts]}


def fixture(system):
    assessment = {
        "evaluation_system": system.snapshot(),
        "project": prose("项目包含地面对照，在轨记录荧光，返回后独立检测分子终点。"),
        "scope": prose("本次判断针对计划书中在轨与返回后测量的科学含义。"),
        "dimensions": {
            item.id: {"body": prose(item.label + "：该读出支持现有比较范围内的描述。")}
            for item in system.criteria
        },
        "overall": prose("荧光差异与机制解释具有不同的证据范围，地面对照确实存在。"),
        "summary": prose(
            "对当前方案的支持范围限于既有设计与观测之间的解释关系。",
            "在轨读出和返回后检测的对象与阶段不同。",
            "总分由代码计算，摘要沿用主审获知的最终结果。",
        ),
        "unknowns": [{"title": "外推范围", "text": prose("跨阶段外推的充分性仍不确定。")}],
    }
    if system.score_grades:
        for row, score in zip(assessment["dimensions"].values(), (22, 20, 15, 16, 5, 7)):
            row["score"] = score
        assessment.update(
            total_score=85,
            score_grade="A",
            recommendation=system.grade_for(85).recommendation,
            safety_veto={"applies": False, "reason": prose("现有材料未形成安全否决依据。")},
        )
    else:
        for index, row in enumerate(assessment["dimensions"].values()):
            row.update(grade="B" if index else None, confidence="C" if index else None)
        assessment.update(
            recommendation="support", support_scope=prose("现有数据所能支持的描述性认识。")
        )
    evidence = {
        "evidence-application": {
            "reference": {"source_id": "source-application", "passage_id": "p1", "visual_id": None},
            "title": "固定计划书",
            "kind": "application",
            "locator": "实验方案／在轨与地面对照",
            "quote": "包含地面对照；返回后再实施分子检测。",
            "reading_scope": "text",
        },
    }
    contributions = [
        {
            "inquiry_id": "inquiry-topic",
            "contribution_ref": "note-selected",
            "note": {
                "title": "测量与解释的范围",
                "body": prose(
                    "原科学稿完整进入报告；观测时间为 3 天。",
                    "跨对象迁移的范围仍未知。",
                    cites=("evidence-paper",)
                ),
            },
            "evidence": {
                "evidence-paper": {
                    "reference": {
                        "source_id": "source-paper",
                        "passage_id": "p2",
                        "visual_id": None,
                    },
                    "title": "固定外部论文",
                    "kind": "external",
                    "locator": "Results, paragraph 2",
                    "quote": "The observation was made at day 3.",
                    "reading_scope": "text",
                    "url": "https://example.org/paper",
                }
            },
            "focus": "测量与解释的范围",
            "current_focus": "测量与解释的范围",
            "author_session_id": "author-topic",
            "author_text_ref": "author-text-topic",
            "paragraph_ids": ["topic-p1", "topic-p2"],
            "author_paragraph_ids": ["p1", "p2"],
        }
    ]
    return assessment, evidence, contributions


def selection(contributions, *, appendix=False):
    refs = [
        {key: row[key] for key in ("inquiry_id", "contribution_ref")}
        for row in contributions
    ]
    return {
        "main_sections": [] if appendix else refs,
        "appendix_sections": refs if appendix else [],
    }


def runtime(output):
    return SimpleNamespace(
        store=SimpleNamespace(root=output / "v5"),
        budget=Budget(output, BudgetLimits(max_model_calls=100, max_seconds=600)),
        scheduler=RequestScheduler(),
    )


async def run(output):
    if output.exists() and any(output.iterdir()):
        raise ValueError("probe_requires_empty_output_directory")
    results = []
    for system in (GBT42048, GENERAL):
        case = output / system.id
        assessment, evidence, contributions = fixture(system)
        report_selection = selection(contributions)
        document, audit = compile_publication(
            assessment, evidence=evidence, report_selection=report_selection,
            contributions=contributions,
        )
        for criterion in system.criteria:
            section = next(row for row in document.main if row.section_id == criterion.id)
            actual = [node.text for node in walk_nodes(section.blocks) if node.kind == "text"]
            expected = [
                part["text"]
                for paragraph in assessment["dimensions"][criterion.id]["body"]["paragraphs"]
                for part in paragraph["parts"]
            ]
            assert actual == expected
        scientific = document.main[3]
        assert (
            scientific.blocks[0].children[0].text
            == contributions[0]["note"]["body"]["paragraphs"][0]["parts"][0]["text"]
        )
        assert scientific.blocks[0].children[1].target == "evidence-paper"
        assert audit["report_selection"] == report_selection
        engine = runtime(case)
        result = await deliver_assessment(
            engine,
            assessment,
            evidence=evidence,
            report_selection=report_selection,
            contributions=contributions,
            english=False,
        )
        assert result["status"] == "completed" and result["en"]["status"] == "disabled"
        assert "en" not in result["paths"] and not (case / "report.en.md").exists()
        assert engine.budget.usage()["model_calls"] == 0
        report = (case / "report.zh.md").read_text("utf-8")
        assert "地面对照确实存在" in report and "实验方案／在轨与地面对照" in report
        if system.id == "general":
            assert document.total_score is None and document.scores == {}
            assert "| 总分 |" not in report and "未评分" in report
        else:
            assert document.total_score == 85 and "| 总分 | 85 | 100 |" in report
        results.append({"case": system.id, "status": "passed", "model_requests": 0})

    summary = {
        "scope": "Fixed assessment publication; offline provider fixtures, no review execution.",
        "results": results,
    }
    write_json(output / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.output)), ensure_ascii=False, indent=2))
