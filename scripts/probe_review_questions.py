"""Change 47 group D: five fixed question-only samples, no preparation or research."""

import argparse
import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from probe_review_quality import LIMITS, ProbeBudget, file_hash, read, totals

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.diagnostics import SubmissionRecorder, digest
from spacebio_review.application import ApplicationBundle, ApplicationSource
from spacebio_review.evidence.documents import parse_document
from spacebio_review.evidence.types import SourceCandidate, SourceDocument
from spacebio_review.models.config import load_config
from spacebio_review.models.provider import model_class
from spacebio_review.review.materials import ApplicationReader
from spacebio_review.v4.config import V4Config
from spacebio_review.v4.execution import Execution
from spacebio_review.v4.questions import QUESTION_VERSION, QuestionGeneration, select_questions
from spacebio_review.v4.records import Records, write_json

CASES = ("D24-1", "D24-2", "D24-3", "D3-1", "D3-2")
EXPERT_FIXTURE = Path("tests/fixtures/review_quality_sample3_experts.json")


class FrozenStore:
    def __init__(self, documents):
        self.documents = documents

    def retained(self, source_id):
        return self.documents[source_id]

    def candidate(self, source_id):
        return self.documents[source_id].candidate


def fixture(case, folder):
    if case not in CASES:
        raise ValueError("unknown_D_case")
    if case.startswith("D24-"):
        root = Path("runs/mytest24")
        state_path = root / "v4/state.json"
        state = read(state_path)
        bundle = ApplicationBundle.model_validate(state["metadata"]["application"])
        docs, paths = {}, [state_path]
        for source in bundle.sources:
            path = root / "sources" / source.source_id / "source.json"
            docs[source.source_id] = SourceDocument.model_validate(read(path))
            paths.extend([path, path.with_name("original.md")])
        for path, expected in state["metadata"]["manifest"]["files"].items():
            if file_hash(path) != expected:
                raise ValueError("historical_original_changed:" + path)
            paths.append(Path(path))
        package = {"experts": deepcopy(state["package"]["experts"])}
    else:
        path = Path("data/sample3.md")
        sid = "probe-sample3-" + file_hash(path)[:12]
        document = parse_document(
            sid,
            SourceCandidate(title="sample3", kind="application"),
            path,
            folder / "parsed-original",
        )
        if document.availability != "full_text":
            raise ValueError("sample3_original_not_fully_read")
        docs = {sid: document}
        bundle = ApplicationBundle(
            sources=[
                ApplicationSource(
                    source_id=sid,
                    title="sample3",
                    role="main",
                    availability="full_text",
                    sections=list(dict.fromkeys(p.section for p in document.passages)),
                )
            ]
        )
        package = {"experts": read(EXPERT_FIXTURE)}
        paths = [path, EXPERT_FIXTURE]
    files = {str(path.resolve()): file_hash(path) for path in paths}
    return bundle, FrozenStore(docs), package, files


class PreviewCaptured(BaseException):
    pass


