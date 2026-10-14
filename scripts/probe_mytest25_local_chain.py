"""Change 52 archive replay, one local evidence chain, and conditional model diagnosis."""

import argparse
import asyncio
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from probe_mytest25_interfaces import (
    HISTORICAL_ROOT,
    NETWORK_LIMITS,
    PreviewCaptured,
    ProbeSession,
    WireRecorder,
    capacity_for,
    historical_knowledge_question,
    implementation_hashes,
    parse_knowledge_question,
    question_fixture,
    runtime_config,
)
from probe_review_evidence import NetworkLedger, fixed_research_ids
from probe_review_quality import evaluation_context, file_hash, read

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.contracts import example_for
from spacebio_review.agents.diagnostics import SubmissionRecorder, digest
from spacebio_review.evidence import EvidenceResources, LiteratureResearcher, load_evidence_config
from spacebio_review.models.config import ModelConfig
from spacebio_review.models.scheduling import RequestScheduler, ScheduledModel
from spacebio_review.v4.assessment import assessment_contract, assessment_request
from spacebio_review.v4.assessment_input import ReviewArchive, freeze_review
from spacebio_review.v4.manuscript_call import ManuscriptCall, ManuscriptConfig
from spacebio_review.v4.records import Records, write_json
from spacebio_review.v4.report_authoring import author_materials

E_CASE = "E-q3-1"
F_CASES = tuple(f"F-q{q}-{model}" for q in (2, 6) for model in ("current", "strong"))
E_QUESTION = {
    "question": "按申请所述在轨与地面的小肠类器官比较，能够认识哪些空间环境相关变化？"
    "若进一步区分重力、辐射及培养条件的作用，已有研究的比较设计分别能支持什么解释？",
    "purpose": "method",
    "needed_information": [
        "实际在轨与地面类器官或相近细胞培养比较中，环境因素与操作条件的对应关系及解释范围",
        "分拆环境因素作用的已发表比较设计、实际观测与适用条件",
    ],
    "assessment_focus": "资料用于区分综合空间环境比较能得到的认识与单一因素解释所需的条件。",
}
F_QUESTIONS = {
    "q2": "依据申请实际描述的设备、镜像调试和在轨观测安排，哪些工作已有方案，"
    "哪些操作及数据可比条件仍未明确？这些材料能够支持怎样的专业判断？",
    "q6": "申请实际说明了怎样的返回处理与mtDNA分析安排？现有原文足以判断什么，"
    "保存安排尚未明确的部分应怎样影响当前分析及其确定程度？",
}
F_PROMPT = (
    "围绕给定局部专业问题，依据完整申请原文形成连贯专业分析。说明方案已给出的安排、"
    "可获得的认识、适用范围及真正未决的条件，以原文内容表达理由和确定程度。"
)


def chain_hashes():
    paths = [
        "scripts/probe_mytest25_local_chain.py",
        "scripts/probe_mytest25_professional.py",
        "src/spacebio_review/v4/assessment_input.py",
        "src/spacebio_review/v4/records.py",
        "src/spacebio_review/v4/research.py",
        "src/spacebio_review/v4/handoff.py",
        "src/spacebio_review/review/access.py",
    ]
    return {**implementation_hashes(), **{p: file_hash(p) for p in paths if Path(p).exists()}}


def downstream_preview(records, selected, application, evaluation, folder):
    """Use actual freeze, assessment request, and appendix selection without a model."""
    frozen = freeze_review(records, selected, application, evaluation)
    write_json(folder / "frozen-review.json", frozen)
    contract = assessment_contract(frozen)
    caller = ManuscriptCall(Records(folder), ManuscriptConfig.load())
    prompt, materials = assessment_request(frozen)
    request = caller.request(prompt, materials, contract, caller.config.assessment_tokens)
    write_json(folder / "assessment-request.json", request)
    synthetic = example_for(contract.schema())
    synthetic["appendix_topics"] = [
        {
            "title": "Offline interface selection",
            "focus": "Original research material handoff",
            "main_sections": [],
            "opinion_ids": list(frozen["materials"]["opinions"]),
        }
    ]
    selected_materials = author_materials(frozen, synthetic, ["topic_1"])
    write_json(
        folder / "appendix-selection.json",
        {
            "synthetic_topic_allocation_only": True,
            "scientific_prose_generated": False,
            "request_materials": selected_materials,
        },
    )
    provenance = frozen["provenance"]["opinions"]
    return {
        "selected_research": selected,
        "opinion_count": len(frozen["materials"]["opinions"]),
        "context_origins": dict(
            Counter(
                row.get("origin", "professional")
                for row in frozen["materials"]["opinion_contexts"].values()
            )
        ),
        "evidence_count": len(frozen["materials"]["evidence"]),
        "opinion_identities": provenance,
        "assessment_capacity": caller.preview(request),
        "appendix_opinion_count": len(selected_materials["materials"]["opinions"]),
        "model_requests": 0,
        "note": "Assembly success is not a judgment of scientific correctness or report quality.",
    }


