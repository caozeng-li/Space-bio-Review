"""Local Change 12-16 experiments. Frozen sources only; no review workflow or search."""

import argparse
import asyncio
import json
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
from probe_statement_flow import QUESTIONS, retained_context

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.context import TokenEstimator
from spacebio_review.agents.diagnostics import SubmissionRecorder
from spacebio_review.agents.types import ContextView
from spacebio_review.evidence.submissions import record
from spacebio_review.models.config import load_config
from spacebio_review.models.provider import model_class
from spacebio_review.tools import ToolRegistry
from spacebio_review.v4.config import ReportingConfig, V4Config
from spacebio_review.v4.contracts import professional_contract
from spacebio_review.v4.delivery import Delivery
from spacebio_review.v4.execution import Execution
from spacebio_review.v4.generation import render_parts, statement_parts
from spacebio_review.v4.publication_delivery import load_document
from spacebio_review.v4.publication_document import Section, parse_text, walk_sections
from spacebio_review.v4.records import Records, write_json
from spacebio_review.v4.synthesis import ArgumentAccess, Synthesis

HISTORY = Path("runs/mytest19_complete")
MANIFEST = "manifest-27bf25e0e1b3f58b3668969f061dfb8a826d3c76660d31a7905583ee8d58fb41"
E = {
    "omics": "evidence-47185ebd6225b710",
    "feric": "evidence-8645d3936bca2776",
    "debug": "evidence-3192f2f5bdf8f095",
    "timeline": "evidence-73d2b7eef0947342",
    "mirror": "evidence-fde35af27acb1c01",
    "background": "evidence-81b8ad81dc3ef8c9",
}


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


class OverlayRecords(Records):
    def __init__(self, output):
        super().__init__(output)
        self.history = Records(HISTORY)

    def get(self, key):
        if not (self.root / (key + ".json")).exists():
            data = self.history.get(key)
            write_json(self.root / (key + ".json"), data)
        return super().get(key)


class PreviewOnly(BaseException):
    pass


class Batch:
    def __init__(self, root, sample, execute, *, limits=(20, 200000, 50000)):
        self.root, self.sample, self.execute = Path(root), sample, execute
        self.limits = limits
        self.output = self.root / (("live-" if execute else "preview-") + sample)
        if self.output.exists():
            raise ValueError("Sample already recorded; use a new independent sample ID")
        self.rows = (
            read(self.root / "requests.json") if (self.root / "requests.json").exists() else []
        )

    def factory(self, config):
        batch = self

        class RecordingModel(model_class(config)):
            def __init__(self, config):
                self.config = config.model_copy(deep=True)
                self._client = None
                self.credential_id = None

            async def close(self):
                if self._client is not None:
                    await super().close()

            async def complete(self, messages, tools=None, **kwargs):
                estimate = TokenEstimator().measure(
                    messages, tools, response_schema=kwargs.get("response_schema")
                )["input_tokens"]
                row = {
                    "sample": batch.sample,
                    "model": self.config.model,
                    "structured_output": self.config.structured_output,
                    "messages": messages,
                    "tools": tools,
                    **kwargs,
                    "input_reserve": estimate,
                    "output_reserve": self.config.max_tokens,
                }
                if not batch.execute:
                    write_json(batch.output / "request.json", row)
                    raise PreviewOnly()
                if hasattr(batch, "before_request"):
                    batch.before_request(row)
                used_in = sum(r.get("input_tokens") or r["input_reserve"] for r in batch.rows)
                used_out = sum(r.get("output_tokens") or r["output_reserve"] for r in batch.rows)
                if (
                    len(batch.rows) >= batch.limits[0]
                    or used_in + estimate > batch.limits[1]
                    or used_out + self.config.max_tokens > batch.limits[2]
                ):
                    raise RuntimeError("local_batch_budget_exhausted")
                if self._client is None:
                    super().__init__(self.config, env_file=".env")
                batch.rows.append(row)
                write_json(batch.root / "requests.json", batch.rows)
                try:
                    reply = await super().complete(messages, tools, **kwargs)
                    row.update(
                        message=reply.message,
                        input_tokens=reply.prompt_tokens,
                        output_tokens=reply.completion_tokens,
                        reasoning_tokens=reply.reasoning_tokens,
                        finish_reason=reply.finish_reason,
                        effective_request=reply.effective_request,
                        retries=reply.retries,
                        provider_response=reply.provider_response,
                    )
                    return reply
                except Exception as exc:
                    row["error"] = str(exc)
                    raise
                finally:
                    write_json(batch.root / "requests.json", batch.rows)

        return RecordingModel(config)


