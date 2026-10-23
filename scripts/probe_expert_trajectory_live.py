"""Observe current autonomous expert research on retained mytest49 sources.

No historical opinions, prescribed reading turns, chief evaluation or report generation.
Request recording is observational; production code and model messages are unchanged.
"""

import argparse
import asyncio
import hashlib
import json
import shutil
import time
from collections import Counter
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

from spacebio_review.evidence.research import load_evidence_config
from spacebio_review.evidence.store import SourceStore
from spacebio_review.models.embedding import DashScopeEmbedding, VectorIndex
from spacebio_review.models.scheduling import ScheduledModel
from spacebio_review.persistence import write_json
from spacebio_review.prose import prose_inputs
from spacebio_review.review.expertise import ExpertiseCatalogue
from spacebio_review.v5.config import load_config
from spacebio_review.v5.session import ResearchRuntime
from spacebio_review.v5.sources import VisualReader
from spacebio_review.v5.topic import TopicResearch, TopicTask

CALL_CONTEXT = ContextVar("trajectory_call", default={})
RESPONSIBILITY = (
    "申请中的地面准备、在轨培养与成像、样品返回后分析怎样衔接？"
    "现有平台和研究先例对这些安排及其预期科学观测提供哪些支持，"
    "哪些实施条件仍需要明确？"
)


def load(path):
    return json.loads(Path(path).read_text("utf-8"))


def hashes(paths):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def production_hashes():
    return hashes([
        *Path("src").rglob("*.py"), *Path("config").rglob("*.yaml"),
        *Path("skills").rglob("*.md"),
    ])


def render_trajectory(run):
    """Readable public transcript, without grading or feeding anything back to the model."""
    trace = run / "trace"
    events = [json.loads(line) for line in (trace / "events.jsonl").read_text("utf-8").split("\n")
              if line]
    lines = ["# 专家研究公开轨迹", "",
             "实际请求保存在trace/*-input.json；本文件只展开公开输出与工具执行摘要。", ""]
    for event in events:
        if event["kind"] == "model_end":
            call_id = event["call_id"]
            result = load(trace / f"{call_id}-output.json")
            lines += [f"## {call_id} · {result['alias']} · turn {result.get('turn', '辅助')}", "",
                      f"输入 {result['input_tokens']} / 输出 {result['output_tokens']} token；"
                      f"结束状态 {result['finish_reason']}。", "",
                      result["message"].get("content") or "（没有伴随正文）", ""]
            for call in result["message"].get("tool_calls") or []:
                function = call["function"]
                lines += [f"工具：`{function['name']}`", "", "```json",
                          function["arguments"], "```", ""]
        elif event["kind"] == "observation":
            observation = event["observation"]
            if observation.get("kind") != "tool":
                continue
            output = observation.get("output", {})
            result = output.get("result", {})
            content = result.get("content")
            summary = {k: result.get(k) for k in ["ok", "error", "reading_scope"]}
            summary["captured_positions"] = len(output.get("readings", {}))
            if isinstance(content, dict):
                summary.update({k: content[k] for k in [
                    "source_id", "returned_ids", "missing_ids", "pending_count", "complete",
                ] if k in content})
            lines += [f"工具返回 `{observation['name']}`：", "",
                      "```json", json.dumps(summary, ensure_ascii=False), "```", ""]
        elif event["kind"] in {"model_error", "external_acquisition_blocked"}:
            lines += [json.dumps(event, ensure_ascii=False), ""]
        elif event["kind"] == "focused_reading":
            lines += [f"## 首次阅读比较 · {event['group']} · turn {event['turn']}", "",
                      event["message"]["content"], ""]
    (run / "trajectory.md").write_text("\n".join(lines), "utf-8")


