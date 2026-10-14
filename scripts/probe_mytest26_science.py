"""Change 58 fixed local matrix. Historical sources are read-only; no research loop."""

import argparse
import asyncio
import json
from copy import deepcopy
from pathlib import Path

from probe_mytest25_interfaces import (
    InterfaceBudget,
    PreviewCaptured,
    ProbeSession,
    WireRecorder,
    capacity_for,
    implementation_hashes,
    literature_operation,
    parse_knowledge_question,
    question_operation,
    runtime_config,
)
from probe_mytest25_professional import professional_operation, scoped_runtime
from probe_review_quality import evaluation_context, file_hash, read
from probe_review_questions import FrozenStore

from spacebio_review.agents.diagnostics import digest
from spacebio_review.agents.types import TaskResult
from spacebio_review.application import ApplicationBundle
from spacebio_review.evidence.knowledge import KnowledgeAnswer, KnowledgeRun
from spacebio_review.evidence.types import Passage, SourceDocument
from spacebio_review.models.provider import ModelError
from spacebio_review.review.project import load_preparation_config
from spacebio_review.v4.assessment import synthesize
from spacebio_review.v4.assessment_input import ReviewArchive, freeze_review
from spacebio_review.v4.config import load_config
from spacebio_review.v4.execution import Execution
from spacebio_review.v4.manuscript_call import ManuscriptCall, ManuscriptConfig
from spacebio_review.v4.preparation import Preparation
from spacebio_review.v4.questions import research_task
from spacebio_review.v4.records import Records, write_json
from spacebio_review.v4.report_authoring import sized_writing_plan

VERSION = "mytest26-science-1"
NATIVE_MATRIX_VERSION = "mytest26-native-professional-1"
MATRIX_VERSION = "mytest26-schema-professional-1"
ROOTS = {"26": Path("runs/mytest26"), "25": Path("runs/mytest25")}
ORIGINALS = {
    "26": ("src-d1684dde2f57", "aa6b3dc61de186cf43d98c7bbfa157069deac078c5a5d194eeb80f03fd85001d"),
    "25": ("src-1f1f195b955a", "03079872a0fe515461326ffe19c5f8788db4693fc51d15efa7a929817b149e02"),
}
KNOWLEDGE = {"TFEB": "research-ca780ca834a5", "AllProtect": "research-ace068fcf459"}
RETAINED_C = ("C-q2-1", "C-q2-2", "C-q4-1")
NATIVE_C = tuple(f"C-native-q{q}-{n}" for q in (2, 4, 5, 7) for n in (1, 2))
RETAINED_NATIVE_C = ("C-native-q2-1",)
SCHEMA_C = ("C-schema-q2-1",) + tuple(
    f"C-schema-q{q}-{n}" for q in (4, 5, 7) for n in (1, 2)
)
RETAINED_D = tuple(f"D26-{n}" for n in (1, 2, 3))
LABELLED_D = ("D25-labelled-1", "D25-labelled-2")
READ_ONLY_CASES = (*RETAINED_C, *NATIVE_C, *RETAINED_D)
CASES = {
    **{f"B-{topic}-{n}": ("B", 1) for topic in KNOWLEDGE for n in (1, 2)},
    **{case: ("C", 1) for case in (*RETAINED_C, *RETAINED_NATIVE_C, *SCHEMA_C)},
    **{case: ("D", 1) for case in (*RETAINED_D, *LABELLED_D)},
    **{
        f"E-{path}{sample}-{n}": ("E", 1 if path == "S" else 2)
        for sample, count in (("26", 3), ("25", 2))
        for n in range(1, count + 1)
        for path in ("S", "T")
    },
    "F-S26-1": ("F", 3),
}


class ScienceBudget(InterfaceBudget):
    version = VERSION
    model_limits = {"requests": 39, "input": 2_500_000, "output": 1_000_000}
    embedding_limits = {"requests": 0, "input": 0}
    network_limits = {"discovery": 0, "acquisition": 0}
    group_limits = {"B": 4, "C": 11, "D": 5, "E": 15, "F": 3, "G": 0}
    cases = CASES
    embedding_group = None

    def reserve(self, *args, **kwargs):
        if self.selection and self.selection["case"] in READ_ONLY_CASES:
            raise ModelError("legacy_case_is_read_only_after_matrix_amendment", request_sent=False)
        return super().reserve(*args, **kwargs)


