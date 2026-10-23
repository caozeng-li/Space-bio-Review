"""Test independent question workspaces and fresh expert synthesis, without production edits.

The controller chooses questions and already-read evidence. The same scientific model
answers in a fresh context; code attaches unchanged original units. Publishing requests
composition directly, rather than generating and then repairing an old manuscript.
"""

import argparse
import asyncio
import json
import re
from copy import deepcopy
from pathlib import Path

import yaml
from probe_expert_trajectory_live import (
    CALL_CONTEXT,
    ObservedRuntime,
    load,
    production_hashes,
    render_trajectory,
    run_one,
    summarize_trajectory,
)
from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.models.provider import ModelError
from spacebio_review.persistence import write_json
from spacebio_review.tools import Tool, ToolResult
from spacebio_review.v5.citation_writing import author_messages, citation_refs
from spacebio_review.v5.research_context import expand_captured_units
from spacebio_review.v5.scientific_roles import EXPERT_OPINION, role_instruction

WORKFLOW = """本次研究使用独立问题工作区。你自主决定要研究的问题、材料和停止时点。
读到影响评审判断的材料后，用 study_question 围绕一个具体科学问题形成认识；
选出需要共同理解的原文编号，问题本身表达要了解的关系，不预先给出答案。
该操作由与你相同的模型和专业背景完成，输入完整申请与所选完整原文单元，
不携带探索聊天或旧稿。回答及其原文保存在本专题工作记录中，你可据此继续补读、研究其他问题。
优先用这个工作方式完成自己的解释；有必要时仍可使用其他阅读工具。
材料和问题认识足够时，publish_manuscript 只填写 title。它会直接以完整申请、
问题认识及随附原文形成专题意见，不采用探索聊天或已有全篇稿。无需自行填 content。
随后沿正常流程决定结束或继续。"""


class StudyQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(description="一个影响项目判断、需要从原文回答的具体科学问题。")
    references: str = Field(
        min_length=1, description="共同阅读的已读原文编号字符串，用逗号分隔，如：证据12,证据25。",
    )


class ComposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str


def originals_text(readings):
    parts = []
    for handle, row in readings.items():
        structure = row.get("reading_structure") or {}
        parts += [
            f"[{handle}] {row.get('title', '')}",
            f"位置：{row.get('locator', '')}",
            "章节：" + " / ".join(structure.get("heading_path") or []),
            row.get("quote", ""), "",
        ]
    return "\n".join(parts)


def application_text(application):
    if isinstance(application, str):
        return application
    return "\n\n".join(
        f"{source['title']} · {p.get('locator', p['passage_id'])}\n{p['text']}"
        for source in application.get("sources", []) for p in source.get("passages", [])
    )


