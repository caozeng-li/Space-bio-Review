"""One-author Change 02 probes; dry by default, at most six live calls per case.

No chief, review runner, retrieval, scoring, or model-based grader is created.
Cases use retained application/paper passages; the fixed-return control is an
explicit synthetic proposal. Run --live only to spend the bounded model quota.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from spacebio_review.agents.diagnostics import digest
from spacebio_review.models.provider import ModelError, model_class
from spacebio_review.persistence import write_json
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance
from spacebio_review.v5.config import load_config
from spacebio_review.v5.records import SessionSettings, SessionTask
from spacebio_review.v5.session import ResearchRuntime

MAX_CALLS = 6
CASE_NAMES = ("in_orbit", "fixed_return")
APPLICATION_ID = "src-214cbb92b88d"
PAPER_ID = "src-8a3b94261aa9"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def retained_material(source, source_id, passage_ids):
    document = read_json(source / "sources" / source_id / "source.json")
    candidate = document["candidate"]
    passages = {item["passage_id"]: item for item in document["passages"]}
    return {
        "source_id": source_id,
        "title": candidate["title"],
        "kind": "application" if source_id == APPLICATION_ID else "external",
        "url": candidate.get("url", ""),
        "authors": candidate.get("authors", []),
        "publication_date": candidate.get("publication_date", ""),
        "passages": [passages[pid] for pid in passage_ids],
        "provenance": str(source / "sources" / source_id / "source.json"),
    }


def snapshots(materials):
    """Use SourceScope's content-and-position identity for the supplied local excerpts."""
    readings = {}
    for material in materials:
        for passage in material["passages"]:
            reference = {
                "source_id": material["source_id"],
                "passage_id": passage["passage_id"],
                "visual_id": None,
            }
            version = digest(
                {
                    "content": passage["text"],
                    "reading_scope": "text",
                    "image_digest": None,
                }
            )
            handle = "evidence-" + digest({"reference": reference, "version": version})[:16]
            readings[handle] = {
                "reference": reference,
                "quote": passage["text"],
                "material_version": version,
                "title": material["title"],
                "kind": material["kind"],
                "locator": passage.get("section", passage["passage_id"]),
                "reading_scope": "text",
                "provided_via": ["probe_supplied_material"],
                "url": material.get("url", ""),
                "authors": material.get("authors", []),
                "publication_date": str(material.get("publication_date") or ""),
                "image_digest": None,
            }
    return readings


def scenarios(source):
    application = retained_material(source, APPLICATION_ID, ["p4", "p13", "p20"])
    paper = retained_material(source, PAPER_ID, ["m-f771e175b675-p17", "m-f771e175b675-p24"])
    fixed = {
        "source_id": "src-probe-fixed-return",
        "title": "局部对照材料：固定返回成像方案（合成申请，不是 sample2 原文）",
        "kind": "application",
        "provenance": "synthetic_control",
        "passages": [
            {
                "passage_id": "p1",
                "section": "合成研究方案",
                "text": "本对照方案以小鼠小肠类器官比较空间与地面条件下的线粒体类核形态。"
                "在轨第3、6、9天分别取样，先用PicoGreen标记活细胞，随后以2% PFA固定、"
                "0.1% Triton X-100透化，保存后返回地面进行共聚焦成像。"
                "本方案不进行在轨活细胞成像。目标读数是类核点状荧光的形态与数量。",
            }
        ],
    }
    biased_assignment = "评估返回固定样本中PicoGreen类核成像的可靠性及其对项目科学产出的影响。"
    return {
        "in_orbit": {
            "objective": biased_assignment,
            "application": application,
            "initial": [paper],
            "inspect": "实际在轨路线是否成为当前问题、正文和结语的主线；不是只添加一句纠正。",
        },
        "fixed_return": {
            "objective": biased_assignment,
            "application": fixed,
            "initial": [paper],
            "inspect": "固定与透化确属实际路线时，作者是否保留相关科学问题，并区分染料证据。",
        },
    }


class RecordedModel:
    """Keep public requests and replies from this execution."""

    def __init__(self, provider, root):
        self.provider, self.config, self.root = provider, provider.config, root
        self.index = 0

    async def complete(self, messages, tools=None, *, response_schema=None):
        self.index += 1
        path = self.root / "requests" / f"request-{self.index:02d}.json"
        record = {
            "messages": deepcopy(messages),
            "tools": tools,
            "response_schema": response_schema,
        }
        write_json(path, record)
        try:
            reply = await self.provider.complete(messages, tools, response_schema=response_schema)
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            if isinstance(exc, ModelError):
                record["reason"] = str(exc)
            write_json(path, record)
            raise
        record["reply"] = {
            key: reply.message[key]
            for key in ("role", "content", "tool_calls")
            if key in reply.message
        }
        record["usage"] = {
            "prompt_tokens": reply.prompt_tokens,
            "completion_tokens": reply.completion_tokens,
            "reasoning_tokens": reply.reasoning_tokens,
            "finish_reason": reply.finish_reason,
        }
        write_json(path, record)
        return reply

    async def close(self):
        await self.provider.close()