def archive_replay(session):
    folder = session.output / "A/archive"
    if (folder / "result.json").exists():
        raise ValueError("archive_preview_already_exists")
    archive = ReviewArchive(HISTORICAL_ROOT)
    paths = list((HISTORICAL_ROOT / "v4").rglob("*.json"))
    for doc in archive.application():
        paths.extend(
            (
                HISTORICAL_ROOT / "sources" / doc["source_id"] / name
                for name in ("source.json", "original.md")
            )
        )
    files = {str(path.resolve()): file_hash(path) for path in paths}
    value = downstream_preview(
        archive, archive.selected(), archive.application(), evaluation_context(), folder
    )
    value.update(
        status="completed",
        source_hashes=files,
        sources_unchanged=all(file_hash(path) == expected for path, expected in files.items()),
        implementation_hashes=chain_hashes(),
    )
    write_json(folder / "result.json", value)
    return value


def diagnosis_request(case):
    if case not in F_CASES:
        raise ValueError("unknown_conditional_diagnosis_case")
    _, qid, tier = case.split("-")
    bundle, store, _, files = question_fixture("C2-1", Path("."))
    config = (
        runtime_config()
        .models["specialist" if tier == "current" else "strong"]
        .model_copy(
            update={
                "max_tokens": 8192,
                "retries": 0,
                "tools": False,
                "extra_body": {"enable_thinking": True},
                "parameters": {},
            }
        )
    )
    if config.request_protocol != "chat_completions":
        raise ValueError("F_requires_same_simple_text_protocol_for_both_models")
    materials = {
        "question": F_QUESTIONS[qid],
        "application": [
            {
                "source_id": source.source_id,
                "title": source.title,
                "passages": [
                    p.model_dump(mode="json") for p in store.documents[source.source_id].passages
                ],
            }
            for source in bundle.sources
        ],
    }
    wire = {
        "messages": [
            {"role": "system", "content": F_PROMPT + "\n" + evaluation_context()["prompt"]},
            {"role": "user", "content": json.dumps(materials, ensure_ascii=False)},
        ],
        "tools": None,
        "schema": None,
        "model": config.model_dump(mode="json"),
    }
    return {
        "case": case,
        "wire": wire,
        "capacity": capacity_for(wire),
        "source_hashes": files,
        "implementation_hashes": chain_hashes(),
    }


def diagnosis_preview(session):
    path = session.output / "F/preview.json"
    if path.exists():
        raise ValueError("F_preview_already_exists")
    value = {case: diagnosis_request(case) for case in F_CASES}
    write_json(path, value)
    return value


async def diagnosis_live(session, case, reason):
    if not reason:
        raise ValueError("F_requires_observed_D_or_E_scientific_misunderstanding")
    expected = read(session.output / "F/preview.json")[case]
    current = diagnosis_request(case)
    if digest(expected) != digest(current):
        raise ValueError("F_frozen_request_changed")
    folder = session.output / "F/cases" / case
    result_path = folder / "result.json"
    if result_path.exists():
        return {**read(result_path), "cache_or_local_replay": True, "new_requests": 0}
    wire = current["wire"]
    recorder = WireRecorder(folder, budget=session.budget, expected=[wire])
    model = recorder.factory()(ModelConfig.model_validate(wire["model"]))
    scheduler = RequestScheduler()
    value = {"case": case, "diagnostic_reason": reason, "request": current, "status": "started"}
    write_json(result_path, value)
    before = len(session.budget.data["requests"])
    try:
        with (
            session.budget.case(case, session.revision),
            scheduler.task(case, "professional-diagnosis", "local_diagnosis"),
        ):
            reply = await ScheduledModel(model, scheduler, "diagnosis").complete(wire["messages"])
        value.update(status="received", reply=reply.message, finish_reason=reply.finish_reason)
    except BaseException as exc:
        value.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        await model.close()
        value["new_requests"] = len(session.budget.data["requests"]) - before
        write_json(result_path, value)
        write_json(session.root / "usage.json", session.budget.report())
    return value