class ProbeRuntime(AgentRuntime):
    async def run_task(self, task, *args, **kwargs):
        task = task.model_copy(update={"max_repairs": 0, "max_truncation_recoveries": 0})
        return await super().run_task(task, *args, **kwargs)


class ProbeExecution(Execution):
    async def call(self, key, kind, *args, **kwargs):
        if kind != "preparation":
            # All fixed C are already loaded. No search, whole specialist, or extra read phase.
            kwargs["read_actions"] = 0
        return await super().call(key, kind, *args, **kwargs)


class FrozenReader:
    references = None

    def tools(self):
        return ToolRegistry()

    def view(self, key, phase, *, refs=(), extra=()):
        return ContextView(agent_id=key, phase=phase, materials=list(extra))

    def coverage(self):
        return {"scope": "fixed_statement_fixture_no_new_reading"}


def config():
    value = load_config("config/runtime.yaml")
    for model in value.models.values():
        model.retries = 0
    for kind in ("assessment", "preparation", "writer", "specialist"):
        value.task_routes[kind].max_tokens = 6144 if kind == "specialist" else 4096
        value.task_routes[kind].max_truncation_recoveries = 0
        value.formal_routes[kind].max_tokens = 8192 if kind == "assessment" else 4096
        value.formal_routes[kind].minimum_body_tokens = 1024
        value.formal_routes[kind].reasoning_reserve = (
            4096 if kind == "assessment" else 2048 if kind != "writer" else 0
        )
    value.formal_routes["assessment"].protocol = "inherited"
    value.formal_routes["preparation"].protocol = "inherited"
    return value


def frozen_evidence():
    review = read(HISTORY / "review.json")
    return {
        k: v
        for artifact in [review["assessment"], *review["results"].values()]
        for k, v in artifact.get("evidence", {}).items()
    }


def fixtures(case, records):
    evidence = frozen_evidence()
    choices = {
        "L1": [
            (
                "external",
                "该多组学研究综合 59 名宇航员及 GeneLab 样本，发现线粒体过程富集，"
                "并将线粒体应激列为空间飞行的一致表型。",
                "仅据摘要；不能据此定位小肠的因果机制。",
                ["omics"],
            ),
            (
                "application",
                "申请计划在进入太空后第 3、6、9 天成像，第 10 天后去掉培养基；"
                "返回地面后通过少量细胞线粒体基因 PCR 扩增与二代测序检测 mtDNA 突变率。",
                "申请的实验计划，不是已取得的结果。",
                ["timeline"],
            ),
        ],
        "L2": [
            (
                "external",
                "Feric 等报道 TFAM 在体外相分离；TFAM 与 mtDNA 形成异质黏弹结构，"
                "并观察到应激下核样体融合增大。",
                "该摘要研究的是线粒体核样体，并非证明申请的空间小肠机制。",
                ["feric"],
            ),
            (
                "application",
                "申请描述对地面镜像装置开展调试。",
                "仅说明申请材料中的用途。",
                ["debug"],
            ),
        ],
        "L3": [
            (
                "application",
                "申请描述用地面镜像装置调试小肠隐窝培养条件和普通明场成像适配。",
                "不能自动等同于在轨实验同期对照。",
                ["debug"],
            ),
            (
                "application",
                "申请写明用地面镜像装置调试小肠细胞线粒体 ROS 和钙信号成像参数。",
                "这是申请拟开展的校准用途。",
                ["mirror"],
            ),
            (
                "unknown",
                "本组材料尚不能确认同期地面对照的配对与同步执行安排。",
                "未在当前材料确认不等于申请完全没有设计。",
                [],
            ),
        ],
    }
    c = professional_contract(
        None, {}, {"q1": {}}, evidence={E[k]: evidence[E[k]] for k in E}, inherited=True
    )
    selected = []
    for kind, text, scope, refs in choices[case]:
        selected.append(
            c.task_context.book.add(
                {
                    "kind": kind,
                    "parts": [{"text": text, "cites": [E[r] for r in refs]}],
                    "scope": [{"text": scope, "cites": []}],
                }
            )
        )
    c.task_context.book.selected["current_claims"] = selected
    c.refresh(c)
    product = c.product(
        c.parse(
            {
                "title": "人工冻结的局部论述夹具",
                "blocks": [{"statement_ids": [key], "member_ids": ["q1"]} for key in selected],
            }
        )
    ).model_dump()
    product["blocks"] = [{**b, "block_id": f"b{i}"} for i, b in enumerate(product["blocks"], 1)]
    product["task_id"] = "fixture/" + case
    ref = records.put("fixture", product)
    return ref, product


