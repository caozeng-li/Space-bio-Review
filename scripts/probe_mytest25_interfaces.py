"""Change 52 local probes. No preparation, whole expert task, or report generation."""

import argparse
import asyncio
import json
import os
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from probe_review_quality import ProbeBudget, file_hash, read, totals
from probe_review_questions import FrozenStore

from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.context import TokenEstimator, request_capacity
from spacebio_review.agents.diagnostics import SubmissionRecorder, digest
from spacebio_review.agents.types import ContextView, Material
from spacebio_review.application import ApplicationBundle, ApplicationSource
from spacebio_review.evidence.documents import parse_document
from spacebio_review.evidence.knowledge import (
    KnowledgeAnswer,
    KnowledgeQuestion,
    LiteratureConfig,
    ReviewKnowledgeQuestion,
)
from spacebio_review.evidence.submissions import scientific_contract
from spacebio_review.evidence.types import SourceCandidate, SourceDocument
from spacebio_review.models.config import load_config
from spacebio_review.models.provider import ModelError, model_class
from spacebio_review.review.materials import ApplicationReader
from spacebio_review.tools import ToolRegistry
from spacebio_review.v4.budget import Budget
from spacebio_review.v4.config import ReportingConfig, V4Config
from spacebio_review.v4.execution import Execution
from spacebio_review.v4.questions import QUESTION_VERSION, QuestionGeneration, select_questions
from spacebio_review.v4.records import Records, write_json

VERSION = "mytest25-interfaces-1"
MODEL_LIMITS = {"requests": 52, "input": 2_000_000, "output": 1_000_000}
EMBEDDING_LIMITS = {"requests": 50, "input": 200_000}
NETWORK_LIMITS = {"discovery": 80, "acquisition": 80}
GROUP_LIMITS = {"B": 6, "C": 5, "D1": 16, "D2": 4, "E": 17, "F": 4}
CASES = {
    **{f"B-q{q}-{n}": ("B", 1) for q in (4, 6) for n in range(1, 4)},
    **{f"C2-{n}": ("C", 1) for n in range(1, 4)},
    **{f"C3-{n}": ("C", 1) for n in range(1, 3)},
    **{f"D1-q{q}-{n}": ("D1", 4) for q in (1, 2) for n in (1, 2)},
    **{f"D2-q{q}-{n}": ("D2", 1) for q in (6, 4) for n in (1, 2)},
    "E-q3-1": ("E", 17),
    **{f"F-q{q}-{model}": ("F", 1) for q in (2, 6) for model in ("current", "strong")},
}
FORMAL_CASES = tuple(case for case in CASES if case.startswith(("B-", "C")))
HISTORICAL_ROOT = Path("runs/mytest25")
EXPERT_FIXTURE = Path("tests/fixtures/review_quality_sample3_experts.json")
KNOWLEDGE_CASES = {"q4": "research-12bd60f8fc9b", "q6": "research-850ea4f8a056"}


def implementation_hashes():
    paths = [
        "scripts/probe_mytest25_interfaces.py",
        "src/spacebio_review/agents/runtime.py",
        "src/spacebio_review/agents/context.py",
        "src/spacebio_review/agents/contracts.py",
        "src/spacebio_review/agents/types.py",
        "src/spacebio_review/agents/serialization.py",
        "src/spacebio_review/prose.py",
        "src/spacebio_review/reference_contract.py",
        "src/spacebio_review/models/capabilities.py",
        "src/spacebio_review/models/config.py",
        "src/spacebio_review/models/messages.py",
        "src/spacebio_review/models/provider.py",
        "src/spacebio_review/models/scheduling.py",
        "src/spacebio_review/evidence/literature.py",
        "src/spacebio_review/evidence/submissions.py",
        "src/spacebio_review/evidence/knowledge.py",
        "src/spacebio_review/v4/questions.py",
        "src/spacebio_review/v4/context.py",
        "src/spacebio_review/v4/contracts.py",
        "src/spacebio_review/v4/execution.py",
        "src/spacebio_review/v4/question_submission.py",
        "src/spacebio_review/v4/handoff.py",
        "src/spacebio_review/v4/research.py",
        "src/spacebio_review/v4/records.py",
        "src/spacebio_review/review/access.py",
        "src/spacebio_review/review/materials.py",
        "skills/literature-understanding/SKILL.md",
    ]
    paths.extend(str(path) for path in Path("skills/exploration-review").rglob("*.md"))
    return {path: file_hash(path) for path in paths}


