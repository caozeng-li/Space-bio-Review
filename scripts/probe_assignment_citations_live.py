"""Local planning and attribution probes; no searches or complete review execution."""

import argparse
import asyncio
import json
import re
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import yaml

from spacebio_review.authored_markdown import Node, plain_text
from spacebio_review.evidence.reading_render import render_originals
from spacebio_review.evidence.store import SourceStore
from spacebio_review.persistence import write_json
from spacebio_review.prose import cited_ids, compile_authored_body, render_authored_body
from spacebio_review.publication import Renderer, compile_publication
from spacebio_review.review.expertise import ExpertiseCatalogue
from spacebio_review.tools import ToolResult
from spacebio_review.v5.assembly import compile_manuscript
from spacebio_review.v5.assessment import AssessmentSubmission
from spacebio_review.v5.budget import SessionAllowance
from spacebio_review.v5.chief import ChiefReview
from spacebio_review.v5.citation_writing import author_messages
from spacebio_review.v5.config import load_config
from spacebio_review.v5.records import SessionTask
from spacebio_review.v5.session import ResearchRuntime
from spacebio_review.v5.topic import TopicResearch, TopicTask


def load(path):
    return json.loads(path.read_text("utf-8"))


async def probe(args):
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose an empty output directory")
    output.mkdir(parents=True)
    config = load_config("config/runtime.yaml")
    data = config.model_dump(mode="json")
    data["budget"] = {"max_model_calls": 20, "max_embedding_calls": 0, "max_seconds": 1800}
    for model in data["models"].values():
        model["retries"] = 0
    path = output / "probe-config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    runtime = ResearchRuntime.from_config(path, output)
    saved = Path(args.run) / "v5"
    summary = {"scope": "Only local planning, authoring and citation binding", "cases": {}}
    allowance = SessionAllowance(completion_calls=0)

    async def call(state, name, messages, schemas=()):
        write_json(output / f"{name}-input.json", {
            "messages": author_messages(messages, state.evidence), "tools": schemas,
        })
        reply = await runtime._call_model(
            state, runtime.models[state.model_alias], messages, schemas,
            purpose="completion", stage=name,
        )
        write_json(output / f"{name}-response.json", reply.message)
        return reply

    async def authored(state, name, instruction):
        messages = [
            *runtime._conversation(state).messages,
            {"role": "user", "content": json.dumps(
                {"originals": render_originals(state.evidence)}, ensure_ascii=False,
            )},
            {"role": "user", "content": instruction},
        ]
        reply = await call(state, name, messages)
        assert reply.finish_reason == "stop" and not reply.message.get("tool_calls")
        content = reply.message["content"]
        (output / f"{name}-author.md").write_text(content, "utf-8")
        ref = runtime.store.save_author_text(
            state, content, "submit_research", absorbed_through=0,
        )
        raw = runtime.store.author_text(ref)
        body = compile_manuscript(raw)
        original = compile_authored_body(re.sub(r"\[证据\s*[1-9][0-9]*\s*\]", "", raw["content"]))
        def as_text(value):
            return plain_text([Node.model_validate(n) for n in value["blocks"]])

        assert as_text(body) == as_text(original)
        (output / f"{name}-bound.md").write_text(render_authored_body(body), "utf-8")
        summary["cases"][name] = {
            "session_id": state.session_id, "author_text_ref": ref,
            "body_unchanged": True, "author_contains_hash": "evidence-" in content,
            "citations": list(dict.fromkeys(cited_ids(body))),
        }
        runtime.budget.finish(state.session_id)
        print(name + " completed", flush=True)

    try:
        def no_network(request):
            raise AssertionError(f"Probe must not acquire external sources: {request.url}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as client:
            store = SourceStore(output / "sources", client)
            application_id = await store.add_local(Path("data/sample2.md"))
            topics = TopicResearch(
                runtime, store, None, application_ids=[application_id],
                catalogue=ExpertiseCatalogue(config.expertise_config, runtime.skills),
                external_research_enabled=False,
            )
            if args.case in {"all", "planning"}:
                manifest = load(next((saved / "chiefs").glob("*.json")))
                chief = ChiefReview(
                    topics, evaluation_system=manifest["evaluation_system"],
                    admission=manifest["review_context"]["admission"],
                )
                sid = chief.create(
                    f"以{date.today().isoformat()}评审 sample2 科学计划书，忽略2022年的申请日期。"
                    "本次只完成初始专家委派，研究任务登记后即停止，不执行专题研究或评分。",
                    allowance=allowance,
                )
                state = runtime.load(sid)
                registry = chief.tools(sid, state=state)
                messages = list(runtime._conversation(state, lambda: chief._context(sid)).messages)
                assignments = []
                async def capture(batch):
                    assignments.extend(
                        request.task.model_dump(mode="json") for request in batch.requests
                        if request.task is not None
                    )
                    return ToolResult(ok=True, content={"status": "captured_without_dispatch"})

                tool = registry._tools["research_topics"]
                registry = registry.select([n for n in registry._tools if n != tool.name])
                registry.add(replace(tool, handler=capture, delegates=False))
                for index in range(6):
                    reply = await call(state, f"planning-{index + 1}", messages, registry.schemas())
                    messages.append(reply.message)
                    for tool_call in reply.message.get("tool_calls", []):
                        function = tool_call["function"]
                        if function["name"] in {
                            "research_topics", "read_skills", "browse_materials", "read_materials",
                        }:
                            result = await registry.execute(function["name"], function["arguments"])
                            messages.append({
                                "role": "tool", "tool_call_id": tool_call["id"],
                                "content": result.model_dump_json(),
                            })
                        else:
                            raise RuntimeError(
                                "Probe only reads skills/application or captures assignments"
                            )
                    if assignments:
                        break
                assert assignments, "No assignments produced"
                write_json(output / "assignments.json", assignments)
                summary["cases"]["planning"] = {"topics": len(assignments)}
                runtime.budget.finish(sid)
                print("planning completed", flush=True)

            if args.case in {"all", "expert"}:
                if args.legacy_assignment:
                    # Optional challenge; retain the misleading task unchanged.
                    old = load(
                        saved / "inquiries" / "inquiry-edc63a6cef2740459cdbabd140eb7889.json"
                    )
                    assignment = old["assignment"]
                else:
                    candidates = load(
                        Path(args.assignments) if args.assignments else output / "assignments.json"
                    )
                    assignment = next(q for q in candidates if "space-operations" in q["expertise"])
                task = TopicTask.model_validate({
                    **assignment, "source_ids": [], "research_ids": [],
                    "application_refs": [
                        {**ref, "source_id": application_id}
                        for ref in assignment["application_refs"]
                    ],
                })
                tid = topics.create(task, allowance=allowance)
                state = topics.state(tid)
                await authored(state, "expert", (
                    "本次只依据已提供的申请原文，解释与委派职责有关的实施安排和待研究问题，"
                    "约400—700字。直接交付正文，本次不检索外部资料，也不形成分数。"
                ))

            if args.case in {"all", "reader"}:
                old = load(saved / "sessions" / "session-714206a2d1254bd7bb8909dd4cc8919c.json")
                sid = runtime.create(SessionTask.model_validate(old["task"]), allowance=allowance)
                state = runtime.load(sid)
                runtime._capture(state, old["evidence"])
                runtime.store.save(state)
                await authored(state, "reader", (
                    "本次只解释已经提供的原文。按当前问题交付约600—1000字阅读说明，"
                    "直接回复正文，不调用工具补充材料。"
                ))

            if args.case in {"all", "report"}:
                prior = Path(args.report_run) / "v5"
                old = load(next((prior / "sessions").glob("*.json")))
                fixture = load(prior / "assessments" / f"{old['assessment_ref']}.json")
                old_work = load(prior / "report-writing" / f"{old['report_writing_ref']}.json")
                sid = runtime.create(SessionTask.model_validate(old["task"]), allowance=allowance)
                state = runtime.load(sid)
                runtime._capture(state, old["evidence"])
                state.assessment_ref = "assessment-fixture"
                state.research_finished = True
                write_json(runtime.store._path("assessments", state.assessment_ref), fixture)
                work = {"drafts": old_work["drafts"]}
                for ref in work["drafts"].values():
                    write_json(runtime.store._path("author-texts", ref), load(
                        prior / "author-texts" / f"{ref}.json",
                    ))
                state.report_writing_ref = "writing-fixture"
                write_json(runtime.store._path("report-writing", state.report_writing_ref), work)
                runtime.store.save(state)
                chief = SimpleNamespace(
                    runtime=runtime, _manifest=lambda _: {
                        "review_context": {"evaluation": fixture["review_input"]["evaluation"]},
                    },
                )
                submission = AssessmentSubmission(chief, sid)
                messages, readings = submission.authoring_input(state, "synthesize_report")
                reply = await call(state, "report", messages)
                assert reply.finish_reason == "stop"
                content = reply.message["content"]
                (output / "report-author.md").write_text(content, "utf-8")
                ref = runtime.store.save_author_text(
                    state, content, "synthesize_report", absorbed_through=0, readings=readings,
                )
                await submission.accept_authoring(state, ref, runtime.store.author_text(ref))
                result = submission.result()
                document, audit = compile_publication(
                    result["assessment"], evidence=result["evidence"],
                    author_sources=result["author_sources"],
                )
                (output / "report.zh.md").write_text(Renderer(document).render("zh"), "utf-8")
                write_json(output / "publication-audit.json", audit)
                write_json(output / "report-result.json", result)
                summary["cases"]["report"] = {
                    "session_id": sid, "author_text_ref": ref,
                    "chapters": len(result["assessment"]["sections"]),
                    "sources": len(document.numbering),
                    "author_contains_hash": "evidence-" in content,
                    "scope": "One synthesis from fixed drafts/decision; code resolves citations",
                }
                runtime.budget.finish(sid)
                print("report completed", flush=True)
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        summary["usage"] = runtime.budget.usage()
        write_json(output / "verification.json", summary)
        await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--run", default="runs/mytest49_sample2")
    parser.add_argument("--report-run", default="runs/report_synthesis_sample2")
    parser.add_argument("--assignments", help="Reuse saved chief assignments for the expert probe")
    parser.add_argument("--legacy-assignment", action="store_true")
    parser.add_argument(
        "--case", choices=["all", "planning", "expert", "reader", "report"], default="all",
    )
    parser.add_argument("--output", required=True)
    asyncio.run(probe(parser.parse_args()))