def make_runtime(root, config, alias, *, live=False, env_file=None):
    budget = Budget(
        root,
        BudgetLimits(
            max_model_calls=MAX_CALLS,
            max_embedding_calls=0,
            max_input_tokens=180000,
            max_output_tokens=MAX_CALLS * config.max_tokens,
            max_seconds=MAX_CALLS * config.timeout_seconds + 30,
        ),
    )
    model = (
        RecordedModel(model_class(config)(config, env_file=env_file, budget=budget), root)
        if live
        else SimpleNamespace(config=config)
    )
    allowance = SessionAllowance(
        max_input_tokens=180000,
        max_output_tokens=MAX_CALLS * config.max_tokens,
        completion_input_tokens=30000,
        completion_output_tokens=config.max_tokens,
        completion_calls=1,
        completion_seconds=config.timeout_seconds,
    )
    runtime = ResearchRuntime(
        root,
        {alias: model},
        {"researcher": alias},
        budget=budget,
        settings=SessionSettings(max_turns=12, context_tokens=30000, completion_turns=1),
        allowances={"researcher": allowance},
    )
    runtime.scheduler.request_budget = budget
    if live:
        runtime._owned_models = [model]
    return runtime, model


def current_summary(runtime, session_id):
    state = runtime.load(session_id)
    inquiry = runtime.store.inquiry(state.inquiry_id)
    contributions = []
    for path in (runtime.store.root / "notes").glob("*.json"):
        artifact = read_json(path)
        if artifact.get("inquiry_id") != state.inquiry_id:
            continue
        contributions.append(
            {
                "contribution_ref": path.stem,
                "focus": artifact.get("focus"),
                "previous_ref": artifact.get("previous_ref"),
                "revision": artifact.get("revision"),
                "author_text_ref": artifact.get("author_text_ref"),
            }
        )
        if artifact.get("author_text_ref"):
            raw = runtime.store.author_text(artifact["author_text_ref"])
            (runtime.store.root.parent / f"{path.stem}.md").write_text(
                raw["content"], encoding="utf-8"
            )
    return {
        "session_id": session_id,
        "inquiry": inquiry.model_dump(mode="json"),
        "status": state.status,
        "reason": state.reason,
        "turns_used": state.turns_used,
        "contributions": sorted(contributions, key=lambda item: item["revision"]),
        "usage": runtime.budget.usage(),
    }


def task_for(case):
    application = case["application"]
    return SessionTask(
        role="researcher",
        objective=case["objective"],
        application={"full_text_in_context": True, "sources": [application]},
        evaluation=json.dumps(
            {
                "scientific_use": "判断该路径能产生的知识、实现依据与重要解释条件。",
                "local_task": "只使用本次提供的原文与普通研究工具。先理解实际对象、操作阶段与读出，"
                "自主形成当前问题；有完整认识时贡献一份简洁专业稿，然后结束本轮。"
                "正文以约400—700字为宜，不安排后续检索或评分。",
            },
            ensure_ascii=False,
        ),
    )


async def run_case(name, case, args, config, alias, root):
    write_json(root / "materials.json", case)
    result = {"case": name, "mode": "live" if args.live else "dry", "inspect": case["inspect"]}
    runtime = None
    session_id = None
    try:
        # One runtime and ledger own construction, execution and result export.
        # Dry mode never constructs an API client or reads credentials.
        runtime, _ = make_runtime(
            root, config, alias, live=args.live, env_file=args.env_file
        )
        initial = snapshots([case["application"], *case["initial"]])
        session_id = runtime.create(task_for(case), readings=initial)
        state = runtime.load(session_id)
        write_json(root / "initial-context.json", runtime._context(state))
        write_json(
            root / "initial-composition.json",
            runtime._writing_messages(state, "final_manuscript", "submit_research", None, None),
        )
        result["initial"] = current_summary(runtime, session_id)
        if args.live:
            await runtime.run(session_id)
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        if isinstance(exc, ModelError):
            result["reason"] = str(exc)
    finally:
        if runtime is not None:
            if session_id is not None:
                result["final"] = current_summary(runtime, session_id)
            await runtime.close()
    result["executed"] = result.get("final", {}).get("usage", {}).get("model_calls", 0) > 0
    write_json(root / "result.json", result)
    return result


async def main(args):
    source, output = Path(args.source_run).resolve(), Path(args.output).resolve()
    if output == source or source in output.parents or output in source.parents:
        raise ValueError("The output must be separate from the retained run")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new or empty output directory")
    selected = CASE_NAMES if args.case == "all" else (args.case,)
    cases = scenarios(source)
    routes = load_config(args.runtime_config)
    alias = routes.roles["researcher"]
    config = routes.models[alias].model_copy(
        update={"retries": 0, "max_tokens": args.max_output_tokens}
    )
    output.mkdir(parents=True, exist_ok=True)
    write_json(
        output / "probe-plan.json",
        {
            "mode": "live" if args.live else "dry",
            "cases": list(selected),
            "provider_calls_limit": MAX_CALLS * len(selected),
            "model_alias": alias,
            "model": config.model_dump(mode="json"),
            "source_run": str(source),
            "synthetic_control": "fixed_return is synthetic; other cases use retained sample2.",
            "quality_assessment": "Read saved manuscripts; no automatic scientific grading.",
        },
    )
    results = []
    for name in selected:
        results.append(await run_case(name, cases[name], args, config, alias, output / name))
    write_json(output / "probe-results.json", results)
    print(
        json.dumps(
            {
                "output": str(output),
                "mode": "live" if args.live else "dry",
                "cases": [
                    {
                        "case": row["case"],
                        "executed": row["executed"],
                        "status": row.get("final", {}).get("status", "failed"),
                        "model_calls": row.get("final", {}).get("usage", {}).get("model_calls", 0),
                        "contributions": len(row.get("final", {}).get("contributions", [])),
                        "error_type": row.get("error_type"),
                    }
                    for row in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-run", default="runs/mytest37_sample2")
    parser.add_argument("--runtime-config", default="config/runtime.yaml")
    parser.add_argument("--case", choices=("all", *CASE_NAMES), default="all")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", default=".env", help="Read only with --live")
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    asyncio.run(main(parser.parse_args()))
