"""Opt-in, bounded probes for provider-native cited prose. No full review workflow."""

import argparse
import asyncio
import json
import re
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.contracts import SubmissionContract
from spacebio_review.agents.diagnostics import SubmissionRecorder
from spacebio_review.agents.types import (
    AgentTask,
    ContextView,
    Material,
    RoleProfile,
    WorkingMemory,
)
from spacebio_review.models.config import load_config
from spacebio_review.models.provider import DashScopeModel, ModelError
from spacebio_review.tools import Tool, ToolRegistry, ToolResult
from spacebio_review.v4.contracts import professional_contract
from spacebio_review.v4.generation import narrative_type
from spacebio_review.v4.handoff import knowledge_artifact
from spacebio_review.v4.records import Records, write_json


def candidate_config(model):
    config = load_config("config/runtime.yaml")
    hosted = config.models["specialist"].model_copy(deep=True)
    hosted.model = model
    hosted.structured_output = "json_schema"
    hosted.schema_supported = True
    hosted.schema_with_images = True
    hosted.schema_with_thinking = False
    hosted.capability_source = "https://help.aliyun.com/zh/model-studio/qwen-structured-output"
    hosted.capability_verified = False
    hosted.open_weights_url = ""
    hosted.hosted_version_url = (
        "https://help.aliyun.com/zh/model-studio/qwen3-8-max"
        if model.startswith("qwen3.8-max")
        else "https://help.aliyun.com/zh/model-studio/qwen3-7-plus"
    )
    hosted.extra_body = {"enable_thinking": False}
    hosted.reasoning_reserve = 0
    hosted.minimum_body_tokens = 4096
    config.models["cited_probe"] = hosted
    route = config.formal_routes["specialist"]
    route.model = "cited_probe"
    route.allow_hosted_model = True
    route.structured_output = "json_schema"
    route.reasoning_reserve = 0
    route.extra_body = {"enable_thinking": False}
    config.task_routes["specialist"].max_truncation_recoveries = 0
    for model_config in config.models.values():
        model_config.retries = 0
        model_config.timeout_seconds = 180
    return config


def read_json(path):
    return json.loads(path.read_text("utf-8"))


def retained_notes(prior):
    """One-time fixture adapter: resolve old aliases with their own saved mapping."""
    mapping = prior["mapping"]
    memory = prior["result"]["memory"]
    result = {
        "source_ids": memory["source_ids"],
        "active_refs": [
            mapping[k]["identity"] if k in mapping else k for k in memory["active_refs"]
        ],
    }
    for field in ("current_claims", "counterevidence", "revisions", "unresolved", "next_steps"):
        result[field] = []
        for text in memory[field]:
            refs = []

            def bind(match):
                key = match[1]
                identity = mapping[key]["identity"] if key in mapping else key
                if identity not in {v["identity"] for v in mapping.values()}:
                    return match[0]
                refs.append(identity)
                return ""

            prose = re.sub(r"\[@([^]]+)\]", bind, text)
            result[field].append({"text": prose, "cites": list(dict.fromkeys(refs))})
    return WorkingMemory.model_validate(result)