def runtime_config():
    config = load_config("config/runtime.yaml")
    for model in config.models.values():
        model.retries = 0
    for route in config.task_routes.values():
        route.retries = 0
        route.max_truncation_recoveries = 0
    if config.embedding:
        config.embedding.retries = 0
    return config


class InterfaceBudget(Budget):
    """The production ledger plus the fixed experiment matrix, not per-case token caps."""

    check = staticmethod(ProbeBudget.check)
    version = VERSION
    model_limits = MODEL_LIMITS
    embedding_limits = EMBEDDING_LIMITS
    network_limits = NETWORK_LIMITS
    group_limits = GROUP_LIMITS
    cases = CASES
    embedding_group = "E"

    def __init__(self, root):
        super().__init__(
            Records(root),
            V4Config(
                max_model_calls=self.model_limits["requests"],
                max_embedding_calls=max(1, self.embedding_limits["requests"]),
                max_input_tokens=self.model_limits["input"] + self.embedding_limits["input"],
                max_output_tokens=self.model_limits["output"],
                completion_reserve_calls=0,
                completion_reserve_seconds=0,
                max_seconds=7 * 86400,
            ),
            ReportingConfig(),
        )
        self.selection = None
        if any(
            row.get("probe", {}).get("version") != self.version for row in self.data["requests"]
        ):
            raise ValueError("probe_version_requires_its_own_budget_root")

    @contextmanager
    def case(self, case, revision):
        if case not in self.cases or self.selection is not None:
            raise ValueError("fixed_case_required_and_parallel_cases_not_allowed")
        self.selection = {
            "version": self.version,
            "group": self.cases[case][0],
            "case": case,
            "revision": revision,
        }
        try:
            yield self
        finally:
            self.selection = None

    def exploration_reason(self, *, calls=1, input_tokens=0, output_tokens=0):
        reason = super().exploration_reason(
            calls=calls, input_tokens=input_tokens, output_tokens=output_tokens
        )
        # Embedding has its own allowance; model completion reservations still protect
        # the separate model input ceiling before the next exploration request.
        used = sum(row["input"] for row in self.data["requests"] if row["kind"] == "model")
        if (
            not reason
            and used + input_tokens + self._protected("input") > self.model_limits["input"]
        ):
            reason = "completion_model_input_reserve"
            self._closed_reason = reason
        return reason

    def reserve(self, config, messages=None, tools=None, response_schema=None, texts=None):
        if self.selection is None:
            raise ModelError("probe_case_not_selected", request_sent=False)
        if getattr(config, "retries", 0):
            raise ModelError("probe_retries_must_be_zero", request_sent=False)
        estimate = (
            TokenEstimator(None, getattr(config, "image_tokens", 0)).measure(
                messages, tools or [], response_schema=response_schema
            )["input_tokens"]
            if messages is not None
            else sum(len(text.encode("utf-8")) for text in texts or [])
        )
        output = getattr(config, "max_tokens", 0)
        kind = "embedding" if texts is not None else "model"
        rows = [row for row in self.data["requests"] if row["kind"] == kind]
        if kind == "embedding":
            if self.selection["group"] != self.embedding_group:
                raise ModelError(
                    "probe_embedding_only_in_" + str(self.embedding_group), request_sent=False
                )
            self.check(rows, self.embedding_limits, estimate, output, "embedding")
        else:
            if self._exploring.get() and not self._formal.get():
                reason = self.exploration_reason(input_tokens=estimate, output_tokens=output)
                if reason:
                    raise ModelError(reason, category="exploration", request_sent=False)
            self.check(rows, self.model_limits, estimate, output, "model_total")
            group = self.selection["group"]
            group_rows = [row for row in rows if row["probe"]["group"] == group]
            self.check(group_rows, {"requests": self.group_limits[group]}, estimate, output, group)
            case_rows = [
                row
                for row in group_rows
                if row["probe"]["case"] == self.selection["case"]
                and row["probe"]["revision"] == self.selection["revision"]
            ]
            self.check(
                case_rows,
                {"requests": self.cases[self.selection["case"]][1]},
                estimate,
                output,
                "case",
            )
        ticket = super().reserve(config, messages, tools, response_schema, texts)
        self.data["requests"][ticket]["probe"] = dict(self.selection)
        self.save()
        return ticket

    def report(self):
        rows = self.data["requests"]
        return {
            "version": self.version,
            "model_limits": self.model_limits,
            "embedding_limits": self.embedding_limits,
            "network_limits": self.network_limits,
            "group_request_limits": self.group_limits,
            "models": totals([row for row in rows if row["kind"] == "model"]),
            "embedding": totals([row for row in rows if row["kind"] == "embedding"]),
            "groups": {
                group: totals(
                    [
                        row
                        for row in rows
                        if row["kind"] == "model" and row["probe"]["group"] == group
                    ]
                )
                for group in self.group_limits
            },
            "requests": rows,
            "note": "Unknown provider usage retains its reservation, not an invented zero. "
            "Reasoning tokens are not added to completion tokens a second time.",
        }

    def settle(self, ticket, usage):
        super().settle(ticket, usage)
        self.data["requests"][ticket]["usage_known"] = {
            "input": getattr(usage, "prompt_tokens", None) is not None,
            "output": self.data["requests"][ticket]["kind"] == "embedding"
            or getattr(usage, "completion_tokens", None) is not None,
        }
        self.save()


