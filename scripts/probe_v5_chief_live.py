"""Opt-in sample2 measurement research only; 18 actual requests and 25 minutes total.

Chief: at most six actual requests. All researchers: at most twelve. Auxiliary
models and embeddings belong to their requesting role and the same total ledger.
No scores, abstracts, translations, full review, or followup forced for coverage.
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

from spacebio_review.models.provider import ModelError  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.prose import prose_parts, render_parts  # noqa: E402
from spacebio_review.v5.chief import ChiefReview  # noqa: E402
from spacebio_review.v5.config import load_config  # noqa: E402
from spacebio_review.v5.topic import TopicResearch  # noqa: E402

OBJECTIVE = (
    "以国标体系主审身份理解完整 sample2 计划书，本次只研究“测量与科学解释”。"
    "研究计划书实际安排的对象、比较、实施阶段、读出及这些读出能支持的科学认识。"
    "围绕判断价值组织一个主要专题，需要时最多加一个直接相关辅助专题；"
    "可自己读取原文和研究文献，也可向已有专题续问，自主决定分工和有价值的研究。"
    "将外部研究的实验条件、观测与本项目实际安排联系起来，保留推断范围与未知。"
    "本次主审最多6次实际请求、研究员合计最多12次，合计最多18次，包含辅助模型和"
    "embedding请求。时间最多25分钟；请在现有资源内及时吸收已有结果并提交当前研究稿。"
    "研究稿是本次停止点；不进行六项评分、摘要、翻译或完整交付。"
)


class BoundedTopics(TopicResearch):
    """A local probe scope, not a production fixed-topic policy."""

    def create(self, task, **kwargs):
        if len(list((self.root / "topics").glob("*.json"))) >= 2:
            raise ValueError("local_probe_two_topic_limit")
        return super().create(task, **kwargs)


def limit_actual_requests(engine):
    """Add experiment caps before the existing ledger's reserve, never double count."""
    budget = engine.budget
    original_reserve = budget.reserve

    def reserve(config, messages=None, tools=None, response_schema=None, texts=None):
        owner = budget._owner.get()
        role = engine.load(owner).task.role
        rows = budget.data["requests"]
        roles = {sid: engine.load(sid).task.role for sid in budget.data["sessions"]}
        used = sum(roles[row["session_id"]] == role for row in rows)
        cap = 6 if role == "chief" else 12
        completing = budget._purpose.get() == "completion" and texts is None
        protected = (
            sum(
                budget._reserve_left(sid, "calls")
                for sid in roles
                if roles[sid] == "researcher" and not (completing and sid == owner)
            )
            if role == "researcher"
            else 0
        )
        reason = None
        if len(rows) >= 18:
            reason = "local_probe_total_actual_request_cap"
        elif used >= cap:
            reason = "local_probe_" + role + "_actual_request_cap"
        elif role == "researcher" and used + 1 + protected > cap:
            reason = "local_probe_researcher_completion_reserve"
        elif role == "chief" and used >= 5 and budget._purpose.get() != "completion":
            reason = "local_probe_chief_completion_reserve"
        if reason:
            raise ModelError(reason, category="budget", request_sent=False)
        return original_reserve(config, messages, tools, response_schema, texts)

    budget.reserve = reserve


def record_public_replies(engine, output):
    """Retain author outputs for scientific inspection, omitting private reasoning."""
    for alias, model in engine.models.items():
        original = model.complete

        async def complete(
            messages, tools=None, *, response_schema=None, _call=original, _alias=alias
        ):
            owner = engine.budget._owner.get()
            before = len(engine.budget.data["requests"])
            try:
                result = await _call(messages, tools, response_schema=response_schema)
            except Exception as exc:
                write_json(
                    output / "public-responses" / f"{before:03d}-{owner}-{_alias}-failure.json",
                    {"session_id": owner, "model": _alias, "error": str(exc)},
                )
                raise
            write_json(
                output / "public-responses" / f"{before:03d}-{owner}-{_alias}.json",
                {
                    "session_id": owner,
                    "model": _alias,
                    "finish_reason": result.finish_reason,
                    "message": {
                        key: result.message.get(key) for key in ("role", "content", "tool_calls")
                    },
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                },
            )
            return result

        model.complete = complete


async def probe(args):
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("probe_requires_empty_output_directory")
    output.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config).model_dump(mode="json")
    config["evaluation_system"] = "gbt42048_2022"
    config["budget"] = {
        "max_model_calls": 18,
        "max_embedding_calls": 18,
        "max_seconds": 1500,
    }
    config["session"].update(max_turns=6, completion_turns=1)
    for model in config["models"].values():
        model.update(timeout_seconds=min(model["timeout_seconds"], 600), retries=0)
    if config["embedding"]:
        config["embedding"].update(retries=0, timeout_seconds=45)
    for role, allowance in config["allowances"].items():
        allowance["completion_output_tokens"] = config["models"][config["roles"][role]][
            "max_tokens"
        ]
    config_path = output / "probe-config.yaml"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), "utf-8")
    topics = await BoundedTopics.from_config(
        config_path,
        output,
        args.application,
        env_file=args.env_file,
        cutoff=date(2026, 10, 15),
    )
    limit_actual_requests(topics.runtime)
    record_public_replies(topics.runtime, output)
    admission = {"status": "skipped", "reason": "formal_review_not_run_in_local_research_probe"}
    chief = ChiefReview(topics, evaluation_system="gbt42048_2022", admission=admission)
    chief_id = chief.create(OBJECTIVE)
    write_json(
        output / "input.json",
        {
            "chief_id": chief_id,
            "objective": OBJECTIVE,
            "application": args.application.resolve().as_posix(),
            "evaluation_system": "gbt42048_2022",
            "admission": admission,
            "limits": {
                "chief_actual": 6,
                "researcher_actual": 12,
                "total_actual": 18,
                "seconds": 1500,
            },
            "scientific_quality_requires_reading_actual_output": True,
        },
    )
    try:
        result = await chief.run(chief_id)
        write_json(output / "result.json", result)
        if result.get("note"):
            note = result["note"]
            (output / "research.md").write_text(
                "# "
                + note["title"]
                + "\n\n"
                + render_parts(prose_parts(note["body"])),
                "utf-8",
            )
        ledger = topics.runtime.budget
        roles = {sid: topics.runtime.load(sid).task.role for sid in ledger.data["sessions"]}
        actual = {
            role: sum(roles[row["session_id"]] == role for row in ledger.data["requests"])
            for role in ("chief", "researcher")
        }
        summary = {
            "chief_id": chief_id,
            "status": result["status"],
            "reason": result.get("reason"),
            "note_ref": result.get("note_ref"),
            "actual_requests_by_role": actual,
            "total_actual_requests": len(ledger.data["requests"]),
            "usage": ledger.usage(),
            "tools": [
                {key: row.get(key) for key in ("task_id", "role", "kind", "name", "status")}
                for row in topics.runtime.scheduler.records
                if row["kind"] in {"tool", "delegation"}
            ],
            "sources": topics.store.catalogue(),
            "full_review_runs": 0,
            "scope": "sample2 measurement and scientific interpretation, at most two topics",
        }
        write_json(output / "summary.json", summary)
        print(
            json.dumps(
                {
                    k: summary[k]
                    for k in (
                        "status",
                        "reason",
                        "actual_requests_by_role",
                        "total_actual_requests",
                    )
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0 if result["status"] == "submitted" else 1
    finally:
        await topics.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/v5-runtime.yaml")
    parser.add_argument("--application", type=Path, default=ROOT / "data/sample2.md")
    parser.add_argument("--env-file", default=".env")
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