def summarize_trajectory(run):
    """Offline execution/exposure counts only; scientific claims require manual review."""
    trace = run / "trace"
    calls = [load(path) for path in sorted(trace.glob("call-*-output.json"))]
    scientific = [row for row in calls if row["alias"] == "scientific"]
    events = [json.loads(line) for line in (trace / "events.jsonl").read_text("utf-8").split("\n")
              if line]
    tool_events = [event["observation"] for event in events
                   if event["kind"] == "observation"
                   and event["observation"].get("kind") == "tool"]
    publications = [row for row in scientific if any(
        call["function"]["name"] == "publish_manuscript"
        for call in row["message"].get("tool_calls") or []
    )]
    source_positions = {}
    for observation in tool_events:
        for reading in observation.get("output", {}).get("readings", {}).values():
            ref = reading["reference"]
            source_positions.setdefault(ref["source_id"], set()).add(ref["passage_id"])
    failures = []
    for observation in tool_events:
        result = observation.get("output", {}).get("result", {})
        if result.get("ok") is False:
            failures.append({"tool": observation["name"], "result": result})
    snapshots = [load(path) for path in sorted(trace.glob("session*-turn-*.json"))]
    scheduler = load(run / "scheduler.json") if (run / "scheduler.json").exists() else []
    exposure = []
    targets = [
        ("src-0b4300b4279e", "p19"),
        ("src-0b4300b4279e", "p30"),
        ("src-b6d47e32405f", "m-e5e1236c279d-p148"),
        ("src-b6d47e32405f", "m-e5e1236c279d-p194"),
        ("src-83a99b4140a1", "m-35f49c203755-p17"),
        ("src-83a99b4140a1", "m-35f49c203755-p28"),
        ("src-83a99b4140a1", "m-35f49c203755-p29"),
        ("src-83a99b4140a1", "m-35f49c203755-p34"),
    ]
    inputs = {row["call_id"]: load(trace / f"{row['call_id']}-input.json")
              for row in scientific}
    for source_id, passage_id in targets:
        source = load(run / "sources" / source_id / "source.json")
        passage = next(p for p in source["passages"] if p["passage_id"] == passage_id)
        # Exact text prefix rather than a source ID in a catalogue: actual content exposure.
        # A table title can also appear in its enclosing native paragraph. That counts
        # as exposure to the title; it does not imply the entire table was delivered.
        needle = passage["text"].splitlines()[0][:100]
        present = [row for row in scientific if any(
            needle in message.get("content", "")
            for message in inputs[row["call_id"]]["messages"]
            if isinstance(message.get("content"), str)
        )]
        present_ids = {row["call_id"] for row in present}
        exposure.append({
            "source_id": source_id, "passage_id": passage_id, "text_prefix": needle,
            "first_visible_input_turn": present[0]["turn"] if present else None,
            "present_at_publication": {
                row["call_id"]: row["call_id"] in present_ids for row in publications
            },
        })
    write_json(run / "trace-summary.json", {
        "verification": load(run / "verification.json"),
        "model_calls_by_alias": dict(Counter(row["alias"] for row in calls)),
        "tools": dict(Counter(row["name"] for row in tool_events)),
        "tool_failures": failures,
        "publications": [{k: row[k] for k in ["call_id", "turn", "input_tokens"]}
                         for row in publications],
        "maximum_scientific_input_tokens": max(row["input_tokens"] for row in scientific),
        "maximum_context_compactions": max(row["context_compactions"] for row in snapshots),
        "explicit_tool_read_positions": {sid: len(ids) for sid, ids in source_positions.items()},
        "exposure": exposure,
        "exposure_method": (
            "Exact first-line text prefix in actual input; not proof of full-unit delivery."
        ),
        "external_acquisition_attempts": sum(
            event["kind"] == "external_acquisition_blocked" for event in events),
        "model_errors": [event for event in events if event["kind"] == "model_error"],
        "scheduler_retry_count": sum(row.get("retries", 0) for row in scheduler),
    })


class Trace:
    def __init__(self, root):
        self.root = Path(root) / "trace"
        self.root.mkdir(parents=True, exist_ok=True)
        self.counter = 0
        self.calls = []

    def event(self, kind, **data):
        event = {"time_utc": datetime.now(timezone.utc).isoformat(), "kind": kind, **data}
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")


