"""Change 47 group B: four isolated literature handoffs, never an expert review."""

import argparse
import asyncio
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from itertools import count
from pathlib import Path
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from probe_review_quality import ProbeBudget, file_hash, read
from probe_review_quality import fixture as review_fixture

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.context import TokenEstimator, request_capacity
from spacebio_review.agents.diagnostics import SubmissionRecorder, digest
from spacebio_review.evidence import EvidenceResources, LiteratureResearcher, load_evidence_config
from spacebio_review.evidence.knowledge import KnowledgeQuestion, ReviewKnowledgeQuestion
from spacebio_review.models.config import load_config
from spacebio_review.models.provider import ModelError, model_class
from spacebio_review.review.access import ReviewAccess
from spacebio_review.v4.handoff import literature_question
from spacebio_review.v4.records import write_json
from spacebio_review.v4.research import LiteratureRequest

CASES = ("B-method-1", "B-method-2", "B-platform-1", "B-platform-2")
NETWORK_CAPS = {"discovery": 40, "acquisition": 40}
METHOD = {
    "question": "申请使用 DCF、Fluo-8 等荧光读出，"
    "能在什么对象和实验条件下说明 ROS、钙及其线粒体来源？",
    "purpose": "method",
    "needed_information": [
        "DCF、Fluo-8 直接响应的对象、定位能力和实验条件，原始方法或官方方法资料中的依据",
        "这些读出与申请拟解释的线粒体 ROS、钙信号之间的对应范围",
    ],
    "changes_judgment_if": "资料决定申请实际读出能承担哪些线粒体特异解释，以及仍需要哪些限定。",
    "evidence_handles": ["E4", "E14", "E20", "E21"],
}
PLATFORM = {
    "question": "目标中国空间站已有的显微成像、细胞培养及相关实际实验能力是什么，"
    "与申请所述实验活动怎样对应？",
    "purpose": "method",
    "needed_information": [
        "中国空间站平台和实际装置的显微成像、细胞培养能力的一手资料及实际实验先例",
        "公开平台能力的范围和条件，以及申请自身已说明的设备与资源安排",
    ],
    "changes_judgment_if": "目标平台资料帮助判断哪些实验活动已有相关能力，哪些项目配置仍待明确。",
    "evidence_handles": ["E6", "E12", "E13", "E20", "E21"],
}


def case_fixture(case):
    if case not in CASES:
        raise ValueError("unknown_B_case")
    frozen, files = review_fixture("A24-1")
    request = LiteratureRequest.model_validate(METHOD if "method" in case else PLATFORM)
    evidence = {
        key: frozen["provenance"]["evidence"][key][0]["record"] for key in request.evidence_handles
    }
    base = KnowledgeQuestion(
        question=request.question,
        background=frozen["materials"]["evidence"]["E4"]["quote"],
        cutoff_date=frozen["evaluation"]["configuration"]["historical_as_of"],
    )
    question = literature_question(request, base, evidence)
    return {
        "request": request.model_dump(mode="json"),
        "question": question.model_dump(mode="json"),
        "originals": evidence,
        "source_hashes": files,
    }


def configs(case_dir):
    runtime = load_config("config/runtime.yaml")
    for model in runtime.models.values():
        model.retries = 0
    for route in runtime.task_routes.values():
        route.retries = 0
    if runtime.embedding:
        runtime.embedding.retries = 0
        runtime.embedding.cache_dir = (case_dir / "embedding-cache").resolve()
    evidence = load_evidence_config("config/evidence.yaml")
    return runtime, evidence


def implementation_hashes():
    paths = [
        "scripts/probe_review_evidence.py",
        "src/spacebio_review/v4/handoff.py",
        "src/spacebio_review/v4/research.py",
        "src/spacebio_review/review/access.py",
        "src/spacebio_review/evidence/literature.py",
        "src/spacebio_review/evidence/knowledge.py",
        "src/spacebio_review/evidence/submissions.py",
        "src/spacebio_review/agents/runtime.py",
        "skills/literature-understanding/SKILL.md",
    ]
    return {path: file_hash(path) for path in paths}


@contextmanager
def fixed_research_ids(case):
    sequence = count()
    with patch(
        "spacebio_review.evidence.literature.uuid4",
        side_effect=lambda: uuid5(NAMESPACE_URL, f"review-evidence-probe/{case}/{next(sequence)}"),
    ):
        yield


class QueryPreviewCaptured(BaseException):
    pass