class ProbeSession:
    """One process owns all cases and the shared budget while this session is open."""

    budget_type = InterfaceBudget

    def __init__(self, root, revision="initial"):
        if Path(revision).name != revision or revision in {".", ".."}:
            raise ValueError("revision_must_be_one_directory_name")
        self.root, self.revision = Path(root).resolve(), revision
        self.output = self.root / "revisions" / revision
        self.lock = self.root / ".probe-session.lock"
        self.budget = None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise ValueError("another_probe_session_or_unresolved_interruption") from None
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "revision": self.revision}, stream)
        try:
            self.budget = self.budget_type(self.root)
        except BaseException:
            self.lock.unlink()
            raise
        return self

    def __exit__(self, *_):
        try:
            self.budget.save()
            write_json(self.root / "usage.json", self.budget.report())
        finally:
            self.lock.unlink()


class PreviewCaptured(BaseException):
    pass


class WireRecorder:
    """Record every actual adapter request and raw provider reply without credentials."""

    def __init__(self, folder, *, budget=None, expected=None, preview=False, allow_following=False):
        self.folder = Path(folder)
        self.budget, self.expected, self.preview = budget, expected, preview
        self.allow_following = allow_following
        self.wires = []

    def factory(self):
        recorder = self

        class Model:
            def __init__(self, config):
                self.config, self.inner = config, None

            async def complete(self, messages, tools=None, *, response_schema=None):
                if self.config.retries:
                    raise ValueError("probe_retries_must_be_zero")
                wire = {
                    "messages": deepcopy(messages),
                    "tools": deepcopy(tools),
                    "schema": deepcopy(response_schema),
                    "model": self.config.model_dump(mode="json"),
                }
                index = len(recorder.wires) + 1
                recorder.wires.append(wire)
                path = recorder.folder / f"request-{index:03d}"
                write_json(path / "wire.json", wire)
                if recorder.expected is not None:
                    past_prefix = index > len(recorder.expected)
                    if (past_prefix and not recorder.allow_following) or (
                        not past_prefix and digest(wire) != digest(recorder.expected[index - 1])
                    ):
                        raise ValueError("actual_request_differs_from_frozen_preview")
                if recorder.preview:
                    raise PreviewCaptured()
                if self.inner is None:
                    self.inner = model_class(self.config)(
                        self.config, env_file=".env", budget=recorder.budget
                    )
                client = getattr(self.inner, "_client", None)
                transport = (
                    client if hasattr(client, "event_hooks") else getattr(client, "_client", None)
                )

                async def request_hook(request):
                    body = await request.aread()
                    write_json(path / "provider-request.json", json.loads(body))

                async def response_hook(response):
                    body = await response.aread()
                    try:
                        payload = json.loads(body)
                    except (UnicodeDecodeError, ValueError):
                        payload = {"unparsed_body": body.decode("utf-8", errors="replace")}
                    write_json(
                        path / "provider-response.json",
                        {"status": response.status_code, "body": payload},
                    )

                hooks = getattr(transport, "event_hooks", None)
                if hooks is not None:
                    hooks["request"].append(request_hook)
                    hooks["response"].append(response_hook)
                try:
                    reply = await self.inner.complete(
                        messages, tools, response_schema=response_schema
                    )
                    write_json(path / "reply.json", asdict(reply))
                    return reply
                except BaseException as exc:
                    write_json(
                        path / "failure.json",
                        {
                            "type": type(exc).__name__,
                            "reason": str(exc),
                            "request_sent": getattr(exc, "request_sent", None),
                            "category": getattr(exc, "category", None),
                        },
                    )
                    raise
                finally:
                    if hooks is not None:
                        hooks["request"].remove(request_hook)
                        hooks["response"].remove(response_hook)

            async def close(self):
                if self.inner is not None:
                    await self.inner.close()

        return Model


