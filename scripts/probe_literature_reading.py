"""Read one fixed real paper with bounded calls; no search or full review workflow."""

import argparse
import asyncio
import hashlib
import json
import shutil
from datetime import date
from pathlib import Path

from probe_literature_changes import ROOT, probe_configs, recorded_models

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.diagnostics import SubmissionRecorder
from spacebio_review.evidence import EvidenceResources, LiteratureResearcher
from spacebio_review.evidence.documents import parse_document
from spacebio_review.evidence.knowledge import KnowledgeQuestion
from spacebio_review.evidence.literature import KnowledgeSession
from spacebio_review.evidence.types import SourceCandidate, SourceDocument
from spacebio_review.v4.budget import Budget
from spacebio_review.v4.config import ReportingConfig, V4Config
from spacebio_review.v4.records import Records, write_json

MODEL_CALLS = 12
TASK_ACTIONS = 10
MAX_SECONDS = 1200
PLOS_URL = "https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1011386"
TSIM_URL = "https://www.nature.com/articles/s41598-023-27814-x"
TSIM_XML = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC9879979/fullTextXML"
PLOS_RETAINED = ROOT / "runs/mytest28_sample2/sources/src-60837b25f41b"


class FixedPaperSession(KnowledgeSession):
    def tools(self):
        # Keep production readers, references and budgets; only discovery is out of scope.
        registry = super().tools()
        return registry.select(
            [
                schema["function"]["name"]
                for schema in registry.schemas()
                if schema["function"]["name"] not in {"search_sources", "related_papers"}
            ]
        )


class FixedPaperResearcher(LiteratureResearcher):
    def new_session(self, question, *, whole_units=True):
        return FixedPaperSession(self, question, whole_units=whole_units)


def file_record(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def retained_organoid_paper(store, source_dir=PLOS_RETAINED):
    """Freeze the original PDF and its existing parse, without importing old judgments."""
    source_dir = Path(source_dir).resolve()
    original_record = source_dir / "source.json"
    doc = SourceDocument.model_validate_json(original_record.read_text("utf-8"))
    original_identifiers = dict(doc.candidate.identifiers)
    recorded_root = Path(doc.original_path).parent
    # The retained discovery metadata described a preprint; the PDF itself identifies
    # the journal article in p1 and its publication date in p5. Use that actual version.
    doc.candidate.url = PLOS_URL
    doc.candidate.identifiers = {"doi": "10.1371/journal.pcbi.1011386"}
    doc.candidate.publication_date = date(2023, 8, 14)
    doc.candidate.date_precision = "day"
    doc.candidate.venue = "PLOS Computational Biology"
    doc.candidate.volume = "19"
    doc.candidate.issue = "8"
    doc.candidate.article_number = "e1011386"
    doc.source_id = store.register(doc.candidate)
    folder = store.root / doc.source_id

    def retain_file(recorded_path):
        path = Path(recorded_path)
        relative = path.relative_to(recorded_root) if path.is_absolute() else path
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_dir / relative, target)
        return str(target)

    # Relocate the source's own referenced files; never copy previous knowledge answers.
    doc.original_path = retain_file(doc.original_path)
    if doc.normalized_path:
        doc.normalized_path = retain_file(doc.normalized_path)
    for visual in doc.visuals:
        if visual.path:
            visual.path = retain_file(visual.path)
    for material in doc.materials:
        if material.original_path:
            material.original_path = retain_file(material.original_path)
    store._save(doc)
    figure = next(v for v in doc.visuals if v.visual_id == "fig5")
    return doc, {
        "official_url": PLOS_URL,
        "acquired_url": doc.acquired_url,
        "retained_source_directory": str(source_dir),
        "original": file_record(Path(doc.original_path)),
        "parse_origin": file_record(original_record),
        "original_discovery_identifiers": original_identifiers,
        "parse_policy": "unchanged original passages and figures; local paths relocated",
        "metadata_origin": "journal citation and publication date on the original PDF, p1/p5",
        "target_figure": {"label": "Figure 3", "visual_id": figure.visual_id},
        "target_pixels": file_record(Path(figure.path)),
    }


async def fixed_tsim_paper(store):
    candidate = SourceCandidate(
        title=(
            "Transmission structured illumination microscopy with tunable frequency "
            "illumination using tilt mirror assembly"
        ),
        identifiers={"doi": "10.1038/s41598-023-27814-x", "pmcid": "PMC9879979"},
        url=TSIM_URL,
        fulltext_urls=[TSIM_XML],
    )
    sid = store.register(candidate)
    folder = store.root / sid
    # Exactly one fixed original request, without discovery, fallback or retry.
    response = await store.client.get(TSIM_XML)
    response.raise_for_status()
    original = folder / "original.xml"
    original.write_bytes(response.content)
    doc = parse_document(
        sid,
        candidate,
        original,
        folder / "parsed",
        content_type="application/xml",
        url="https://pmc.ncbi.nlm.nih.gov/articles/PMC9879979/",
    )
    doc.acquired_url = str(response.url)
    if doc.availability != "full_text":
        raise RuntimeError("fixed_tsim_fulltext_unavailable")
    store._save(doc)
    return doc, {
        "official_url": TSIM_URL,
        "acquired_url": doc.acquired_url,
        "original": file_record(original),
        "parse_policy": "current XML parser; unchanged public full text",
    }