class WorkspaceRuntime(ObservedRuntime):
    def __init__(self, root, *args, **kwargs):
        super().__init__(root, *args, **kwargs)
        self.probe_root = Path(root)
        self.workspace_enabled = load(Path(root).parent / "plan.json")["workspace_enabled"]
        self.question_records = []

    def independent_messages(self, state, task, body):
        skills = "\n\n".join(str(value) for value in state.skill_bodies.values())
        return [
            {"role": "system", "content": role_instruction("researcher") + "\n" + task},
            {"role": "user", "content": (
                "专业背景\n" + skills + "\n\n评审职责\n"
                + self._inquiry(state).responsibility
                + "\n\n完整申请\n" + application_text(state.task.application)
                + "\n\n" + body
            )},
        ]

    async def isolated_call(self, state, messages, stage, schema=None):
        # Workspace model calls consume the same real token/request/time budget.
        # They are tool work, not additional controller interaction turns.
        token = CALL_CONTEXT.set({
            "session_id": state.session_id, "role": state.task.role,
            "turn": state.turns_used, "stage": stage,
        })
        purpose = "completion" if stage == "question_synthesis" else "research"
        try:
            with (
                self.scheduler.task(state.session_id, state.task.role, stage),
                self.scheduler.alias(state.model_alias),
                self.budget.scope(self.budget_account(state), purpose=purpose),
            ):
                async with asyncio.timeout(self.budget.remaining_seconds(
                    session_id=self.budget_account(state), purpose=purpose,
                )):
                    options = {"response_schema": schema} if schema else {}
                    reply = await self.models[state.model_alias].complete(
                        author_messages(messages, state.evidence), [], **options,
                    )
            if reply.finish_reason != "stop":
                raise ModelError("workspace_finish_" + reply.finish_reason)
            return reply.message.get("content") or ""
        finally:
            CALL_CONTEXT.reset(token)

    def _tools(self, state, external, **kwargs):
        registry = super()._tools(state, external, **kwargs)
        if not self.workspace_enabled or state.task.role != "researcher":
            return registry

        async def study(args):
            choices = citation_refs(state.evidence)
            references = re.findall(r"证据\d+|evidence-[0-9a-f]{16}", args.references)
            handles = [choices.get(ref, ref) for ref in references]
            if not handles:
                return ToolResult(ok=False, error="no_reference_selected")
            missing = [handle for handle in handles if handle not in state.evidence]
            if missing:
                return ToolResult(ok=False, error="unknown_reference", content=missing)
            selected = expand_captured_units(state.evidence, handles)
            messages = self.independent_messages(
                state,
                "现在只围绕本次具体问题形成独立认识。结合完整原文理解对象、阶段和条件，"
                "说明材料实际发现与本申请的关系。直接作出有内容的科学判断，用自然语言回答，"
                "按三个短标题展开：研究对象与阶段；材料所得认识；对本申请的意义。"
                "采用材料结果时使用提供的 [证据N]。",
                "本次问题\n" + args.question + "\n\n原文\n" + originals_text(selected),
            )
            finding = await self.isolated_call(state, messages, "question_reading")
            record = {
                "question": args.question, "finding": finding, "originals": selected,
                "turn": state.turns_used,
            }
            self.question_records.append(record)
            write_json(self.probe_root / "question-records.json", self.question_records)
            self.trace.event("question_record", **record)
            return ToolResult(ok=True, content={
                "question": args.question, "finding": finding,
                "original_references": references,
                "saved": "本次认识及其原文已进入专题成稿输入。",
            })

        registry.add(Tool(
            "study_question", "以独立上下文研究一个具体问题；同一模型直接阅读所选原文与申请，"
            "保存认识及原文供专题成稿。", StudyQuestion, study,
            timeout_seconds=600, delegates=True,
        ))
        return registry

    async def _call_model(self, state, model, messages, schemas, **kwargs):
        if not self.workspace_enabled or state.task.role != "researcher":
            return await super()._call_model(state, model, messages, schemas, **kwargs)
        wire_schemas = deepcopy(schemas)
        for row in wire_schemas:
            if row["function"]["name"] == "publish_manuscript":
                row["function"].update(
                    description="以完整申请、独立问题认识及其原文直接形成专题意见。只提交标题。",
                    parameters=ComposeRequest.model_json_schema(),
                )
        reply = await super()._call_model(
            state, model, [*messages, {"role": "user", "content": WORKFLOW}],
            wire_schemas, **kwargs,
        )
        for call in reply.message.get("tool_calls") or []:
            if call["function"]["name"] != "publish_manuscript":
                continue
            args = ComposeRequest.model_validate_json(call["function"]["arguments"])
            parts = []
            for index, record in enumerate(self.question_records, 1):
                parts += [
                    f"问题 {index}：{record['question']}",
                    record["finding"],
                    "随附原文：\n" + originals_text(record["originals"]),
                ]
            body = await self.isolated_call(state, self.independent_messages(
                state, EXPERT_OPINION + "\n直接输出 content 正文，不输出 JSON 或工具调用。",
                "拟交付专题：" + args.title + "\n\n本专题独立问题记录\n\n" + "\n\n".join(parts),
            ), "question_synthesis")
            # Protocol adapter routes newly composed prose to the unchanged persistence
            # path. No controller-written draft is produced or corrected beforehand.
            call["function"]["arguments"] = json.dumps(
                {"title": args.title, "content": body}, ensure_ascii=False,
            )
            self.trace.event("composition_delivery", title=args.title, content=body,
                             question_count=len(self.question_records))
        return reply


