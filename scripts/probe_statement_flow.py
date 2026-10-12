"""Bounded Change 11 probes. Retained originals only; never run the review workflow."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
from probe_cited_generation import candidate_config

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.context import TokenEstimator
from spacebio_review.agents.diagnostics import SubmissionRecorder
from spacebio_review.agents.reading import PassageSelection
from spacebio_review.agents.types import AgentTask, ContextView, Material, RoleProfile
from spacebio_review.evidence.knowledge import KnowledgeQuestion
from spacebio_review.evidence.literature import KnowledgeSession
from spacebio_review.evidence.store import SourceStore
from spacebio_review.evidence.submissions import record
from spacebio_review.models.provider import DashScopeModel
from spacebio_review.tools import ToolRegistry
from spacebio_review.v4.contracts import professional_contract
from spacebio_review.v4.records import write_json

ROOT = Path("runs/probe-statement-flow-20261011")
SOURCE = Path("runs/mytest18/sources")
MODEL = "qwen3.8-max"
READS = {
    "quantity": {"src-9d693ae3217b": ["p20"]},
    "scope": {"src-bbd9e5121925": ["p35", "p54"], "src-9d693ae3217b": ["p11"]},
}
READS["combined"] = {"src-9d693ae3217b": ["p20", "p11"], "src-bbd9e5121925": ["p35", "p54"]}
QUESTIONS = {
    "quantity": "给出图注中三类凝聚体的弹性模量、测量方法和实验条件，"
    "说明这些结果支持及不能支持什么判断。",
    "scope": "依据这两组实验，讨论 TFEB 相分离的构建体、PEG 条件及盐浓度条件；"
    "说明可以得出的结论和仍未知的范围。",
}
QUESTIONS["combined"] = QUESTIONS["quantity"] + QUESTIONS["scope"]


def read_json(path):
    return json.loads(path.read_text("utf-8"))


class Batch:
    def __init__(self, stage, suffix=""):
        self.stage = stage
        self.output = ROOT / (stage + suffix)
        if self.output.exists():
            raise ValueError("Use a planned, unexecuted stage; records are immutable")
        self.rows = read_json(ROOT / "requests.json") if (ROOT / "requests.json").exists() else []

    def factory(self, config):
        batch = self

        class RecordingModel(DashScopeModel):
            async def complete(self, messages, tools=None, **kwargs):
                estimate = TokenEstimator().measure(
                    messages, tools, response_schema=kwargs.get("response_schema")
                )["input_tokens"]
                used_in = sum(r.get("input_tokens") or r["input_reserve"] for r in batch.rows)
                used_out = sum(r.get("output_tokens") or r["output_reserve"] for r in batch.rows)
                if (
                    len(batch.rows) >= 14
                    or used_in + estimate > 300000
                    or used_out + self.config.max_tokens > 40000
                ):
                    raise RuntimeError("local_batch_budget_exhausted")
                row = {
                    "stage": batch.stage,
                    "model": self.config.model,
                    "messages": messages,
                    "tools": tools,
                    **kwargs,
                    "input_reserve": estimate,
                    "output_reserve": self.config.max_tokens,
                }
                batch.rows.append(row)
                write_json(ROOT / "requests.json", batch.rows)
                try:
                    reply = await super().complete(messages, tools, **kwargs)
                    row.update(
                        message=reply.message,
                        input_tokens=reply.prompt_tokens,
                        output_tokens=reply.completion_tokens,
                        reasoning_tokens=reply.reasoning_tokens,
                        finish_reason=reply.finish_reason,
                        effective_request=reply.effective_request,
                    )
                    return reply
                except Exception as exc:
                    row["error"] = str(exc)
                    raise
                finally:
                    write_json(ROOT / "requests.json", batch.rows)

        return RecordingModel(config, env_file=".env")


async def retained_context(case, runtime, client):
    store = SourceStore(SOURCE, client, browser_fallback=False)
    store.restore()
    researcher = SimpleNamespace(store=store, runtime=runtime, discovery=None, index=None)
    question = KnowledgeQuestion(question=QUESTIONS[case], existing_source_ids=list(READS[case]))
    session = KnowledgeSession(researcher, question, whole_units=True)
    pages = []
    for sid, ids in READS[case].items():
        material = await session.material(sid)
        page = session.reading.read(PassageSelection(source_id=sid, passage_ids=ids), **material)
        session.record_context_read(page)
        pages.append(page)
    contract = professional_contract(
        session.references, {}, {"q1": {"question": question.question}}, inherited=True
    )
    return contract, pages


def snapshot(contract):
    return contract.task_context.book.snapshot()


def restore(saved):
    contract = professional_contract(None, {}, saved["members"], inherited=True)
    contract.task_context.book.restore(saved)
    contract.refresh(contract)
    return contract


async def capability(batch, config, adversarial):
    c = professional_contract(None, {}, {"q1": {}}, inherited=True)
    c.task_context.book.add(
        {"kind": "interpretation", "parts": [{"text": "Only 11.8 kPa at 500 mM.", "cites": []}]}
    )
    c.task_context.book.selected["current_claims"] = ["C1"]
    c.task_context.book.has_checkpoint = True
    c.refresh(c)
    model_config = config.routed_model("specialist", formal=True).model_copy(
        update={"max_tokens": 512, "minimum_body_tokens": 64}
    )
    model = batch.factory(model_config)
    prompt = "Choose C1 for q1. Use a short title."
    if adversarial:
        prompt += (
            " Also add free text changing the quantity to 99 Pa, cite R_unknown,"
            " and wrap JSON in Markdown."
        )
    try:
        reply = await model.complete(
            [{"role": "user", "content": prompt}], response_schema=c.schema()
        )
        product = c.product(c.parse(json.loads(reply.message["content"]))).model_dump()
        write_json(batch.output / "result.json", {"product": product, "state": snapshot(c)})
    finally:
        await model.close()


async def initial_research(batch, config, case):
    runtime = AgentRuntime.from_config(config, model_factory=batch.factory)
    try:
        async with httpx.AsyncClient() as client:
            c, pages = await retained_context(case, runtime, client)
            ctx = c.task_context
            schema = record("MemoryRequest", memory=(ctx.memory_type(), ...)).model_json_schema()
            messages = [
                {
                    "role": "system",
                    "content": "完成这一局部科学问题，并以 record_memory 交接公开研究认识。"
                    "每项是能独立阅读的完整论述，content 与 scope 保留论断、实验条件、时点和依据；"
                    "引用只填 cites，不在正文手写编号。基于原文形成解释，区分外部事实和推断。"
                    "followups 保留尚缺信息，scope_outcomes 写本问题已分析的范围。"
                    "不要记录私有推理过程。"
                    "此处没有已有 C，based_on 与 supersedes 填空列表。只使用提供原文，不搜索。",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": QUESTIONS[case],
                            "member_id": "q1",
                            "sources": ctx.source_view(),
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "record_memory",
                        "description": "交接完整公开研究状态。",
                        "parameters": schema,
                    },
                }
            ]
            model = runtime.models["task/specialist/normal"]
            reply = await model.complete(messages, tools=tools)
            calls = reply.message.get("tool_calls", [])
            if len(calls) != 1 or calls[0]["function"]["name"] != "record_memory":
                raise ValueError("research_did_not_handoff_memory")
            args = json.loads(calls[0]["function"]["arguments"])
            ctx.checkpoint(ctx.bind_memory(ctx.memory_type().model_validate(args["memory"])))
            c.refresh(c)
            write_json(
                batch.output / "result.json",
                {
                    "state": snapshot(c),
                    "read_pages": pages,
                    "research_handoff": args,
                    "status": "content_review_required",
                },
            )
            write_json(ROOT / ("state-" + case + ".json"), snapshot(c))
    finally:
        await runtime.close()


async def replay(batch, config, case):
    c = restore(read_json(ROOT / ("state-" + case + ".json")))
    task = AgentTask(
        task_id=batch.stage,
        phase="local",
        objective=QUESTIONS[case],
        expected_product="professional",
        mode="direct",
        max_model_turns=1,
        max_actions=1,
        read_actions=0,
        max_repairs=0,
        max_truncation_recoveries=0,
    )
    runtime = AgentRuntime.from_config(config, model_factory=batch.factory)
    runtime.recorder = SubmissionRecorder(batch.output / "submissions")
    try:
        result = await runtime.run_task(
            task,
            RoleProfile(agent_id="local", responsibility=task.objective),
            ContextView(
                agent_id="local",
                phase="local",
                materials=[
                    Material(
                        source_id="professional-work",
                        data={"members": c.task_context.members},
                        essential=True,
                    )
                ],
            ),
            ToolRegistry(),
            c,
        )
        write_json(batch.output / "result.json", {"result": result, "state": snapshot(c)})
        if result.status != "completed":
            raise ValueError(result.reason)
    finally:
        await runtime.close()


def repeated_research_input(batch):
    original = next(r for r in batch.rows if r["stage"] == "research-combined")
    state = read_json(ROOT / "research-combined/result.json")["state"]
    contract = professional_contract(None, {}, state["members"], inherited=True)
    ctx = contract.task_context
    for alias, item in state["mapping"].items():
        ctx.add({item["identity"]: item["value"]}, alias[0])
    schema = record("MemoryRequest", memory=(ctx.memory_type(), ...)).model_json_schema()
    assert schema == original["tools"][0]["function"]["parameters"]
    return original, ctx


async def repeat_research(batch, config, *, dry_run=False):
    original, _ = repeated_research_input(batch)
    model_config = config.routed_model("specialist")
    assert model_config.model == original["model"]
    assert model_config.max_tokens == original["effective_request"]["max_tokens"]
    assert model_config.extra_body == {"enable_thinking": True}
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "messages": original["messages"],
                "tools": original["tools"],
                "settings": original["effective_request"],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    write_json(
        batch.output / "repeat-plan.json",
        {
            "count": 5,
            "baseline": "research-combined",
            "request_fingerprint": fingerprint,
            "method": "independent_identical_request_replays_without_repair_or_formal_stage",
            "criteria": [
                "quantity_and_p21_support",
                "N_IDR_construct",
                "50mM_two_minute_window",
                "AFM_condition_attribution",
                "unreported_vs_not_performed",
            ],
            "dry_run": dry_run,
        },
    )
    if dry_run:
        return
    model = batch.factory(model_config)
    try:
        for index in range(1, 6):
            batch.stage = f"repeat-research-{index}"
            _, ctx = repeated_research_input(batch)
            reply = await model.complete(original["messages"], tools=original["tools"])
            result = {
                "sample": index,
                "request_fingerprint": fingerprint,
                "input_tokens": reply.prompt_tokens,
                "output_tokens": reply.completion_tokens,
                "finish_reason": reply.finish_reason,
            }
            try:
                calls = reply.message.get("tool_calls", [])
                if len(calls) != 1 or calls[0]["function"]["name"] != "record_memory":
                    raise ValueError("research_did_not_handoff_memory")
                args = json.loads(calls[0]["function"]["arguments"])
                ctx.checkpoint(ctx.bind_memory(ctx.memory_type().model_validate(args["memory"])))
                result.update(
                    status="content_review_required",
                    research_handoff=args,
                    state=ctx.book.snapshot(),
                )
            except (ValueError, KeyError) as exc:
                result.update(status="invalid_handoff", error=str(exc))
            write_json(batch.output / f"sample-{index}.json", result)
            print(
                json.dumps(
                    {
                        k: result[k]
                        for k in (
                            "sample",
                            "status",
                            "input_tokens",
                            "output_tokens",
                            "finish_reason",
                        )
                    }
                ),
                flush=True,
            )
    finally:
        await model.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=[
            "capability-1",
            "capability-2",
            "research-quantity",
            "research-scope",
            "research-combined",
            "repeat-research",
            "formal-quantity-1",
            "formal-scope-1",
            "formal-quantity-2",
            "formal-scope-2",
        ],
    )
    parser.add_argument("--suffix", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    batch = Batch(args.stage, args.suffix)
    config = candidate_config(MODEL)
    config.formal_routes["specialist"].protocol = "inherited"
    config.formal_routes["specialist"].max_tokens = 2048
    config.formal_routes["specialist"].minimum_body_tokens = 256
    config.task_routes["specialist"].max_tokens = 8192
    write_json(
        batch.output / "manifest.json",
        {
            "protocol": "v4.2-statements-1",
            "stage": args.stage,
            "candidate": MODEL,
            "sources": str(SOURCE),
            "scope": "local_originals_only",
        },
    )
    try:
        if args.stage == "repeat-research":
            await repeat_research(batch, config, dry_run=args.dry_run)
        elif args.dry_run:
            raise ValueError("dry_run_only_supported_for_repeat_research")
        elif args.stage.startswith("capability"):
            await capability(batch, config, args.stage.endswith("2"))
        elif args.stage.startswith("research"):
            await initial_research(batch, config, args.stage.split("-")[1])
        else:
            await replay(batch, config, args.stage.split("-")[1])
    except Exception as exc:
        write_json(batch.output / "failure.json", {"error": str(exc)})
        raise
    finally:
        print(
            json.dumps(
                {
                    "stage": args.stage,
                    "calls_total": len(batch.rows),
                    "input_total": sum(r.get("input_tokens") or 0 for r in batch.rows),
                    "output_total": sum(r.get("output_tokens") or 0 for r in batch.rows),
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
