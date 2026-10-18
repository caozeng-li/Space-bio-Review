"""Bounded local scientific acceptance: fixed originals or captured initial planning.

Each case stops at one manuscript or the first delegation. One shared 24-request
ledger covers all cases and optional alternate routes; no full review or tests/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_chief_live import record_public_replies  # noqa: E402

from spacebio_review.agents.skills import SkillLibrary  # noqa: E402
from spacebio_review.evidence.access import SourceAccess  # noqa: E402
from spacebio_review.evidence.store import SourceStore  # noqa: E402
from spacebio_review.evidence.types import SourceDocument  # noqa: E402
from spacebio_review.models.provider import model_class  # noqa: E402
from spacebio_review.models.scheduling import RequestScheduler  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.review.evaluation import GBT42048  # noqa: E402
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance  # noqa: E402
from spacebio_review.v5.chief import CHIEF_INSTRUCTION  # noqa: E402
from spacebio_review.v5.config import load_config  # noqa: E402
from spacebio_review.v5.records import SessionTask  # noqa: E402
from spacebio_review.v5.session import ResearchRuntime  # noqa: E402
from spacebio_review.v5.source_reading import READER_INSTRUCTION  # noqa: E402
from spacebio_review.v5.topic import TOPIC_INSTRUCTION  # noqa: E402

HISTORY = ROOT / "runs/v5-local/change21-source-real-20261016/probe.json"
MAROTTA = ROOT / "scripts/fixtures/v5_grounding/marotta-abstract.json"
CASES = (
    "positive",
    "changed-comparison",
    "measurement-value",
    "cell-abstract",
    "cell-body",
    "siew",
    "huang",
    "sample1-planning",
    "sample2-planning",
)
COMPARISON = (
    "在国际空间站（ISS）的既有培养和成像载荷上，将同一分化批次的样品随机分入微重力组"
    "和在轨离心1g组，两组共用培养箱、培养液批次与成像流程，运行时程同步；"
    "温度、换液、辐射剂量及离心振动均记录。地面镜像装置同批同步运行作为辅助参照。"
)
CHANGED_COMPARISON = (
    "在国际空间站（ISS）的既有培养和成像载荷上只设置微重力组，不设在轨1g组；"
    "比较对象为一年前另一批供体、另一培养液和另一套固定后切片成像流程的地面存档样本。"
    "本项目把两组差异作为微重力对动态成熟的效应，未设置连接两种测量流程的比较。"
)
SYNTHETIC = """# 合成计划：用于局部验收，不是真实申请或已发表结果

目标是在神经类器官中定量描述微重力下的成熟速度与分支形态轨迹，区分终点表达变化
与何时发生形态分化；不以形态轨迹单独证明细胞内分子机制。既有研究已有一个月培养
与返回检测先例，本计划增加同一样本的在轨连续观测及重力比较，不声称全球首次。

{comparison}

采用三个独立供体，每供体四次独立分化批次，以批次为随机分配和统计单位，使用预先
定义的分支长度与面积轨迹，在第1、7、14、21天测量。盲法分割与统计模型考虑供体、
批次及重复观测；既有先导方差支持该配置识别预定的生物学差异幅度。

以下前期依据均为本合成案例给定事实：已在同型号地面载荷完成两轮21天培养、换液
和成像，样本存活率分别为92%与94%，分割与人工标注一致性良好；装置方确认接口、
供电和数据带宽满足需求，所需在轨离心条件已经获配。计划阶段仍需完成样品单元
适配、最终焦面参数优化与一次流程联合演练。试剂、封装和返回条件已有相应预研。

