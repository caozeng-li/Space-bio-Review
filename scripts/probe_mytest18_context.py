"""Opt-in retained-material research probe. No discovery or full review workflow."""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from pydantic import BaseModel

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.diagnostics import SubmissionRecorder
from spacebio_review.agents.reading import PassageSelection, ReadingWindow
from spacebio_review.agents.types import AgentTask, ContextView, Material, RoleProfile
from spacebio_review.models.config import load_config
from spacebio_review.models.provider import DashScopeModel
from spacebio_review.tools import Tool, ToolRegistry, ToolResult
from spacebio_review.v4.contracts import professional_contract
from spacebio_review.v4.records import write_json


class ReadSaved(BaseModel):
    continuation: str | None = None


async def probe(case, source, output, formal_from=None, repairs=0):
    task_id = {"q14": "research/q14/expert-3/r0", "q5": "research/q5/expert-1/r0"}[case]
    receipt = next(
        value
        for path in (source / "v4").glob("receipt-*.json")
        if (value := json.loads(path.read_text("utf-8"))["value"]).get("identity", {}).get("task")
        == task_id
    )
    saved = json.loads((source / "v4/inputs" / (receipt["input"] + ".json")).read_text("utf-8"))
    view = ContextView.model_validate(saved["context"])
    members = next(m.data["members"] for m in view.materials if m.source_id == "professional-work")
    contract = professional_contract(None, {}, members)
    for alias, item in receipt["mapping"].items():
        contract.task_context.add({item["identity"]: item["value"]}, alias[0])
    context = contract.task_context
    contract.refresh(contract)
    context.active = {
        k for k, v in context.evidence.items() if v.get("kind") == "application"
    } | set(context.results)
    window = ReadingWindow()
    if case == "q14":
        from spacebio_review.evidence.types import SourceDocument

        doc = SourceDocument.model_validate(
            json.loads((source / "sources/src-581c32de3568/source.json").read_text("utf-8"))
        )
        ids = [
            p.passage_id
            for p in doc.passages
            if p.passage_id
            in {
                v["reference"].get("passage_id")
                for v in context.evidence.values()
                if v["reference"]["source_id"] == doc.source_id
            }
        ]

    async def read(args):
        if case == "q14":
            from spacebio_review.agents.reading import BrowseSelection

            selection = (
                BrowseSelection(source_id=doc.source_id, continuation=args.continuation)
                if args.continuation
                else PassageSelection(source_id=doc.source_id, passage_ids=ids)
            )
            page = window.read(selection, passages=doc.passages)
            context.activate_read(page)
            return ToolResult(ok=True, content=page, reading_scope="retained_passages")
        answer = json.loads(
            (source / "knowledge/research-6d7bf4292626/answer.json").read_text("utf-8")
        )["answer"]
        context.activate(answer.get("evidence_resolution", {}))
        return ToolResult(
            ok=True, content={"answer": answer, "provenance": "retained_delegated_answer"}
        )

    registry = ToolRegistry(
        [
            Tool(
                "read_saved_material",
                "读取本问题历史保留材料，continuation 继续；无外部搜索。",
                ReadSaved,
                read,
            )
        ]
    )
    registry.reading_windows.append(window)
    task = AgentTask.model_validate(saved["task"]).model_copy(
        update={
            "max_model_turns": 6,
            "max_actions": 6,
            "max_repairs": 0,
            "max_truncation_recoveries": 0,
            "context_soft_tokens": 48000,
            "objective": saved["task"]["objective"]
            + "\n本次仅局部验证：先 read_saved_material 读取保留资料；"
            "形成公开认识后用 record_memory 记录判断、反证、条件、未决问题及 active_refs，"
            "再 submit_result。没有新外部搜索，不能声称做过新的全面调查。",
        }
    )
    if formal_from:
        prior = json.loads((Path(formal_from) / (case + ".json")).read_text("utf-8"))
        context.active = set(prior["active_refs"])
        view.materials.append(
            Material(
                source_id="retained-working-notes", data=prior["result"]["memory"], essential=True
            )
        )
        if case == "q5":
            answer = json.loads(
                (source / "knowledge/research-6d7bf4292626/answer.json").read_text("utf-8")
            )["answer"]
            view.materials.append(
                Material(source_id="retained-delegated-findings", data=answer, essential=True)
            )
        task = task.model_copy(
            update={
                "mode": "direct",
                "read_actions": 0,
                "max_model_turns": 1 + repairs,
                "max_repairs": repairs,
                "objective": saved["task"]["objective"]
                + "依据保留的公开工作笔记和原文直接完成正式提交，不开展新研究。",
            }
        )
    config = load_config("config/runtime.yaml")
    for model in config.models.values():
        model.retries = 0
        model.timeout_seconds = 180
    requests = []

    class RecordingModel(DashScopeModel):
        async def complete(self, messages, tools=None, **kwargs):
            requests.append({"messages": messages, "tools": tools, **kwargs})
            return await super().complete(messages, tools, **kwargs)

    runtime = AgentRuntime.from_config(
        config, model_factory=lambda c: RecordingModel(c, env_file=".env")
    )
    runtime.recorder = SubmissionRecorder(output / case)
    try:
        result = await runtime.run_task(
            task,
            RoleProfile(
                agent_id=task.task_id,
                responsibility=task.objective,
                allowed_tools=["read_saved_material"],
            ),
            view,
            registry,
            contract,
        )
        write_json(output / (case + "-requests.json"), requests)
        write_json(output / (case + "-operations.json"), runtime.scheduler.records)
        write_json(
            output / (case + ".json"),
            {"result": result, "mapping": context.mapping(), "active_refs": sorted(context.active)},
        )
        print(
            json.dumps(
                {
                    "case": case,
                    "status": result.status,
                    "reason": result.reason,
                    "turns": result.model_turns,
                    "archive": len(context.mapping()),
                    "active": len(context.active),
                    "usage": result.context_usage,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        await runtime.close()


async def main(args):
    output = Path(args.output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("use_an_empty_probe_output")
    output.mkdir(parents=True, exist_ok=True)
    for case in args.cases:
        await probe(case, Path(args.source), output, args.formal_from, args.repairs)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="runs/mytest18")
    parser.add_argument("--output", required=True)
    parser.add_argument("--repairs", type=int, choices=[0, 1], default=0)
    parser.add_argument(
        "--formal-from", help="Replay only submission from a saved probe checkpoint"
    )
    parser.add_argument("--cases", nargs="+", choices=["q14", "q5"], default=["q14", "q5"])
    asyncio.run(main(parser.parse_args()))