def probe_models(captured, *, budget=None, expected=None, preview_only=False):
    class Model:
        def __init__(self, config):
            self.config, self.inner = config, None

        async def complete(self, messages, tools=None, *, response_schema=None):
            if not captured:
                captured.update(
                    messages=messages,
                    tools=tools,
                    schema=response_schema,
                    model=self.config.model_dump(mode="json"),
                )
                if expected is not None and digest(captured) != digest(expected):
                    raise ValueError("B_first_query_wire_changed_after_preview")
                if preview_only:
                    raise QueryPreviewCaptured()
            if self.inner is None:
                self.inner = model_class(self.config)(self.config, env_file=".env", budget=budget)
            return await self.inner.complete(messages, tools, response_schema=response_schema)

        async def close(self):
            if self.inner:
                await self.inner.close()

    return Model


async def query_preview(item, runtime_config, evidence_config, folder, case):
    captured = {}
    runtime = AgentRuntime.from_config(
        runtime_config, model_factory=probe_models(captured, preview_only=True)
    )
    runtime.recorder = SubmissionRecorder(folder, runtime_config.submission_max_bytes)
    resources = EvidenceResources.create(runtime, folder, evidence_config)
    literature = LiteratureResearcher(
        runtime,
        resources.store,
        resources.discovery,
        config=evidence_config.literature,
        rerank=evidence_config.model_rerank,
    )
    try:
        with fixed_research_ids(case):
            await literature.investigate(
                ReviewKnowledgeQuestion.model_validate(item["question"]), single_submission=True
            )
    except QueryPreviewCaptured:
        pass
    finally:
        await resources.close()
        await runtime.close()
    if not captured:
        raise ValueError("B_query_preview_did_not_reach_request")
    model = runtime_config.routed_model("literature")
    estimate = TokenEstimator(None, model.image_tokens).measure(
        captured["messages"],
        captured["tools"] or [],
        response_schema=captured["schema"],
        output_reserve=model.max_tokens,
    )
    return {"request": captured, "capacity": request_capacity(estimate, model.context_window)}


def preview(output):
    target = output / "evidence-preview.json"
    if target.exists() or (output / "evidence-cases").exists():
        raise ValueError("preserve_previous_B_preview_use_an_explicit_revision")
    cases = {}
    for case in CASES:
        runtime, evidence = configs(output / "evidence-cases" / case)
        item = case_fixture(case)
        cases[case] = {
            **item,
            "runtime_config": runtime.model_dump(mode="json"),
            "evidence_config": evidence.model_dump(mode="json"),
            "model_routes": {
                kind + ("_formal" if formal else ""): runtime.routed_model(
                    kind, formal=formal
                ).model_dump(mode="json")
                for kind, formal in (
                    ("query", True),
                    ("literature", False),
                    ("literature", True),
                    ("rerank", False),
                )
            },
            "question_input_estimate": TokenEstimator().measure(
                [
                    {
                        "role": "user",
                        "content": json.dumps(item["question"], ensure_ascii=False),
                    }
                ]
            )["input_tokens"],
            "first_query": asyncio.run(
                query_preview(
                    item, runtime, evidence, output / "evidence-query-preview" / case, case
                )
            ),
        }
    value = {
        "group": "B",
        "cases": cases,
        "implementation": implementation_hashes(),
        "model_limits_per_case": {"requests": 10, "input": 120000, "output": 40000},
        "network_limits": NETWORK_CAPS,
        "cache_snapshot": "Empty source, discovery and embedding caches for each independent case.",
        "preview_note": "First query records exact production messages/schema with a "
        "probe-scoped deterministic task ID. Later research depends on tool results; those "
        "requests are unknown here and reserved individually before dispatch.",
    }
    write_json(target, value)
    return {
        "group": "B",
        "cases": {
            k: {
                "question_input_estimate": v["question_input_estimate"],
                "formal_output_capacity": v["model_routes"]["literature_formal"]["max_tokens"],
                "first_query_capacity": v["first_query"]["capacity"],
            }
            for k, v in cases.items()
        },
        "model_limits_per_case": value["model_limits_per_case"],
        "network_limits": NETWORK_CAPS,
    }