本案例只评价实验方案与工程可实现性，其他指标不在此次局部任务范围。
"""


class NoDiscovery:
    async def search(self, *args, **kwargs):
        raise AssertionError("fixed_science_case_has_no_discovery")


def selected_original(name, *, abstract=False):
    info = json.loads(HISTORY.read_text("utf-8"))["cases"][name]
    path = Path(info["provenance"])
    doc = SourceDocument.model_validate_json(path.read_text("utf-8"))
    chosen = [p for p in doc.passages if p.passage_id in info["passage_ids"]]
    if abstract:
        chosen = chosen[:1]
    return doc.model_copy(update={"passages": chosen, "visuals": []}), {
        "path": str(path),
        "sha256": sha256(path.read_bytes()).hexdigest(),
        "selection": [p.passage_id for p in chosen],
        "scope": "Original abstract only"
        if abstract
        else "Selected original methods/results/captions",
        "selection_reason": "Retain groups, measurements, interventions and relevant results",
        "interpretations_supplied": False,
    }


def marotta_original():
    fixture = json.loads(MAROTTA.read_text("utf-8"))
    return SourceDocument(
        source_id="fixed-marotta",
        candidate=fixture["candidate"],
        availability="abstract",
        passages=[fixture["passage"]],
    ), fixture["provenance"]


def specification(case):
    if case in {"positive", "changed-comparison"}:
        comparison = COMPARISON if case == "positive" else CHANGED_COMPARISON
        return {
            "role": "chief",
            "skills": [GBT42048.skill],
            "application_text": SYNTHETIC.format(comparison=comparison),
            "sources": [marotta_original()],
            "objective": "以主审身份仅评价合成计划的实验方案与工程可实现性。说明能获得什么"
            "认识、方法与实施依据、决定判断的限制，并按所给本项国标分档给一个整数分。"
            "不评价其他五项，不形成总分、摘要或报告。无需以未实施为由等待研究全部完成。",
            "evaluation": GBT42048.prompt(["experiment_feasibility"]),
        }
    if case == "measurement-value":
        return {
            "role": "researcher",
            "skills": ["literature-understanding", "measurement-analysis"],
            "application_path": ROOT / "data/sample2.md",
            "sources": [selected_original("huang"), marotta_original()],
            "objective": "围绕本计划的形态、在轨荧光观察与返回检测，形成一次局部专业意见："
            "这些数据可能增加什么知识，Huang的实际测量与干预、Marotta的类器官先例"
            "分别能支持哪些认识和实现路径，申请与外部实验有哪些实质差异。"
            "材料已固定，直接作出有依据的价值与方法判断，不评分、不另设计整改方案。",
        }
    source = "cell" if case.startswith("cell-") else case
    questions = {
        "cell": "解释这篇研究对空间生物学线粒体背景提供了什么知识，其实际对象和结果"
        "对小肠类器官研究有什么作用。按本次确实提供的原文范围回答。",
        "siew": "解释Siew论文的研究设计与实际比较，特别是Fig.7各类动物如何组成、"
        "与其他任务结果有什么关系，以及这些比较带来了什么认识。",
        "huang": "解释Huang研究中DCFH-DA、其他读出和干预实验的关系，说明其主要"
        "发现及这些结果对线粒体、mtDNA与衰老过程提供的科学依据。",
    }
    return {
        "role": "source_reader",
        "skills": ["scientific-source-reading"],
        "application_text": "本次只解释指定论文问题；不作项目整体评价。",
        "sources": [selected_original(source, abstract=case == "cell-abstract")],
        "objective": questions[source],
    }


async def fixed_case(engine, config, output, case):
    spec = specification(case)
    folder = output / case
    if (folder / "result.json").exists():
        raise ValueError("case_already_finished_use_new_output_and_same_ledger")

    def no_http(request):
        raise AssertionError("fixed_science_case_has_no_external_http")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_http)) as client:
        store = SourceStore(folder / "sources", client, browser_fallback=False)
        app_path = spec.get("application_path")
        if app_path is None:
            app_path = folder / "local-task.md"
            app_path.parent.mkdir(parents=True, exist_ok=True)
            app_path.write_text(spec["application_text"], "utf-8")
        application_id = await store.add_local(app_path)
        app = store.retained(application_id)
        docs, provenance = [app], []
        for original, origin in spec["sources"]:
            doc = deepcopy(original)
            doc.source_id = store.register(doc.candidate)
            store._save(doc)
            docs.append(doc)
            provenance.append(origin)
        access = SourceAccess(store, NoDiscovery(), visible=[d.source_id for d in docs])
        material = []
        for doc in docs:
            row = {
                "source_id": doc.source_id,
                "title": doc.candidate.title,
                "passages": [p.model_dump(mode="json") for p in doc.passages],
            }
            access.record_context_read(row)
            material.append(row)
        role_instruction = {
            "chief": CHIEF_INSTRUCTION,
            "researcher": TOPIC_INSTRUCTION,
            "source_reader": READER_INSTRUCTION,
        }[spec["role"]]
        sid = engine.create(
            SessionTask(
                role=spec["role"],
                objective=spec["objective"],
                application={"sources": material, "local_scope": "Fixed original excerpts only"},
                skills=spec["skills"],
                evaluation=json.dumps(
                    {
                        "scientific_role": role_instruction,
                        "single_criterion": spec.get("evaluation"),
                    },
                    ensure_ascii=False,
                ),
            ),
            readings=access.references.snapshot_choices(selected=False),
            allowance=SessionAllowance(completion_calls=1),
        )
        state = engine.load(sid)
        state.research_finished = True
        state.authoring_phase = "final_manuscript"
        engine.store.save(state)
        messages = engine._writing_messages(
            state, "final_manuscript", "submit_research", None, None
        )
        write_json(
            folder / "input.json",
            {
                "messages": messages,
                "provenance": provenance,
                "application_path": str(app_path),
                "synthetic_application": case in {"positive", "changed-comparison"},
                "scope": "One local scientific manuscript; no final assessment submission",
                "estimated_request": engine.budget.measure(
                    engine.models[state.model_alias].config, messages, []
                ),
            },
        )
        before = engine.budget.usage()["model_calls"]
        outcome = await engine.run(sid)
        result = {
            **outcome.model_dump(mode="json"),
            "case": case,
            "actual_requests": engine.budget.usage()["model_calls"] - before,
            "usage": engine.budget.usage(sid),
            "scientific_acceptance": "Manual reading required",
            "search_requests": 0,
            "full_review_runs": 0,
        }
        if outcome.note_ref:
            note = engine.store.note(outcome.note_ref)
            write_json(folder / "note.json", note)
            raw = engine.store.author_text(note["author_text_ref"])
            (folder / "manuscript.md").write_text(raw["content"], "utf-8")
        write_json(folder / "result.json", result)
        engine.budget.finish(sid)
        return result


async def run(args):
    selected = args.case or list(CASES)
    if not args.execute:
        return {
            "cases": selected,
            "execute": False,
            "estimated_author_requests": sum(
                3 if name.endswith("planning") else 1 for name in selected
            ),
            "ledger_root": str(args.ledger_root),
            "maximum_generation_requests": 24,
        }
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config)
    budget = Budget(
        args.ledger_root.resolve(),
        BudgetLimits(
            max_model_calls=24,
            max_embedding_calls=0,
            max_seconds=3600,
            max_input_tokens=1500000,
            max_output_tokens=200000,
        ),
    )
    roles = dict(config.roles)
    if args.author_route:
        roles = dict.fromkeys(roles, args.author_route)
    needed = set(roles.values()) | {config.auxiliary["delivery_parser"]}
    routes = {alias: config.models[alias].model_copy(deep=True) for alias in needed}
    if args.author_route:
        baseline = config.models[config.roles["researcher"]]
        alternate = routes[args.author_route]
        routes[args.author_route] = alternate.model_copy(
            update={
                "tools": True,
                "max_tokens": baseline.max_tokens,
                "extra_body": {
                    **alternate.extra_body,
                    "enable_thinking": baseline.extra_body.get("enable_thinking", False),
                },
            }
        )
    models = {
        alias: model_class(routes[alias])(
            routes[alias].model_copy(update={"retries": 0}),
            env_file=args.env_file,
            budget=budget,
        )
        for alias in needed
    }
    engine = ResearchRuntime(
        output,
        models,
        roles,
        budget=budget,
        scheduler=RequestScheduler(config.concurrency),
        settings=config.session,
        session_overrides=config.session_overrides,
        skills=SkillLibrary(config.skills_dir),
        delivery_parser=models[config.auxiliary["delivery_parser"]],
    )
    engine._owned_models = list(models.values())
    engine.scheduler.request_budget = budget
    record_public_replies(engine, output)
    results = []
    try:
        for case in selected:
            if case.endswith("-planning"):
                from probe_v5_scientific_planning import run_case

                sample = case.split("-")[0]
                result = await run_case(engine, config, output, case, ROOT / f"data/{sample}.md")
            else:
                result = await fixed_case(engine, config, output, case)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        write_json(
            output / "summary.json",
            {
                "results": results,
                "series_usage": budget.usage(),
                "ledger_root": str(args.ledger_root.resolve()),
                "roles": roles,
                "full_review_runs": 0,
                "models": {a: m.config for a, m in models.items()},
            },
        )
        await engine.close()
    return {"results": results, "series_usage": budget.usage()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--case", action="append", choices=CASES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ledger-root", type=Path, required=True)
    parser.add_argument(
        "--author-route", help="Existing configured route, used only for this probe"
    )
    parser.add_argument("--config", type=Path, default=ROOT / "config/runtime.yaml")
    parser.add_argument("--env-file", default=".env")
    print(json.dumps(asyncio.run(run(parser.parse_args())), ensure_ascii=False, indent=2))
