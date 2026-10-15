"""Local question-generation acceptance. Never invokes research, synthesis or delivery."""

import argparse
import asyncio
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.diagnostics import SubmissionRecorder, digest
from spacebio_review.application import ApplicationBundle, ApplicationSource
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument
from spacebio_review.models.config import load_config
from spacebio_review.models.provider import model_class
from spacebio_review.review.evaluation import GENERAL
from spacebio_review.review.materials import ApplicationReader
from spacebio_review.review.project import load_preparation_config
from spacebio_review.v4.budget import Budget
from spacebio_review.v4.config import ReportingConfig, V4Config
from spacebio_review.v4.coordinator import Coordinator
from spacebio_review.v4.execution import Execution
from spacebio_review.v4.questions import QUESTION_VERSION, QuestionGeneration
from spacebio_review.v4.records import Records, write_json

CASES = ("K", "A1", "A2", "A3", "A-small", "B")
LIMITS = {"requests": 6, "input": 120000, "output": 100000}


def read_json(path):
    return json.loads(Path(path).read_text("utf-8"))


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FrozenStore:
    def __init__(self, documents):
        self.documents = documents

    def retained(self, source_id):
        return self.documents[source_id]

    def candidate(self, source_id):
        return self.documents[source_id].candidate


def fixture(case):
    if case == "K":
        doc = SourceDocument(
            source_id="protocol-fixture",
            candidate=SourceCandidate(title="Measurement proposal"),
            passages=[
                Passage(
                    passage_id="p1",
                    section="Method",
                    locator="Method",
                    text="项目拟研究温度变化，预期变化时间尺度约二十秒，采样间隔两秒；需评估可辨认的动态范围。",
                )
            ],
        )
        bundle = ApplicationBundle(
            sources=[
                ApplicationSource(
                    source_id=doc.source_id,
                    title=doc.candidate.title,
                    role="main",
                    availability="full_text",
                    sections=["Method"],
                )
            ]
        )
        package = {
            "project": {
                "title": "Measurement proposal",
                "objective": "测量温度动态范围",
                "paths": [],
                "understanding_limits": [],
                "evidence": {},
            },
            "experts": {
                "expert-1": {
                    "purpose": "分析仪器测量能力",
                    "profile_labels": ["计量与仪器"],
                    "method_skill_ids": [],
                    "skills": [],
                    "model_tier": "normal",
                }
            },
        }
        return bundle, FrozenStore({doc.source_id: doc}), package, {}
    root = Path("runs/mytest20" if case == "B" else "runs/mytest22").resolve()
    path = root / "v4/state.json"
    state = read_json(path)
    original = state["package"]
    bundle = ApplicationBundle.model_validate(state["metadata"]["application"])
    files = {str(path): file_hash(path)}
    docs = {}
    for source in bundle.sources:
        source_path = root / "sources" / source.source_id / "source.json"
        files[str(source_path)] = file_hash(source_path)
        docs[source.source_id] = SourceDocument.model_validate(read_json(source_path))
        original_path = source_path.with_name("original.md")
        files[str(original_path)] = file_hash(original_path)
    package = {
        "project": {
            k: deepcopy(original["project"][k])
            for k in (
                "title",
                "objective",
                "project_objective",
                "paths",
                "understanding_limits",
                "evidence",
            )
            if k in original["project"]
        },
        "experts": {
            eid: {
                k: deepcopy(expert[k])
                for k in (
                    "purpose",
                    "profile_labels",
                    "profile_ids",
                    "method_skill_ids",
                    "skills",
                    "model_tier",
                )
                if k in expert
            }
            for eid, expert in original["experts"].items()
            if case != "A-small" or eid in {"expert-1", "expert-3", "expert-4"}
        },
    }
    return bundle, FrozenStore(docs), package, files


def prepared(ex, bundle, store):
    prep = SimpleNamespace(ex=ex, reader=lambda: ApplicationReader(bundle, store, ex.runtime))
    prep.questions = lambda value: QuestionGeneration(prep, value)
    return prep


