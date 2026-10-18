"""Compose two retained scientific manuscripts; dry by default, live costs <= 2 calls.

No admission, search, expert execution, or whole-review entry is used. The supplied
scientific manuscripts are manually prepared fixtures from sample2 originals;
this probe measures report composition, not discovery or scientific reliability.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from spacebio_review.evidence.store import SourceStore
from spacebio_review.evidence.types import SourceDocument
from spacebio_review.models.config import ModelConfig
from spacebio_review.models.provider import model_class
from spacebio_review.persistence import write_json
from spacebio_review.v5.assessment import AssessmentSubmission
from spacebio_review.v5.authoring import research_note
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance
from spacebio_review.v5.chief import ChiefReview
from spacebio_review.v5.config import load_config
from spacebio_review.v5.delivery import deliver_assessment
from spacebio_review.v5.records import ReportSelection
from spacebio_review.v5.session import ResearchRuntime
from spacebio_review.v5.topic import TopicResearch, TopicTask

IMAGING_NOTE = "note-8690f4fa29d1a6be2533c5f9e389945073ffd584460b580871e177c90220ad91"
METHOD_NOTE = "note-b0665c93fcce285fb0e5db347ffa009f11e668c1940c25c44d4824f05c7d009d"
APPLICATION = "evidence-d66cb4eba9ef4ff6"
APPLICATION_GOAL = "evidence-743b36027261abdb"
AMPLICON = "evidence-4905f09a95e06473"
WHOLE_GENOME = "evidence-87402b43c4232bc7"
CLONAL = "evidence-e0108d037071155b"

MANUSCRIPTS = (
    {
        "title": "在轨成像与返回后测序形成互补观察",
        "focus": "实际成像阶段及两类读出的科学关系",
        "handles": [APPLICATION, APPLICATION_GOAL],
        "content": (
            "申请把荧光成像安排在进入太空后第 3、6、9 天：在小鼠小肠隐窝向类器官"
            "分化时染色，并用自带激光共聚焦显微镜观察 mtDNA 类核和线粒体。"
            "返回地面后的操作是少量细胞线粒体基因 PCR 扩增和二代测序。"
            "因此，返回后固定样本能否继续保持活体染料信号，并不是该申请既定"
            "成像链路的直接前提。[@E1]\n\n"
            "在轨不同时间点的类核形态与返回后序列变异构成互补读出，能够把小肠"
            "类器官发育过程中的空间组织变化和最终分子结果放在同一研究框架下。"
            "这是具体的知识价值；它还不是对每个被拍摄类核的序列身份追踪，申请"
            "没有给出这种一一对应设计。地面镜像调试与比较也属于原方案的组成部分。[@E2]"
        ),
    },
    {
        "title": "类器官 mtDNA 测序先例给出的实现路径",
        "focus": "方法先例怎样支持返回后测序而不过度外推测量能力",
        "handles": [AMPLICON, WHOLE_GENOME, CLONAL, APPLICATION],
        "content": (
            "已有线粒体碱基编辑研究采用两轮 PCR 构建目标区域文库，并在 Illumina "
            "iSeq 100 上进行 2 × 150 bp 双端测序。该先例说明类器官相关 mtDNA "
            "变异比例可以通过扩增子路线研究，而不只是笼统地声称技术未知。[@E1]\n\n"
            "同一论文的全线粒体基因组方法使用 20–40 ng 基因组 DNA、两组引物及"
            "约 8.5 kb 的 PCR 产物，再做 NovaSeq 测序。方法中大于 0.1% 的转换率"
            "是分析筛选条件，不能直接解释为本项目已验证的 0.1% 分析检测限。[@E2]\n\n"
            "论文中的单细胞步骤服务于分选和克隆培养：消化后按信号分选，在 Matrigel "
            "中稀疏接种，约两周后挑取单个类器官测序和扩增。这提供了观察克隆间差异"
            "的研究路径，也说明这种数据不是未经培养的返回样本即时单细胞读出。[@E3]\n\n"
            "申请已经安排返回后 PCR 与二代测序。上述先例支持这条技术路线有可借鉴"
            "的实现基础；如何适配返回的小鼠小肠类器官和实际 DNA 量，仍是本项目"
            "需要完成的具体方法工作。不能把外部肝脏类器官编辑模型等同于本项目"
            "已经完成的飞行样本验证。[@E4]"
        ),
    },
)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sample_materials(source):
    """Read scientific artifacts only; do not load any historical execution state."""
    source = Path(source)
    evidence, origins = {}, []
    for ref in (IMAGING_NOTE, METHOD_NOTE):
        artifact = read_json(source / "v5" / "notes" / (ref + ".json"))
        evidence.update(artifact["evidence"])
        origins.append({"note_ref": ref, "author_text_ref": artifact["author_text_ref"]})
    handles = dict.fromkeys(handle for item in MANUSCRIPTS for handle in item["handles"])
    evidence = {handle: evidence[handle] for handle in handles}
    source_ids = dict.fromkeys(row["reference"]["source_id"] for row in evidence.values())
    return {
        "evidence": evidence,
        "sources": [read_json(source / "sources" / sid / "source.json") for sid in source_ids],
        "provenance": {
            "source_run": str(source.resolve()),
            "historical_artifacts": origins,
            "old_report": str((source / "report.zh.md").resolve()),
            "preparation": "Manual scientific fixtures grounded in retained original passages.",
            "corrections": [
                "Preserve in-orbit imaging and returned-sample sequencing as distinct stages.",
                "Use the stated two amplicons; separate filtering threshold from detection limit.",
                "Retain clonal growth without unsupported timing or precision assertions.",
            ],
        },
    }


def save_scientific_manuscript(topics, inquiry_id, manuscript, evidence):
    runtime = topics.runtime
    state = topics.state(inquiry_id)
    runtime._capture(state, evidence)
    # The independent writers intentionally both use E1 for different originals.
    state.aliases = {
        f"E{index + 1}": handle for index, handle in enumerate(manuscript["handles"])
    }
    runtime.store.set_focus(inquiry_id, manuscript["focus"])
    raw_ref = runtime.store.save_author_text(
        state, manuscript["content"], "submit_research",
        absorbed_through=state.observation_index, phase="final_manuscript",
        title=manuscript["title"],
    )
    note_ref = runtime.store.save_note(
        state, research_note(runtime.store.author_text(raw_ref), title=manuscript["title"]),
        absorbed_through=state.observation_index, author_text_ref=raw_ref,
    )
    state.submitted_ref, state.status = note_ref, "submitted"
    state.submissions.append(note_ref)
    runtime.store.save(state)
    runtime.budget.finish(runtime.budget_account(state))
    return {"inquiry_id": inquiry_id, "contribution_ref": note_ref}


def prepare_fixture(output, materials, system="gbt42048_2022", *, models=None, budget=None):
    """Construct ready manuscripts in fresh stores without executing scientific sessions."""
    output = Path(output)
    budget = budget or Budget(output, BudgetLimits(max_model_calls=2, max_embedding_calls=0))
    default = SimpleNamespace(config=ModelConfig(
        model="unused-local", base_url="https://unused.invalid", max_tokens=2048, retries=0
    ))
    models = models or {"author": default}
    runtime = ResearchRuntime(
        output, models, {"chief": "author", "researcher": "author"}, budget=budget,
        allowances={
            "chief": SessionAllowance(completion_calls=2),
            "researcher": SessionAllowance(completion_calls=0),
        },
        delivery_parser=models.get("parser"),
    )
    runtime.scheduler.request_budget = budget
    sources = SourceStore(output / "sources", client=None, env_file=None)
    for raw in materials["sources"]:
        document = SourceDocument.model_validate(raw)
        folder = sources.root / document.source_id
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / "candidate.json", document.candidate)
        write_json(folder / "source.json", document)
    sources.restore()
    application_ids = [
        row["source_id"] for row in materials["sources"]
        if row["candidate"]["kind"] == "application"
    ]
    topics = TopicResearch(runtime, sources, None, application_ids=application_ids)
    selected = []
    for manuscript in MANUSCRIPTS:
        inquiry_id = topics.create(TopicTask(objective=manuscript["focus"]))
        selected.append(save_scientific_manuscript(
            topics, inquiry_id, manuscript, materials["evidence"]
        ))
    chief = ChiefReview(
        topics, evaluation_system=system,
        admission={"status": "skipped", "reason": "local_composition_fixture"},
    )
    chief_id = chief.create("依据两篇预先提供的科学稿，形成项目综合及完整评价。")
    manifest = chief._manifest(chief_id)
    manifest["topic_ids"] = [row["inquiry_id"] for row in selected]
    manifest["mode"] = "assessment"
    write_json(chief._path(chief_id), manifest)
    state = runtime.load(chief_id)
    runtime._capture(state, materials["evidence"])
    runtime.store.save(state)
    write_json(output / "fixture-provenance.json", materials.get("provenance", {}))
    return SimpleNamespace(
        runtime=runtime, topics=topics, chief=chief, chief_id=chief_id,
        selection=ReportSelection(main_sections=selected), materials=materials,
    )


def fixed_manuscript(system, *, main_sections=None, appendix_sections=()):
    """Handwritten text and mapping; scores are assembly fixtures, not a project verdict."""
    blocks = []
    mapping = {"provided_fields": [], "missing_fields": [], "unknowns": []}

    def block(text):
        blocks.append(text)
        return f"p{len(blocks)}"

    def prose(key, text):
        block("## " + key)
        mapping[key] = [block(text)]
        mapping["provided_fields"].append(key)

    prose("project", "本项目拟在轨观察小鼠小肠类器官的形态与 mtDNA 类核，返回后测序。[@E1]")
    prose("scope", "评价对象为申请拟开展的实验；外部方法论文提供实现基础，尚非该项目的飞行结果。")
    prose("overall", "本项目的价值在于将小肠类器官发育中的在轨形态观察与返回后分子读出结合，"
          "获得地面对照下的空间环境响应认识。现有方法先例支持具体实施路线；样品适配仍需"
          "在研究中完成，当前设计不直接给出每个成像类核的序列身份。")
    mapping["summary"] = list(mapping["overall"])
    mapping["provided_fields"].append("summary")
    mapping["dimensions"] = {}
    scores = [21, 18, 15, 16, 5, 10]
    for index, criterion in enumerate(system.criteria):
        block("### " + criterion.id)
        row = {"paragraph_ids": [block(
            f"{criterion.label}：在轨与返回后读出的互补关系提供具体评价依据；"
            "结合已选科学章节说明的实现条件，按当前研究计划阶段作此项判断。"
        )]}
        if system.score_grades:
            row["score"] = scores[index]
            block("score：" + str(scores[index]))
        else:
            row.update(grade="B", confidence="C")
            block("grade：B；confidence：C")
        mapping["dimensions"][criterion.id] = row
    mapping["provided_fields"].append("dimensions")
    block("未知事项：无")
    mapping["provided_fields"].append("unknowns")
    if system.score_grades:
        block("安全否决：不适用")
        mapping["safety_veto"] = {"applies": False, "paragraph_ids": [block(
            "本固定装配样例未输入具体安全否决事实，此决定仅用于测试国标计算路径。"
        )]}
        mapping["provided_fields"].append("safety_veto")
    else:
        prose("support_scope", "支持在轨与地面对照下的描述性比较及所述方法适配工作。")
        block("recommendation：support")
        mapping["recommendation"] = "support"
        mapping["provided_fields"].append("recommendation")
    if main_sections is not None:
        block("主文选文：" + ", ".join(main_sections))
        block("附录选文：" + (", ".join(appendix_sections) or "无"))
        mapping.update(main_sections=list(main_sections), appendix_sections=list(appendix_sections))
    return "\n\n".join(blocks), mapping


async def publish(fixture, submission, output):
    result = submission.result()
    write_json(Path(output) / "result.json", result)
    if result["report_ref"]:
        return await deliver_assessment(
            fixture.runtime, result["assessment"], evidence=result["evidence"],
            report_selection=result["report_selection"], contributions=result["contributions"],
            author_sources=result["author_sources"], english=False,
        )
    return {"status": "partial"}


async def dry_case(output, materials, system):
    fixture = prepare_fixture(output, materials, system)
    runtime, state = fixture.runtime, fixture.runtime.load(fixture.chief_id)
    submission = AssessmentSubmission(fixture.chief, fixture.chief_id)
    submission.prepare(state, fixture.selection)
    content, values = fixed_manuscript(submission.system)
    raw_ref = runtime.store.save_author_text(
        state, content, "submit_assessment", absorbed_through=state.observation_index
    )
    raw = runtime.store.author_text(raw_ref)
    schema, _, _ = submission.authoring_contract(raw)
    mapping = schema.model_validate(values)
    parse_ref = runtime.store.save_delivery_parse(raw_ref, {}, mapping)
    accepted = submission.accept_authoring(state, raw_ref, raw, mapping, parse_ref=parse_ref)
    delivery = await publish(fixture, submission, output)
    write_json(Path(output) / "author-input.json", submission.completion_input(
        state, "submit_assessment"
    ))
    await runtime.close()
    return {"accepted": accepted.ok, "status": delivery["status"], "model_calls": 0}


async def live_case(args, materials):
    output = Path(args.output)
    config = load_config(args.config)
    budget = Budget(output, BudgetLimits(
        max_model_calls=2, max_embedding_calls=0, max_seconds=900,
        max_input_tokens=args.max_input_tokens, max_output_tokens=20480,
    ))
    models = {}
    for role, alias in (("author", config.roles["chief"]),
                        ("parser", config.auxiliary["delivery_parser"])):
        settings = config.models[alias].model_copy(update={"retries": 0})
        models[role] = model_class(settings)(settings, env_file=args.env_file, budget=budget)
    fixture = prepare_fixture(output, materials, args.system, models=models, budget=budget)
    fixture.runtime._owned_models = list(models.values())
    submission = AssessmentSubmission(fixture.chief, fixture.chief_id)
    state = fixture.runtime.load(fixture.chief_id)
    submission.prepare(state, None if args.fallback else fixture.selection)
    write_json(
        output / "author-input.json", submission.completion_input(state, "submit_assessment")
    )
    result = {}
    try:
        # Direct final writing only: one scientific author and its existing structure mapper.
        await fixture.runtime._write_phase(
            state, models["author"], phase="final_manuscript", operation="submit_assessment",
            context=lambda: fixture.chief._context(fixture.chief_id), submission=submission,
        )
        result = await publish(fixture, submission, output)
    except Exception as exc:
        result = {"status": "failed", "error": type(exc).__name__, "reason": str(exc)}
    finally:
        budget.save()
        result.update(usage=budget.usage(), model_calls_limit=2)
        await fixture.runtime.close()
    return result


async def main(args):
    source, output = Path(args.source_run).resolve(), Path(args.output).resolve()
    if output == source or source in output.parents or output in source.parents:
        raise ValueError("Use an output directory separate from the historical run")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty output directory")
    materials = sample_materials(source)
    output.mkdir(parents=True, exist_ok=True)
    (output / "baseline-report.zh.md").write_text(
        (source / "report.zh.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    if args.live:
        results = {args.system: await live_case(args, materials)}
    else:
        results = {}
        for system in ("gbt42048_2022", "general"):
            results[system] = await dry_case(output / system, materials, system)
    report = {
        "mode": "live" if args.live else "dry", "results": results,
        "full_review_runs": 0, "retrieval_calls": 0, "expert_execution_calls": 0,
        "science": "Prepared scientific fixtures; no claim of measured discovery improvement.",
    }
    write_json(output / "probe-report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", default="runs/mytest37_sample2")
    parser.add_argument("--output", required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--fallback", action="store_true", help="Choose frozen short IDs in prose")
    parser.add_argument("--system", choices=["gbt42048_2022", "general"], default="gbt42048_2022")
    parser.add_argument("--config", default="config/v5-runtime.yaml")
    parser.add_argument("--env-file", default=".env", help="Only read by provider when --live")
    parser.add_argument("--max-input-tokens", type=int, default=100000)
    asyncio.run(main(parser.parse_args()))