class ScienceSession(ProbeSession):
    budget_type = ScienceBudget


def question_id(case):
    return case.split("-")[2] if case in (*NATIVE_C, *SCHEMA_C) else case.split("-")[1]


def hashes():
    paths = [
        Path(__file__),
        Path("scripts/probe_mytest25_professional.py"),
        Path("config/runtime.yaml"),
        Path("config/review.yaml"),
        Path("config/preparation.yaml"),
        Path("config/assessment.yaml"),
        *Path("src/spacebio_review/v4").glob("*.py"),
    ]
    return {**implementation_hashes(), **{str(p.resolve()): file_hash(p) for p in paths}}


def retain_file(files, path):
    path = Path(path)
    files[str(path.resolve())] = file_hash(path)
    return path


def archived_application(sample):
    root = ROOTS[sample]
    files = {}
    state = read(retain_file(files, root / "v4/state.json"))
    bundle = ApplicationBundle.model_validate(state["metadata"]["application"])
    documents = {}
    for row in bundle.sources:
        folder = root / "sources" / row.source_id
        documents[row.source_id] = SourceDocument.model_validate(
            read(retain_file(files, folder / "source.json"))
        )
        retain_file(files, folder / "original.md")
    sid, expected = ORIGINALS[sample]
    if file_hash(root / "sources" / sid / "original.md") != expected:
        raise ValueError("fixed_application_changed:" + sample)
    return bundle, FrozenStore(documents), deepcopy(state["package"]), files


def question_fixture(case, folder):
    return archived_application(case[1:3])


def literature_fixture(case):
    folder = ROOTS["26"] / "knowledge" / KNOWLEDGE[case.split("-")[1]]
    files = {}
    historical = read(retain_file(files, folder / "answer.json"))
    question = parse_knowledge_question(historical["question"])
    evidence = {
        **question.application_statements,
        **read(retain_file(files, folder / "evidence-readings.json")),
    }
    for sid in sorted({row["reference"]["source_id"] for row in evidence.values()}):
        source = ROOTS["26"] / "sources" / sid
        for name in ("candidate.json", "source.json", "original.md"):
            if (source / name).exists():
                retain_file(files, source / name)
    return {
        "question": question.model_dump(mode="json"),
        "evidence": evidence,
        "source_hashes": files,
    }


class RetainedStore:
    def __init__(self, sample, documents, files, readings=None):
        self.sample, self.documents, self.files = sample, dict(documents), files
        self.readings = readings or {}

    def retained(self, source_id):
        if source_id not in self.documents:
            folder = ROOTS[self.sample] / "sources" / source_id
            if (folder / "source.json").exists():
                document = SourceDocument.model_validate(
                    read(retain_file(self.files, folder / "source.json"))
                )
            else:
                document = SourceDocument(
                    source_id=source_id,
                    candidate=read(retain_file(self.files, folder / "candidate.json")),
                    availability="abstract",
                )
            present = {passage.passage_id for passage in document.passages}
            for row in self.readings.values():
                reference = row["reference"]
                if reference["source_id"] != source_id or reference["passage_id"] in present:
                    continue
                # Search-result abstracts were actually read, but are not acquired source files.
                # Replay the exact recorded excerpt and reading depth, never synthesize a full text.
                document.passages.append(
                    Passage(
                        passage_id=reference["passage_id"],
                        section=row["locator"],
                        locator=row["locator"],
                        text=row["quote"],
                        kind="abstract"
                        if row["reading_scope"] == "metadata/abstract"
                        else row["reading_scope"],
                    )
                )
                present.add(reference["passage_id"])
            self.documents[source_id] = document
            if (folder / "original.md").exists():
                retain_file(self.files, folder / "original.md")
        return self.documents[source_id]

    def candidate(self, source_id):
        return self.retained(source_id).candidate

    def related_versions(self, source_id):
        return []

    async def visual_bytes(self, *_):
        raise ValueError("fixed_formal_probe_does_not_acquire_images")


def professional_fixture(case, folder, runtime, store=None):
    qid = question_id(case)
    bundle, frozen, package, files = archived_application("26")
    state = read(ROOTS["26"] / "v4/state.json")
    task = research_task(qid, state["questions"][qid])
    task["application_refs"] = [
        {"source_id": sid, "passage_id": passage.passage_id}
        for sid, document in frozen.documents.items()
        for passage in document.passages
    ]
    config = load_config("config/review.yaml")
    prep_config = load_preparation_config(config.preparation_config)
    evaluation = evaluation_context()
    ex = Execution(runtime, Records(folder), config, evaluation)
    store = store or RetainedStore("26", frozen.documents, files)
    prep = Preparation(ex, store, bundle, prep_config)
    return prep, package, task, files, evaluation