def replay_history(output):
    records = OverlayRecords(output)
    old = records.value(MANIFEST)
    delivery = Delivery(SimpleNamespace(records=records), output)
    ref = delivery.assemble(old["assessment"], delivery_input=old["delivery_input"])
    manifest = records.value(ref)
    document = load_document(records, manifest)
    state = {
        "manifest": ref,
        "status": "partial",
        "zh": {"status": "body_ready"},
        "en": {"status": "not_run"},
        "appendix": {"status": "completed"},
        "translations": {},
        "technical_limits": [],
        "historical_mode": "structural_replay_without_prose_editing",
    }
    delivery.export(state, document)
    write_json(
        Path(output) / "replay.json",
        {
            "old_manifest": MANIFEST,
            "new_manifest": ref,
            "historical_run_unchanged": True,
            "format_version": document.format_version,
            "evidence_snapshots": len(document.locations),
            "bibliographic_sources": len(document.sources),
            "appendix_analyses": sum(s.kind != "topic" for s in walk_sections(document.appendix)),
        },
    )


async def audit(root):
    root = Path(root)
    rows = read(root / "requests.json") if (root / "requests.json").exists() else []
    summaries = []
    estimator = TokenEstimator()
    records = Records(root / "context-scaling")
    ref, product = fixtures("L1", records)
    synthesis = Synthesis(
        SimpleNamespace(records=records, runtime=SimpleNamespace(config=config())), None
    )

    def view(handle):
        access = ArgumentAccess(records, [handle], records.result_blocks([handle]))
        c = synthesis.contract(FrozenReader(), access)
        return c.task_context.source_view(), c.schema()

    before_view, before_schema = view(ref)
    inflated = deepcopy(product)
    registry = inflated["statement_content"]["/registry"]["statements"]
    for i in range(100):
        registry[f"statement-unrelated{i}"] = {
            "statement_id": f"statement-unrelated{i}",
            "kind": "external",
            "parts": [{"text": "Unrelated archived observation. " * 100, "cites": [f"unused{i}"]}],
        }
        inflated["evidence"][f"unused{i}"] = next(iter(product["evidence"].values()))
    after_view, after_schema = view(records.put("fixture", inflated))
    for index, row in enumerate(rows, 1):
        wire_schema = (
            row.get("response_schema")
            if row.get("effective_request", {}).get("response_format") == "json_schema"
            else None
        )
        sources = {}
        for message in row["messages"]:
            content = message.get("content")
            parts = [{"text": content}] if isinstance(content, str) else content or []
            for part in parts:
                try:
                    payload = json.loads(part.get("text", ""))
                except (ValueError, TypeError):
                    continue
                if isinstance(payload, dict):
                    sources.update(payload.get("task_sources", payload.get("sources", {})))
        summaries.append(
            {
                "request": index,
                **{
                    k: row.get(k)
                    for k in (
                        "sample",
                        "model",
                        "input_tokens",
                        "output_tokens",
                        "reasoning_tokens",
                        "finish_reason",
                        "error",
                        "retries",
                    )
                },
                "wire_input_estimate": estimator.measure(
                    row["messages"], row.get("tools"), response_schema=wire_schema
                )["input_tokens"],
                "view_characters": {
                    k: len(json.dumps(v, ensure_ascii=False)) for k, v in sources.items()
                },
                "schema_characters": len(
                    json.dumps(row.get("response_schema"), ensure_ascii=False)
                ),
            }
        )
    links, failures = 0, []
    replay = root / "historical-replay"
    for path in replay.glob("*.md"):
        text = path.read_text("utf-8")
        for target in re.findall(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", text):
            if target.startswith(("https:", "http:")):
                continue
            filename, _, anchor = target.partition("#")
            dest = replay / filename if filename else path
            if not dest.exists() or (anchor and f'id="{anchor}"' not in dest.read_text("utf-8")):
                failures.append({"file": path.name, "target": target})
            links += 1
    write_json(
        root / "audit.json",
        {
            "requests": summaries,
            "calls": len(rows),
            "input_tokens": sum(r.get("input_tokens") or 0 for r in rows),
            "output_tokens": sum(r.get("output_tokens") or 0 for r in rows),
            "replay_local_links": links,
            "replay_missing_targets": failures,
            "history_growth": {
                "added_unselected_statements_and_evidence": 100,
                "archive_characters_before": len(json.dumps(product, ensure_ascii=False)),
                "archive_characters_after": len(json.dumps(inflated, ensure_ascii=False)),
                "source_view_characters": len(json.dumps(before_view, ensure_ascii=False)),
                "source_view_unchanged": before_view == after_view,
                "schema_unchanged": before_schema == after_schema,
            },
            "projection_comparison": [
                {
                    "sample": row["sample"],
                    "wire_input_estimate": estimator.measure(
                        row["messages"],
                        row.get("tools"),
                        response_schema=row.get("response_schema")
                        if row.get("structured_output") == "json_schema"
                        else None,
                    )["input_tokens"],
                    "schema_characters": len(
                        json.dumps(row.get("response_schema"), ensure_ascii=False)
                    ),
                }
                for path in sorted(root.glob("preview-*-projection4*/request.json"))
                for row in [read(path)]
            ],
            "cost_note": "Reasoning is included in provider completion tokens, not added twice. "
            "Wire estimates exclude schemas ignored by json_object/yaml transport.",
        },
    )
    translation_path = root / "live-L7-final/result.json"
    if translation_path.exists():
        write_json(
            root / "translation-compatibility.json",
            {
                "original_result": str(translation_path),
                "status": "retained_without_rebinding",
                "reason": "Legacy flat units are not rebound to tree slots by guesswork. "
                "Use probe_publication.py for current checks; prior replay is retained.",
            },
        )


async def first_read(batch, runtime, case):
    if case == "L5":
        async with httpx.AsyncClient() as client:
            c, pages = await retained_context("scope", runtime, client)
        question = QUESTIONS["scope"]
    else:
        evidence = frozen_evidence()
        question = (
            "区分申请中的成像观测时序与返回后测序：实际拟获取哪些数据，能否确定突变发生时点？"
        )
        c = professional_contract(
            None,
            {},
            {"q1": {"question": question}},
            evidence={E[k]: evidence[E[k]] for k in ("timeline", "mirror")},
            inherited=True,
        )
        pages = list(c.task_context.evidence.values())
    ctx = c.task_context
    schema = record("MemoryRequest", memory=(ctx.memory_type(), ...)).model_json_schema()
    messages = [
        {
            "role": "system",
            "content": "只基于提供的已读原文处理这一个局部问题，以 record_memory 交接公开研究认识。"
            "每项论述完整保留对象、构建体、处理条件、时点和结论；scope 写适用范围。"
            "区分申请自述、外部事实、推断和未知。引用只填 cites，不手写编号。"
            "followups 保留缺口，scope_outcomes 写 q1 的已分析范围。"
            "没有已有 C，based_on/premise_ids/supersedes 为空。不要记录私有推理过程。",
        },
        {
            "role": "user",
            "content": json.dumps(
                {"question": question, "member_id": "q1", "sources": ctx.source_view()},
                ensure_ascii=False,
            ),
        },
    ]
    write_json(batch.output / "fixture.json", {"question": question, "read_pages": pages})
    reply = await runtime.models["task/specialist/normal"].complete(
        messages,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "record_memory",
                    "description": "交接完整公开认识。",
                    "parameters": schema,
                },
            }
        ],
    )
    calls = reply.message.get("tool_calls", [])
    if len(calls) != 1 or calls[0]["function"]["name"] != "record_memory":
        raise ValueError("research_did_not_handoff_memory")
    args = json.loads(calls[0]["function"]["arguments"])
    ctx.checkpoint(ctx.bind_memory(ctx.memory_type().model_validate(args["memory"])))
    write_json(batch.output / "result.json", {"handoff": args, "state": ctx.book.snapshot()})