class ChainNetworkLedger(NetworkLedger):
    def reserve(self, kind, url, *, transport="http"):
        if sum(row["kind"] == kind for row in self.data["requests"]) >= NETWORK_LIMITS[kind]:
            from spacebio_review.models.provider import ModelError

            raise ModelError(
                "probe_external_" + kind + "_limit", category="budget", request_sent=False
            )
        parsed = urlsplit(str(url))
        self.data["requests"].append(
            {
                "kind": kind,
                "url": parsed.scheme + "://" + parsed.netloc + parsed.path,
                "case": self.case,
                "revision": self.revision,
                "transport": transport,
                "operation": self.scope.get()[1],
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        write_json(self.path, self.data)


def chain_fixture():
    path = HISTORICAL_ROOT / "knowledge/research-be4f307a838a/answer.json"
    original = read(path)["question"]
    question, files = historical_knowledge_question({**original, **E_QUESTION})
    files[str(path.resolve())] = file_hash(path)
    return {
        "question": question.model_dump(mode="json"),
        "source_hashes": files,
        "cache_snapshot": "New source, discovery and embedding caches. "
        "Historical knowledge answers are not reused.",
    }


def chain_configs(folder):
    config = runtime_config()
    if config.embedding:
        config.embedding.cache_dir = (folder / "embedding-cache").resolve()
    evidence = load_evidence_config("config/evidence.yaml")
    evidence.literature.max_model_turns = 16
    return config, evidence


def seed_application(store):
    """Give the new local source store the same original application as production."""
    _, historical, _, files = question_fixture("C2-1", store.root.parent)
    for sid, original in historical.documents.items():
        folder = store.root / sid
        if (folder / "source.json").exists():
            raise ValueError("probe_application_seed_already_exists")
        folder.mkdir(parents=True, exist_ok=True)
        local_original = folder / "original.md"
        shutil.copyfile(HISTORICAL_ROOT / "sources" / sid / "original.md", local_original)
        document = original.model_copy(
            deep=True,
            update={
                "original_path": str(local_original.resolve()),
                "normalized_path": str(local_original.resolve()),
            },
        )
        write_json(folder / "candidate.json", document.candidate.model_dump(mode="json"))
        write_json(folder / "source.json", document.model_dump(mode="json"))
    store.restore()
    return files


async def chain_preview(session):
    folder = session.output / "E/preview"
    if (folder / "result.json").exists():
        raise ValueError("E_preview_already_exists")
    item = chain_fixture()
    config, evidence = chain_configs(session.output / "E/case")
    recorder = WireRecorder(folder, preview=True)
    runtime = AgentRuntime.from_config(config, model_factory=recorder.factory())
    runtime.recorder = SubmissionRecorder(folder, config.submission_max_bytes)
    resources = EvidenceResources.create(runtime, folder, evidence)
    seed_application(resources.store)
    literature = LiteratureResearcher(
        runtime,
        resources.store,
        resources.discovery,
        config=evidence.literature,
        rerank=evidence.model_rerank,
    )
    try:
        with fixed_research_ids(E_CASE):
            await literature.investigate(
                parse_knowledge_question(item["question"]), single_submission=True
            )
    except PreviewCaptured:
        pass
    finally:
        await resources.close()
        await runtime.close()
    if len(recorder.wires) != 1:
        raise ValueError("E_preview_must_capture_exact_first_query_without_search")
    value = {
        **item,
        "status": "previewed",
        "first_wire": recorder.wires[0],
        "capacity": capacity_for(recorder.wires[0]),
        "runtime_config": config.model_dump(mode="json"),
        "evidence_config": evidence.model_dump(mode="json"),
        "implementation_hashes": chain_hashes(),
        "literature_requests": 16,
        "professional_formal_requests": 1,
    }
    write_json(folder / "result.json", value)
    return value


async def chain_live(session):
    from probe_mytest25_professional import professional_formal

    planned = read(session.output / "E/preview/result.json")
    item = chain_fixture()
    if (
        any(digest(item[key]) != digest(planned[key]) for key in item)
        or chain_hashes() != planned["implementation_hashes"]
    ):
        raise ValueError("E_material_or_implementation_changed")
    folder = session.output / "E/case"
    if (folder / "result.json").exists():
        raise ValueError("E_case_already_started_no_automatic_repetition")
    config, evidence = chain_configs(folder)
    if (
        config.model_dump(mode="json") != planned["runtime_config"]
        or evidence.model_dump(mode="json") != planned["evidence_config"]
    ):
        raise ValueError("E_configuration_changed_after_preview")
    recorder = WireRecorder(
        folder / "literature",
        budget=session.budget,
        expected=[planned["first_wire"]],
        allow_following=True,
    )
    runtime = AgentRuntime.from_config(config, model_factory=recorder.factory())
    runtime.recorder = SubmissionRecorder(folder, config.submission_max_bytes)
    runtime.scheduler.request_budget = session.budget
    resources = EvidenceResources.create(runtime, folder, evidence, env_file=".env")
    seed_application(resources.store)
    literature = LiteratureResearcher.from_resources(resources, env_file=".env")
    ledger = ChainNetworkLedger(session.root, E_CASE, session.revision)
    resources.store.client.event_hooks["request"].append(ledger.on_request)
    resources.discovery.search = ledger.wrap(
        resources.discovery.search,
        "discovery",
        cache_counter=lambda: resources.discovery.cache_hits,
    )
    resources.discovery.related = ledger.wrap(
        resources.discovery.related,
        "discovery",
        cache_counter=lambda: resources.discovery.cache_hits,
    )
    resources.store.acquire = ledger.wrap(resources.store.acquire, "acquisition")
    import spacebio_review.evidence.store as store_module

    render = store_module.render_public_page

    async def browser(url):
        ledger.reserve("acquisition", url, transport="browser-navigation")
        return await render(url)

    value = {"case": E_CASE, "status": "started", "fixture": item}
    write_json(folder / "result.json", value)
    before = len(session.budget.data["requests"])
    try:
        with session.budget.case(E_CASE, session.revision):
            with (
                fixed_research_ids(E_CASE),
                patch.object(store_module, "render_public_page", browser),
            ):
                run = await literature.investigate(
                    parse_knowledge_question(item["question"]), single_submission=True
                )
            write_json(folder / "knowledge-run.json", run.model_dump(mode="json"))
            value.update(
                literature_status=run.task_result.status, literature_reason=run.task_result.reason
            )
            if run.answer is None or run.task_result.status != "completed":
                value.update(
                    status="failed",
                    reason="literature_did_not_deliver_valid_answer",
                    professional_status="not_executed",
                )
                return value
            outcome = await professional_formal(
                session,
                E_CASE,
                knowledge_run=run,
                store=resources.store,
                folder=folder / "professional",
            )
            value["professional"] = {k: v for k, v in outcome.items() if k != "records"}
            if not outcome.get("research_ref"):
                value.update(status="failed", reason="professional_formal_did_not_deliver")
                return value
            _, application_store, _, _ = question_fixture("C2-1", folder)
            value["downstream"] = downstream_preview(
                outcome["records"],
                [outcome["research_ref"]],
                [doc.model_dump(mode="json") for doc in application_store.documents.values()],
                evaluation_context(),
                folder / "downstream",
            )
            value["status"] = "completed"
    except BaseException as exc:
        value.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        value["new_requests"] = len(session.budget.data["requests"]) - before
        value["sources_unchanged"] = all(
            file_hash(path) == expected for path, expected in item["source_hashes"].items()
        )
        write_json(folder / "result.json", value)
        write_json(folder / "metrics.json", runtime.scheduler.metrics())
        write_json(session.root / "usage.json", session.budget.report())
        await literature.close()
        await resources.close()
        await runtime.close()
    return value


async def main(args):
    with ProbeSession(args.output, args.revision) as session:
        if args.stage == "archive":
            result = archive_replay(session)
        elif args.stage == "E-preview":
            result = await chain_preview(session)
        elif args.stage == "E-run":
            result = await chain_live(session)
        elif args.stage == "F-preview":
            result = diagnosis_preview(session)
        else:
            result = []
            for case in args.cases or F_CASES:
                result.append(await diagnosis_live(session, case, args.reason))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("archive", "E-preview", "E-run", "F-preview", "F-run"))
    parser.add_argument(
        "--output", type=Path, default=Path("runs/probe-mytest25-interfaces-20261013")
    )
    parser.add_argument("--revision", default="initial")
    parser.add_argument("--cases", nargs="+", choices=F_CASES)
    parser.add_argument(
        "--reason", help="D/E observation that warrants the conditional fixed-material diagnosis"
    )
    asyncio.run(main(parser.parse_args()))
