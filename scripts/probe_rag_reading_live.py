"""Opt-in production source-reader probe. No discovery, topic execution or review.

Historical oversized manuals are explicit regression fixtures, not newly admitted
research materials. Only this harness disables admission limits for those fixtures.
"""

import argparse
import asyncio
import json
from pathlib import Path

import httpx
from docling_core.types.doc import DoclingDocument

from spacebio_review.agents.skills import SkillLibrary
from spacebio_review.evidence.documents import document_from_docling
from spacebio_review.evidence.knowledge import RetrieveKnowledge
from spacebio_review.evidence.material_limits import MaterialLimits
from spacebio_review.evidence.research import load_evidence_config
from spacebio_review.evidence.store import SourceStore
from spacebio_review.evidence.types import SourceDocument
from spacebio_review.models.embedding import DashScopeEmbedding, VectorIndex
from spacebio_review.models.provider import DashScopeModel
from spacebio_review.models.scheduling import RequestScheduler
from spacebio_review.persistence import write_json
from spacebio_review.prose import render_authored_body
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance
from spacebio_review.v5.config import load_config
from spacebio_review.v5.records import SessionSettings
from spacebio_review.v5.session import ResearchRuntime
from spacebio_review.v5.source_reading import SourceQuestion
from spacebio_review.v5.topic import TopicResearch, TopicTask

MANUAL = Path("runs/mytest40_sample2/sources/src-267dc61be9d7/source.json")
POSTER = Path("runs/mytest41_sample2/sources/src-d26a2a2552bc/source.json")
CASES = {
    "sato": (
        Path("runs/mytest42_sample2/sources/src-aa5eea81a1ec/source.json"),
        "Sato 2009 的隐窝来源小肠类器官培养，在第3、6、9天分别处于什么阶段？"
        "请根据这份来源说明形态、分化与首次传代的时间关系，"
        "以及这些时间点作为空间实验观测窗口的依据。",
    ),
    "cells": (
        MANUAL,
        "阅读这份手册，说明小鼠小肠类器官实验的地面镜像装置可以依据哪些"
        "在轨条件进行培养、换液与显微成像适配，哪些条件需要另行取得？"
        "请给出有用的具体能力及其所属设备，区分培养、样品保存和其他实验对象的条件。",
    ),
    "cold": (
        MANUAL,
        "这份手册中的样本冷藏、样本离心处理、细胞组织培养和营养液保存"
        "各自有哪些条件？说明它们的用途和彼此关系，让实验团队能据此安排地面准备。",
    ),
    "flumias": (
        POSTER,
        "FLUMIAS实际采用什么成像原理？near-confocal说明了什么？"
        "如何利用其培养、成像和重力条件，设计有解释力的活细胞对照实验？"
        "区分该资料陈述的已有能力、研究计划和已经观察到的结果。",
    ),
}
RETRIEVALS = {
    "sato": ["crypt culture time course differentiation first passage", "Supplementary Movie"],
    "cells": ["细胞组织实验 培养温度 换液 成像 激光共聚焦", "植物类实验 生命生态实验柜 温度控制"],
    "cold": ["样本冷藏 温度控制 有效存储容积", "营养液保存 培养温度"],
    "flumias": ["FLUMIAS SIM near-confocal microscope", "gravity controls culture conditions"],
}
STARTING_PASSAGES = {
    # Keep the misleading time hints in every Sato replay, independent of reranking.
    "sato": [f"m-36647ce43d64-p{i}" for i in (23, 24, 25, 26, 58, 60)],
}


class NoDiscovery:
    async def search(self, *args, **kwargs):
        raise AssertionError("source_reader_probe_has_no_discovery")


def retain_fixture(store, fixture, output):
    original = SourceDocument.model_validate_json(fixture.read_text("utf-8"))
    sid = store.register(original.candidate)
    if not any(
        Path(m.original_path or "").suffix.lower() == ".pdf"
        and (Path(m.original_path).parent / "parsed/docling.json").exists()
        for m in original.materials
    ):
        original.source_id = sid
        store._save(original)
        return sid
    document = SourceDocument(source_id=sid, candidate=original.candidate)
    for material in original.materials:
        native = Path(material.original_path).parent / "parsed/docling.json"
        parsed = document_from_docling(
            DoclingDocument.load_from_json(native),
            sid,
            original.candidate,
            output / material.material_id,
            original_path=material.original_path,
        )
        parsed.availability = material.availability
        retained = material.model_copy(deep=True)
        document.materials.append(retained)
        store._append_document(document, retained, parsed)
    store._save(document)
    return sid