async def run(batch, case, *, legacy=False):
    if case == "L6":
        raise ValueError("L6 request routing is retired; followups remain scientific unknowns")
    cfg = config()
    if legacy:
        cfg.formal_routes["assessment"].protocol = "legacy"
        cfg.formal_routes["preparation"].protocol = "legacy"
    runtime = ProbeRuntime.from_config(cfg, model_factory=batch.factory)
    runtime.recorder = SubmissionRecorder(batch.output / "submissions")
    records = OverlayRecords(batch.output)
    ex = ProbeExecution(
        runtime, records, V4Config(task_turns=4), {}, ReportingConfig(max_repairs=0)
    )
    write_json(
        batch.output / "manifest.json",
        {
            "sample": batch.sample,
            "execute": batch.execute,
            "scope": "local_frozen_sources_only",
            "runtime_config": cfg,
            "limits": {"calls": 20, "input": 200000, "output": 50000},
            "repairs": 0,
            "transport_retries": 0,
            "truncation_recoveries": 0,
            "projection_baseline": "legacy" if legacy else "statements",
        },
    )
    try:
        if case in {"L4", "L5"}:
            await first_read(batch, runtime, case)
        elif case in {"L1", "L2", "L3"}:
            ref, product = fixtures(case, records)
            write_json(
                batch.output / "fixture.json",
                {
                    "manual_C_fixture": True,
                    "fixture_version": 2,
                    "purpose": "isolate_downstream_flow_not_initial_understanding",
                    "artifact": ref,
                    "product": product,
                },
            )
            synthesis = Synthesis(ex, SimpleNamespace(reader=FrozenReader))
            result = await synthesis.generate(
                records.result_blocks([ref]),
                {
                    "objective": {
                        "L1": "解释多组学线粒体发现对申请研究动机的支持边界。",
                        "L2": "区分 Feric 的核样体认识与申请实验方法的适用边界。",
                        "L3": "说明镜像装置的已述用途、同期对照的未知及条件性建议。",
                    }[case]
                },
                {},
                [ref],
                "rigor",
                "aspect-final",
            )
            write_json(
                batch.output / "result.json", {"artifact": result, "product": records.value(result)}
            )
        else:
            ref, product = fixtures("L3", records)
            registry = product["statement_content"]["/registry"]["statements"]
            selected = list(registry)
            text = render_parts(statement_parts(registry[selected[0]]))
            text += "\n\n若需将其作为同期对照，应说明配对与同步条件；当前尚不能确认已落实。"
            text += "\n推理前提：[@" + selected[0] + "]"
            section = Section(
                section_id="local", title="镜像装置与对照条件", blocks=parse_text(text)
            )
            write_json(batch.output / "fixture.json", section)
            result = await Delivery(ex, batch.output).translate(section)
            write_json(batch.output / "result.json", result)
    except PreviewOnly:
        pass
    except Exception as exc:
        write_json(batch.output / "failure.json", {"type": type(exc).__name__, "error": str(exc)})
        print(f"{batch.sample}: {type(exc).__name__}: {exc}", flush=True)
    finally:
        await runtime.close()
        print(
            json.dumps(
                {
                    "sample": batch.sample,
                    "calls_total": len(batch.rows),
                    "input_total": sum(r.get("input_tokens") or 0 for r in batch.rows),
                    "output_total": sum(r.get("output_tokens") or 0 for r in batch.rows),
                }
            ),
            flush=True,
        )


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage", choices=["offline", "audit", "L1", "L2", "L3", "L4", "L5", "L6", "L7"]
    )
    parser.add_argument("--root", default="runs/probe-report-citations-20261012")
    parser.add_argument("--sample", default="1")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--legacy-preview", action="store_true")
    args = parser.parse_args()
    if args.legacy_preview and args.execute:
        parser.error("Legacy comparison is offline only")
    if args.stage == "offline":
        replay_history(Path(args.root) / "historical-replay")
    elif args.stage == "audit":
        await audit(args.root)
    else:
        sample = args.stage + "-" + args.sample + ("-legacy" if args.legacy_preview else "")
        await run(Batch(args.root, sample, args.execute), args.stage, legacy=args.legacy_preview)


if __name__ == "__main__":
    asyncio.run(main())
