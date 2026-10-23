"""Isolated, fresh-session question relay; production files and historical runs are read-only."""

import argparse
import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Literal

import yaml
from probe_expert_trajectory_live import (
    ObservedRuntime,
    hashes,
    load,
    production_hashes,
    render_trajectory,
    run_one,
    summarize_trajectory,
)
from pydantic import BaseModel, Field

from spacebio_review.persistence import write_json
from spacebio_review.v5.scientific_roles import EXPERT_OPINION
from spacebio_review.v5.session import PublishManuscript

ROUND_DELIVERY = """你负责当前专题的一轮研究。本轮可多次读取原文、追索条件和比较解释。
第一轮从主审分配的研究专题出发，选择有价值的具体问题研究；后续轮优先研究交接的小问题。
全局研究专题始终是研究方向，不因上一轮提出的小问题而改变。申请事实以完整申请为准。
完成本轮聚焦问题后，用 publish_manuscript 一次提交本轮研究成果和问题交接：
content 面向主审，保留具体发现、对象、阶段、论据、对项目科学价值和限制的判断。
篇幅随实际问题决定，无需每轮重写整个专题的全篇评审。正文采用原文时用提供的 [证据N] 引用。
studied_questions 只记录本轮实际研究的小问题及处理状态，不填写答案或结论摘要。
next_questions 提出0至3个值得下一位专家研究的问题；它们来自本轮研究的推进，不要求凑数。
把未确立的关系写成待研究的问题，避免在问句前附带未经确立的答案。
已经研究过的问题默认不重复；当前问题需要或新材料改变理解时，可以重新研究。
本轮成果直接存给主审，不会交给下一轮专家。下一轮只有小问题记录，并直接读原文。
仍有值得继续的问题时，把它们交给下一轮，不在本轮继续把所有问题做完。
没有值得继续的问题时 next_questions=[]，说明停止理由；研究过不自动表示所有问题已解决。
提交后用 finish_research 结束本轮；所有轮次成果最终交给主审综合，不另加整篇专家综合稿。
"""


class StudiedQuestion(BaseModel):
    question: str = Field(min_length=1, description="本轮研究的小问题，不附答案摘要")
    status: Literal["researched", "unresolved"] = Field(
        description="完成本轮研究，或材料不足仍待解决；不是正确性认证"
    )


class RoundPublication(PublishManuscript):
    studied_questions: list[StudiedQuestion] = Field(min_length=1)
    next_questions: list[str] = Field(max_length=3, description="0至3个下一轮小问题")
    stopping_reason: str = Field(description="本轮为何交接或停止；仅给主审，不传给下一轮")


class RelayRuntime(ObservedRuntime):
    def __init__(self, root, *args, **kwargs):
        super().__init__(root, *args, **kwargs)
        self.probe_root = Path(root)
        self.probe_plan = load(self.probe_root.parent / "plan.json")

    def _research_task(self, state, submission=None):
        message = super()._research_task(state, submission)
        if state.task.role != "researcher":
            return message
        view = json.loads(message["content"])
        view["global_research_topics"] = self.probe_plan["global_topics"]
        view["assigned_research_topic"] = self.probe_plan["assignment"]["responsibility"]
        if self.probe_plan["condition"] == "relay":
            view["instruction"] = view["instruction"].replace(EXPERT_OPINION, ROUND_DELIVERY)
            view["question_handoff"] = self.probe_plan["handoff"]
            view["previous_original_locations"] = self.probe_plan["original_locations"]
        message["content"] = json.dumps(view, ensure_ascii=False)
        return message

    def _tools(self, state, external, **kwargs):
        registry = super()._tools(state, external, **kwargs)
        if state.task.role != "researcher" or self.probe_plan["condition"] != "relay":
            return registry
        # This experimental relay replaces peer-answer exchange. Each round's
        # filesystem contains only originals and its own current work.
        registry = registry.select(
            [
                s["function"]["name"]
                for s in registry.schemas()
                if s["function"]["name"] not in {"request_inquiry", "reply_consultation"}
            ]
        )
        if "publish_manuscript" not in registry._tools:
            return registry
        ordinary = registry._tools["publish_manuscript"]

        async def publish(args):
            result = await ordinary.handler(args)
            packet = args.model_dump(exclude={"title", "content"})
            self.trace.event("question_handoff", session_id=state.session_id, packet=packet)
            write_json(self.probe_root / "question-handoff.json", packet)
            return result

        registry._tools["publish_manuscript"] = replace(
            ordinary,
            description="向主审保存本轮研究成果，同时交付已研究问题和0至3个后续问题。",
            arguments=RoundPublication,
            handler=publish,
        )
        return registry


