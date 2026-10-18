"""Two authors and one directed consultation; dry by default, at most ten live calls.

The application and starting manuscripts are explicitly synthetic fixtures.
Code registers one fixed scientific question on B's behalf. Live models supply
A's short answer and B's integrated manuscript; no chief, retrieval, or grader runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

from spacebio_review.evidence.store import SourceStore
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument
from spacebio_review.models.provider import ModelError, model_class
from spacebio_review.persistence import write_json
from spacebio_review.tools import ToolRegistry
from spacebio_review.v5.authoring import research_note
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance
from spacebio_review.v5.collaboration import RequestInquiry
from spacebio_review.v5.config import load_config
from spacebio_review.v5.records import SessionSettings
from spacebio_review.v5.session import ResearchRuntime
from spacebio_review.v5.topic import ApplicationRef, TopicResearch, TopicTask

MAX_CALLS = 10
MAX_PER_CALL = 2048
MAX_TOTAL_OUTPUT = 10240
PASSAGES = (
    ("p1", "合成申请：比较空间与地面条件下的小鼠小肠类器官。在轨第3、6、9天对活细胞"
     "进行类核荧光成像，记录形态、数量及时间变化；申请希望研究空间条件与类核状态的关系。"),
    ("p2", "合成申请：另一批样本在预定时间点保存并返回地面进行线粒体DNA测序。"
     "成像与测序来自不同样本，不承诺追踪同一个细胞；未提供初步实验结果。"),
)
QUESTION = (
    "在轨活细胞成像的时间信息怎样与不同返样的线粒体DNA测序形成互补解释？"
    "请用已有稿说明能获得的知识，以及哪些对应关系仍需实验设计支持。"
)
SCIENTIFIC_USE = "将动态表型与返样分子读出连接成具体科学问题，解释项目的知识贡献。"


class RecordedModel:
    """Record only public model messages and usage; credentials stay in the provider."""

    def __init__(self, provider, root, author):
        self.provider, self.config = provider, provider.config
        self.root, self.author, self.records = root, author, []

    async def complete(self, messages, tools=None, *, response_schema=None):
        record = {"author": self.author, "messages": deepcopy(messages), "tools": tools}
        self.records.append(record)
        path = self.root / "requests" / f"{self.author}-{len(self.records):02d}.json"
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
            key: reply.message[key] for key in ("role", "content", "tool_calls")
            if key in reply.message
        }
        record["usage"] = {
            "prompt_tokens": reply.prompt_tokens, "completion_tokens": reply.completion_tokens,
            "reasoning_tokens": reply.reasoning_tokens, "finish_reason": reply.finish_reason,
        }
        write_json(path, record)
        return reply

    async def close(self):
        await self.provider.close()


class ProbeTopics(TopicResearch):
    def tools(self, topic_id):
        # The sole consultation is registered below. No retrieval or additional dispatch exists.
        return ToolRegistry()


def build_team(root, config, args):
    budget = Budget(root, BudgetLimits(
        max_model_calls=MAX_CALLS, max_embedding_calls=0, max_input_tokens=100000,
        max_output_tokens=args.total_output_tokens,
        max_seconds=MAX_CALLS * config.timeout_seconds + 30,
    ))
    models = {
        name: RecordedModel(
            model_class(config)(config, env_file=args.env_file, budget=budget), root, name
        ) if args.live else SimpleNamespace(config=config, records=[])
        for name in ("author-a", "author-b")
    }
    runtime = ResearchRuntime(
        root, models, {"researcher": "author-a"}, budget=budget,
        settings=SessionSettings(max_turns=7, context_tokens=24000, completion_turns=1),
        allowances={"researcher": SessionAllowance(
            max_input_tokens=100000, max_output_tokens=args.total_output_tokens,
            completion_input_tokens=12000, completion_output_tokens=config.max_tokens,
            completion_calls=1, completion_seconds=config.timeout_seconds,
        )},
    )
    runtime.scheduler.request_budget = budget
    if args.live:
        runtime._owned_models = list(models.values())
    sources = SourceStore(root / "sources", client=None, env_file=None, browser_fallback=False)
    source_id = sources.register(SourceCandidate(title="局部协作合成申请", kind="application"))
    sources._save(SourceDocument(
        source_id=source_id, candidate=sources.candidate(source_id), availability="full_text",
        passages=[Passage(passage_id=pid, locator=pid, text=text) for pid, text in PASSAGES],
    ))
    topics = ProbeTopics(
        runtime, sources, None, application_ids=[source_id], parallel_topics=1,
        external_research_enabled=False,
    )
    return topics, models, source_id


def seed_author(topics, name, source_id, focus, text, instruction):
    runtime = topics.runtime
    runtime.routes["researcher"] = name
    inquiry_id = topics.create(TopicTask(
        objective=focus, impact=SCIENTIFIC_USE,
        application_refs=[
            ApplicationRef(source_id=source_id, passage_id=pid) for pid, _ in PASSAGES
        ],
    ))
    state = topics.state(inquiry_id)
    runtime.store.set_focus(inquiry_id, focus)
    aliases = {
        item["reference"]["passage_id"]: alias
        for alias, handle in state.aliases.items() for item in [state.evidence[handle]]
    }
    raw_ref = runtime.store.save_author_text(
        state, text.format(**aliases), "update_research", absorbed_through=state.observation_index,
        phase="contribution", title=focus, focus=focus,
        reason="probe提供的合成起始稿，并非模型成果",
    )
    ref = runtime.store.save_note(
        state, research_note(runtime.store.author_text(raw_ref), title=focus),
        absorbed_through=state.observation_index, author_text_ref=raw_ref,
    )
    runtime._submit_note(state)
    runtime.budget.finish(runtime.budget_account(state))
    # This is a one-time arrangement, consumed by the next manuscript like real followups.
    runtime._observe(state, {"kind": "followup", "content": instruction})
    topics.collaboration._actor(inquiry_id).completed = True
    topics.collaboration.publish(inquiry_id, ref)
    return inquiry_id, ref


def export_result(root, topics, models, authors, initial, *, mode, request, elapsed, error):
    runtime, team = topics.runtime, topics.collaboration
    current = {}
    for label, inquiry_id in authors.items():
        state = topics.state(inquiry_id)
        ref = topics.inquiry(inquiry_id).current_contribution_ref
        current[label] = {
            "inquiry_id": inquiry_id, "session_id": state.session_id,
            "initial_contribution_ref": initial[label], "current_contribution_ref": ref,
            "status": state.status, "reason": state.reason,
            "usage": runtime.budget.usage(runtime.budget_account(state)),
        }
        if ref:
            raw = runtime.store.author_text(runtime.store.note(ref)["author_text_ref"])
            (root / f"{label}-current.md").write_text(raw["content"], encoding="utf-8")
    consultation = team.requests[request].view(full=True) if request else {
        "status": "planned", "from_inquiry": authors.get("B"), "target_inquiry": authors.get("A"),
        "question": QUESTION, "scientific_use": SCIENTIFIC_USE,
    }
    result = {
        "mode": mode, "request": consultation, "authors": current,
        "usage": runtime.budget.usage(), "elapsed_seconds": elapsed, "error": error,
        "question_origin": "probe代码代B登记的固定问题；非模型生成提问",
        "starting_manuscripts": "两份合成fixture；只有live产生的后续答复/修订才是模型输出",
        "cost_basis": "调用与token用量来自本次账本，不估算货币价格",
    }
    write_json(root / "result.json", result)
    lines = ["# 局部协作公开对话", "", "以下为提问、答复和模型公开输出摘录，不含自动语义评分。",
             "", "## B → A（probe登记的固定问题）", "", QUESTION, "", SCIENTIFIC_USE]
    if consultation.get("answer"):
        lines += ["", "## A的绑定答复", "", json.dumps(
            consultation["answer"], ensure_ascii=False, indent=2
        )]
    for model in models.values():
        for index, record in enumerate(model.records, 1):
            lines += ["", f"## {record['author']} / 调用 {index}", ""]
            reply = record.get("reply", {})
            if reply.get("content"):
                lines.append(reply["content"])
            for call in reply.get("tool_calls", []):
                function = call["function"]
                lines.append(function["name"] + ": " + function["arguments"])
    (root / "dialogue.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


async def main(args):
    root = Path(args.output).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("Use a new or empty output directory")
    routes = load_config(args.runtime_config)
    alias = routes.roles["researcher"]
    config = routes.models[alias].model_copy(update={"retries": 0, "max_tokens": args.max_tokens})
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "probe-plan.json", {
        "mode": "live" if args.live else "dry", "model_alias": alias, "model": config.model,
        "max_calls": MAX_CALLS, "per_call_max_output_tokens": config.max_tokens,
        "total_output_token_budget": args.total_output_tokens, "retries": 0,
        "question_origin": "固定问题由probe代B登记，不测试模型自主选择咨询对象",
        "materials": dict(PASSAGES), "question": QUESTION, "scientific_use": SCIENTIFIC_USE,
        "human_reading": ["A是否用已有稿解释具体科学用途", "B是否连接动态成像与返样测序的知识贡献"],
    })
    topics, models, source_id = build_team(root, config, args)
    authors, initial, request, error = {}, {}, None, None
    started = perf_counter()
    try:
        authors["A"], initial["A"] = seed_author(
            topics, "author-a", source_id, "在轨时间序列能解释什么类核动态",
            "在轨活成像可以比较空间与地面条件下的类核形态、数量及时间变化。[@{p1}]\n\n"
            "返样测序提供另一组样本的分子终点；两类读出可按条件和时间点互补解释，"
            "但不能直接视为同一细胞的连续分子记录。[@{p2}]",
            "本局部任务已有完整current稿。只需回答一个咨询：在finish_research的reason中用"
            "约150—250字解释当前稿如何回答，并写明其contribution_ref。可纠正提问前提，"
            "不需要再写整篇或发起新研究；没有检索工具。",
        )
        authors["B"], initial["B"] = seed_author(
            topics, "author-b", source_id, "返样测序怎样连接动态表型认识",
            "返样线粒体DNA测序可比较不同条件、时间点的分子状态。[@{p2}]\n\n"
            "申请还提供在轨活细胞成像，二者如何形成相互补充的科学解释需要进一步连接。[@{p1}]",
            "唯一咨询已由probe代你登记，不再创建咨询。收到A的回答后，基于短申请与已有稿"
            "形成约300—500字的完整科学解释，说明动态读出和返样测序如何互补以及可获得"
            "什么知识，再finish_research。答案未到且无独立工作时按request_id显式等待。",
        )
        if args.live:
            request = topics.collaboration.request(authors["B"], RequestInquiry(
                question=QUESTION, scientific_use=SCIENTIFIC_USE,
                input_refs=[initial["B"]], target=authors["A"],
            ))["request_id"]
            await topics.run(
                authors["B"], followup=(
                    f"已登记咨询{request}。取得A解释后连接动态与分子读出并更新当前稿。"
                ),
            )
    except Exception as exc:
        error = {"type": type(exc).__name__}
        if isinstance(exc, ModelError):
            error["reason"] = str(exc)
    finally:
        await topics.close()
        result = export_result(
            root, topics, models, authors, initial, mode="live" if args.live else "dry",
            request=request, elapsed=perf_counter() - started, error=error,
        )
        await topics.runtime.close()
    print(json.dumps({"output": str(root), "mode": result["mode"], "usage": result["usage"],
                      "request_id": request, "error": error}, ensure_ascii=False, indent=2))


def limited_tokens(value):
    number = int(value)
    if not 1 <= number <= MAX_PER_CALL:
        raise argparse.ArgumentTypeError(f"Choose 1..{MAX_PER_CALL}")
    return number


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--runtime-config", default="config/runtime.yaml")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", default=".env", help="Read only with --live")
    parser.add_argument("--max-tokens", type=limited_tokens, default=1024)
    parser.add_argument("--total-output-tokens", type=int, default=8192)
    args = parser.parse_args()
    if not args.max_tokens <= args.total_output_tokens <= MAX_TOTAL_OUTPUT:
        parser.error(f"--total-output-tokens must be between --max-tokens and {MAX_TOTAL_OUTPUT}")
    asyncio.run(main(args))