def prepare(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    parent = Path(args.baseline).resolve()
    plan = load(parent / "plan.json")
    config = yaml.safe_load((parent / "probe-config.yaml").read_text("utf-8"))
    if production_hashes() != plan["production_hashes"]:
        raise ValueError("production_changed_since_parent_experiment")
    for name in ["baseline", "workspace"]:
        folder = output / name
        folder.mkdir()
        (folder / "probe-config.yaml").write_text(
            yaml.safe_dump(config, allow_unicode=True), "utf-8",
        )
        write_json(folder / "plan.json", {
            **plan, "workspace_enabled": name == "workspace",
            "scope": [
                "Empty expert opinion; autonomous current research over the same retained corpus.",
                "No historical draft, known-error passages or gold answer injected.",
                "Baseline unchanged; treatment uses independent questions and fresh composition.",
                "Controller selects questions and already-read originals without oracle selection.",
                "Current original-unit expansion; full application shown in workspace.",
                "Shared production budgets; workspace tool calls do not consume controller turns.",
                "No new discovery or full review; production code/config/skills unchanged.",
            ],
        })
    write_json(output / "design.json", {
        "conditions": ["baseline", "workspace"], "repeats_per_condition": 3,
        "concurrency": 2, "production_hashes": production_hashes(),
        "manual_evaluation": plan["manual_targets"],
        "additional_targets": [
            "First focused understanding versus synthesis, retention of useful positive judgments.",
            "Actual question/evidence selection and non-adoption are reported, not corrected.",
        ],
    })
    (output / "protocol.md").write_text(
        "# 独立问题工作区实验\n\n"
        "当前专题研究对照3次、独立问题工作区3次。两组均从空白意见开始，使用完整sample2申请和"
        "mytest49该专题的5个已留存来源；实际召回、重排、阅读全文工具、专家技能和停止机制保持。"
        "不启动新网络检索，不运行完整评审。模型均为正式配置，thinking关闭。\n\n"
        "实验组自主选问题和已读证据；代码展开所在原文单元，同一专家模型在独立上下文中回答。"
        "成稿仅看完整申请、专业背景、职责、问题认识和相邻原文，不带探索聊天或旧稿。"
        "没有人为指定科学答案或容易错的段落，没有事后纠错调用。子调用计入同一预算，"
        "不计入研究控制会话的24轮上限。保留全部公开输入输出及调用统计。\n\n"
        "人工比较对象与阶段混用、申请安排被改写、定量结论适用范围，以及有用科学判断。"
        "遗漏某事实不记为已正确理解。先做每组第1次检查接口，若修正实验接口须单列记录；"
        "科学失败样本不能因结论不好而排除。正式代码、配置、技能和原始材料前后哈希核对。\n",
        "utf-8",
    )


async def main(args):
    if args.prepare:
        prepare(args)
        return
    if not args.live:
        raise ValueError("Real model calls require --live")
    gate = asyncio.Semaphore(2)

    async def job(spec):
        group, repeat = spec.split(":")
        if group not in {"baseline", "workspace"}:
            raise ValueError(group)
        output = Path(args.output).resolve() / group
        async with gate:
            await run_one(output, int(repeat), runtime_class=WorkspaceRuntime)
            render_trajectory(output / f"run-{repeat}")
            summarize_trajectory(output / f"run-{repeat}")

    results = await asyncio.gather(*(job(spec) for spec in args.jobs), return_exceptions=True)
    failures = [f"{spec}: {result!r}" for spec, result in zip(args.jobs, results, strict=True)
                if isinstance(result, BaseException)]
    if failures:
        raise RuntimeError("\n".join(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--baseline", default="runs/expert_trajectory_sample2")
    parser.add_argument("--output", default="runs/question_workspace_sample2")
    parser.add_argument("--jobs", nargs="+", default=["baseline:1", "workspace:1"])
    asyncio.run(main(parser.parse_args()))