def fixed_tfeb(session):
    folder = session.output / "cases/B-TFEB-1"
    result_path, fixture_path = folder / "task-result.json", folder / "fixture.json"
    result = TaskResult.model_validate(read(result_path))
    if result.status != "completed" or result.product is None:
        raise ValueError("designated_B_TFEB_1_is_not_structurally_complete")
    fixture = read(fixture_path)
    run = KnowledgeRun(
        research_id="fixed-B-TFEB-1",
        question=parse_knowledge_question(fixture["question"]),
        answer=KnowledgeAnswer.model_validate(result.product),
        task_result=result,
    )
    files = dict(fixture["source_hashes"])
    for path in (result_path, fixture_path):
        retain_file(files, path)
    return run, files


def professional_materials(session, qid):
    archive = ReviewArchive(ROOTS["26"])
    refs = archive.state["heads"][qid]
    files, runs, originals, readings = {}, [], {}, {}
    for ref in refs:
        path = retain_file(files, archive.root / (ref + ".json"))
        value = read(path)["value"]
        readings.update(value.get("evidence", {}))
        for sid, row in value["reading"]["literature"].items():
            if row.get("visual_ids"):
                raise ValueError("fixed_professional_material_requires_visual_replay")
            originals.setdefault(sid, []).extend(row.get("passage_ids", []))
        for row in value["reading"].get("delegated", []):
            reading_path = ROOTS["26"] / "knowledge" / row["research_id"] / "evidence-readings.json"
            readings.update(read(retain_file(files, reading_path)))
            if qid == "q4" and row["research_id"] == KNOWLEDGE["TFEB"]:
                run, source_files = fixed_tfeb(session)
                files.update(source_files)
            else:
                path = ROOTS["26"] / "knowledge" / row["research_id"] / "answer.json"
                run = KnowledgeRun.model_validate(read(retain_file(files, path)))
            runs.append(run)
    return (
        runs,
        [
            {"source_id": sid, "passage_ids": list(dict.fromkeys(ids))}
            for sid, ids in originals.items()
        ],
        files,
        readings,
    )


class CombinedArchive(Records):
    """Resolve immutable records from selected sample roots without copying or rewriting them."""

    def __init__(self, archives, files):
        self.archives, self.files = archives, files

    def get(self, key):
        for archive in self.archives:
            path = archive.root / (key + ".json")
            if path.is_file():
                retain_file(self.files, path)
                return archive.get(key)
        raise FileNotFoundError(key)


def synthesis_fixture(session, sample, *, historical=False):
    files = {}
    archive = ReviewArchive(ROOTS[sample])
    retain_file(files, archive.path)
    application = archive.application()
    for document in application:
        for name in ("source.json", "original.md"):
            retain_file(files, ROOTS[sample] / "sources" / document["source_id"] / name)
    replacements, archives = {}, [archive]
    if sample == "26" and not historical:
        for qid in ("q2", "q4", "q5", "q7"):
            folder = session.output / "cases" / f"C-schema-{qid}-1"
            result = read(retain_file(files, folder / "result.json"))
            if result["status"] != "completed":
                raise ValueError("designated_first_professional_sample_incomplete:" + qid)
            replacements[qid] = [result["research_ref"]]
            archives.insert(0, Records(folder))
            files.update(result["source_hashes"])
    selected = list(
        dict.fromkeys(
            ref
            for qid, refs in archive.state["heads"].items()
            for ref in replacements.get(qid, refs)
        )
    )
    frozen = freeze_review(
        CombinedArchive(archives, files), selected, application, evaluation_context()
    )
    return frozen, files


def manuscript_config():
    config = ManuscriptConfig.load()
    config.model.retries = 0
    if config.model.tools:
        raise ValueError("manuscript_probe_requires_no_tools")
    return config


def caller_for(folder, recorder, budget):
    return ManuscriptCall(
        Records(folder), manuscript_config(), budget=budget, model_factory=recorder.factory()
    )


