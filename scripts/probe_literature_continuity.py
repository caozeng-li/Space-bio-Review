"""Ask one paper question and one follow-up within a single shared research budget."""

import argparse
import asyncio
import json
from pathlib import Path

from probe_literature_changes import ROOT, probe_configs, recorded_models
from probe_literature_reading import (
    PLOS_RETAINED,
    FixedPaperResearcher,
    retained_organoid_paper,
)

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.diagnostics import SubmissionRecorder
from spacebio_review.evidence import EvidenceResources
from spacebio_review.evidence.knowledge import KnowledgeQuestion
from spacebio_review.v4.budget import Budget
from spacebio_review.v4.config import ReportingConfig, V4Config
from spacebio_review.v4.records import Records, write_json

TOTAL_MODEL_CALLS = 16
MODEL_CALLS_PER_QUESTION = 8
TASK_ACTIONS_PER_QUESTION = 8
MAX_SECONDS = 1200
PARENT_RESEARCH = "probe-organoid-parent"


def questions(source_id):
    first = KnowledgeQuestion(
        question=(
            "这篇肠道类器官论文的自动计数方法测量什么？请联系成像、分割、"
            "算法参数和训练过程，解释其计数范围及训练结果的适用条件。"
            "本问关注方法本身，不需要重读 Figure 3 的显著性标记。"
        ),
        purpose="method",
        background=(
            "固定论文：In-silico and in-vitro morphometric analysis of intestinal organoids，"
            f"DOI 10.1371/journal.pcbi.1011386，source_id={source_id}。"
            "完整正文和原件已保存，当前任务尚未阅读；只使用本次已有原文，不外搜或下载。"
        ),
        existing_source_ids=[source_id],
        assessment_focus="理解实际测量对象、训练单位、参数与适用条件。",
        needed_information=[
            "用于建立方法的图像与分割流程。",
            "计数规则和参数是怎样训练出来的。",
            "训练与观测支持的计数范围，以及并未测得的量。",
        ],
    )
    followup = KnowledgeQuestion(
        question=(
            "沿着刚才得到的方法认识，若将同一计数算法用于另一批肠道类器官图像，"
            "改变显微镜、像素采样、聚焦方式或培养天龄，原研究能支持哪些具体的迁移判断？"
            "归一化参数是否足以保证原参数和训练表现仍适用？"
            "请把已得认识用于这个新问题，必要时继续读原文，区分论文实际观察与条件性推断。"
        ),
        purpose="method",
        background=(
            "这是上一次研究的续问，只使用本次已保存的论文及此前认识，不外搜或下载。"
            "以下是待分析的假设应用条件，不是原论文已经完成的实验。"
        ),
        conditions=[
            "目标图像来自另一显微镜，像素采样可能不同。",
            "目标流程拟使用单焦面明场图像，并可能包含不同培养天龄和重叠程度。",
            "目前尚无目标数据的人工隐窝计数或独立性能结果。",
        ],
        assessment_focus="以原研究的对象、条件和证据解释迁移范围，产生有用的后续认识。",
        needed_information=[
            "哪些参数与原成像、分割和培养条件有关。",
            "原文怎样解释形态差异及训练表现的变化。",
            "这些证据能支持怎样的迁移判断，以及不能直接承诺什么表现。",
        ],
    )
    return first, followup


