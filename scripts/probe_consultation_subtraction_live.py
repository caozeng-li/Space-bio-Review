"""Exercise real directed consultation with/without automatic whole-peer manuscripts.

Peer opinions are consecutive outputs from this experiment, never fabricated or selected
for their errors. Both arms retain the same original-source handoff behavior.
"""

import argparse
import asyncio
import json
import shutil
from copy import deepcopy
from pathlib import Path

import httpx
from probe_expert_trajectory_live import (
    ObservedRuntime,
    hashes,
    load,
    production_hashes,
    render_trajectory,
)

from spacebio_review.evidence.research import load_evidence_config
from spacebio_review.evidence.store import SourceStore
from spacebio_review.models.embedding import DashScopeEmbedding, VectorIndex
from spacebio_review.models.scheduling import ScheduledModel
from spacebio_review.persistence import write_json
from spacebio_review.prose import prose_inputs
from spacebio_review.review.expertise import ExpertiseCatalogue
from spacebio_review.tools import ToolResult
from spacebio_review.v5.collaboration import RequestInquiry, ScientificCollaboration
from spacebio_review.v5.config import load_config
from spacebio_review.v5.records import ResearchNote
from spacebio_review.v5.sources import VisualReader
from spacebio_review.v5.topic import TopicResearch, TopicTask

RESPONSIBILITY = (
    "评价本申请的样品保存与返回条件怎样影响mtDNA突变读出，以及该读出能支持怎样的科学认识。"
    "先向团队中已有的空间任务专家作一次定向咨询：样品保存、返回与mtDNA终点检测衔接的"
    "已有支持和待明确条件是什么？随后结合申请与资料原文继续研究，形成自己的专业意见。"
)


class FixtureCollaboration(ScientificCollaboration):
    def request(self, inquiry_id, args):
        peer = getattr(self, "fixture_peer", None)
        if peer and args.target in {None, "space-operations"}:
            self.runtime.trace.event("fixture_dispatch", original_target=args.target, target=peer)
            args = args.model_copy(update={"target": peer})
        return super().request(inquiry_id, args)


class NarrowCollaboration(FixtureCollaboration):
    def without_manuscript(self, content, event):
        result = deepcopy(content)
        removed = result.pop("note", None)
        # Seeded peers have no reader-note records, but keep the policy explicit.
        reader_notes = result.pop("source_readings", None)
        self.runtime.trace.event(
            "automatic_manuscript_omitted",
            event=event,
            characters=len(json.dumps(removed, ensure_ascii=False)) if removed else 0,
            reader_records=len(reader_notes or []),
            contribution_ref=result.get("contribution_ref"),
        )
        return result

    def request(self, inquiry_id, args):
        result = super().request(inquiry_id, args)
        if "current_contribution" in result:
            result["current_contribution"] = self.without_manuscript(
                result["current_contribution"],
                "request_result",
            )
        return result

    def _deliver(self, recipient, kind, key, content, readings):
        if kind == "contribution" and recipient != self.chief_inquiry:
            content = self.without_manuscript(content, "publication_update")
        return super()._deliver(recipient, kind, key, content, readings)


def prepare(output, *, seeded=False):
    plan = load(output / "baseline" / "plan.json")
    groups = ["handoff_full", "handoff_narrow"] if seeded else ["consult_full", "consult_narrow"]
    for group in groups:
        folder = output / group
        folder.mkdir(exist_ok=False)
        shutil.copyfile(output / "baseline" / "probe-config.yaml", folder / "probe-config.yaml")
        write_json(
            folder / "plan.json",
            {
                **plan,
                "condition": group,
                "recipient_responsibility": RESPONSIBILITY,
                "seeded_request": seeded,
            },
        )
    with (output / "protocol.md").open("a", encoding="utf-8") as stream:
        stream.write(
            "\n## 定向咨询对照\n\n"
            "以本轮baseline连续3份真实专题稿分别作为空间任务专家的已提交状态，不按正误挑稿。"
            "每份各建full/narrow两组；恢复其稿件和累计原文，咨询从这一已提交状态开始，"
            "不重放其此前探索聊天。此恢复条件两组一致。另一个线粒体专题专家从空白意见开始，"
            "收到相同完整申请、5来源池和中性职责；任务明确要求就保存/返回/mtDNA检测先咨询一次，"
            "具体调用、后续阅读、追问和停止由模型决定。\n\n"
            "两组均调用真实request_inquiry、被咨询者研究与reply_consultation、接收答复和后续成稿。"
            "narrow仅从请求返回及后续自动稿件更新中去掉整篇note和阅读说明，保留同一原文、身份与元数据；"
            "主动read_references读整稿仍允许。专门答复没有删减或改写。"
            "这是三个共同起点的两组真实继续研究，不是完全相同问题/答复的配对输出；模型可自主改变行为。"
            "该对照保留原文以隔离自动整稿传递，尚未测试减少原文交付量。\n",
        )