async def operation(session, case, folder, runtime, recorder):
    if case.startswith("B-"):
        return await literature_operation(case, folder, runtime, fixture_factory=literature_fixture)
    if case.startswith("D"):
        return await question_operation(case, folder, runtime, fixture_factory=question_fixture)
    if case in SCHEMA_C:
        runs, originals, files, readings = professional_materials(session, question_id(case))
        _, application, _, app_files = archived_application("26")
        files.update(app_files)
        store = RetainedStore("26", application.documents, files, readings)
        for original in originals:
            document = store.retained(original["source_id"])
            missing = set(original["passage_ids"]) - {p.passage_id for p in document.passages}
            if missing:
                raise ValueError("historical_read_excerpt_missing:" + repr(sorted(missing)))
        try:
            return await professional_operation(
                case,
                folder,
                runtime,
                knowledge_runs=runs,
                originals=originals,
                final_only=True,
                fixture_factory=professional_fixture,
                store=store,
            )
        finally:
            fixture_path = folder / "fixture.json"
            if fixture_path.exists():
                fixture = read(fixture_path)
                fixture["source_hashes"].update(files)
                fixture["provided_originals"] = originals
                fixture["provided_knowledge"] = [run.research_id for run in runs]
                write_json(fixture_path, fixture)
    if case.startswith("E-"):
        branch = case.split("-")[1]
        frozen, files = synthesis_fixture(session, branch[1:])
        write_json(
            folder / "fixture.json", {"source_hashes": files, "frozen": frozen, "path": branch[0]}
        )
        caller = caller_for(folder, recorder, session.budget)
        ref = caller.records.put("frozen-review", frozen)
        if branch[0] == "S":
            result_ref = await synthesize(caller, ref)
        else:
            from spacebio_review.v4.scientific_review import synthesize_scientific

            result_ref = await synthesize_scientific(caller, ref)
        return {"status": "completed", "frozen_ref": ref, "result_ref": result_ref}
    if case == "F-S26-1":
        source = session.output / "cases/E-S26-1"
        result_path = source / "result.json"
        previous = read(result_path)
        if previous["status"] != "completed":
            raise ValueError("designated_S26_1_incomplete")
        original_records = Records(source)
        frozen = original_records.value(previous["frozen_ref"])
        assessment = original_records.value(previous["result_ref"])
        caller = caller_for(folder, recorder, session.budget)
        frozen_ref = caller.records.put("frozen-review", frozen)
        assessment_ref = caller.records.put("global-assessment", assessment, [frozen_ref])
        plan = sized_writing_plan(caller, frozen, assessment)
        files = dict(previous["source_hashes"])
        retain_file(files, result_path)
        write_json(folder / "fixture.json", {"source_hashes": files, "plan": plan})
        if len(plan) > 3:
            return {
                "status": "not_executed",
                "reason": "author_plan_exceeds_three_requests",
                "plan": plan,
            }
        from spacebio_review.v4.assessed_delivery import deliver

        delivery_ref = await deliver(
            caller, assessment_ref, frozen_ref, folder / "publication", translate=False
        )
        return {
            "status": "completed",
            "frozen_ref": frozen_ref,
            "result_ref": assessment_ref,
            "delivery_ref": delivery_ref,
            "plan": plan,
        }
    raise ValueError("unknown_fixed_case")


async def publish_scientific(session, case):
    if not case.startswith("E-T") or case not in CASES:
        raise ValueError("scientific_delivery_requires_completed_T_case")
    folder = session.output / "cases" / case
    value = read(folder / "result.json")
    if value["status"] != "completed":
        raise ValueError("scientific_case_not_complete")

    def no_model(config):
        raise AssertionError("scientific_publication_must_not_request_a_model")

    from spacebio_review.v4.assessed_delivery import deliver_review_result

    caller = ManuscriptCall(Records(folder), manuscript_config(), model_factory=no_model)
    reference = await deliver_review_result(
        caller, value["result_ref"], folder / "publication", translate=False
    )
    result = {"case": case, "delivery_ref": reference, "new_requests": 0}
    write_json(folder / "publication-result.json", result)
    return result


