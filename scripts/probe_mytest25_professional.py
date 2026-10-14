"""Change 52 D observations and the single professional submission shared with E."""

import argparse
import asyncio
import json
from contextlib import nullcontext
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from probe_mytest25_interfaces import (
    HISTORICAL_ROOT,
    KNOWLEDGE_CASES,
    PreviewCaptured,
    ProbeSession,
    WireRecorder,
    capacity_for,
    file_hash,
    historical_knowledge_question,
    implementation_hashes,
    parse_knowledge_question,
    question_fixture,
    read,
    runtime_config,
)

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.diagnostics import SubmissionRecorder, digest
from spacebio_review.agents.types import TaskResult
from spacebio_review.context import product_material
from spacebio_review.evidence.knowledge import KnowledgeAnswer, KnowledgeRun
from spacebio_review.evidence.types import SourceDocument
from spacebio_review.review.project import load_preparation_config
from spacebio_review.tools import ToolRegistry
from spacebio_review.v4.assessment_input import build_evaluation_context
from spacebio_review.v4.config import load_config
from spacebio_review.v4.execution import Execution
from spacebio_review.v4.preparation import Preparation
from spacebio_review.v4.questions import research_task
from spacebio_review.v4.records import Records, write_json
from spacebio_review.v4.research import LiteratureRequest, Research

CASES = tuple(f"D1-q{q}-{n}" for q in (1, 2) for n in (1, 2)) + tuple(
    f"D2-q{q}-{n}" for q in (6, 4) for n in (1, 2)
)


class ObservationStopped(BaseException):
    def __init__(self, reason, response=None):
        super().__init__(reason)
        self.response = response


class RetainedStore:
    """Only documents already saved in the historical run can be read in D."""

    def __init__(self, documents, files):
        self.documents, self.files = dict(documents), files

    def retained(self, source_id):
        if source_id not in self.documents:
            path = HISTORICAL_ROOT / "sources" / source_id / "source.json"
            self.documents[source_id] = SourceDocument.model_validate(read(path))
            self.files[str(path.resolve())] = file_hash(path)
        return self.documents[source_id]

    def candidate(self, source_id):
        return self.retained(source_id).candidate

    def related_versions(self, source_id):
        return []

    async def visual_bytes(self, *_):
        raise ValueError("D_has_no_new_external_acquisition")


def fixed_knowledge(session, qid):
    if qid == "q6":
        path = HISTORICAL_ROOT / "knowledge" / KNOWLEDGE_CASES[qid] / "answer.json"
        run = KnowledgeRun.model_validate(read(path))
        if run.answer is not None:
            raise ValueError("q6_fixture_requires_failed_knowledge")
        question, files = historical_knowledge_question(run.question.model_dump(mode="json"))
        run.question = question
        files[str(path.resolve())] = file_hash(path)
        return run, files
    folder = session.output / "cases/B-q4-1"
    result_path, fixture_path = folder / "task-result.json", folder / "fixture.json"
    result = TaskResult.model_validate(read(result_path))
    if result.status != "completed" or result.product is None:
        raise ValueError("fixed_B_q4_1_has_no_valid_answer")
    fixture = read(fixture_path)
    run = KnowledgeRun(
        research_id="fixed-B-q4-1",
        question=parse_knowledge_question(fixture["question"]),
        answer=KnowledgeAnswer.model_validate(result.product),
        task_result=result,
    )
    return run, {
        **fixture["source_hashes"],
        **{str(path.resolve()): file_hash(path) for path in (result_path, fixture_path)},
    }


def professional_fixture(case, folder, runtime, store=None):
    qid = "q3" if case == "E-q3-1" else case.split("-")[1]
    bundle, frozen, _, files = question_fixture("C2-1", folder)
    state = read(HISTORICAL_ROOT / "v4/state.json")
    package = deepcopy(state["package"])
    task = research_task(qid, state["questions"][qid])
    # The fixed observation supplies the whole original through the existing selection interface.
    task["application_refs"] = [
        {"source_id": sid, "passage_id": passage.passage_id}
        for sid, doc in frozen.documents.items()
        for passage in doc.passages
    ]
    store = store or RetainedStore(frozen.documents, files)
    config = load_config("config/review.yaml")
    prep_config = load_preparation_config(config.preparation_config)
    evaluation = build_evaluation_context(
        runtime.evaluation, runtime.config.evaluation_core, prep_config.evaluation
    )
    records = Records(folder)
    ex = Execution(runtime, records, config, evaluation)
    prep = Preparation(ex, store, bundle, prep_config)
    files.update(
        {
            str(Path(path).resolve()): file_hash(path)
            for path in ("config/review.yaml", config.preparation_config)
        }
    )
    return prep, package, task, files, evaluation


