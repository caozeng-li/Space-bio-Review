"""Single-author, per-model-call question-only memory experiment.

The existing runtime executes every scientific tool and owns one session/budget.
An experimental response envelope carries findings, questions and native tool
actions in the SAME model response. Findings are archived, never summarized.
Production source, configuration and historical runs remain read-only.
"""

import argparse
import asyncio
import json
import re
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from probe_expert_trajectory_live import (
    ObservedRuntime, load, production_hashes, render_trajectory, run_one,
)
from spacebio_review.agents.context import TokenEstimator
from spacebio_review.evidence.reading_render import render_originals
from spacebio_review.models.provider import ModelError
from spacebio_review.persistence import write_json
from spacebio_review.prose import compile_authored_body, prose_inputs
from spacebio_review.v5.citation_writing import citation_refs
from spacebio_review.v5.records import ResearchNote
from spacebio_review.v5.config import load_config
from spacebio_review.v5.research_context import application_originals, compact_tool_payload
from spacebio_review.v5.scientific_roles import role_instruction


INSTRUCTION = """你持续负责同一个研究专题，每次调用是这位专家继续研究的一步。
主审分配的研究专题与完整申请始终可见。首步围绕专题开始；后续结合当前小问题、
已研究的问题和原文决定下一步。研究问题可能带有不成立的前提，申请安排以申请原文为准。
每次只输出一个完整的研究步骤JSON对象，必须包含以下六个字段，在同一次响应里完成以下工作：
findings：本步新形成的实质认识及依据，直接归档给主审。没有新认识时留空。
按实际材料交代研究对象、阶段、结果与申请的关系、可获得的认识和实质限制。
采用原文时用给定的 [证据N] 标记。无需重复全篇专题评价或每步撰写一篇报告。
studied_questions：本步确实已经研究过的小问题，只写问题，不写答案。
next_questions：三个问题位置 first、second、third，不需要的位置填空字符串，
表示下一步需要处理的0至3个小问题，可以保留尚在研究的当前问题。
问题来自本步研究推进，不必凑数；未确立的关系写成问题，不在问句里预设答案。
actions：下一批正常工具操作，用于取得下一步需要的原文或执行状态。
只有发起检索、还没看到结果，不算已经研究过。一个问题可以经过多次工具调用。
已研究的问题默认不重复，但当前材料带来新的实质线索时可以重开；研究过不等于已解决。
代码保存全部 findings 给主审，本步 findings 不会进入你的下一次输入；你也不回读这些旧稿。
原文和资料入口仍然可读。研究的连续性由问题清单保持，不另写答案摘要或记忆摘要。
stop_research：没有值得继续、可能改变专题判断的问题时结束，不以参数是否全部说明为目标。
结束时同时保存最后的新认识和停止理由。无需重新综合全部历史成果，主审直接接收各步记录。
"""

# Coordination is outside this single-author comparison in BOTH conditions.
COORDINATION = {"request_inquiry", "transfer_inquiry", "reply_consultation"}
CONTROL = {"publish_manuscript", "finish_research", "set_research_focus", "read_assignment"}


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    arguments: dict