def question_fixture(case, folder):
    if case not in CASES or CASES[case][0] != "C":
        raise ValueError("not_a_fixed_question_case")
    if case.startswith("C2-"):
        state_path = HISTORICAL_ROOT / "v4/state.json"
        state = read(state_path)
        bundle = ApplicationBundle.model_validate(state["metadata"]["application"])
        documents, paths = {}, [state_path]
        for source in bundle.sources:
            path = HISTORICAL_ROOT / "sources" / source.source_id / "source.json"
            documents[source.source_id] = SourceDocument.model_validate(read(path))
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
        documents = {sid: document}
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
    return bundle, FrozenStore(documents), package, files


def literature_fixture(case):
    if case not in CASES or CASES[case][0] != "B":
        raise ValueError("not_a_fixed_literature_case")
    qid = case.split("-")[1]
    folder = HISTORICAL_ROOT / "knowledge" / KNOWLEDGE_CASES[qid]
    answer_path, reading_path = folder / "answer.json", folder / "evidence-readings.json"
    historical = read(answer_path)
    # Only the question and the read ledger become model input. Failed candidate prose
    # remains in the historical archive, never an example or a manufactured answer.
    question, application_files = historical_knowledge_question(historical["question"])
    evidence = {
        **getattr(question, "application_statements", {}),
        **read(reading_path),
    }
    files = {str(path.resolve()): file_hash(path) for path in (answer_path, reading_path)}
    files.update(application_files)
    for sid in sorted({row["reference"]["source_id"] for row in evidence.values()}):
        for name in ("source.json", "original.md"):
            path = HISTORICAL_ROOT / "sources" / sid / name
            if path.is_file():
                files[str(path.resolve())] = file_hash(path)
    return {
        "question": question.model_dump(mode="json"),
        "evidence": evidence,
        "source_hashes": files,
    }


def historical_knowledge_question(value):
    """Rebind the retained question through the new handoff using actual application reads."""
    from spacebio_review.v4.handoff import literature_question
    from spacebio_review.v4.research import LiteratureRequest

    bundle, store, _, files = question_fixture("C2-1", Path("."))
    reader = ApplicationReader(bundle, store, None)
    if value.get("input_version") == "review-knowledge-2":
        locations = [row["reference"] for row in value["application_statements"].values()]
    else:
        locations = [r for row in value.get("known_project_facts", []) for r in row["source_refs"]]
    selected = {(row["source_id"], row["passage_id"]) for row in locations}
    for sid, doc in store.documents.items():
        reader.record_context_read(
            {
                "source_id": sid,
                "passages": [
                    p.model_dump(mode="json")
                    for p in doc.passages
                    if (sid, p.passage_id) in selected
                ],
            }
        )
    evidence = reader.references.snapshot_choices()
    request = LiteratureRequest(
        question=value["question"],
        purpose=value["purpose"],
        needed_information=value["needed_information"],
        changes_judgment_if=value["assessment_focus"],
        evidence_handles=list(evidence),
    )
    base = KnowledgeQuestion(question=value["question"], cutoff_date=value.get("cutoff_date"))
    return literature_question(request, base, evidence), files


def parse_knowledge_question(value):
    kind = (
        ReviewKnowledgeQuestion
        if value.get("input_version") == "review-knowledge-2"
        else KnowledgeQuestion
    )
    return kind.model_validate(value)


