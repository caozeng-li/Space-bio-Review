"""Opt-in application understanding to delegation; eight requests, no child research.

Both cases share one durable budget. The fixed external material is an actual
retained abstract, while the positive-control application is explicitly synthetic.
All author and parser requests count, including failed attempts; retries are off.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_chief_live import record_public_replies  # noqa: E402

from spacebio_review.agents.skills import SkillLibrary  # noqa: E402
from spacebio_review.evidence.store import SourceStore  # noqa: E402
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument  # noqa: E402
from spacebio_review.models.provider import ModelError, model_class  # noqa: E402
from spacebio_review.models.scheduling import RequestScheduler  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.review.expertise import ExpertiseCatalogue  # noqa: E402
from spacebio_review.tools import ToolResult  # noqa: E402
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance  # noqa: E402
from spacebio_review.v5.chief import ChiefReview  # noqa: E402
from spacebio_review.v5.config import load_config  # noqa: E402
from spacebio_review.v5.records import ResearchNote, SessionSettings  # noqa: E402
from spacebio_review.v5.session import ResearchRuntime  # noqa: E402
from spacebio_review.v5.topic import TopicResearch  # noqa: E402

FIXTURE = ROOT / "scripts/fixtures/v5_grounding/marotta-abstract.json"
OBJECTIVE = (
    "本次局部工作只到申请理解和科学派题，不执行专题、不评分、不写报告。"
    "阅读完整申请与已提供的外部摘要；先以普通正文 update_research 保存带申请出处的"
    "项目理解，说明研究对象、比较、实施阶段、读出、预期认识及影响判断的未说明条件。"
    "然后按当前认识，通过一次 research_topics 提出有判断价值的问题，并选择相关"
    "application_refs。研究方向关注知识增量及比较、在轨实施条件，按科学联系合并，"
    "无需固定专题数量。申请和外部研究各自陈述了什么，以它们实际文字为依据。"
    "本地程序会截获委派请求，不运行子专题。全部材料已提供，不能检索或获取新资料。"
    "两份案例共八次实际模型请求（包含解析），每份优先用一次理解作者、一次解析、"
    "一次派题作者完成；无需额外调用 update_agenda。"
)
SYNTHETIC = """# 合成计划书：仅用于原文锚定正向对照

本项目明确提出首次在国际空间站（ISS）对小鼠小肠隐窝分化成小肠类器官过程开展
线粒体 DNA 类核形态与线粒体 ROS、钙信号的并行研究；此处“首次”是申请方待评估的
优先权主张。目标是确定该模型在在轨与地面条件间的变化，不把其他组织结果等同于小肠。

在镜像装置完成培养、换液、染色和成像调试后，ISS 上与地面镜像装置采用同批隐窝，
实施时间同步的培养对照，在第 3、6、9 天成像。两端温度、换液、染色时间与取像流程
按同一程序执行。计划测定类器官大小、管腔数以及荧光信号，返回后检测 mtDNA。