class RecordingModel:
    def __init__(self, delegate, trace, alias):
        self.delegate, self.trace, self.alias = delegate, trace, alias

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def complete(self, messages, tools=None, **options):
        self.trace.counter += 1
        call_id = f"call-{self.trace.counter:03d}"
        metadata = {**CALL_CONTEXT.get(), "alias": self.alias, "model": self.config.model}
        write_json(self.trace.root / f"{call_id}-input.json", {
            **metadata,
            "messages": [{k: v for k, v in message.items() if k != "reasoning_content"}
                         for message in messages],
            "reasoning_protocol": [
                {"message_index": index, "characters": len(message["reasoning_content"] or "")}
                for index, message in enumerate(messages) if "reasoning_content" in message
            ],
            "tools": tools, "options": options,
        })
        self.trace.event("model_start", call_id=call_id, **metadata)
        print(json.dumps({"start": call_id, **metadata}), flush=True)
        started = time.monotonic()
        try:
            reply = await self.delegate.complete(messages, tools, **options)
        except BaseException as exc:
            self.trace.event("model_error", call_id=call_id, error=repr(exc))
            raise
        row = {
            "call_id": call_id, **metadata, "finish_reason": reply.finish_reason,
            "input_tokens": reply.prompt_tokens, "output_tokens": reply.completion_tokens,
            "reasoning_tokens": reply.reasoning_tokens,
            "reasoning_characters": len(reply.message.get("reasoning_content") or ""),
            "response_seconds": time.monotonic() - started,
            "effective_request": reply.effective_request,
        }
        write_json(self.trace.root / f"{call_id}-output.json", {
            **row,
            "message": {k: v for k, v in reply.message.items() if k != "reasoning_content"},
        })
        self.trace.calls.append(row)
        write_json(self.trace.root / "calls.json", self.trace.calls)
        self.trace.event("model_end", **row)
        print(json.dumps(row), flush=True)
        return reply


class ObservedRuntime(ResearchRuntime):
    def __init__(self, root, *args, **kwargs):
        super().__init__(root, *args, **kwargs)
        self.trace = Trace(root)
        self.models = {
            alias: RecordingModel(model, self.trace, alias)
            for alias, model in self.models.items()
        }

    async def _call_model(self, state, *args, **kwargs):
        metadata = {
            "session_id": state.session_id, "role": state.task.role,
            "turn": state.turns_used + 1, "stage": kwargs.get("stage"),
        }
        token = CALL_CONTEXT.set(metadata)
        snapshot = self.trace.root / f"{state.session_id}-turn-{state.turns_used + 1:02d}.json"
        write_json(snapshot, state)
        try:
            return await super()._call_model(state, *args, **kwargs)
        finally:
            CALL_CONTEXT.reset(token)

    def _observe(self, state, content):
        result = super()._observe(state, content)
        # Preserve public observations before normal manuscript publication absorbs them.
        self.trace.event(
            "observation", session_id=state.session_id, role=state.task.role,
            observation_index=state.observation_index, observation=content,
        )
        return result


def prepare(args):
    output, source = Path(args.output).resolve(), Path(args.run).resolve()
    output.mkdir(parents=True, exist_ok=False)
    saved = source / "v5"
    manifest = load(next((saved / "chiefs").glob("*.json")))
    topic_id = manifest["topic_ids"][2]
    topic = load(saved / "topics" / f"{topic_id}.json")
    inquiry = load(saved / "inquiries" / f"{topic_id}.json")
    sources = list(dict.fromkeys(
        load(saved / "source-readings" / f"{rid}.json")["source_id"]
        for rid in topic["reading_ids"]
    ))
    assignment = {
        **inquiry["assignment"], "responsibility": RESPONSIBILITY,
        "impact": "形成对实验方案工程可实现性、已有支持和实质限制的专业判断。",
        "source_ids": sources, "research_ids": [],
    }
    config = load_config(args.config).model_dump(mode="json")
    # Keep all per-expert production budgets, model routes and research turn settings.
    config["budget"] = {"max_model_calls": 100, "max_embedding_calls": 100,
                        "max_seconds": 7200}
    (output / "probe-config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True), "utf-8",
    )
    source_files = [
        p for sid in [*topic["application_ids"], *sources]
        for p in (source / "sources" / sid).rglob("*") if p.is_file()
    ]
    write_json(output / "plan.json", {
        "source_run": str(source), "historical_topic": topic_id,
        "application_ids": topic["application_ids"], "source_ids": sources,
        "historical_assignment": inquiry["assignment"], "assignment": assignment,
        "production_hashes": production_hashes(), "original_source_hashes": hashes(source_files),
        "scope": [
            "Current TopicResearch.run from an empty opinion state; retained sources only.",
            "Application and source directories initially; originals selected by the expert.",
            "Production reading/retrieval/optional consultation/publication/stop flow.",
            "No historical opinions, no scripted questions or forced rewrite turns.",
            "No new web discovery, no chief, no formal scoring or report generation.",
            "Same open engineering responsibility in independent runs; current skills.",
        ],
        "manual_targets": [
            "Device identity, scope and implementation stage; specific CSS status may be unknown.",
            "Application p19 explicitly includes full-length mtDNA PCR; it is not invented.",
            "Missing preservation description versus an assertion of no preservation.",
            "KCPP Table1 tubules available for RNAseq versus extraction yield or causal proof.",
            "Ground preparation versus in-orbit operations; useful scientific/engineering support.",
            "Source ambiguities and parser errors are distinguished from model interpretation.",
        ],
    })
    print(json.dumps({"prepared": str(output), "sources": sources}), flush=True)