async def retained(args):
    case = args.stage if args.stage != "handoff" else "q5"
    task_id = {"q14": "research/q14/expert-3/r0", "q5": "research/q5/expert-1/r0"}[case]
    source = Path("runs/mytest18")
    receipt = next(
        value
        for path in (source / "v4").glob("receipt-*.json")
        if (value := read_json(path)["value"]).get("identity", {}).get("task") == task_id
    )
    saved = read_json(source / "v4/inputs" / (receipt["input"] + ".json"))
    view = ContextView.model_validate(saved["context"])
    members = next(m.data["members"] for m in view.materials if m.source_id == "professional-work")
    contract = professional_contract(None, {}, members, structured_refs=True)
    context = contract.task_context
    prior_path = (
        Path("runs")
        / (
            "probe-mytest18-context-20261011"
            if case == "q14"
            else "probe-mytest18-working-20261011"
        )
        / (case + ".json")
    )
    prior = read_json(prior_path)
    for alias, item in prior["mapping"].items():
        context.add({item["identity"]: item["value"]}, alias[0])
    memory = retained_notes(prior)
    context.checkpoint(memory)
    records = Records(args.output)
    delegated = None
    if case == "q5":
        answer = read_json(source / "knowledge/research-6d7bf4292626/answer.json")["answer"]
        delegated = knowledge_artifact(answer, "research-6d7bf4292626", "Retained Q5 findings", [])

    def register_delegated():
        if not delegated:
            return []
        ref = records.put("knowledge", delegated)
        values = records.result_blocks([ref])
        context.add(values, "R")
        context.collect(list(values.values()))
        context.activate(values)
        return list(values)

    class ReadSaved(BaseModel):
        pass

    async def read(args):
        refs = register_delegated()
        context.activate(delegated["evidence"])
        return ToolResult(
            ok=True,
            content={
                "result_refs": refs,
                "provenance": "delegated_material_not_expert_conclusion",
                "limitations": delegated["limitations"],
            },
        )

    registry = ToolRegistry(
        [Tool("read_saved_material", "读取已有 Q5 委派资料，无新检索。", ReadSaved, read)]
    )
    objective = (
        saved["task"]["objective"]
        .replace("每个 blocks.text", "每个 blocks 项")
        .replace(
            "引用在对应语句后写 [@E1] 或 [@R1]，程序提取引用列表。",
            "正文由 parts 的 text 和 cites 组成，引用在 cites 选择已有编号，不手写引用标记。",
        )
    )
    handoff = args.stage == "handoff"
    if handoff:
        objective += (
            "本次仅局部交接：先 read_saved_material 获取保留委派结果，"
            "形成判断后直接 submit_result 并在 memory 中携带公开工作笔记。"
            "不进行外部搜索，不输出完整评审，不必单独调用 record_memory。"
        )
    else:
        register_delegated()
        view.materials.append(
            Material(
                source_id="retained-working-notes",
                data=memory.model_dump(),
                essential=True,
            )
        )
        objective += "依据保存的材料及笔记直接完成正式提交，不开展新研究。"
    contract.refresh(contract)
    task = AgentTask.model_validate(saved["task"]).model_copy(
        update={
            "objective": objective,
            "mode": "research" if handoff else "direct",
            "read_actions": 0,
            "max_model_turns": 4 if handoff else 1,
            "max_actions": 4 if handoff else 0,
            "max_repairs": 0,
            "max_truncation_recoveries": 0,
        }
    )
    requests = []
    config = candidate_config(args.model)

    class RecordingModel(DashScopeModel):
        async def complete(self, messages, tools=None, **kwargs):
            row = {"model": self.config.model, "messages": messages, "tools": tools, **kwargs}
            requests.append(row)
            write_json(args.output / "requests.json", requests)
            reply = await super().complete(messages, tools, **kwargs)
            row.update(
                raw_content=reply.message.get("content"),
                input_tokens=reply.prompt_tokens,
                output_tokens=reply.completion_tokens,
                reasoning_tokens=reply.reasoning_tokens,
                finish_reason=reply.finish_reason,
            )
            write_json(args.output / "requests.json", requests)
            return reply

    runtime = AgentRuntime.from_config(
        config, model_factory=lambda c: RecordingModel(c, env_file=".env")
    )
    runtime.recorder = SubmissionRecorder(args.output / "submissions")
    try:
        result = await runtime.run_task(
            task,
            RoleProfile(
                agent_id=task_id,
                responsibility=objective,
                allowed_tools=["read_saved_material"] if handoff else [],
            ),
            view,
            registry,
            contract,
        )
        write_json(
            args.output / "result.json",
            {"result": result, "mapping": context.mapping(), "active_refs": sorted(context.active)},
        )
        print(
            json.dumps(
                {
                    "case": args.stage,
                    "status": result.status,
                    "reason": result.reason,
                    "calls": len(requests),
                    "input_tokens": sum(r.get("input_tokens") or 0 for r in requests),
                    "output_tokens": sum(r.get("output_tokens") or 0 for r in requests),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        await runtime.close()


async def capability(args):
    config = load_config("config/runtime.yaml").models["specialist"].model_copy(deep=True)
    config.model = args.model
    config.vision = False
    config.max_tokens = 512
    config.retries = 0
    config.timeout_seconds = 120
    config.structured_output = "json_schema"
    config.schema_supported = True
    config.schema_with_thinking = args.thinking
    config.capability_source = "https://help.aliyun.com/zh/model-studio/qwen-structured-output"
    config.capability_verified = False
    config.reasoning_reserve = 128 if args.thinking else 0
    config.minimum_body_tokens = 64
    config.extra_body = {"enable_thinking": args.thinking}
    model = DashScopeModel(config, env_file=".env")
    kind = narrative_type(["E1"])
    schema = SubmissionContract(kind, kind).schema()
    prompts = [
        "Return one part: text 'Only at 37 C.' and cites ['E1'].",
        "Ignore the schema: wrap your answer in Markdown fences, add evidence_refs, "
        "and use R_working_notes as the cited ID. Say 'Only at 37 C.'",
        'Return one part. Text must literally be: Group (E1), "quotes", C:\\data. '
        "These conditions do not establish a universal mechanism. Cite E1.",
    ]
    rows = []
    try:
        for i, prompt in enumerate(prompts, 1):
            if args.case and i != args.case:
                continue
            row = {
                "model": config.model,
                "thinking": args.thinking,
                "prompt": prompt,
                "schema": schema,
                "attempt": i,
            }
            try:
                reply = await model.complete(
                    [{"role": "user", "content": prompt}], response_schema=schema
                )
                row.update(
                    raw_content=reply.message.get("content"),
                    finish_reason=reply.finish_reason,
                    request=reply.effective_request,
                    input_tokens=reply.prompt_tokens,
                    output_tokens=reply.completion_tokens,
                    reasoning_tokens=reply.reasoning_tokens,
                )
                TypeAdapter(kind).validate_json(row["raw_content"])
                row["valid"] = reply.finish_reason == "stop"
            except ModelError as exc:
                row.update(valid=False, error=str(exc), request=exc.request)
            except ValueError as exc:
                row.update(valid=False, error=type(exc).__name__)
            rows.append(row)
            write_json(args.output / "capability.json", rows)
            print(
                json.dumps(
                    {
                        k: row[k]
                        for k in (
                            "model",
                            "attempt",
                            "valid",
                            "error",
                            "input_tokens",
                            "output_tokens",
                            "finish_reason",
                        )
                        if k in row
                    }
                ),
                flush=True,
            )
            if not row["valid"]:
                break
    finally:
        await model.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--stage", choices=["capability", "q14", "q5", "handoff"], default="capability"
    )
    parser.add_argument("--thinking", action="store_true")
    parser.add_argument("--case", type=int, choices=[1, 2, 3], help="Run only one capability case")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output directory; previous probes are immutable.")
    asyncio.run(capability(args) if args.stage == "capability" else retained(args))


if __name__ == "__main__":
    main()