async def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    runtime_config, evidence_config = probe_configs(
        args.runtime_config, args.evidence_config, output
    )
    evidence_config.literature.max_model_turns = MODEL_CALLS_PER_QUESTION
    evidence_config.literature.max_actions = TASK_ACTIONS_PER_QUESTION
    evidence_config.discovery.enabled = []
    evidence_config.browser_fallback = False
    budget = Budget(
        Records(output),
        V4Config(
            max_model_calls=TOTAL_MODEL_CALLS,
            max_embedding_calls=64,
            max_seconds=MAX_SECONDS,
            completion_reserve_calls=0,
            completion_reserve_seconds=0,
        ),
        ReportingConfig(),
    )
    runtime = resources = literature = None
    source_http_requests, checkpoints, runs = [], [], []

    async def record_source_request(request):
        source_http_requests.append({"method": request.method, "url": str(request.url)})

    def checkpoint(label, pool):
        row = {
            "stage": label,
            "research_root": pool.root,
            "shared_pool_limit": pool.limit,
            "shared_pool_remaining": pool.remaining,
            "provider_calls_remaining": budget.remaining_calls,
            "source_http_requests": len(source_http_requests),
        }
        checkpoints.append(row)
        write_json(output / "budget-checkpoints.json", checkpoints)

    try:
        runtime = AgentRuntime.from_config(
            runtime_config, model_factory=recorded_models(output, args.env_file, budget)
        )
        runtime.scheduler.request_budget = budget
        runtime.recorder = SubmissionRecorder(output, runtime_config.submission_max_bytes)
        resources = EvidenceResources.create(
            runtime, output, evidence_config, env_file=args.env_file
        )
        resources.store.discovery = None
        resources.store.client.event_hooks["request"].append(record_source_request)
        doc, origin = retained_organoid_paper(resources.store, args.paper_source)
        first_question, followup_question = questions(doc.source_id)
        manifest = {
            "change": "change08",
            "case": "organoid-continuation",
            "source": origin,
            "first_question": first_question.model_dump(mode="json"),
            "followup_question": followup_question.model_dump(mode="json"),
            "limits": {
                "model_calls_total": TOTAL_MODEL_CALLS,
                "model_calls_per_question": MODEL_CALLS_PER_QUESTION,
                "actions_per_question": TASK_ACTIONS_PER_QUESTION,
                "max_seconds": MAX_SECONDS,
                "automatic_resampling": False,
                "external_search": False,
            },
            "runtime_config": runtime_config.model_dump(mode="json"),
            "evidence_config": evidence_config.model_dump(mode="json"),
        }
        write_json(output / "input.json", manifest)
        literature = FixedPaperResearcher.from_resources(resources, env_file=args.env_file)
        async with asyncio.timeout(budget.remaining_seconds()):
            # Both attempts inherit this same pool. Neither scheduler nor Budget is reset.
            with runtime.scheduler.research_calls(PARENT_RESEARCH, TOTAL_MODEL_CALLS, 0) as pool:
                checkpoint("before_first", pool)
                first = await literature.investigate(first_question, single_submission=True)
                runs.append(first)
                write_json(output / "first-answer.json", first)
                checkpoint("after_first", pool)
                if first.answer is None or first.task_result.status != "completed":
                    raise RuntimeError("first_research_not_completed; followup_not_started")
                followup_question.previous_research_id = first.research_id
                manifest["followup_question"] = followup_question.model_dump(mode="json")
                write_json(output / "input.json", manifest)
                checkpoint("before_followup", pool)
                second = await literature.investigate(followup_question, single_submission=True)
                runs.append(second)
                write_json(output / "followup-answer.json", second)
                checkpoint("after_followup", pool)
        summary = {
            "case": "organoid-continuation",
            "status": "completed" if second.answer is not None else second.task_result.status,
            "model_calls": TOTAL_MODEL_CALLS - budget.remaining_calls,
            "source_http_requests": len(source_http_requests),
            "researches": [
                {
                    "research_id": run.research_id,
                    "previous_research_id": run.previous_research_id,
                    "study_id": run.study_id,
                    "status": run.task_result.status,
                    "reason": run.task_result.reason,
                    "model_turns": run.task_result.model_turns,
                    "actions": run.task_result.actions,
                }
                for run in runs
            ],
        }
        write_json(output / "result.json", summary)
        print(json.dumps(summary, ensure_ascii=False))
    except BaseException as exc:
        write_json(
            output / "result.json",
            {
                "status": "interrupted",
                "error_type": type(exc).__name__,
                "completed_research_ids": [run.research_id for run in runs if run.answer],
                "model_calls": TOTAL_MODEL_CALLS - budget.remaining_calls,
            },
        )
        raise
    finally:
        budget.save()
        write_json(output / "source-http-requests.json", source_http_requests)
        if runtime:
            write_json(output / "metrics.json", runtime.scheduler.metrics())
            write_json(output / "request-records.json", runtime.scheduler.records)
        if literature:
            await literature.close()
        if resources:
            await resources.close()
        if runtime:
            await runtime.close()


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "--output",
        type=Path,
        default=ROOT / "runs/literature-local-20261014/change08/organoid-continuation",
    )
    cli.add_argument("--runtime-config", type=Path, default=ROOT / "config/runtime.yaml")
    cli.add_argument(
        "--paper-source",
        type=Path,
        default=PLOS_RETAINED,
        help="Saved PLOS source directory containing source.json, original and figures.",
    )
    cli.add_argument("--evidence-config", type=Path, default=ROOT / "config/evidence.yaml")
    cli.add_argument("--env-file", type=Path, default=ROOT / ".env")
    return cli


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(parser().parse_args()))