async def run_case(output, budget_root, case, *, live=False, revision="initial"):
    preview_folder = output / "D/preview" / case
    folder = output / "D/cases" / case if live else preview_folder
    if not live and (folder / "result.json").exists():
        raise ValueError("preserve_previous_preview_use_an_explicit_revision")
    bundle, store, package, files = fixture(case, folder)
    planned = read(preview_folder / "result.json") if live else None
    expected_wire = read(preview_folder / "wire.json") if live else None
    captured = {}
    budget = ProbeBudget(budget_root, "D", case, revision) if live else None

    class Model:
        def __init__(self, config):
            self.config, self.inner = config, None

        async def complete(self, messages, tools=None, *, response_schema=None):
            if captured:
                raise ValueError("one_request_per_question_sample")
            if tools or self.config.retries:
                raise ValueError("D_probe_requires_no_tools_and_zero_retries")
            captured.update(
                messages=messages, schema=response_schema, model=self.config.model_dump(mode="json")
            )
            write_json(folder / "wire.json", captured)
            if not live:
                raise PreviewCaptured()
            if digest(captured) != digest(expected_wire):
                raise ValueError("question_request_changed_after_preview")
            self.inner = model_class(self.config)(self.config, env_file=".env", budget=budget)
            return await self.inner.complete(messages, tools, response_schema=response_schema)

        async def close(self):
            if self.inner:
                await self.inner.close()

    runtime = AgentRuntime.from_config(load_config("config/runtime.yaml"), model_factory=Model)
    runtime.recorder = SubmissionRecorder(folder)
    ex = Execution(runtime, Records(folder), V4Config(max_questions=10), {})
    preparation = SimpleNamespace(ex=ex, reader=lambda: ApplicationReader(bundle, store, runtime))
    generation = QuestionGeneration(preparation, package)
    value = {
        "case": case,
        "live": live,
        "version": QUESTION_VERSION,
        "source_hashes": files,
        "capacity": generation.preview(),
        "fixture_digest": digest({"package": package, "sources": generation.sources}),
        "runtime_config_hash": file_hash("config/runtime.yaml"),
    }
    if planned and (
        planned["fixture_digest"] != value["fixture_digest"]
        or planned["source_hashes"] != files
        or planned["runtime_config_hash"] != value["runtime_config_hash"]
    ):
        raise ValueError("question_fixture_changed_after_preview")
    write_json(
        folder / "fixture.json",
        {
            "package": package,
            "application": bundle.model_dump(mode="json"),
            "documents": {sid: doc.model_dump(mode="json") for sid, doc in store.documents.items()},
            "source_hashes": files,
            "expert_fixture_note": "sample3 uses a manually fixed discipline/method profile; "
            "no team model was called. mytest24 uses its originally activated experts unchanged.",
        },
    )
    before = len(budget.data["requests"]) if budget else 0
    attempt = {"revision": revision, "live": live}
    try:
        if not value["capacity"]["fits"]:
            raise ValueError("question_context_exceeds_capacity")
        try:
            batch = await generation.run()
        except PreviewCaptured:
            attempt["status"] = "previewed"
            value["wire_digest"] = digest(captured)
        else:
            if not live:
                raise ValueError("preview_unexpectedly_completed_without_model_capture")
            write_json(folder / "questions.json", batch)
            selected = select_questions(batch, 10)
            write_json(folder / "selected-questions.json", selected)
            value.update(
                batch=ex.records.put("question-batch", batch),
                generated=len(batch["questions"]),
                selected=len(selected),
            )
            attempt["status"] = "completed"
    except Exception as exc:
        attempt.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        await runtime.close()
        value["sources_unchanged"] = all(file_hash(path) == hash_ for path, hash_ in files.items())
        attempt["new_requests"] = len(budget.data["requests"]) - before if budget else 0
        attempt["cache_or_local_replay"] = live and attempt["new_requests"] == 0
        result_path = folder / "result.json"
        previous = read(result_path) if result_path.exists() else {}
        value["attempts"] = [*previous.get("attempts", []), attempt]
        value["status"] = attempt.get("status", "interrupted")
        write_json(result_path, value)
        if budget:
            budget.save()
            write_json(budget_root / "usage.json", budget.report())
    return value


async def preview(output, budget_root):
    matrix_path = output / "D/matrix-preview.json"
    if matrix_path.exists():
        raise ValueError("preserve_previous_preview_use_an_explicit_revision")
    results = [await run_case(output, budget_root, case) for case in CASES]
    ledger = budget_root / "v4/budget.json"
    spent = read(ledger)["requests"] if ledger.exists() else []
    prior = totals([row for row in spent if row.get("probe", {}).get("group") == "D"])
    estimate = sum(row["capacity"]["input_tokens"] for row in results)
    matrix = {
        "group": "D",
        "version": QUESTION_VERSION,
        "limits": LIMITS["D"],
        "cases": results,
        "prior_usage": prior,
        "input_forecast": estimate,
        "output_capacity_sum": sum(
            read(output / "D/preview" / case / "wire.json")["model"]["max_tokens"] for case in CASES
        ),
        "output_note": "Per-call capacities are not predicted usage. Shared ledger reserves "
        "each request and stops before exceeding the group cap.",
    }
    matrix["fits"] = (
        all(row["status"] == "previewed" and row["capacity"]["fits"] for row in results)
        and estimate + prior["accounted"]["input"] <= LIMITS["D"]["input"]
        and len(CASES) + prior["requests"] <= LIMITS["D"]["requests"]
    )
    write_json(matrix_path, matrix)
    return matrix


async def main(args):
    root = args.output.resolve()
    if Path(args.revision).name != args.revision or args.revision in {".", ".."}:
        raise ValueError("revision_must_be_a_single_directory_name")
    output = root / "revisions" / args.revision
    if args.stage == "preview":
        result = await preview(output, root)
        print(
            json.dumps(
                {key: result[key] for key in ("fits", "input_forecast", "output_capacity_sum")},
                ensure_ascii=False,
            )
        )
    else:
        if not read(output / "D/matrix-preview.json")["fits"]:
            raise ValueError("D_matrix_does_not_fit")
        result = await run_case(output, root, args.case, live=True, revision=args.revision)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("preview", "question"))
    parser.add_argument("--output", type=Path, default=Path("runs/probe-review-quality-20261013"))
    parser.add_argument("--revision", default="initial")
    parser.add_argument("--case", choices=CASES, default="D24-1")
    asyncio.run(main(parser.parse_args()))