class PreviewCaptured(BaseException):
    pass


async def dispatch_fixture(output, package, batch):
    results = {}
    for limit in dict.fromkeys((1, 3, 10, len(batch["questions"]) + 1)):
        records = Records(output / f"selection-{limit}")
        ex = SimpleNamespace(
            records=records,
            system=GENERAL,
            evaluation={},
            configuration="dispatch-fixture",
            config=V4Config(max_questions=limit),
        )
        c = Coordinator(ex)
        c._state["package"] = deepcopy(package)
        c._state["question_batch"] = records.put("question-batch", batch)
        c.save()
        prep = SimpleNamespace(
            questions=lambda _: (_ for _ in ()).throw(AssertionError("no generation"))
        )
        assert await c.prepare_questions(prep)
        calls = []

        async def run(task, **_):
            calls.append(task)
            return records.put(
                "dispatch-fixture",
                {
                    "author": task["expert_id"],
                    "followups": [],
                    "blocks": [],
                    "scope_outcomes": [],
                    "not_a_scientific_assessment": True,
                },
            )

        await c.research(SimpleNamespace(run=run))
        first = len(calls)
        await Coordinator(ex).research(SimpleNamespace(run=run))
        assert len(calls) == first == min(limit, len(batch["questions"]))
        assert [next(iter(row["members"])) for row in calls] == [
            f"q{i}" for i in range(1, first + 1)
        ]
        results[str(limit)] = {
            "tasks": first,
            "resume_tasks": len(calls) - first,
            "experts": [row["expert_id"] for row in calls],
        }
    write_json(output / "dispatch.json", results)
    return results


async def run_case(output, case, *, live=False, budget=None):
    bundle, store, package, files = fixture(case)
    preview_root = output / f"preview-{QUESTION_VERSION}"
    folder = (
        output / "cases" / (case if case == "K" else f"{QUESTION_VERSION}/{case}")
        if live
        else preview_root / case
    )
    if live and folder.exists():
        raise ValueError("case_already_started_no_automatic_resampling")
    captured = {}
    expected_wire = read_json(preview_root / case / "wire.json") if live else None

    class Model:
        def __init__(self, config):
            self.config, self.inner = config, None

        async def complete(self, messages, tools=None, *, response_schema=None):
            if captured:
                raise ValueError("one_request_per_case")
            captured.update(
                messages=messages, schema=response_schema, model=self.config.model_dump(mode="json")
            )
            write_json(folder / "wire.json", captured)
            if not live:
                raise PreviewCaptured()
            if digest(captured) != digest(expected_wire):
                raise ValueError("wire_changed_after_preview")
            if self.config.request_protocol != "messages":
                raise ValueError("unexpected_probe_protocol")
            self.inner = model_class(self.config)(self.config, env_file=".env", budget=budget)
            return await self.inner.complete(messages, tools, response_schema=response_schema)

        async def close(self):
            if self.inner:
                await self.inner.close()

    runtime = AgentRuntime.from_config(load_config("config/runtime.yaml"), model_factory=Model)
    runtime.recorder = SubmissionRecorder(folder)
    evaluation = load_preparation_config("config/preparation.yaml").evaluation.prompt()
    ex = Execution(runtime, Records(folder), V4Config(max_questions=10), {"prompt": evaluation})
    prep = prepared(ex, bundle, store)
    generation = prep.questions(package)
    result = {
        "case": case,
        "live": live,
        "version": QUESTION_VERSION,
        "sources": files,
        "capacity": generation.preview(),
        "fixture_digest": digest({"package": package, "sources": generation.sources}),
    }
    write_json(
        folder / "fixture.json",
        {"package": package, "application": bundle.model_dump(mode="json"), "files": files},
    )
    try:
        if not result["capacity"]["fits"]:
            raise ValueError("question_context_exceeds_capacity")
        if live:
            planned = read_json(preview_root / case / "result.json")
            if planned["fixture_digest"] != result["fixture_digest"]:
                raise ValueError("fixture_changed_after_preview")
            before = len(budget.data["requests"])
            batch = await generation.run()
            result.update(
                status="completed",
                generated=len(batch["questions"]),
                batch=ex.records.put("question-batch", batch),
                requests=len(budget.data["requests"]) - before,
            )
            write_json(folder / "questions.json", batch)
            result["dispatch"] = await dispatch_fixture(folder, package, batch)
        else:
            try:
                await generation.run()
            except PreviewCaptured:
                result["status"] = "previewed"
            result["wire_digest"] = digest(captured)
    except Exception as exc:
        result.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
        if live:
            raise
    finally:
        result["sources_unchanged"] = all(file_hash(path) == value for path, value in files.items())
        write_json(folder / "result.json", result)
        await runtime.close()
    return result