async def question_operation(case, folder, runtime, *, fixture_factory=None):
    bundle, store, package, files = (fixture_factory or question_fixture)(case, folder)
    ex = Execution(runtime, Records(folder), V4Config(max_questions=10), {})
    preparation = SimpleNamespace(ex=ex, reader=lambda: ApplicationReader(bundle, store, runtime))
    generation = QuestionGeneration(preparation, package)
    value = {
        "kind": "question",
        "version": QUESTION_VERSION,
        "source_hashes": files,
        "application": bundle.model_dump(mode="json"),
        "experts": package["experts"],
        "sources": generation.sources,
        "capacity": generation.preview(),
    }
    write_json(folder / "fixture.json", value)
    if not value["capacity"]["fits"]:
        raise ValueError("question_context_exceeds_capacity")
    result = await generation.run()
    write_json(folder / "questions.json", result)
    selected = select_questions(result, 10)
    write_json(folder / "selected-questions.json", selected)
    return {"product": result, "selected": selected}


async def literature_operation(case, folder, runtime, *, fixture_factory=None):
    from spacebio_review.evidence.literature import literature_task

    value = (fixture_factory or literature_fixture)(case)
    question = parse_knowledge_question(value["question"])
    scope = SimpleNamespace(submission_choices=lambda: deepcopy(value["evidence"]))
    session = SimpleNamespace(
        question=question, whole_units=False, state=SimpleNamespace(stop_reason="")
    )
    contract = scientific_contract(scope, KnowledgeAnswer, session=session)
    task, profile = literature_task("fixed-literature", LiteratureConfig())
    task = task.model_copy(
        update={
            "mode": "direct",
            "max_actions": 1,
            "max_model_turns": 1,
            "reserve_actions": 0,
            "reserve_model_turns": 0,
            "max_repairs": 0,
            "max_truncation_recoveries": 0,
        }
    )
    view = ContextView(
        agent_id=profile.agent_id,
        phase=task.phase,
        materials=[
            Material(
                source_id="knowledge-question",
                data=question.model_dump(mode="json"),
                essential=True,
            )
        ],
    )
    capacity = runtime.preview_formal(task, profile, view, contract, ToolRegistry())
    write_json(
        folder / "fixture.json",
        {
            **value,
            "kind": "fixed_literature_formal",
            "version": contract.version,
            "task": task.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"),
            "capacity": capacity,
            "failed_candidates_in_model_input": False,
        },
    )
    if not capacity["fits"]:
        raise ValueError("literature_formal_context_exceeds_capacity")
    result = await runtime.run_task(task, profile, view, ToolRegistry(), contract)
    write_json(folder / "task-result.json", result.model_dump(mode="json"))
    return {"status": result.status, "reason": result.reason, "product": result.product}


def capacity_for(wire):
    model = wire["model"]
    estimate = TokenEstimator(None, model.get("image_tokens", 0)).measure(
        wire["messages"],
        wire["tools"] or [],
        response_schema=wire["schema"],
        output_reserve=model["max_tokens"],
    )
    return request_capacity(estimate, model["context_window"])


async def run_case(session, case, *, live=False):
    if case not in FORMAL_CASES:
        raise ValueError("this_entry_only_implements_B_and_C")
    preview_folder = session.output / "preview" / case
    folder = session.output / "cases" / case if live else preview_folder
    result_path = folder / "result.json"
    if result_path.exists():
        result = read(result_path)
        if live and result["status"] == "completed":
            if (
                result["implementation_hashes"] != implementation_hashes()
                or result["runtime_config"] != runtime_config().model_dump(mode="json")
                or not all(
                    file_hash(path) == expected
                    for path, expected in result["source_hashes"].items()
                )
            ):
                raise ValueError("completed_case_is_from_different_implementation_or_material")
            return {**result, "cache_or_local_replay": True, "new_requests": 0}
        raise ValueError("case_already_started_preserve_result_no_automatic_repetition")
    expected = None
    config = runtime_config()
    implementation = implementation_hashes()
    if live:
        planned = read(preview_folder / "result.json")
        if planned["status"] != "previewed" or not planned["capacity"]["fits"]:
            raise ValueError("successful_preview_required")
        if planned["implementation_hashes"] != implementation or planned[
            "runtime_config"
        ] != config.model_dump(mode="json"):
            raise ValueError("implementation_or_configuration_changed_use_new_revision")
        expected = [read(preview_folder / "request-001/wire.json")]
        fixture = read(preview_folder / "fixture.json")
        if not all(file_hash(path) == value for path, value in fixture["source_hashes"].items()):
            raise ValueError("original_material_changed_after_preview")
    recorder = WireRecorder(
        folder, budget=session.budget if live else None, expected=expected, preview=not live
    )
    runtime = AgentRuntime.from_config(config, model_factory=recorder.factory())
    runtime.recorder = SubmissionRecorder(folder, config.submission_max_bytes)
    if live:
        runtime.scheduler.request_budget = session.budget
    value = {
        "case": case,
        "revision": session.revision,
        "live": live,
        "runtime_config": config.model_dump(mode="json"),
        "implementation_hashes": implementation,
        "knowledge_cutoff": None,
        "source_cache_start": "read-only historical originals; no new discovery",
    }
    before = len(session.budget.data["requests"])
    operation = literature_operation if case.startswith("B-") else question_operation
    try:
        with session.budget.case(case, session.revision):
            value.update(await operation(case, folder, runtime))
        value.setdefault("status", "completed")
    except PreviewCaptured:
        value["status"] = "previewed"
    except BaseException as exc:
        value.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        await runtime.close()
        if recorder.wires:
            value["capacity"] = capacity_for(recorder.wires[0])
            value["wire_digest"] = digest(recorder.wires[0])
        value["new_requests"] = len(session.budget.data["requests"]) - before
        if (folder / "fixture.json").exists():
            fixture = read(folder / "fixture.json")
            value["source_hashes"] = fixture["source_hashes"]
            value["sources_unchanged"] = all(
                file_hash(path) == expected for path, expected in value["source_hashes"].items()
            )
        write_json(result_path, value)
        write_json(session.root / "usage.json", session.budget.report())
    return value