def scoped_runtime(recorder, budget=None, *, observe=False, config=None):
    current_kind = ContextVar("probe_task_kind", default="")
    base_factory = recorder.factory()
    responses = 0

    class Model(base_factory):
        async def complete(self, messages, tools=None, *, response_schema=None):
            nonlocal responses
            if (
                observe
                and current_kind.get() == "specialist"
                and self.config.structured_output == "yaml"
            ):
                raise ObservationStopped("closed_before_formal")
            reply = await super().complete(messages, tools, response_schema=response_schema)
            responses += 1
            if observe:
                calls = reply.message.get("tool_calls", [])
                names = [call["function"]["name"] for call in calls]
                reason = (
                    "first_knowledge_request"
                    if "research_knowledge" in names
                    else "natural_end"
                    if current_kind.get() == "specialist" and not calls
                    else "submission_requested"
                    if "submit_result" in names
                    else "four_response_limit"
                    if responses >= 4
                    else None
                )
                if reason:
                    raise ObservationStopped(reason, reply.message)
            return reply

    runtime = AgentRuntime.from_config(config or runtime_config(), model_factory=Model)
    runtime.recorder = SubmissionRecorder(recorder.folder, runtime.config.submission_max_bytes)
    runtime.scheduler.request_budget = budget
    run_task = runtime.run_task

    async def one_attempt(task, *args, **kwargs):
        task = task.model_copy(update={"max_repairs": 0, "max_truncation_recoveries": 0})
        token = current_kind.set(task.kind)
        try:
            return await run_task(task, *args, **kwargs)
        finally:
            current_kind.reset(token)

    runtime.run_task = one_attempt
    return runtime


async def professional_operation(
    case,
    folder,
    runtime,
    *,
    knowledge_run=None,
    store=None,
    knowledge_runs=None,
    originals=(),
    final_only=False,
    fixture_factory=None,
):
    prep, package, task, files, evaluation = (fixture_factory or professional_fixture)(
        case, folder, runtime, store
    )
    ex = prep.ex
    fixed = knowledge_run is not None or knowledge_runs is not None or final_only
    retained_runs = (
        list(knowledge_runs)
        if knowledge_runs is not None
        else ([knowledge_run] if knowledge_run is not None else [])
    )
    pending_runs = iter(retained_runs)

    async def investigate(question, **kwargs):
        if not fixed:
            raise AssertionError("D1_must_stop_before_executing_knowledge_request")
        write_json(folder / "delegation-question.json", question)
        return next(pending_runs).model_copy(deep=True)

    literature = SimpleNamespace(
        runtime=runtime, minimum_turns=lambda **_: 3, investigate=investigate
    )
    source = {
        "case": case,
        "source_hashes": files,
        "question": deepcopy(task["members"]),
        "evaluation": evaluation,
        "whole_original_supplied": True,
    }
    write_json(folder / "fixture.json", source)
    if fixed:
        production_call = ex.call

        async def formal_call(key, kind, objective, contract, **kwargs):
            ex.context_view(
                key,
                contract,
                reader=kwargs["reader"],
                refs=kwargs["refs"],
                materials=kwargs["materials"],
            )
            tools = kwargs["tools"]
            tools.argument_transform = contract.task_context.tool_arguments
            provided = []
            for index, run in enumerate(retained_runs, 1):
                question = run.question
                handles = [
                    contract.task_context.aliases[handle]
                    for handle in question.application_statements
                ]
                request = LiteratureRequest(
                    question=question.question,
                    purpose=question.purpose,
                    needed_information=question.needed_information,
                    changes_judgment_if=question.assessment_focus,
                    evidence_handles=handles,
                )
                response = await tools.execute("research_knowledge", request.model_dump_json())
                name = (
                    "knowledge-tool-result.json"
                    if index == 1
                    else f"knowledge-tool-result-{index}.json"
                )
                write_json(folder / name, response)
                if response.ok != (run.answer is not None):
                    raise ValueError("fixed_knowledge_handoff_failed")
                provided.append(
                    product_material(
                        f"provided-knowledge-{index}", response.model_dump(mode="json")
                    )
                )
            kwargs["materials"] = [
                *kwargs["materials"],
                *provided,
            ]
            kwargs.update(research=False, tools=ToolRegistry(), read_actions=0)
            return await production_call(key, kind, objective, contract, **kwargs)

        ex.call = formal_call
    reference = await Research(prep, package, literature).run(task, originals=originals)
    source.update(research_ref=reference, source_hashes=files)
    write_json(folder / "fixture.json", source)
    return {"status": "completed", "research_ref": reference, "records": ex.records, **source}