本合成材料没有给出样本量、随机化安排、探针特异性验证和仪器校准数据。它不是
sample2 的补充说明，不应作为 sample2 的事实来源。
"""


class NoDiscovery:
    async def search(self, *args, **kwargs):
        raise AssertionError("grounding_probe_has_no_retrieval")


class CapturedChief(ChiefReview):
    """Exercise production framing but stop at the explicit local acceptance boundary."""

    def __init__(self, *args, material, **kwargs):
        super().__init__(*args, **kwargs)
        self.material = material
        self.captured = []
        self.delegation_error = None

    def _context(self, chief_id):
        return {**super()._context(chief_id), "fixed_external_material": self.material}

    def tools(self, chief_id, *, state=None):
        registry = super().tools(chief_id, state=state)
        # A fixed-input experiment needs no discovery or acquisition entry point.
        return registry.select(
            [name for name in ("update_agenda", "research_topics") if name in registry._tools]
        )

    async def _research(self, chief_id, args):
        self.captured = [
            {
                "request": request.model_dump(mode="json"),
                "application_basis": self.topics.application_basis(
                    request.task.application_refs, self.access(chief_id)
                )
                if request.task
                else [],
            }
            for request in args.requests
        ]
        return ToolResult(ok=True, content={"local_probe_delegation_captured": True})


def bounded_author(model, active, budget, *, case_cap=4):
    original = model.complete

    async def complete(messages, tools=None, *, response_schema=None):
        case = active.get("case")
        owner = budget._owner.get()
        if case and owner:
            for row in reversed(case.runtime.load(owner).observations):
                if row.get("name") == "research_topics":
                    result = row["output"]["result"]
                    if not result["ok"]:
                        case.delegation_error = result
                    break
        reason = None
        if case and case.captured:
            reason = "local_probe_delegation_captured"
        elif case and case.delegation_error:
            reason = "local_probe_invalid_delegation"
        elif owner and budget.usage(owner)["model_calls"] >= case_cap:
            reason = "local_probe_case_request_limit"
        if reason:
            raise ModelError(reason, category="exploration", request_sent=False)
        return await original(messages, tools, response_schema=response_schema)

    model.complete = complete


async def run_case(engine, config, output, name, application, fixture, active):
    case_root = output / name
    case_root.mkdir(parents=True, exist_ok=True)

    def no_http(request):
        raise AssertionError("grounding_probe_has_no_source_http")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_http)) as client:
        store = SourceStore(case_root / "sources", client, browser_fallback=False)
        application_id = await store.add_local(application)
        candidate = SourceCandidate.model_validate(fixture["candidate"])
        source_id = store.register(candidate)
        passage = Passage.model_validate(fixture["passage"])
        doc = SourceDocument(
            source_id=source_id, candidate=candidate, availability="abstract", passages=[passage]
        )
        store._save(doc)
        topics = TopicResearch(
            engine,
            store,
            NoDiscovery(),
            application_ids=[application_id],
            catalogue=ExpertiseCatalogue(config.expertise_config, engine.skills),
            external_research_enabled=False,
        )
        chief = CapturedChief(
            topics,
            evaluation_system="gbt42048_2022",
            admission={"status": "skipped", "reason": "local_framing_probe"},
            material={
                "source_id": source_id,
                "title": candidate.title,
                "reading_scope": fixture["reading_scope"],
                "provenance": fixture["provenance"],
                "use": "External literature; read its original in the shared evidence view.",
            },
        )
        chief_id = chief.create(
            OBJECTIVE,
            allowance=SessionAllowance(completion_calls=0),
        )
        access = chief.access(chief_id)
        access.visible.add(source_id)
        access.record_context_read(
            {
                "source_id": source_id,
                "title": candidate.title,
                "passages": [passage.model_dump(mode="json")],
            }
        )
        access.save()
        state = engine.load(chief_id)
        engine._capture(state, access.references.snapshot_choices(selected=False))
        engine.store.save(state)
        active["case"] = chief
        result = await chief.run(chief_id)
        write_json(case_root / "result.json", result)
        write_json(case_root / "delegation.json", chief.captured)
        if chief.delegation_error:
            write_json(case_root / "delegation-error.json", chief.delegation_error)
        state = engine.load(chief_id)
        if state.note_ref:
            write_json(
                case_root / "application-understanding.json", engine.store.note(state.note_ref)
            )
        engine.budget.finish(chief_id)
        return {
            "case": name,
            "chief_id": chief_id,
            "status": "captured" if chief.captured else "incomplete",
            "session_reason": result["reason"],
            "note_ref": state.note_ref,
            "captured_tasks": len(chief.captured),
            "usage": engine.budget.usage(chief_id),
            "children_executed": 0,
            "scientific_quality": "Manual reading of understanding and delegation required.",
        }


async def reframe_case(engine, config, output, prior, name, active):
    """Retain exact prior science and original identities; exercise only current framing."""
    old_result = json.loads((prior / name / "result.json").read_text("utf-8"))
    previous = json.loads(
        (prior / "v5/sessions" / (old_result["chief_id"] + ".json")).read_text("utf-8")
    )
    artifact = json.loads((prior / name / "application-understanding.json").read_text("utf-8"))
    case_root = output / name
    case_root.mkdir(parents=True, exist_ok=True)

    def no_http(request):
        raise AssertionError("grounding_probe_has_no_source_http")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_http)) as client:
        # restore() and application_basis() only read the previous source store.
        store = SourceStore(prior / name / "sources", client, browser_fallback=False)
        store.restore()
        topics = TopicResearch(
            engine,
            store,
            NoDiscovery(),
            application_ids=[s["source_id"] for s in previous["task"]["application"]["sources"]],
            catalogue=ExpertiseCatalogue(config.expertise_config, engine.skills),
            external_research_enabled=False,
        )
        chief = CapturedChief(
            topics,
            evaluation_system="gbt42048_2022",
            admission={"status": "skipped", "reason": "local_framing_probe"},
            material={"reading_scope": "Previous original snapshots; no new external material."},
        )
        chief_id = chief.create(
            "本次只验证科学派题入口。当前研究说明是此前已保存的申请理解，原文与身份均已保留；"
            "不重写研究说明，不评分，不生成报告。依据原申请及已有研究理解，"
            "通过一次 research_topics 提出有判断价值的问题，按科学联系组织，"
            "关注知识增量与比较、在轨实施条件。局部程序截获委派后停止，不执行子专题。"
            "所有原文已提供，无需调用 update_agenda。每份材料最多两次作者请求，"
            "如工具交付不成功立即保留实际失败并停止。",
            allowance=SessionAllowance(completion_calls=0),
        )
        state = engine.load(chief_id)
        state.evidence = previous["evidence"]
        state.aliases = previous["aliases"]
        engine.store.save_note(
            state,
            ResearchNote.model_validate(artifact["note"]),
            absorbed_through=0,
            aliases={key: key for key in state.evidence},
        )
        access = chief.access(chief_id)
        access.receive(state.evidence)
        access.save()
        active["case"] = chief
        result = await chief.run(chief_id)
        write_json(case_root / "result.json", result)
        write_json(case_root / "delegation.json", chief.captured)
        if chief.delegation_error:
            write_json(case_root / "delegation-error.json", chief.delegation_error)
        write_json(
            case_root / "reused-understanding.json",
            {
                "source_artifact": (prior / name / "application-understanding.json").as_posix(),
                "note": artifact["note"],
                "rewritten": False,
            },
        )
        engine.budget.finish(chief_id)
        return {
            "case": name,
            "chief_id": chief_id,
            "status": "captured" if chief.captured else "incomplete",
            "session_reason": result["reason"],
            "captured_tasks": len(chief.captured),
            "usage": engine.budget.usage(chief_id),
            "children_executed": 0,
        }


async def probe(args):
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("probe_requires_empty_output_directory")
    output.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config)
    fixture = json.loads(FIXTURE.read_text("utf-8"))
    prior = args.framing_only_from.resolve() if args.framing_only_from else None
    prior_summary = json.loads((prior / "summary.json").read_text("utf-8")) if prior else None
    synthetic = output / "synthetic-application.md"
    if not prior:
        synthetic.write_text(SYNTHETIC, "utf-8")
    budget = Budget(
        output,
        BudgetLimits(max_model_calls=4 if prior else 8, max_embedding_calls=0, max_seconds=900),
    )
    author_alias, parser_alias = config.roles["chief"], config.auxiliary["delivery_parser"]
    models, cases, active, error, engine = {}, [], {}, None, None
    try:
        for alias in dict.fromkeys((author_alias,) if prior else (author_alias, parser_alias)):
            settings = config.models[alias].model_copy(
                update={
                    "retries": 0,
                    "timeout_seconds": min(config.models[alias].timeout_seconds, 240),
                    "max_tokens": min(config.models[alias].max_tokens, 8192),
                },
                deep=True,
            )
            models[alias] = model_class(settings)(settings, env_file=args.env_file, budget=budget)
        engine = ResearchRuntime(
            output,
            models,
            {"chief": author_alias, "researcher": author_alias},
            budget=budget,
            scheduler=RequestScheduler(config.concurrency),
            settings=SessionSettings(max_turns=6, completion_turns=1, context_tokens=64000),
            skills=SkillLibrary(config.skills_dir),
            delivery_parser=models.get(parser_alias),
        )
        engine._owned_models = list(models.values())
        engine.scheduler.request_budget = budget
        bounded_author(models[author_alias], active, budget, case_cap=2 if prior else 4)
        record_public_replies(engine, output)
        write_json(
            output / "input.json",
            {
                "limits": budget.limits,
                "expected_requests": 2 if prior else 6,
                "framing_only_from": prior.as_posix() if prior else None,
                "prior_actual_requests": prior_summary["usage"]["model_calls"] if prior else 0,
                "models": {name: model.config for name, model in models.items()},
                "external_original": fixture,
                "application": args.application.resolve().as_posix(),
                "synthetic_positive_control": synthetic.as_posix(),
                "process_no_proxy": {key: os.environ.get(key) for key in ("NO_PROXY", "no_proxy")},
                "environment_modified_by_probe": False,
                "full_review_runs": 0,
            },
        )
        async with asyncio.timeout(budget.remaining_seconds()):
            if prior:
                for name in ("sample2", "explicit-positive"):
                    cases.append(await reframe_case(engine, config, output, prior, name, active))
            else:
                for name, application in (
                    ("sample2", args.application),
                    ("explicit-positive", synthetic),
                ):
                    cases.append(
                        await run_case(engine, config, output, name, application, fixture, active)
                    )
    except Exception as exc:
        error = {"type": type(exc).__name__, "reason": str(exc)}
    finally:
        budget.save()
        summary = {
            "status": "completed"
            if not error and len(cases) == 2 and all(case["status"] == "captured" for case in cases)
            else "incomplete",
            "cases": cases,
            "error": error,
            "usage": budget.usage(),
            "prior_actual_requests": prior_summary["usage"]["model_calls"] if prior else 0,
            "cumulative_actual_requests": budget.usage()["model_calls"]
            + (prior_summary["usage"]["model_calls"] if prior else 0),
            "requests": budget.data["requests"],
            "elapsed_seconds": 900 - budget.remaining_seconds(),
            "full_review_runs": 0,
            "children_executed": 0,
            "retrieval_requests": 0,
            "embedding_requests": 0,
            "scientific_quality": "Structural completion does not establish scientific fidelity.",
        }
        write_json(output / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if engine:
            await engine.close()
        else:
            for model in models.values():
                await model.close()
    return 0 if summary["status"] == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--application", type=Path, default=ROOT / "data/sample2.md")
    parser.add_argument("--config", type=Path, default=ROOT / "config/runtime.yaml")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--framing-only-from", type=Path)
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