class NetworkLedger:
    """Probe-only external-request accounting, including failed HTTP attempts and redirects."""

    def __init__(self, root, case, revision):
        self.path = root / "evidence-network.json"
        self.data = read(self.path) if self.path.exists() else {"requests": [], "operations": []}
        self.case, self.revision = case, revision
        self.scope = ContextVar("probe_network_scope", default=("acquisition", None))

    def reserve(self, kind, url, *, transport="http"):
        if sum(r["kind"] == kind for r in self.data["requests"]) >= NETWORK_CAPS[kind]:
            raise ModelError(
                "probe_external_" + kind + "_limit", category="budget", request_sent=False
            )
        from urllib.parse import urlsplit

        parsed = urlsplit(str(url))
        row = {
            "kind": kind,
            "url": parsed.scheme + "://" + parsed.netloc + parsed.path,
            "case": self.case,
            "revision": self.revision,
            "transport": transport,
            "operation": self.scope.get()[1],
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self.data["requests"].append(row)
        write_json(self.path, self.data)

    async def on_request(self, request):
        self.reserve(self.scope.get()[0], request.url)

    def wrap(self, handler, kind, *, cache_counter=None):
        async def invoke(*args, **kwargs):
            operation = f"{self.revision}/{self.case}/{len(self.data['operations'])}"
            row = {"id": operation, "kind": kind, "status": "started"}
            self.data["operations"].append(row)
            before = cache_counter() if cache_counter else None
            token = self.scope.set((kind, operation))
            try:
                result = await handler(*args, **kwargs)
                row["status"] = "completed"
                return result
            except BaseException:
                row["status"] = "failed"
                raise
            finally:
                self.scope.reset(token)
                row["network_requests"] = sum(
                    r["operation"] == operation for r in self.data["requests"]
                )
                row["cache_hit"] = (
                    cache_counter() > before if cache_counter else row["network_requests"] == 0
                )
                write_json(self.path, self.data)

        return invoke


async def live(output, root, case, *, revision="initial"):
    planned = read(output / "evidence-preview.json")
    item = planned["cases"][case]
    current = case_fixture(case)
    if any(digest(current[k]) != digest(item[k]) for k in current):
        raise ValueError("B_original_or_request_changed_after_preview")
    if implementation_hashes() != planned["implementation"]:
        raise ValueError("B_implementation_changed_after_preview")
    folder = output / "evidence-cases" / case
    if folder.exists():
        raise ValueError("B_case_already_started_no_automatic_repetition")
    runtime_config, evidence_config = configs(folder)
    if (
        runtime_config.model_dump(mode="json") != item["runtime_config"]
        or evidence_config.model_dump(mode="json") != item["evidence_config"]
    ):
        raise ValueError("B_configuration_changed_after_preview")
    write_json(folder / "attempt.json", {"case": case, "status": "started"})
    budget = ProbeBudget(root, "B", case, revision)
    first_query = {}
    runtime = AgentRuntime.from_config(
        runtime_config,
        model_factory=probe_models(
            first_query, budget=budget, expected=item["first_query"]["request"]
        ),
    )
    runtime.recorder = SubmissionRecorder(folder, runtime_config.submission_max_bytes)
    runtime.scheduler.request_budget = budget
    resources = EvidenceResources.create(runtime, folder, evidence_config, env_file=".env")
    ledger = NetworkLedger(root, case, revision)
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

    render_page = store_module.render_public_page

    async def counted_browser(url):
        ledger.reserve("acquisition", url, transport="browser-navigation")
        return await render_page(url)

    store_module.render_public_page = counted_browser
    literature = LiteratureResearcher.from_resources(resources, env_file=".env")
    result = {
        "case": case,
        "revision": revision,
        "input": current,
        "cache_snapshot": planned["cache_snapshot"],
    }
    try:
        question = literature_question(
            LiteratureRequest.model_validate(item["request"]),
            ReviewKnowledgeQuestion.model_validate(item["question"]),
            item["originals"],
        )
        with fixed_research_ids(case), runtime.scheduler.research_calls(case, 10, 0):
            run = await literature.investigate(question, single_submission=True)
        access = ReviewAccess(resources.store, None, cutoff=question.cutoff_date)
        answer = access.register_knowledge(run)
        result.update(
            status=run.task_result.status,
            reason=run.task_result.reason,
            research_id=run.research_id,
            answer=answer,
            parent_reading=access.reading_records(),
            child_reading=run.sources,
            limitations=run.limitations,
            reused=run.reused,
        )
    except BaseException as exc:
        result.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        store_module.render_public_page = render_page
        write_json(folder / "first-query.json", first_query)
        write_json(folder / "attempt.json", result)
        write_json(folder / "metrics.json", runtime.scheduler.metrics())
        write_json(root / "usage.json", budget.report())
        await literature.close()
        await resources.close()
        await runtime.close()
    print(
        json.dumps(
            {
                "case": case,
                "status": result["status"],
                "reason": result["reason"],
                "usage": budget.report()["groups"]["B"],
            },
            ensure_ascii=False,
        )
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("preview", "research"))
    parser.add_argument("--output", type=Path, default=Path("runs/probe-review-quality-20261013"))
    parser.add_argument("--case", choices=CASES, default=CASES[0])
    parser.add_argument("--revision", default="initial")
    args = parser.parse_args()
    if Path(args.revision).name != args.revision or args.revision in {".", ".."}:
        raise ValueError("revision_must_be_a_single_directory_name")
    root = args.output.resolve()
    output = root / "revisions" / args.revision
    if args.stage == "preview":
        print(json.dumps(preview(output), ensure_ascii=False, indent=2))
    else:
        asyncio.run(live(output, root, args.case, revision=args.revision))


if __name__ == "__main__":
    main()