async def professional_formal(
    session, case, *, knowledge_run, store, folder, preview=False, expected_wire=None
):
    """Real parent handoff and one direct specialist submission; the caller owns the case budget."""
    folder = Path(folder)
    preview_folder = folder / "preview"
    preview_recorder = WireRecorder(preview_folder, preview=True)
    runtime = scoped_runtime(preview_recorder)
    try:
        await professional_operation(
            case, preview_folder, runtime, knowledge_run=knowledge_run, store=store
        )
    except PreviewCaptured:
        pass
    finally:
        await runtime.close()
    if len(preview_recorder.wires) != 1:
        raise ValueError("one_professional_formal_request_required")
    wire = preview_recorder.wires[0]
    if expected_wire is not None and digest(wire) != digest(expected_wire):
        raise ValueError("professional_request_changed_after_frozen_preview")
    capacity = capacity_for(wire)
    write_json(preview_folder / "capacity.json", capacity)
    if not capacity["fits"]:
        raise ValueError("professional_formal_capacity_exceeded")
    if preview:
        return {"status": "previewed", "capacity": capacity, "wire_digest": digest(wire)}
    recorder = WireRecorder(folder, budget=session.budget, expected=[wire])
    runtime = scoped_runtime(recorder, session.budget)
    try:
        result = await professional_operation(
            case, folder, runtime, knowledge_run=knowledge_run, store=store
        )
        return {**result, "capacity": capacity, "wire_digest": digest(wire)}
    finally:
        await runtime.close()


def probe_hashes():
    paths = (
        __file__,
        "src/spacebio_review/v4/research.py",
        "src/spacebio_review/v4/handoff.py",
        "src/spacebio_review/v4/execution.py",
        "src/spacebio_review/v4/contracts.py",
        "src/spacebio_review/review/access.py",
    )
    return {
        **implementation_hashes(),
        **{str(Path(path).resolve()): file_hash(path) for path in paths},
    }


async def run_case(session, case, *, live=False):
    if case not in CASES:
        raise ValueError("only_fixed_D_cases")
    folder = session.output / ("cases" if live else "preview") / case
    result_path = folder / "result.json"
    if result_path.exists():
        raise ValueError("preserve_existing_case_no_automatic_repeat")
    before = len(session.budget.data["requests"])
    result = {
        "case": case,
        "live": live,
        "revision": session.revision,
        "implementation_hashes": probe_hashes(),
        "runtime_config": runtime_config().model_dump(mode="json"),
    }
    scope = session.budget.case(case, session.revision) if live else nullcontext()
    try:
        preview_folder = session.output / "preview" / case
        if live:
            frozen = read(preview_folder / "result.json")
            if (
                frozen["implementation_hashes"] != result["implementation_hashes"]
                or frozen["runtime_config"] != result["runtime_config"]
                or not all(
                    file_hash(path) == expected
                    for path, expected in frozen["source_hashes"].items()
                )
            ):
                raise ValueError("D_request_changed_after_preview")
        with scope:
            if case.startswith("D2"):
                run, files = fixed_knowledge(session, case.split("-")[1])
                result.update(
                    await professional_formal(
                        session,
                        case,
                        knowledge_run=run,
                        store=None,
                        folder=folder,
                        preview=not live,
                        expected_wire=read(preview_folder / "preview/request-001/wire.json")
                        if live
                        else None,
                    )
                )
                result["source_hashes"] = {**result.get("source_hashes", {}), **files}
            else:
                expected = None
                if live:
                    expected = [read(preview_folder / "request-001/wire.json")]
                recorder = WireRecorder(
                    folder,
                    budget=session.budget if live else None,
                    expected=expected,
                    preview=not live,
                    allow_following=True,
                )
                runtime = scoped_runtime(recorder, session.budget if live else None, observe=True)
                try:
                    await professional_operation(case, folder, runtime)
                    raise AssertionError("D1_did_not_stop_at_observation_boundary")
                except PreviewCaptured:
                    result["status"] = "previewed"
                except ObservationStopped as stopped:
                    result.update(
                        status="observed", reason=str(stopped), last_response=stopped.response
                    )
                finally:
                    await runtime.close()
                    if recorder.wires:
                        result["capacity"] = capacity_for(recorder.wires[0])
                        result["wire_digest"] = digest(recorder.wires[0])
    except BaseException as exc:
        result.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        result.pop("records", None)
        fixture = folder / "fixture.json"
        if fixture.exists():
            result["source_hashes"] = {
                **read(fixture)["source_hashes"],
                **result.get("source_hashes", {}),
            }
        result["sources_unchanged"] = all(
            file_hash(path) == expected
            for path, expected in result.get("source_hashes", {}).items()
        )
        result["new_requests"] = len(session.budget.data["requests"]) - before
        write_json(result_path, result)
    return result


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("cases", nargs="+", choices=CASES)
    parser.add_argument("--revision", default="initial")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    with ProbeSession(args.root, args.revision) as session:
        for case in args.cases:
            result = await run_case(session, case, live=args.live)
            print(json.dumps({"case": case, "status": result["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