def trace(model, output, alias):
    original = model.complete
    calls = 0

    async def complete(messages, tools=None, *, response_schema=None):
        nonlocal calls
        calls += 1
        name = f"{alias}-{calls:02}"
        write_json(
            output / "requests" / f"{name}.json",
            {
                "messages": messages,
                "tools": tools,
                "response_schema": response_schema,
            },
        )
        print(f"request {name}", flush=True)
        reply = await original(messages, tools, response_schema=response_schema)
        write_json(
            output / "replies" / f"{name}.json",
            {
                "message": reply.message,
                "finish_reason": reply.finish_reason,
                "input_tokens": reply.prompt_tokens,
                "output_tokens": reply.completion_tokens,
            },
        )
        print(f"returned {name}: {reply.finish_reason}", flush=True)
        return reply

    model.complete = complete


async def run(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = load_config(args.config)
    budget = Budget(
        output,
        BudgetLimits(
            max_model_calls=32,
            max_embedding_calls=64,
            max_input_tokens=1800000,
            max_output_tokens=100000,
        ),
    )
    scheduler = RequestScheduler(config.concurrency)
    scheduler.request_budget = budget
    aliases = {
        config.roles["source_reader"],
        config.auxiliary["assembly"],
        config.auxiliary["rerank"],
    }
    models = {
        alias: DashScopeModel(
            config.models[alias], scheduler=scheduler, budget=budget, env_file=args.env_file
        )
        for alias in aliases
    }
    for alias, model in models.items():
        trace(model, output, alias)
    reader = config.roles["source_reader"]
    runtime = ResearchRuntime(
        output,
        models,
        {"researcher": reader, "source_reader": reader},
        budget=budget,
        scheduler=scheduler,
        skills=SkillLibrary(config.skills_dir),
        settings=SessionSettings(max_turns=8, context_tokens=96000),
        assembly_route=config.auxiliary["assembly"],
    )
    embedder = DashScopeEmbedding(
        config.embedding, scheduler, budget=budget, env_file=args.env_file
    )
    index = VectorIndex(embedder, cache_dir=config.embedding.cache_dir)

    def no_source_http(request):
        raise AssertionError("fixture_reader_must_not_download")

    summaries = []
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(no_source_http)) as client:
            store = SourceStore(
                output / "sources",
                client,
                browser_fallback=False,
                material_limits=MaterialLimits(enabled=False),
            )
            topics = TopicResearch(
                runtime,
                store,
                NoDiscovery(),
                application_ids=[],
                index=index,
                rerank_model=models[config.auxiliary["rerank"]],
                evidence_config=load_evidence_config(config.evidence_config),
            )
            for case in args.cases:
                fixture, question = CASES[case]
                sid = retain_fixture(store, fixture, output / "parsed" / case)
                topic_id = topics.create(
                    TopicTask(responsibility=question, source_ids=[sid]),
                    allowance=SessionAllowance(completion_calls=0),
                )
                if args.retrieve_only:
                    access = topics.access(topic_id)
                    with budget.scope(topics.inquiry(topic_id).budget_account_id):
                        result = await access.retrieve(
                            RetrieveKnowledge(
                                source_id=sid,
                                queries=RETRIEVALS[case],
                                limit=5,
                            )
                        )
                    write_json(output / f"{case}-retrieval.json", result)
                    summaries.append(
                        {
                            "case": case,
                            "ok": result.ok,
                            "channels": result.content.get("channels"),
                            "returned_ids": result.content.get("returned_ids"),
                            "complete": result.content.get("complete"),
                        }
                    )
                    continue
                print(f"case {case}: source-reader only", flush=True)
                result = await topics.reader.research(
                    topic_id,
                    SourceQuestion(
                        source_id=sid,
                        question=question,
                        passage_ids=STARTING_PASSAGES.get(case, []),
                    ),
                )
                write_json(output / f"{case}-result.json", result)
                reading_id = result.content["reading_id"]
                state = runtime.load(reading_id)
                access = topics.reader.access(reading_id)
                if state.note_ref:
                    note = runtime.store.note(state.note_ref)["note"]
                    (output / f"{case}.md").write_text(render_authored_body(note["body"]), "utf-8")
                summaries.append(
                    {
                        "case": case,
                        "ok": result.ok,
                        "reason": result.error,
                        "reading_id": reading_id,
                        "read_passages": {sid: sorted(ids) for sid, ids in access.read.items()},
                        "read_images": {sid: sorted(ids) for sid, ids in access.images.items()},
                        "retrieval_channels": access.channels,
                    }
                )
                print(f"case {case}: ok={result.ok} reason={result.error}", flush=True)
    finally:
        write_json(
            output / "summary.json",
            {
                "cases": summaries,
                "usage": budget.usage(),
                "full_review_runs": 0,
                "fixture_policy": "Historical originals; admission disabled only in this probe",
                "semantic_acceptance": "Inspect the first manuscripts against original pages",
            },
        )
        for model in models.values():
            await model.close()
        await embedder.close()
        await runtime.close()
    print(json.dumps({"cases": summaries, "usage": budget.usage()}, ensure_ascii=False), flush=True)
    return 0 if all(row["ok"] for row in summaries) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", default="config/runtime.yaml")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--retrieve-only", action="store_true")
    raise SystemExit(asyncio.run(run(parser.parse_args())))
