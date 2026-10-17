"""Fixed-material Change 17 authors and captured chief decisions; no child execution.

Three cases use five actual requests normally. All cases and reruns share one
durable 12-request/20-minute ledger, with no retries, search, or embeddings.
Scientific acceptance requires reading the saved public manuscripts manually.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_chief_live import record_public_replies  # noqa: E402
from probe_v5_evidence_handoff import author_input, read_json, rebind  # noqa: E402

from spacebio_review.agents.skills import SkillLibrary  # noqa: E402
from spacebio_review.models.provider import model_class  # noqa: E402
from spacebio_review.models.scheduling import RequestScheduler, ScheduledModel  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.prose import bind_prose  # noqa: E402
from spacebio_review.v5.authoring import (  # noqa: E402
    DeliveryParser,
    numbered_text,
    research_note,
)
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance  # noqa: E402
from spacebio_review.v5.chief import CHIEF_INSTRUCTION, TopicBatch  # noqa: E402
from spacebio_review.v5.config import load_config  # noqa: E402
from spacebio_review.v5.session import INSTRUCTION, UpdateUnderstanding  # noqa: E402
from spacebio_review.v5.topic import TOPIC_INSTRUCTION  # noqa: E402

CASES = ("comparison", "chief-continue", "chief-stop-local")
OBJECTIVES = {
    "comparison": (
        "根据固定原始材料，解释 Cell 先例对本申请科学背景与创新比较能提供什么，"
        "以及 Marotta 先例实际实现了哪些培养与观察能力、对申请路线有何借鉴价值。"
        "形成连贯研究说明，写明证据范围、可比条件与当前仍影响解释的未知。"
    ),
    "chief-continue": (
        "你刚收到三份历史专题交付。结合申请与旁附原文，吸收这些研究并形成更新的"
        "主审工作说明。历史作者结论可能有误，以原文支持的范围形成你自己的判断。"
        "解释现有认识对评价有什么作用，哪些科学问题仍可能改变判断。"
        "本轮停在吸收与下一步研究决定，不进行评分或报告。"
    ),
    "chief-stop-local": (
        "本轮只处理一个局部资料问题：Marotta 这篇研究的静态培养与取像安排，"
        "是否已经足以作为申请多指标共聚焦流程的直接实现依据。"
        "已有专题说明与该篇相关原文均已给出，请吸收并说明这一局部关系是否已经解释充分；"
        "剩余项目硬件参数如只能由申请给出，说明它对判断的影响。"
        "仅决定是否继续这一局部资料方向；其他测量、对照和项目评价问题不在此决定范围。"
    ),
}


def author_messages(case, payload):
    chief = case.startswith("chief-")
    skill = SkillLibrary(ROOT / "skills").get(
        "gbt42048-experiment-review" if chief else "literature-understanding"
    )
    system = INSTRUCTION + "\n" + (CHIEF_INSTRUCTION if chief else TOPIC_INSTRUCTION)
    system += "\n" + skill.body
    user = {
        "objective": OBJECTIVES[case],
        "current_stage": (
            "固定材料局部写作。只写一次普通正文，不调用工具、不重新检索；"
            "标题与正文自然段之间用空行分隔；仍影响科学解释的未知写在正文中。"
            "引用写本会话的 [@E编号]。"
            "本次只做该局部工作，已有证据之外的待研究内容说明为未知。"
        ),
        **payload,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def decision_tools():
    return [
        {
            "type": "function",
            "function": {"name": name, "description": description, "parameters": schema},
        }
        for name, description, schema in (
            (
                "update_understanding",
                "说明需要更新当前项目理解的原因。",
                UpdateUnderstanding.model_json_schema(),
            ),
            (
                "research_topics",
                "提出下一步有价值的研究；本探针仅保存请求，不执行。",
                TopicBatch.model_json_schema(),
            ),
        )
    ]


async def run_case(case, models, parser, budget, output, *, prior_specialist=None):
    folder = output / case
    owner = f"{output.name}-{case}"
    budget.register(owner, SessionAllowance(completion_calls=0))
    payload, aliases, evidence = author_input(case)
    if case == "chief-stop-local":
        if not prior_specialist:
            raise ValueError("stop_countercase_requires_completed_specialist_manuscript")
        reverse = {key: alias for alias, key in aliases.items()}
        payload["completed_local_research"] = rebind(prior_specialist, reverse)
    write_json(folder / "input.json", payload)
    write_json(folder / "evidence.json", {"aliases": aliases, "evidence": evidence})
    messages = author_messages(case, payload)
    author = models["chief" if case.startswith("chief-") else "researcher"]
    try:
        with budget.scope(owner):
            reply = await author.complete(messages)
        content = reply.message.get("content")
        raw = {
            "content": content,
            "operation": "update_research" if case.startswith("chief-") else "submit_research",
            "paragraphs": numbered_text(content) if isinstance(content, str) else [],
            "aliases": aliases,
        }
        write_json(folder / "author.json", raw)
        if reply.finish_reason != "stop" or reply.message.get("tool_calls"):
            raise ValueError("incomplete_local_author_manuscript")
        note = research_note(raw, title=OBJECTIVES[case]).model_dump(mode="json")
        note["body"] = bind_prose(note["body"], aliases)
        write_json(folder / "note.json", note)
        captured = []
        if case.startswith("chief-"):
            # The same scientific author receives its saved absorption and chooses
            # the next direction. These tools are schema-only: no child dispatch.
            messages.extend(
                [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "研究原稿已完整保存。按当前局部任务的认识决定下一步；"
                            "需要修订工作理解时可调用 update_understanding，若仍有值得研究的问题，"
                            "也可调用 research_topics 给出具体任务或续问。工具请求只会被截获，"
                            "不会执行新研究。请选择实际有价值的下一步；不写评分或报告。"
                        ),
                    },
                ]
            )
            with budget.scope(owner):
                decision = await author.complete(messages, tools=decision_tools())
            write_json(folder / "decision.json", decision.message)
            for call in decision.message.get("tool_calls", []):
                name = call["function"]["name"]
                schema = {
                    "update_understanding": UpdateUnderstanding,
                    "research_topics": TopicBatch,
                }[name]
                args = schema.model_validate_json(call["function"]["arguments"])
                captured.append({"tool": name, "arguments": args.model_dump(mode="json")})
            if not captured:
                raise ValueError("no_local_decision_captured")
            write_json(folder / "captured-decisions.json", captured)
        result = {
            "case": case,
            "status": "completed",
            "usage": budget.usage(owner),
            "captured_tools": [row["tool"] for row in captured],
            "child_research_executed": 0,
            "scientific_acceptance": "Requires manual comparison with supplied original evidence.",
        }
        write_json(folder / "result.json", result)
        return result, note
    finally:
        budget.finish(owner)


async def probe(args):
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("probe_requires_empty_output_directory")
    output.mkdir(parents=True, exist_ok=True)
    cases = CASES if args.case == "all" else (args.case,)
    # Reusing the ledger enforces cumulative limits across output directories.
    budget = Budget(
        args.ledger_root,
        BudgetLimits(
            max_model_calls=12,
            max_embedding_calls=0,
            max_seconds=1200,
        ),
    )
    used = budget.usage()["model_calls"]
    print(
        json.dumps(
            {
                "used_requests": used,
                "remaining_requests": 12 - used,
                "remaining_seconds": budget.remaining_seconds(),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    config = load_config(args.config)
    scheduler = RequestScheduler(config.concurrency)
    scheduler.request_budget = budget
    models, results, error = {}, [], None
    prior = read_json(args.prior_specialist) if args.prior_specialist else None
    try:
        aliases = {
            *[config.roles[key] for key in ("chief", "researcher")],
            config.auxiliary["delivery_parser"],
        }
        for alias in aliases:
            settings = config.models[alias].model_copy(
                update={
                    "retries": 0,
                    "timeout_seconds": min(config.models[alias].timeout_seconds, 240),
                    "max_tokens": min(config.models[alias].max_tokens, 8192),
                },
                deep=True,
            )
            models[alias] = model_class(settings)(settings, env_file=args.env_file, budget=budget)
        record_public_replies(SimpleNamespace(models=models, budget=budget), output)
        routed = {
            role: ScheduledModel(models[config.roles[role]], scheduler, config.roles[role])
            for role in ("chief", "researcher")
        }
        parser_alias = config.auxiliary["delivery_parser"]
        parser = DeliveryParser(ScheduledModel(models[parser_alias], scheduler, parser_alias))
        write_json(
            output / "probe-input.json",
            {
                "cases": list(cases),
                "ledger_path": str(budget.path),
                "limits": budget.limits,
                "expected_actual_requests": sum(2 if case == "comparison" else 3 for case in cases),
                "models": {alias: model.config for alias, model in models.items()},
                "process_no_proxy": {key: os.environ.get(key) for key in ("NO_PROXY", "no_proxy")},
                "retrieval_requests": 0,
                "embedding_requests": 0,
                "full_review_runs": 0,
            },
        )
        async with asyncio.timeout(budget.remaining_seconds()):
            for case in cases:
                result, note = await run_case(
                    case, routed, parser, budget, output, prior_specialist=prior
                )
                results.append(result)
                if case == "comparison":
                    prior = note
                print(json.dumps(result, ensure_ascii=False), flush=True)
    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "reason": str(exc),
            "category": getattr(exc, "category", None),
            "request_sent": getattr(exc, "request_sent", None),
        }
        write_json(output / "error.json", error)
    finally:
        budget.save()
        summary = {
            "status": "completed" if not error and len(results) == len(cases) else "incomplete",
            "results": results,
            "error": error,
            "cumulative_usage": budget.usage(),
            "this_run_actual_requests": budget.usage()["model_calls"] - used,
            "requests": budget.data["requests"],
            "full_review_runs": 0,
            "scientific_acceptance": "Manual reading required; no keyword or score checker.",
        }
        write_json(output / "summary.json", summary)
        await asyncio.gather(*(model.close() for model in models.values()))
    return 0 if summary["status"] == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=("all", *CASES), default="all")
    parser.add_argument("--prior-specialist", type=Path)
    parser.add_argument(
        "--ledger-root", type=Path, default=ROOT / "runs/v5-local/change17-live-budget"
    )
    parser.add_argument("--config", type=Path, default=ROOT / "config/runtime.yaml")
    parser.add_argument("--env-file", default=".env")
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