async def main(args):
    output = Path(args.output).resolve()
    if args.preview:
        results = [await run_case(output, case) for case in CASES]
        protocol = output / "cases/K/result.json"
        reuse_protocol = protocol.exists() and read_json(protocol)["status"] == "completed"
        remaining = [row for row in results if not (reuse_protocol and row["case"] == "K")]
        ledger = output / "v4/budget.json"
        spent = read_json(ledger)["requests"] if ledger.exists() else []
        estimate = sum(row["capacity"]["input_tokens"] for row in remaining) + sum(
            row["input"] for row in spent
        )
        matrix = {
            "limits": LIMITS,
            "cases": results,
            "estimated_input": estimate,
            "estimated_output": len(remaining) * 16384 + sum(row["output"] for row in spent),
            "estimated_requests": len(spent) + len(remaining),
            "reused_protocol": str(protocol) if reuse_protocol else None,
            "runtime_config_hash": file_hash("config/runtime.yaml"),
        }
        matrix["fits"] = (
            all(row["status"] == "previewed" for row in results)
            and estimate <= LIMITS["input"]
            and matrix["estimated_output"] <= LIMITS["output"]
            and matrix["estimated_requests"] <= LIMITS["requests"]
        )
        write_json(output / f"matrix-{QUESTION_VERSION}.json", matrix)
        print(
            json.dumps(
                {
                    "fits": matrix["fits"],
                    "input": estimate,
                    "output_reserve": matrix["estimated_output"],
                }
            )
        )
        return
    if not args.live or args.case not in CASES:
        raise ValueError("use --preview or --live --case NAME")
    matrix = read_json(output / f"matrix-{QUESTION_VERSION}.json")
    if not matrix["fits"] or matrix["runtime_config_hash"] != file_hash("config/runtime.yaml"):
        raise ValueError("matrix_must_fit_and_remain_frozen")
    if args.case != "K" and read_json(output / "cases/K/result.json")["status"] != "completed":
        raise ValueError("protocol_probe_required")
    config = V4Config(
        max_model_calls=LIMITS["requests"],
        max_input_tokens=LIMITS["input"],
        max_output_tokens=LIMITS["output"],
        completion_reserve_calls=0,
        completion_reserve_seconds=0,
        max_seconds=14400,
    )
    budget = Budget(Records(output), config, ReportingConfig())
    try:
        result = await run_case(output, args.case, live=True, budget=budget)
        print(
            json.dumps(
                {k: result[k] for k in ("case", "status", "generated", "requests")},
                ensure_ascii=False,
            )
        )
    finally:
        budget.save()
        totals = {k: sum(row[k] for row in budget.data["requests"]) for k in ("input", "output")}
        write_json(
            output / "requests.json", {"requests": budget.data["requests"], "totals": totals}
        )
        print(json.dumps({"requests": len(budget.data["requests"]), **totals}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--case", choices=CASES)
    asyncio.run(main(parser.parse_args()))