def amend_matrix(session, *, reason):
    """Append the explicitly authorized native matrix without changing the initial manifest."""
    if not reason or not reason.strip():
        raise ValueError("matrix_amendment_requires_explicit_reason")
    if session.revision != "initial":
        raise ValueError("native_matrix_reuses_initial_fixed_materials")
    path = session.output / "matrix-amendment-native-professional.json"
    if path.exists():
        return read(path)
    current = runtime_config()
    models = {
        tier: current.routed_model("specialist", tier, formal=True).model_dump(mode="json")
        for tier in ("normal", "strong")
    }
    if any(model["structured_output"] != "json_schema" for model in models.values()):
        raise ValueError("native_professional_configuration_required")
    files = {}
    retain_file(files, session.output / "manifest.json")
    retained = {}
    for case in (*RETAINED_C, *RETAINED_D):
        result_path = session.output / "cases" / case / "result.json"
        result = read(retain_file(files, result_path))
        retained[case] = {key: result.get(key) for key in ("status", "new_requests", "wire_digest")}
    fixed_tfeb(session)
    for name in ("task-result.json", "fixture.json"):
        retain_file(files, session.output / "cases/B-TFEB-1" / name)
    value = {
        "matrix_version": NATIVE_MATRIX_VERSION,
        "reason": reason,
        "retained_legacy_C": {case: retained[case] for case in RETAINED_C},
        "retained_D26": {case: retained[case] for case in RETAINED_D},
        "new_native_C": list(NATIVE_C),
        "new_labelled_D25": list(LABELLED_D),
        "cancelled": ["remaining_legacy_C", "D25-1", "D25-2", "G"],
        "fixed_choices": {
            "Q4_knowledge": "B-TFEB-1",
            "F26_new": [f"C-native-q{q}-1" for q in (2, 4, 5, 7)],
            "S_delivery": "E-S26-1",
        },
        "model_limits": ScienceBudget.model_limits,
        "group_limits": ScienceBudget.group_limits,
        "matrix_maximum_requests": 38,
        "unallocated_requests": 1,
        "source_hashes": files,
        "formal_models": models,
        "runtime_config": current.model_dump(mode="json"),
        "implementation_hashes": hashes(),
        "note": "Legacy failures remain charged and unchanged; no substitution by a better sample.",
    }
    write_json(path, value)
    return value


def amend_schema_matrix(session, *, reason):
    """Retain the failed native sample and freeze only the seven unspent C requests."""
    if not reason or not reason.strip():
        raise ValueError("matrix_amendment_requires_explicit_reason")
    if session.revision != "initial":
        raise ValueError("schema_matrix_reuses_initial_fixed_materials")
    path = session.output / "matrix-amendment-schema-professional.json"
    if path.exists():
        return read(path)
    files = {}
    previous_path = session.output / "matrix-amendment-native-professional.json"
    previous = read(retain_file(files, previous_path))
    if not source_unchanged(previous["source_hashes"]):
        raise ValueError("retained_samples_changed_after_matrix_amendment")
    files.update(previous["source_hashes"])
    retained = {}
    for case in RETAINED_NATIVE_C:
        result = read(retain_file(files, session.output / "cases" / case / "result.json"))
        retained[case] = {key: result.get(key) for key in ("status", "new_requests", "wire_digest")}
    for case in set(NATIVE_C) - set(RETAINED_NATIVE_C):
        if (session.output / "cases" / case / "result.json").exists():
            raise ValueError("unallocated_native_case_has_result:" + case)
    current = runtime_config()
    models = {
        tier: current.routed_model("specialist", tier, formal=True).model_dump(mode="json")
        for tier in ("normal", "strong")
    }
    if models != previous["formal_models"]:
        raise ValueError("schema_amendment_must_keep_native_models")
    value = {
        "matrix_version": MATRIX_VERSION,
        "reason": reason,
        "previous_amendment": str(previous_path.resolve()),
        "retained_legacy_C": list(RETAINED_C),
        "retained_native_C": retained,
        "new_schema_C": list(SCHEMA_C),
        "cancelled_native_C": [case for case in NATIVE_C if case not in RETAINED_NATIVE_C],
        "fixed_choices": {
            "Q4_knowledge": "B-TFEB-1",
            "F26_new": [f"C-schema-q{q}-1" for q in (2, 4, 5, 7)],
            "S_delivery": "E-S26-1",
        },
        "model_limits": ScienceBudget.model_limits,
        "group_limits": ScienceBudget.group_limits,
        "matrix_maximum_requests": 38,
        "unallocated_requests": 1,
        "source_hashes": files,
        "formal_models": models,
        "runtime_config": current.model_dump(mode="json"),
        "implementation_hashes": hashes(),
        "note": (
            "The paid native Q2 failure remains unchanged. Q2 has one schema-version sample; "
            "only Q4/Q5/Q7 have two same-version repetitions. No additional request is allocated."
        ),
    }
    write_json(path, value)
    return value