def reading_question(case, doc):
    common = {
        "purpose": "method",
        "existing_source_ids": [doc.source_id],
        "background": (
            f"这是固定原文阅读咨询，只研究所给论文。论文：{doc.candidate.title}。"
            f"source_id={doc.source_id}，完整原文已保存在资料库，尚未作为本任务已读内容。"
            "按具体目的打开原文，自行决定章节续读或图像读取，不开展外部检索。"
        ),
    }
    if case == "organoid-counting":
        common["background"] += "论文 Figure 3 的实际图像对应 visual_id=fig5。"
        return KnowledgeQuestion(
            **common,
            question=(
                "这篇论文的自动形态计数方法在什么样的肠道类器官图像上建立，如何训练和比较？"
                "结合方法、结果以及 Figure 3 的实际图像，说明 D5、D7 两种自动流程"
                "相对人工计数的偏差方向及统计比较，解释这些结果支持怎样的形态学认识。"
                "它能否直接测量管腔数或同一个类器官的连续增长率？"
            ),
            assessment_focus="把图像中的比较结果与分割、训练和观测对象联系起来。",
            needed_information=[
                "成像和分割过程、计数规则及其训练依据。",
                "Figure 3 中两种自动流程与人工计数在 D5、D7 的关系。",
                "上述观测能回答哪些问题，以及适用于其他测量目的时还缺什么。",
            ],
        )
    return KnowledgeQuestion(
        **common,
        question=(
            "文中 20× 成像结果依赖哪些光学和处理条件？若另一项目计划书仅写了 20×，"
            "能否据此确定该项目实际分辨率为 0.84 μm，或直接采用本文 tSIM 的分辨率结论？"
            "请解释论文中的相关数值分别表示什么，给出有科学价值的条件估算与适用判断。"
        ),
        conditions=["待讨论的另一项目仅给出放大倍率 20×，未提供其他光学参数。"],
        assessment_focus="保留原研究的对象、光学条件和计算含义，区分假设估算与项目实际性能。",
        needed_information=[
            "论文实际光学配置、波长数值的用途及图像处理方法。",
            "分辨率结果如何获得，适用于哪些采集与重建条件。",
            "仅已知另一个设备放大倍率时能做的判断及仍需的具体参数。",
        ],
    )


async def run(args):
    output = (args.output or ROOT / "runs/literature-local-20261014/change07" / args.case).resolve()
    output.mkdir(parents=True, exist_ok=False)
    runtime_config, evidence_config = probe_configs(
        args.runtime_config, args.evidence_config, output
    )
    evidence_config.literature.max_model_turns = MODEL_CALLS
    evidence_config.literature.max_actions = TASK_ACTIONS
    evidence_config.discovery.enabled = []
    evidence_config.browser_fallback = False
    manifest = {
        "change": "change07",
        "case": args.case,
        "limits": {
            "model_calls": MODEL_CALLS,
            "task_actions": TASK_ACTIONS,
            "max_seconds": MAX_SECONDS,
            "automatic_resampling": False,
            "external_search": False,
        },
        "runtime_config": runtime_config.model_dump(mode="json"),
        "evidence_config": evidence_config.model_dump(mode="json"),
    }
    write_json(output / "input.json", manifest)
    budget = Budget(
        Records(output),
        V4Config(
            max_model_calls=MODEL_CALLS,
            max_embedding_calls=64,
            max_seconds=MAX_SECONDS,
            completion_reserve_calls=0,
            completion_reserve_seconds=0,
        ),
        ReportingConfig(),
    )
    runtime = resources = literature = None
    try:
        runtime = AgentRuntime.from_config(
            runtime_config, model_factory=recorded_models(output, args.env_file, budget)
        )
        runtime.scheduler.request_budget = budget
        runtime.recorder = SubmissionRecorder(output, runtime_config.submission_max_bytes)
        resources = EvidenceResources.create(
            runtime, output, evidence_config, env_file=args.env_file
        )
        resources.store.discovery = None
        async with asyncio.timeout(budget.remaining_seconds()):
            doc, origin = (
                retained_organoid_paper(resources.store, args.paper_source)
                if args.case == "organoid-counting"
                else await fixed_tsim_paper(resources.store)
            )
            question = reading_question(args.case, doc)
            manifest.update(source=origin, question=question.model_dump(mode="json"))
            write_json(output / "input.json", manifest)
            literature = FixedPaperResearcher.from_resources(resources, env_file=args.env_file)
            result = await literature.investigate(question, single_submission=True)
        write_json(output / "answer.json", result)
        summary = {
            "case": args.case,
            "research_id": result.research_id,
            "status": result.task_result.status,
            "reason": result.task_result.reason,
            "model_calls": MODEL_CALLS - budget.remaining_calls,
            "output": str(output),
        }
        write_json(output / "result.json", summary)
        print(json.dumps(summary, ensure_ascii=False))
    except BaseException as exc:
        write_json(
            output / "result.json", {"status": "interrupted", "error_type": type(exc).__name__}
        )
        raise
    finally:
        budget.save()
        if runtime:
            write_json(output / "metrics.json", runtime.scheduler.metrics())
        if literature:
            await literature.close()
        if resources:
            await resources.close()
        if runtime:
            await runtime.close()


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--case", choices=("organoid-counting", "tsim-20x"), required=True)
    cli.add_argument("--output", type=Path)
    cli.add_argument(
        "--paper-source",
        type=Path,
        default=PLOS_RETAINED,
        help="Saved PLOS source directory with source.json, original and figures (organoid case).",
    )
    cli.add_argument("--runtime-config", type=Path, default=ROOT / "config/runtime.yaml")
    cli.add_argument("--evidence-config", type=Path, default=ROOT / "config/evidence.yaml")
    cli.add_argument("--env-file", type=Path, default=ROOT / ".env")
    return cli


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(parser().parse_args()))