class QuestionSlots(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first: str = ""
    second: str = ""
    third: str = ""

    def questions(self):
        return [q for q in self.model_dump().values() if q.strip()]


class ResearchStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: str
    studied_questions: list[str]
    next_questions: QuestionSlots
    actions: list[Action]
    stop_research: bool
    stop_reason: str


def inline_schema(schema):
    definitions = schema.get("$defs", {})

    def visit(value):
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return visit({**definitions[value["$ref"].split("/")[-1]],
                          **{k: v for k, v in value.items() if k != "$ref"}})
        return {k: visit(v) for k, v in value.items() if k != "$defs"}

    return visit(schema)


def step_schema(native_schemas):
    parameters = inline_schema(ResearchStep.model_json_schema())
    choices = []
    for row in native_schemas:
        fn = row["function"]
        if fn["name"] in CONTROL | COORDINATION:
            continue
        choices.append({
            "type": "object", "additionalProperties": False,
            "description": fn["description"],
            "properties": {
                "name": {"type": "string", "enum": [fn["name"]]},
                "arguments": inline_schema(fn["parameters"]),
            },
            "required": ["name", "arguments"],
        })
    parameters["properties"]["actions"] = (
        {"type": "array", "items": {"anyOf": choices}}
        if choices else {"type": "array", "maxItems": 0, "items": {"type": "object"}}
    )
    return [{"type": "function", "function": {
        "name": "research_step", "description": INSTRUCTION, "parameters": parameters,
    }}]


def bind_record(text, readings):
    """Bind only supplied identities; unresolved labels remain explicitly unresolved.

    Raw findings and their exact local reference table are also archived. Partial
    research records are not put through the final-manuscript admission contract.
    """
    refs = citation_refs(readings)
    unresolved = []

    def bind(match):
        label = "证据" + match[1]
        if label in refs:
            return f"[{label}](evidence:{refs[label]})"
        unresolved.append(label)
        return f"〔本步未绑定引用：{label}〕"

    return re.sub(r"\[证据\s*(\d+)\]", bind, text), unresolved, refs


class QuestionOnlyRuntime(ObservedRuntime):
    def __init__(self, root, *args, **kwargs):
        super().__init__(root, *args, **kwargs)
        self.probe_root = Path(root)
        self.plan = load(self.probe_root.parent / "plan.json")
        self.question_only = self.plan["condition"] == "questions"
        self.steps = []
        self.studied = []
        self.pending = []
        self.input_watermark = 0

    def _research_task(self, state, submission=None):
        message = super()._research_task(state, submission)
        if state.task.role == "researcher":
            body = json.loads(message["content"])
            body["global_research_topics"] = self.plan["global_topics"]
            message["content"] = json.dumps(body, ensure_ascii=False)
        return message

    def _tools(self, state, external, **kwargs):
        registry = super()._tools(state, external, **kwargs)
        if state.task.role == "researcher":
            registry = registry.select([
                row["function"]["name"] for row in registry.schemas()
                if row["function"]["name"] not in COORDINATION
            ])
        return registry

    def next_input(self, state, conversation):
        """Construct allowed state directly; no semantic filtering of past prose."""
        application = deepcopy(state.task.application)
        present = application_originals(application, state.evidence)
        for handle, item in present.items():
            item["citation"] = f"[{handle}](evidence:{handle})"
        latest = []
        for row in state.observations:
            if (row["index"] <= self.input_watermark or row.get("kind") != "tool"
                    or row.get("name") in CONTROL | COORDINATION):
                continue
            output = row["output"]
            latest.append({
                "tool": row["name"],
                "result": compact_tool_payload(
                    output["result"], output.get("readings", {}),
                )["result"],
            })
        payload = {
            "research_topic": self.plan["assignment"]["responsibility"],
            "global_research_topics": self.plan["global_topics"],
            "application": application,
            "skills": state.skill_bodies,
            "next_questions": self.pending,
            "studied_questions": self.studied,
            "available_sources": state.task.evaluation.get("available_sources", []),
            "latest_tool_results": latest,
            "resources": self.resources(state.session_id),
            "originals": [],
        }
        # Use the production complete-unit reading selector; retain all captured
        # originals when they fit. A prior authored citation gives no priority.
        estimator = TokenEstimator()
        base = estimator.measure_texts([json.dumps(payload, ensure_ascii=False)])["input_tokens"]
        room = max(0, state.settings.context_tokens - base - 16000)
        selected = conversation.select_readings(room, exclude=present)
        payload["originals"] = render_originals({
            h: {**item, "citation": f"[{h}](evidence:{h})"}
            for h, item in selected.items()
        })
        conversation.shown_handles = set(present) | set(selected)
        conversation.presented_handles.update(conversation.shown_handles)
        return [
            {"role": "system", "content": role_instruction("researcher") + "\n" + INSTRUCTION},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            *conversation.visual_messages(payload["originals"]),
        ]

    def _sync_conversation(self, state, *, delivered=()):
        if not self.question_only or state.task.role != "researcher":
            return super()._sync_conversation(state, delivered=delivered)
        conversation = self._conversations[state.session_id]
        conversation.remember(state.evidence)
        # Mutate in place: the existing tool loop retains this list reference.
        conversation.messages[:] = self.next_input(state, conversation)
        conversation.ack(state.observation_index)

    def _rebase_conversation(self, state, model, schemas, context=None, submission=None):
        if not self.question_only or state.task.role != "researcher":
            return super()._rebase_conversation(state, model, schemas, context, submission)
        state.authoring_phase = None
        self._sync_conversation(state)
        self.store.save(state)

    async def _call_model(self, state, model, messages, schemas, **kwargs):
        if not self.question_only or state.task.role != "researcher":
            return await super()._call_model(state, model, messages, schemas, **kwargs)
        # _sync_conversation has already rebuilt the live input before budgeting.
        # Replace any forced-publication instruction by the same step contract.
        conversation = self._conversations[state.session_id]
        outgoing = self.next_input(state, conversation)
        allowed = {r["function"]["name"] for r in schemas} - CONTROL - COORDINATION
        if not allowed:
            outgoing.append({"role": "user", "content":
                "研究额度已到收束窗口。本步保存最后的新认识并结束；不重新概括此前成果。"})
        self.input_watermark = state.observation_index
        contract = step_schema(schemas)[0]["function"]["parameters"]
        outgoing[0]["content"] += "\n本次JSON接口（actions由程序调用现有工具执行）：\n" + json.dumps(
            contract, ensure_ascii=False,
        )
        outgoing[0]["content"] += (
            '\n完整对象的字段形状：{"findings":"本步的新认识，没有则空字符串",'
            '"studied_questions":[],"next_questions":{"first":"","second":"","third":""},"actions":[],'
            '"stop_research":false,"stop_reason":""}。'
            '据实际研究填写六个字段；继续读取时在actions中填写工具名称及参数。'
        )
        reply = await super()._call_model(
            state, model, outgoing, [], response_schema=contract, **kwargs,
        )
        if reply.finish_reason != "stop" or reply.message.get("tool_calls"):
            raise ModelError("incomplete_research_step", category="exploration")
        step = ResearchStep.model_validate_json(reply.message["content"])
        if step.stop_research and step.actions:
            raise ModelError("stop_and_actions_in_same_step", category="exploration")
        record = {
            "turn": state.turns_used, "session_id": state.session_id,
            "questions_received": deepcopy(self.pending), **step.model_dump(),
            "next_questions": step.next_questions.questions(),
            "input_readings": sorted(conversation.shown_handles),
        }
        if step.findings.strip():
            body, unresolved, refs = bind_record(step.findings, state.evidence)
            record.update(unbound_citations=unresolved, citation_refs=refs)
            note = ResearchNote(
                title=f"专题研究第{state.turns_used}步", body=compile_authored_body(body),
            )
            record["note_ref"] = self.store.save_note(
                state, note, absorbed_through=self.input_watermark,
            )
            record["opinion"] = prose_inputs(note.model_dump())
        self.steps.append(record)
        for question in step.studied_questions:
            if question not in self.studied:
                self.studied.append(question)
        self.pending = step.next_questions.questions()
        write_json(self.probe_root / "research-steps.json", self.steps)
        write_json(self.probe_root / "question-state.json", {
            "studied_questions": self.studied, "next_questions": self.pending,
        })
        self.trace.event("question_step", **record)
        native = []
        for index, action in enumerate(step.actions):
            if action.name in CONTROL | COORDINATION:
                raise ModelError("unavailable_step_action:" + action.name, category="exploration")
            # The normal registry returns tool_not_available for an unknown
            # scientific tool, just as it does in the baseline conversation.
            native.append({"id": f"step_{state.turns_used}_{index}", "type": "function", "function": {
                "name": action.name, "arguments": json.dumps(action.arguments, ensure_ascii=False),
            }})
        if step.stop_research:
            if not state.note_ref:
                raise ModelError("stopped_without_research_record", category="exploration")
            native.append({"id": f"step_{state.turns_used}_finish", "type": "function", "function": {
                "name": "finish_research", "arguments": json.dumps({"reason": step.stop_reason}),
            }})
        # Exact findings remain in the trace/archive. Only execution commands go
        # back into the existing runtime; no additional model has been invoked.
        return replace(reply, message={"role": "assistant", "content": "", "tool_calls": native},
                       finish_reason="tool_calls" if native else "stop")


def prepare(root):
    root.mkdir(parents=True, exist_ok=False)
    previous = load("runs/question_relay_sample2/design.json")
    design = {
        "production_hashes": production_hashes(),
        "assignments": previous["assignments"], "global_topics": previous["global_topics"],
        "conditions": ["baseline", "questions"], "repeats_per_topic": 3,
        "instruction": INSTRUCTION,
        "scope": "One expert session; native tools; per-call memory change; chief evaluation only.",
    }
    config = load_config("config/runtime.yaml").model_dump(mode="json")
    config["budget"] = {"max_model_calls": 100, "max_embedding_calls": 100, "max_seconds": 7200}
    (root / "probe-config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), "utf-8")
    write_json(root / "design.json", design)
    for condition in design["conditions"]:
        for topic, assignment in design["assignments"].items():
            folder = root / condition / topic
            folder.mkdir(parents=True)
            plan = deepcopy(previous["plan"])
            plan.update(
                production_hashes=design["production_hashes"], condition=condition,
                assignment=assignment, source_ids=assignment["source_ids"],
                global_topics=design["global_topics"],
                scope=design["scope"],
            )
            write_json(folder / "plan.json", plan)
            (folder / "probe-config.yaml").write_text(
                yaml.safe_dump(config, allow_unicode=True), "utf-8")
    (root / "protocol.md").write_text(
        "# 单专家逐次调用的问题记忆实验\n\n"
        "正式实现不变。当前流程与问题记忆各做工程、线粒体两个专题，每专题计划3次。"
        "同一会话、同一作者、生产24次主专家调用和个人预算。来源池与完整申请一致，"
        "不注入旧意见、答案或指定易错段落；真实检索、重排、阅读及可选阅读员可用。"
        "两组不提供跨专题咨询；没有新增网页发现，没有完整评审与报告生成。\n\n"
        "实验组每次模型响应同时输出新认识、已研究问题、至多3个后续问题及原有工具操作。"
        "一个响应使用JSON输出模式，再由代码交回原有工具执行循环；没有额外整理调用。"
        "新认识按当次引用映射原样存档，不传入下一次。下一次保留专题、完整申请、技能、"
        "问题清单、实际原文和最新工具返回。问题清单只追加原问题，不保存答案或正确性标签。"
        "资料容量沿用完整单元选择，不按旧认识引用排序；记录实际曝光，不能将少读算纠正。\n\n"
        "结束时各步记录直接给主审；专家不再综合重写。主审对照使用相同国标、原文交付规则，"
        "其他专题固定，不把总分高低作为正确率。记录事实关系、申请转述、有效认识、重复、"
        "停止、接口失败、实际请求与成本。已知错误与新错误一并人工核对；不设自动科学纠错。"
        "这是上下文、输出接口和成果交付的组合实验，不能把净效果全归因于一个字段。\n",
        "utf-8",
    )


def summarize(run):
    calls = [load(p) for p in sorted((run / "trace").glob("call-*-output.json"))]
    main = [r for r in calls if r.get("role") == "researcher"]
    steps = load(run / "research-steps.json") if (run / "research-steps.json").exists() else []
    snapshots = [load(p) for p in (run / "trace").glob("session*-turn-*.json")]
    events = [json.loads(line) for line in (run / "trace/events.jsonl").read_text("utf-8").split("\n") if line]
    tools = [r["observation"] for r in events
             if r["kind"] == "observation" and r["observation"].get("kind") == "tool"]
    write_json(run / "execution.json", {
        "main_calls": len(main), "all_model_calls": len(calls),
        "main_session_ids": sorted({r["session_id"] for r in main}),
        "input_tokens": sum(r["input_tokens"] or 0 for r in calls),
        "output_tokens": sum(r["output_tokens"] or 0 for r in calls),
        "records": sum(bool(r.get("opinion")) for r in steps),
        "explicit_stop": bool(steps and steps[-1]["stop_research"]),
        "maximum_context_compactions": max((r["context_compactions"] for r in snapshots), default=0),
        "tool_errors": [{"tool": r["name"], "result": r["output"]["result"]} for r in tools
                        if not r["output"]["result"]["ok"]],
    })
    if steps:
        (run / "research.md").write_text("\n\n".join(
            f"## 第{r['turn']}次调用\n\n"
            + "本次问题：" + "；".join(r["questions_received"] or ["从专题开始"])
            + "\n\n" + (r.get("opinion", {}).get("body") or "（本步未形成研究认识）")
            + "\n\n后续问题：" + "；".join(r["next_questions"])
            + ("\n\n结束：" + r["stop_reason"] if r["stop_research"] else "")
            for r in steps
        ), "utf-8")


async def main(args):
    root = Path(args.output).resolve()
    if args.prepare:
        prepare(root)
        return
    if not args.live:
        raise ValueError("Use --live for real services")
    gate = asyncio.Semaphore(args.concurrency)

    async def job(spec):
        condition, topic, repeat = spec.split(":")
        folder = root / condition / topic
        async with gate:
            try:
                await run_one(folder, int(repeat), runtime_class=QuestionOnlyRuntime)
            finally:
                run = folder / f"run-{repeat}"
                if (run / "trace/events.jsonl").exists():
                    render_trajectory(run)
                    summarize(run)

    outcomes = await asyncio.gather(*(job(spec) for spec in args.jobs), return_exceptions=True)
    failures = [f"{s}: {r!r}" for s, r in zip(args.jobs, outcomes) if isinstance(r, BaseException)]
    if failures:
        raise RuntimeError("\n".join(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", default="runs/question_only_context_sample2")
    parser.add_argument("--jobs", nargs="+", default=[])
    parser.add_argument("--concurrency", type=int, default=2)
    asyncio.run(main(parser.parse_args()))