def configuration_for(session, case):
    config = runtime_config()
    if case in (*SCHEMA_C, *LABELLED_D):
        name = "schema" if case in SCHEMA_C else "native"
        path = session.output / f"matrix-amendment-{name}-professional.json"
        if not path.exists():
            raise ValueError("new_case_requires_explicit_matrix_amendment")
        amendment = read(path)
        if not source_unchanged(amendment["source_hashes"]):
            raise ValueError("retained_samples_changed_after_matrix_amendment")
        if case in SCHEMA_C:
            for tier, expected in amendment["formal_models"].items():
                actual = config.routed_model("specialist", tier, formal=True).model_dump(
                    mode="json"
                )
                if actual != expected:
                    raise ValueError("schema_C_model_changed_after_matrix_amendment")
    return config


def source_unchanged(files):
    return all(
        Path(path).is_file() and file_hash(path) == expected for path, expected in files.items()
    )


async def run_case(session, case, *, live=False):
    if case in READ_ONLY_CASES and case not in CASES:
        raise ValueError("legacy_case_is_read_only_after_matrix_amendment")
    if case not in CASES:
        raise ValueError("unknown_fixed_case")
    folder = session.output / ("cases" if live else "preview") / case
    result_path = folder / "result.json"
    if result_path.exists():
        saved = read(result_path)
        if live and saved["status"] == "completed":
            return {**saved, "cache_or_local_replay": True, "new_requests": 0}
        raise ValueError("preserve_existing_case_use_explicit_revision")
    if case in READ_ONLY_CASES:
        raise ValueError("legacy_case_is_read_only_after_matrix_amendment")
    implementation = hashes()
    runtime_configuration = configuration_for(session, case)
    config = runtime_configuration.model_dump(mode="json")
    value = {
        "case": case,
        "revision": session.revision,
        "live": live,
        "implementation_hashes": implementation,
        "runtime_config": config,
        "manuscript_config": manuscript_config().model_dump(mode="json"),
    }
    expected = None
    if live:
        frozen_manifest = read(session.output / "manifest.json")
        if not source_unchanged(frozen_manifest["source_hashes"]):
            raise ValueError("source_changed_after_manifest")
        preview_folder = session.output / "preview" / case
        planned = read(preview_folder / "result.json")
        if planned["status"] != "previewed" or not planned["capacity"]["fits"]:
            raise ValueError("successful_preview_required")
        # The recorder compares the exact production wire, including model and schema.
        # Unrelated edits in another experiment group do not create a new scientific sample.
        if not source_unchanged(planned["source_hashes"]):
            raise ValueError("fixed_sources_changed_after_preview")
        expected = [read(preview_folder / "request-001/wire.json")]
    recorder = WireRecorder(
        folder,
        budget=session.budget if live else None,
        expected=expected,
        preview=not live,
        allow_following=CASES[case][1] > 1,
    )
    runtime = scoped_runtime(
        recorder, session.budget if live else None, config=runtime_configuration
    )
    before = len(session.budget.data["requests"])
    try:
        with session.budget.case(case, session.revision):
            value.update(await operation(session, case, folder, runtime, recorder))
        value.setdefault("status", "completed")
    except PreviewCaptured:
        value["status"] = "previewed"
    except BaseException as exc:
        value.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        await runtime.close()
        value.pop("records", None)
        if recorder.wires:
            value["capacity"] = capacity_for(recorder.wires[0])
            value["wire_digest"] = digest(recorder.wires[0])
            value["requests"] = [
                {"wire_digest": digest(wire), "capacity": capacity_for(wire)}
                for wire in recorder.wires
            ]
        fixture = folder / "fixture.json"
        value["source_hashes"] = read(fixture)["source_hashes"] if fixture.exists() else {}
        value["sources_unchanged"] = source_unchanged(value["source_hashes"])
        value["new_requests"] = len(session.budget.data["requests"]) - before
        write_json(result_path, value)
        write_json(session.root / "usage.json", session.budget.report())
    return value