def prepare(args):
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    parent = Path("runs/subtractive_research_sample2/baseline")
    plan = load(parent / "plan.json")
    plan["production_hashes"] = production_hashes()
    source = Path(plan["source_run"])
    fixture = load("runs/selective_transfer_sample2/fixture.json")
    topics = [a["note"]["title"] for a in fixture["artifacts"]]
    engineering = deepcopy(plan["assignment"])
    saved = source / "v5"
    manifest = load(next((saved / "chiefs").glob("*.json")))
    mito_id = manifest["topic_ids"][1]
    mito = load(saved / "inquiries" / f"{mito_id}.json")["assignment"]
    mito["responsibility"] = (
        "申请关于线粒体DNA类核形态、ROS、钙信号和mtDNA突变率的观测，"
        "相对已有研究能提供哪些新认识？各读出及天地比较支持怎样的解释，"
        "哪些条件影响这些认识的获得与解释？"
    )
    mito["impact"] = "评价线粒体部分的原创问题、可获得认识和方法解释力。"
    mito["research_ids"] = []
    mito_sources = [
        load(saved / "source-readings" / f"{rid}.json")["source_id"]
        for rid in load(saved / "topics" / f"{mito_id}.json")["reading_ids"]
    ]
    mito["source_ids"] = list(dict.fromkeys([*mito_sources, *plan["source_ids"]]))
    assignments = {"engineering": engineering, "mitochondria": mito}
    all_sources = list(dict.fromkeys([*plan["application_ids"], *mito["source_ids"]]))
    plan["original_source_hashes"] = hashes(
        [p for sid in all_sources for p in (source / "sources" / sid).rglob("*") if p.is_file()]
    )
    write_json(
        root / "design.json",
        {
            "production_hashes": plan["production_hashes"],
            "plan": plan,
            "assignments": assignments,
            "global_topics": topics,
            "round_delivery": ROUND_DELIVERY,
            "conditions": ["baseline", "relay"],
            "scheduled_repeats": {"engineering": 3, "mitochondria": 2},
            "maximum_rounds": 4,
            "total_main_turns": 64,
            "total_model_calls": 200,
            "total_embedding_calls": 200,
            "total_researcher_input_tokens": 4000000,
            "total_researcher_output_tokens": 393216,
            "scope": (
                "Fresh autonomous topic chains, original corpus with real retrieval/readers, "
                "then current chief assessment. No web discovery or full review."
            ),
        },
    )
    config = yaml.safe_load((parent / "probe-config.yaml").read_text("utf-8"))
    (root / "probe-config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), "utf-8")
    (root / "protocol.md").write_text(
        "# 问题接力实验方案\n\n"
        "当前流程与问题接力：工程专题各3次，线粒体专题各2次。均从空白开始；"
        "完整申请、固定原始来源、主审专题目录、技能、模型和thinking关闭保持匹配。"
        "工程沿用5来源；线粒体补入其历史真实研究的3来源，共8来源。"
        "来源池包含原文，不含历史专家解释；真实召回、重排和按需阅读员可用，无新网络发现。\n\n"
        "接力每轮使用新runtime、会话、专题和独立目录；只继承问题及处理状态、原文位置索引。"
        "没有先前稿件、咨询答复、观察或聊天。发布工具一次生成研究正文与问题，"
        "最多3个后续问题，允许0个自主停止。已研究记录仅追加，不由模型重写历史。"
        "首轮只从专题研究；后续可重新打开确有必要的问题，不用代码语义去重。"
        "每轮正文及停止理由仅存给主审，不传下轮；没有最后专家再综合调用。\n\n"
        "两组总预算均64个主专家控制轮、200次模型调用、200次embedding，"
        "研究者输入400万token、输出393216token；接力按剩余额度继续，最多4轮。"
        "预算或轮次上限结束与专家主动停止分开报告。材料读取及意见仍走当前TopicResearch.run。\n\n"
        "主审使用现有国标与评价接口；实验组同一专题内展示所有轮次成果及问题，"
        "保留轮次归属，代码合并实际原文。其他专题意见固定，不以整体分数当作正确率。"
        "绝对评价具体事实、原文含义、推断成立性、有效认识与引用；相对比较错误传递、"
        "重复与推进、停止、有效认识保留、主审采用和成本。未读或未提及不能算纠正。"
        "不注入标准答案、人工错稿或指向已知错误段落的固定问题；所有失败保留。\n",
        "utf-8",
    )


def locations(readings, accumulated):
    for item in readings.values():
        ref = item.get("reference", {})
        sid = ref.get("source_id")
        position = ref.get("passage_id") or ref.get("visual_id")
        if sid and position:
            accumulated.setdefault(sid, [])
            if position not in accumulated[sid]:
                accumulated[sid].append(position)


async def run_chain(root, condition, topic, repeat):
    design = load(root / "design.json")
    folder = root / condition / f"{topic}-{repeat}"
    folder.mkdir(parents=True, exist_ok=False)
    config = yaml.safe_load((root / "probe-config.yaml").read_text("utf-8"))
    ledger, original_locations, packets, outputs = [], {}, [], []
    remaining_turns = design["total_main_turns"]
    remaining_calls = design["total_model_calls"]
    remaining_embeddings = design["total_embedding_calls"]
    remaining_input = design["total_researcher_input_tokens"]
    remaining_output = design["total_researcher_output_tokens"]
    stop = "round_limit"
    summary = {
        "condition": condition,
        "topic": topic,
        "repeat": repeat,
        "status": "running",
        "budget_profile": {k: v for k, v in design.items() if k.startswith("total_")},
    }
    try:
        for round_no in range(1, 5 if condition == "relay" else 2):
            if remaining_turns < 3 or remaining_calls < 3 or remaining_embeddings < 1:
                stop = "shared_budget_limit"
                break
            allowance = config["allowances"]["researcher"]
            if (
                remaining_input < allowance["completion_input_tokens"]
                or remaining_output < allowance["completion_output_tokens"]
            ):
                stop = "shared_token_limit"
                break
            config["session"]["max_turns"] = remaining_turns
            config["budget"].update(
                max_model_calls=remaining_calls, max_embedding_calls=remaining_embeddings
            )
            allowance.update(max_input_tokens=remaining_input, max_output_tokens=remaining_output)
            parent = folder / f"round-{round_no}"
            parent.mkdir()
            plan = deepcopy(design["plan"])
            plan.update(
                assignment=design["assignments"][topic],
                source_ids=design["assignments"][topic]["source_ids"],
                global_topics=design["global_topics"],
                condition=condition,
                handoff={
                    "next_questions": packets[-1]["next_questions"],
                    "studied_questions": ledger,
                }
                if packets
                else {},
                original_locations=original_locations,
            )
            write_json(parent / "plan.json", plan)
            (parent / "probe-config.yaml").write_text(
                yaml.safe_dump(config, allow_unicode=True), "utf-8"
            )
            await run_one(parent, 1, runtime_class=RelayRuntime)
            run = parent / "run-1"
            render_trajectory(run)
            summarize_trajectory(run)
            result = load(run / "topic-result.json")
            verification = load(run / "verification.json")
            outputs.append({"round": round_no, "run": str(run), "status": result["status"]})
            calls = [load(p) for p in (run / "trace").glob("call-*-output.json")]
            remaining_turns -= result["turns_used"]
            remaining_calls -= len(calls)
            budget = load(run / "v5/budget.json")
            requests = budget["requests"]
            remaining_embeddings -= sum(r.get("kind") == "embedding" for r in requests)
            # Main and nested reader/auxiliary calls retain the current budget ownership.
            remaining_input -= verification["usage"]["input_tokens"]
            remaining_output -= verification["usage"]["output_tokens"]
            locations(result["readings"], original_locations)
            write_json(folder / "rounds.json", outputs)
            if condition == "baseline":
                stop = result["reason"] or result["status"]
                break
            packet_path = run / "question-handoff.json"
            if not packet_path.exists():
                stop = "no_question_handoff"
                break
            packet = load(packet_path)
            packets.append(packet)
            ledger.extend({"round": round_no, **q} for q in packet["studied_questions"])
            write_json(folder / "question-ledger.json", ledger)
            if result["status"] != "submitted":
                stop = result["reason"] or result["status"]
                break
            if not packet["next_questions"]:
                stop = "expert_stopped"
                break
        summary.update(status="complete", stop=stop)
    except BaseException as exc:
        summary.update(status="failed", error=repr(exc))
        raise
    finally:
        summary.update(
            rounds=outputs,
            handoffs=packets,
            remaining_main_turns=remaining_turns,
            remaining_model_calls=remaining_calls,
            remaining_researcher_input=remaining_input,
            remaining_researcher_output=remaining_output,
            production_unchanged=production_hashes() == design["production_hashes"],
        )
        write_json(folder / "chain.json", summary)


async def main(args):
    if args.prepare:
        prepare(args)
        return
    if not args.live:
        raise ValueError("Use --live for real services")
    gate = asyncio.Semaphore(2)

    async def job(spec):
        condition, topic, repeat = spec.split(":")
        async with gate:
            await run_chain(Path(args.output).resolve(), condition, topic, int(repeat))

    results = await asyncio.gather(*(job(s) for s in args.jobs), return_exceptions=True)
    errors = [
        f"{s}: {r!r}"
        for s, r in zip(args.jobs, results, strict=True)
        if isinstance(r, BaseException)
    ]
    if errors:
        raise RuntimeError("\n".join(errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", default="runs/question_relay_sample2")
    parser.add_argument("--jobs", nargs="+", default=[])
    asyncio.run(main(parser.parse_args()))