async def run_one(output, repeat, runtime_class=ObservedRuntime):
    plan = load(output / "plan.json")
    run = output / f"run-{repeat}"
    run.mkdir(exist_ok=False)
    for sid in [*plan["application_ids"], *plan["source_ids"]]:
        shutil.copytree(Path(plan["source_run"]) / "sources" / sid, run / "sources" / sid)
    config = load_config(output / "probe-config.yaml")
    runtime = runtime_class.from_config(output / "probe-config.yaml", run)
    # from_config retains ownership of the original provider clients.
    embedder = None
    topics = None
    summary = {"repeat": repeat, "status": "running", "full_review_runs": 0}

    def retained_only(request):
        runtime.trace.event("external_acquisition_blocked", url=str(request.url))
        return httpx.Response(404, text="Retained-material experiment: no new acquisition.")

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(retained_only)) as client:
            store = SourceStore(run / "sources", client, browser_fallback=False)
            store.restore()
            auxiliary = {
                kind: ScheduledModel(runtime.models[alias], runtime.scheduler, alias)
                for kind, alias in config.auxiliary.items()
            }
            evidence_config = load_evidence_config(config.evidence_config)
            index = None
            if config.embedding:
                embedder = DashScopeEmbedding(config.embedding, runtime.scheduler,
                                              env_file=".env", budget=runtime.budget)
                index = VectorIndex(embedder, cache_dir=run / "embedding-cache")
            topics = TopicResearch(
                runtime, store, None, application_ids=plan["application_ids"],
                rerank_model=auxiliary.get("rerank") if evidence_config.model_rerank else None,
                index=index, visual_reader=VisualReader(auxiliary["visual"], store),
                catalogue=ExpertiseCatalogue(config.expertise_config, runtime.skills),
                evidence_config=evidence_config,
            )
            ordinary_tools = topics.tools

            def retained_tools(topic_id):
                registry = ordinary_tools(topic_id)
                return registry.select([
                    row["function"]["name"] for row in registry.schemas()
                    if row["function"]["name"] not in {"search_sources", "related_papers"}
                ])

            topics.tools = retained_tools
            topic_id = topics.create(TopicTask.model_validate(plan["assignment"]))
            runtime.probe_source_access = topics.access(topic_id)
            summary.update(topic_id=topic_id, session_id=topics.state(topic_id).session_id)
            write_json(run / "initial-state.json", topics.state(topic_id))
            write_json(run / "verification.json", summary)
            result = await topics.run(topic_id)
            write_json(run / "topic-result.json", result)
            summary.update(status=result.status, reason=result.reason, turns_used=result.turns_used,
                           reader_sessions=topics._task(topic_id)["reading_ids"])
            if result.note:
                note = prose_inputs(result.note)
                (run / "expert.md").write_text(
                    "# " + note["title"] + "\n\n" + note["body"], "utf-8",
                )
    except Exception as exc:
        summary.update(status="failed", error=repr(exc))
        raise
    finally:
        summary.update(
            usage=runtime.budget.usage(), captured_model_responses=len(runtime.trace.calls),
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
        print(json.dumps(summary, ensure_ascii=False), flush=True)


async def main(args):
    if args.prepare:
        prepare(args)
        return
    if args.render:
        for repeat in args.repeats:
            run = Path(args.output).resolve() / f"run-{repeat}"
            render_trajectory(run)
            summarize_trajectory(run)
        return
    if not args.live:
        raise ValueError("Real model calls require --live")
    for repeat in args.repeats:
        await run_one(Path(args.output).resolve(), repeat)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--run", default="runs/mytest49_sample2")
    parser.add_argument("--output", default="runs/expert_trajectory_sample2")
    parser.add_argument("--config", default="config/runtime.yaml")
    parser.add_argument("--repeats", type=int, nargs="+", default=[1, 2, 3])
    asyncio.run(main(parser.parse_args()))