def manifest(session):
    path = session.output / "manifest.json"
    if path.exists():
        return read(path)
    files = {}
    for sample in ROOTS:
        files.update(archived_application(sample)[3])
    for topic in KNOWLEDGE:
        files.update(literature_fixture(f"B-{topic}-1")["source_hashes"])
    value = {
        "version": VERSION,
        "revision": session.revision,
        "implementation_hashes": hashes(),
        "runtime_config": runtime_config().model_dump(mode="json"),
        "manuscript_config": manuscript_config().model_dump(mode="json"),
        "source_hashes": files,
        "model_limits": ScienceBudget.model_limits,
        "cases": {
            case: {"group": group, "requests": count} for case, (group, count) in CASES.items()
        },
        "fixed_choices": {
            "Q4_knowledge": "B-TFEB-1",
            "F26_new": [f"C-schema-q{q}-1" for q in (2, 4, 5, 7)],
            "S_delivery": "E-S26-1",
        },
        "scope": (
            "Cached original material only; no retrieval, embedding, research loop or translation."
        ),
    }
    write_json(path, value)
    return value


def matrix_preview(session):
    config = manuscript_config()
    runtime = runtime_config()
    literature_tokens = runtime.routed_model("literature", "normal", formal=True).max_tokens
    professional_tokens = runtime.routed_model("specialist", "normal", formal=True).max_tokens
    question_tokens = runtime.routed_model("question_generation", "strong", formal=True).max_tokens
    retained_output = sum(
        row["output"]
        for row in session.budget.data["requests"]
        if row["probe"]["case"] in (*RETAINED_C, *RETAINED_NATIVE_C)
    )
    capacities = {
        "B": 4 * literature_tokens,
        "C": retained_output + len(SCHEMA_C) * professional_tokens,
        "D": 5 * question_tokens,
        "E": 5 * (config.assessment_tokens + config.scientific_tokens + config.decision_tokens),
        "F": 3 * max(config.summary_tokens, config.author_tokens),
        "G": 0,
    }
    cases = {}
    for case in CASES:
        path = session.output / "preview" / case / "result.json"
        if path.exists():
            row = read(path)
            cases[case] = {
                key: row.get(key) for key in ("status", "capacity", "wire_digest", "reason")
            }
    value = {
        "version": VERSION,
        "matrix_version": MATRIX_VERSION,
        "fixed_main_requests": 38,
        "retained_legacy_C_requests": 3,
        "retained_native_C_requests": 1,
        "new_schema_C_requests": 7,
        "conditional_requests": 0,
        "unallocated_requests": 1,
        "limits": ScienceBudget.model_limits,
        "output_reservation_by_group": capacities,
        "maximum_output_reservation": sum(capacities.values()),
        "output_matrix_fits": sum(capacities.values()) <= ScienceBudget.model_limits["output"],
        "previews": cases,
        "missing_previews": [case for case in CASES if case not in cases],
        "dynamic_input_note": (
            "Q4 depends on B-TFEB-1; F26-new on each first C product; T decision and F author "
            "input depend on their designated prior response. Preview those exact inputs before "
            "dispatch. Output reservation is an upper bound, not measured spending."
        ),
        "usage": session.budget.report(),
    }
    write_json(session.output / "matrix-preview-schema-professional.json", value)
    return value


async def main(args):
    with ScienceSession(args.output, args.revision) as session:
        if args.stage == "manifest":
            value = manifest(session)
        elif args.stage == "usage":
            value = session.budget.report()
        elif args.stage == "matrix":
            value = matrix_preview(session)
        elif args.stage == "amend-native":
            value = amend_matrix(session, reason=args.reason)
        elif args.stage == "amend-schema":
            value = amend_schema_matrix(session, reason=args.reason)
        else:
            value = []
            for case in args.cases or []:
                result = (
                    await publish_scientific(session, case)
                    if args.stage == "publish"
                    else await run_case(session, case, live=args.stage == "run")
                )
                value.append(
                    {
                        key: result.get(key)
                        for key in ("case", "status", "new_requests", "reason", "capacity")
                    }
                )
                if result.get("status", "completed") not in {"previewed", "completed"}:
                    break
        print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=(
            "manifest", "preview", "run", "matrix", "usage", "publish", "amend-native",
            "amend-schema",
        ),
    )
    parser.add_argument("--output", type=Path, default=Path("runs/probe-mytest26-science-20261014"))
    parser.add_argument("--revision", default="initial")
    parser.add_argument("--cases", nargs="+", choices=tuple(CASES))
    parser.add_argument("--reason")
    asyncio.run(main(parser.parse_args()))