async def run_cases(session, cases, *, live=False):
    if len(set(cases)) != len(cases):
        raise ValueError("duplicate_case_in_fixed_matrix")
    results = []
    for case in cases:
        result = await run_case(session, case, live=live)
        results.append(result)
        if result["status"] not in {"previewed", "completed"}:
            break
    return results


def matrix_preview(session):
    """Maximum output reservation, not a forecast of provider token use."""
    capacities = {
        "B": 6 * 24576,
        "C": 5 * 16384,
        "D1": 16 * 16384,
        "D2": 4 * 16384,
        "E": 16 * 24576 + 16384,
        "F": 4 * 8192,
    }
    cases = {}
    for case in FORMAL_CASES:
        path = session.output / "preview" / case / "result.json"
        if path.exists():
            value = read(path)
            cases[case] = {
                "capacity": value["capacity"],
                "status": value["status"],
                "wire_digest": value["wire_digest"],
            }
    value = {
        "version": VERSION,
        "fixed_matrix": {
            case: {"group": group, "requests": cap} for case, (group, cap) in CASES.items()
        },
        "model_limits": MODEL_LIMITS,
        "embedding_limits": EMBEDDING_LIMITS,
        "output_reservation_by_group": capacities,
        "maximum_output_reservation": sum(capacities.values()),
        "output_matrix_fits": sum(capacities.values()) <= MODEL_LIMITS["output"],
        "previewed_formal_cases": cases,
        "previewed_input_sum": sum(row["capacity"]["input_tokens"] for row in cases.values()),
        "unpreviewed_formal_cases": [case for case in FORMAL_CASES if case not in cases],
        "dynamic_input_note": "D1 and E later requests depend on actual tool results. Their "
        "full input cost cannot be forecast from the first request. "
        "Use the production research allocation and current formal "
        "reservation; no per-case token ceiling is imposed by this probe.",
        "prior_usage": session.budget.report(),
    }
    write_json(session.output / "matrix-preview.json", value)
    return value


async def main(args):
    with ProbeSession(args.output, args.revision) as session:
        if args.stage == "usage":
            result = session.budget.report()
        else:
            cases = args.cases or [case for case in FORMAL_CASES if case.startswith(args.group)]
            result = await run_cases(session, cases, live=args.stage == "run")
            if args.stage == "preview":
                matrix_preview(session)
            result = [
                {
                    key: row.get(key)
                    for key in ("case", "status", "new_requests", "reason", "capacity")
                }
                for row in result
            ]
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("preview", "run", "usage"))
    parser.add_argument(
        "--output", type=Path, default=Path("runs/probe-mytest25-interfaces-20261013")
    )
    parser.add_argument("--revision", default="initial")
    parser.add_argument("--group", choices=("B", "C"), default="B")
    parser.add_argument("--cases", nargs="+", choices=FORMAL_CASES)
    asyncio.run(main(parser.parse_args()))