async def run_case(output, group, repeat):
    folder = output / group
    plan = load(folder / "plan.json")
    seed = output / "baseline" / f"run-{repeat}"
    previous = load(seed / "topic-result.json")
    run = folder / f"run-{repeat}"
    run.mkdir(exist_ok=False)
    for sid in [*plan["application_ids"], *plan["source_ids"]]:
        shutil.copytree(seed / "sources" / sid, run / "sources" / sid)
    runtime = ObservedRuntime.from_config(folder / "probe-config.yaml", run)
    config = load_config(folder / "probe-config.yaml")
    embedder = topics = None
    summary = {
        "status": "running",
        "group": group,
        "repeat": repeat,
        "peer_seed": str(seed),
        "full_review_runs": 0,
    }

    def retained_only(request):
        runtime.trace.event("external_acquisition_blocked", url=str(request.url))
        return httpx.Response(404, text="Retained sources only")

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(retained_only)) as client:
            store = SourceStore(run / "sources", client, browser_fallback=False)
            store.restore()
            auxiliary = {
                kind: ScheduledModel(runtime.models[alias], runtime.scheduler, alias)
                for kind, alias in config.auxiliary.items()
            }
            evidence = load_evidence_config(config.evidence_config)
            index = None
            if config.embedding:
                embedder = DashScopeEmbedding(
                    config.embedding, runtime.scheduler, env_file=".env", budget=runtime.budget
                )
                index = VectorIndex(embedder, cache_dir=run / "embedding-cache")
            topics = TopicResearch(
                runtime,
                store,
                None,
                application_ids=plan["application_ids"],
                rerank_model=auxiliary.get("rerank") if evidence.model_rerank else None,
                index=index,
                visual_reader=VisualReader(auxiliary["visual"], store),
                catalogue=ExpertiseCatalogue(config.expertise_config, runtime.skills),
                evidence_config=evidence,
            )
            if group.endswith("narrow"):
                topics.collaboration = NarrowCollaboration(topics)
            elif plan.get("seeded_request"):
                topics.collaboration = FixtureCollaboration(topics)
            ordinary_tools = topics.tools

            def retained_tools(topic_id):
                registry = ordinary_tools(topic_id)
                return registry.select(
                    [
                        s["function"]["name"]
                        for s in registry.schemas()
                        if s["function"]["name"] not in {"search_sources", "related_papers"}
                    ]
                )

            topics.tools = retained_tools
            peer = topics.create(TopicTask.model_validate(plan["assignment"]))
            if plan.get("seeded_request"):
                topics.collaboration.fixture_peer = peer
            state = topics.state(peer)
            topics.access(peer).receive(previous["readings"])
            runtime._capture(state, previous["readings"])
            runtime.store.save_note(
                state, ResearchNote.model_validate(previous["note"]), absorbed_through=0
            )
            state.status, state.research_finished = "submitted", True
            runtime.store.save(state)
            assignment = {
                **plan["assignment"],
                "responsibility": RESPONSIBILITY,
                "impact": "评价返回后mtDNA分析对项目科学目标的支持及其实际条件。",
                "expertise": ["mitochondrial-metabolism"],
                "skills": ["expert-mitochondrial-metabolism", "measurement-analysis"],
            }
            recipient = topics.create(TopicTask.model_validate(assignment))
            summary.update(
                peer=peer, topic_id=recipient, session_id=topics.state(recipient).session_id
            )
            write_json(run / "initial-peer.json", topics.result(peer))
            write_json(run / "initial-state.json", topics.state(recipient))
            write_json(run / "verification.json", summary)
            if plan.get("seeded_request"):
                args = RequestInquiry(
                    target=peer,
                    question="对本申请的样品保存、返回与mtDNA终点检测之间的衔接，"
                    "现有平台和研究先例提供什么支持，哪些条件仍需要明确？",
                    scientific_use="理解实施条件怎样支持或限制返回后的mtDNA突变读出。",
                )
                content = topics.collaboration.request(recipient, args)
                state = topics.state(recipient)
                originals = topics.access(recipient).references.snapshot_choices(selected=False)
                runtime._capture(state, originals)
                runtime._observe(
                    state,
                    {
                        "kind": "tool",
                        "name": "request_inquiry",
                        "arguments": args.model_dump(mode="json"),
                        "output": {
                            "result": ToolResult(ok=True, content=content).model_dump(mode="json"),
                            "readings": originals,
                        },
                    },
                )
                runtime.store.save(state)
                write_json(
                    run / "seeded-request.json",
                    {
                        "arguments": args.model_dump(mode="json"),
                        "returned": content,
                        "originals": originals,
                    },
                )
            result = await topics.run(recipient)
            # A locally completed recipient does not close the real review team.
            # Let already dispatched authors finish before this bounded harness
            # shuts down, and deliver late answers in the same active conversation.
            wave = 0
            while True:
                pending = [a.task for a in topics.collaboration.actors.values() if a.task]
                if pending:
                    write_json(run / f"before-pending-answer-{wave}.json", result)
                    await asyncio.gather(*pending)
                answered = any(
                    r.kind == "consultation" and r.sender == recipient and r.status == "answered"
                    for r in topics.collaboration.requests.values()
                )
                deliveries = topics.collaboration._actor(recipient).deliveries
                if not answered or not deliveries or result.status in {"failed", "partial"}:
                    break
                wave += 1
                runtime.trace.event("late_answer_continuation", wave=wave)
                result = await topics.run(
                    recipient,
                    followup="定向咨询答复已到。请结合答复与原文继续研究，"
                    "据实际认识决定是否更新专业意见，然后结束本轮研究。",
                )
            summary["late_answer_continuations"] = wave
            write_json(run / "topic-result.json", result)
            write_json(run / "peer-result.json", topics.result(peer))
            requests = [r.view(full=True) for r in topics.collaboration.requests.values()]
            write_json(run / "consultations.json", requests)
            summary.update(
                status=result.status,
                reason=result.reason,
                turns_used=result.turns_used,
                peer_turns_used=topics.state(peer).turns_used,
                consultations=sum(r["kind"] == "consultation" for r in requests),
                answers=sum(
                    r["kind"] == "consultation" and r["status"] == "answered" for r in requests
                ),
            )
            if result.note:
                note = prose_inputs(result.note)
                (run / "expert.md").write_text(
                    "# " + note["title"] + "\n\n" + note["body"], "utf-8"
                )
    except BaseException as exc:
        summary.update(status="failed", error=repr(exc))
        raise
    finally:
        summary.update(
            usage=runtime.budget.usage(),
            captured_model_responses=len(runtime.trace.calls),
            production_unchanged=production_hashes() == plan["production_hashes"],
            original_sources_unchanged=hashes([Path(p) for p in plan["original_source_hashes"]])
            == plan["original_source_hashes"],
        )
        write_json(run / "verification.json", summary)
        write_json(run / "scheduler.json", runtime.scheduler.records)
        if topics:
            await topics.close()
        if embedder:
            await embedder.close()
        await runtime.close()
        render_trajectory(run)
        print(json.dumps(summary, ensure_ascii=False), flush=True)


async def main(args):
    output = Path(args.output).resolve()
    if args.prepare:
        prepare(output, seeded=args.seed_request)
        return
    if not args.live:
        raise ValueError("Use --live for real services")
    # A case contains two scientific authors. Sequential cases avoid multiplying
    # independently paced provider clients across simultaneous teams.
    for spec in args.jobs:
        group, repeat = spec.split(":")
        await run_case(output, group, int(repeat))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--seed-request", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", default="runs/subtractive_research_sample2")
    parser.add_argument(
        "--jobs",
        nargs="+",
        default=[
            "consult_full:1",
            "consult_narrow:1",
            "consult_full:2",
            "consult_narrow:2",
            "consult_full:3",
            "consult_narrow:3",
        ],
    )
    asyncio.run(main(parser.parse_args()))
