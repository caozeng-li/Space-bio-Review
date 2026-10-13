"""V4 public entry: ranked question research, bounded synthesis and bilingual delivery."""

import asyncio
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.admission import ProposalAdmission, load_admission_config
from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.diagnostics import SubmissionRecorder, digest
from spacebio_review.application import ApplicationBundle
from spacebio_review.context import load_application
from spacebio_review.evidence import EvidenceResources, LiteratureResearcher, load_evidence_config
from spacebio_review.models import load_config as load_runtime_config
from spacebio_review.models.provider import model_class
from spacebio_review.review.project import load_preparation_config
from spacebio_review.v4.budget import Budget
from spacebio_review.v4.config import load_config, load_reporting
from spacebio_review.v4.coordinator import Coordinator
from spacebio_review.v4.execution import Execution
from spacebio_review.v4.preparation import Preparation
from spacebio_review.v4.records import Records


class ReviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime_config: Path = Path("config/runtime.yaml")
    review_config: Path = Path("config/review.yaml")
    evidence_config: Path = Path("config/evidence.yaml")
    admission_config: Path = Path("config/admission.yaml")
    reporting_config: Path = Path("config/reporting.yaml")
    evaluation_skill: str | None = None
    env_file: str = ".env"
    output: Path = Path("runs/review")
    resume: bool = False


class ReviewResult(BaseModel):
    schema_version: str = "4.2"
    research_requests: list[dict] = Field(default_factory=list)
    research_history: dict[str, list[str]] = Field(default_factory=dict)
    status: str = "failed"
    stage: str = "admission"
    scientific_status: str = "not_run"
    reporting_status: str = "not_run"
    research_status: str = "not_run"
    coverage: dict = Field(default_factory=dict)
    scientific_limits: list[str] = Field(default_factory=list)
    tasks_used: int = 0
    result_path: str = ""
    report_path: str = ""
    report_en_path: str = ""
    appendix_path: str = ""
    metrics_path: str = ""
    recommendation: str | None = None
    admission: dict | None = None
    application: dict = Field(default_factory=dict)
    project: dict = Field(default_factory=dict)
    questions: dict = Field(default_factory=dict)
    results: dict = Field(default_factory=dict)
    assessment: dict | None = None
    snapshot: str | None = None
    delivery: dict = Field(default_factory=dict)
    tasks: list[dict] = Field(default_factory=list)
    technical_limits: list[str] = Field(default_factory=list)


def export(output, result):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    result.result_path = "review.json"
    result.report_path = "report.md"
    result.report_en_path = "report.en.md"
    result.appendix_path = "appendix.md"
    from spacebio_review.v4.records import write_json

    write_json(output / result.result_path, result)
    for name in (result.report_path, result.report_en_path, result.appendix_path):
        path = output / name
        if not path.exists():
            path.write_text(
                "# Review delivery status\n\nNo report available.\n"
                + "\n".join(result.technical_limits),
                "utf-8",
            )

    # Persist portable paths; expose resolved paths to callers of this run.
    for field in ("result_path", "report_path", "report_en_path", "appendix_path"):
        setattr(result, field, str((output / getattr(result, field)).resolve()))


async def review_bundle(
    runtime,
    store,
    literature,
    bundle,
    output,
    config,
    preparation_config,
    reporting,
    *,
    records=None,
    result=None,
    coordinator=None,
):
    records = records or Records(output)
    ex = (
        coordinator.ex
        if coordinator
        else Execution(
            runtime, records, config, {"prompt": preparation_config.evaluation.prompt()}, reporting
        )
    )
    coordinator = coordinator or Coordinator(ex)
    result = result or ReviewResult(application=bundle.model_dump(mode="json"))
    try:
        prep = Preparation(ex, store, bundle, preparation_config)
        values = await coordinator.run(prep, literature, output)
        result = result.model_copy(update=values)
    except Exception as exc:
        result = result.model_copy(update=coordinator.public_result())
        result.technical_limits.append(f"{type(exc).__name__}: {exc}")
    export(output, result)
    return result


def read_result(path):
    data = json.loads(Path(path).read_text("utf-8"))
    root = Path(path).resolve().parent
    resolved = {
        k: str((root / v).resolve())
        for k, v in data.items()
        if k in {"result_path", "report_path", "report_en_path", "appendix_path"}
        and v
        and not Path(v).is_absolute()
    }
    return {
        "schema_version": data.get("schema_version", 1),
        "data": data,
        "resolved_paths": resolved,
    }


async def run_review(application_paths, config=None):
    config = config or ReviewConfig()
    output = config.output.resolve()
    if not config.resume and output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty output directory or --resume for a V4.2 run")
    if config.resume and not (output / "v4/state.json").is_file():
        raise ValueError("Resume requires a V4.2 checkpoint")
    records = Records(output)  # Reject legacy versions before any model construction.
    paths = [Path(p).resolve() for p in application_paths]
    if not paths:
        raise ValueError("A main application is required")
    runtime_config = load_runtime_config(config.runtime_config)
    if config.evaluation_skill:
        runtime_config.evaluation_skill = config.evaluation_skill
    review_config = load_config(config.review_config)
    prep_config = load_preparation_config(review_config.preparation_config)
    reporting = load_reporting(config.reporting_config)
    evidence_config = load_evidence_config(config.evidence_config)
    admission_config = load_admission_config(config.admission_config)
    manifest = {
        "files": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        "configuration": digest(
            {
                "runtime": runtime_config.model_dump(mode="json"),
                "review": review_config.model_dump(mode="json", exclude={"max_questions"}),
                "preparation": prep_config.model_dump(mode="json"),
                "reporting": reporting.model_dump(mode="json"),
                "evidence": evidence_config.model_dump(mode="json"),
                "admission": admission_config.model_dump(mode="json"),
            }
        ),
        "max_questions": review_config.max_questions,
    }
    metadata = records.state["metadata"]
    previous_manifest = dict(metadata.get("manifest") or {})
    if not records.state.get("question_selection"):
        previous_manifest["max_questions"] = manifest["max_questions"]
    if config.resume and previous_manifest != manifest:
        raise ValueError("Resume input/configuration differs; use a new output directory")
    runtime = evidence = literature = coordinator = None
    result = ReviewResult()
    budget = Budget(records, review_config, reporting)
    # A conservative minimum final synthesis/summary token floor, within the same totals.
    final_requests = min(8, review_config.completion_reserve_calls)
    budget.completion_tokens.update(
        input=review_config.context_tokens * final_requests,
        output=max(
            runtime_config.routed_model(kind, "normal", formal=True).max_tokens
            for kind in ("assessment", "writer")
        )
        * final_requests,
    )
    try:
        runtime = AgentRuntime.from_config(
            runtime_config,
            model_factory=lambda c: model_class(c)(c, env_file=config.env_file, budget=budget),
        )
        runtime.scheduler.request_budget = budget  # Embeddings use the same ledger.
        runtime.recorder = SubmissionRecorder(output, runtime_config.submission_max_bytes)
        ex = Execution(
            runtime, records, review_config, {"prompt": prep_config.evaluation.prompt()}, reporting
        )
        coordinator = Coordinator(ex, budget)
        coordinator.metadata(manifest=manifest)
        evidence = EvidenceResources.create(
            runtime, output, evidence_config, env_file=config.env_file
        )
        if config.resume and metadata.get("application"):
            evidence.store.restore()
            bundle = ApplicationBundle.model_validate(metadata["application"])
        else:
            bundle = await load_application(evidence.store, paths[0], paths[1:])
            coordinator.metadata(application=bundle.model_dump(mode="json"))
        result.application = bundle.model_dump(mode="json")
        async with asyncio.timeout(budget.remaining_seconds()):
            if metadata.get("admission", {}).get("status") == "completed":
                result.admission = metadata["admission"]
            else:
                admission = await ProposalAdmission(
                    runtime, evidence.store, admission_config
                ).check(bundle)
                result.admission = admission.model_dump(mode="json")
                coordinator.metadata(admission=result.admission)
            (output / "admission.json").write_text(
                json.dumps(result.admission, ensure_ascii=False, indent=2), "utf-8"
            )
            decision = result.admission["decision"]["decision"]
            if decision != "admit":
                result.status = "rejected" if decision == "reject" else "failed"
            else:
                literature = LiteratureResearcher.from_resources(evidence, env_file=config.env_file)
                result = await review_bundle(
                    runtime,
                    evidence.store,
                    literature,
                    bundle,
                    output,
                    review_config,
                    prep_config,
                    reporting,
                    records=records,
                    result=result,
                    coordinator=coordinator,
                )
    except Exception as exc:
        if coordinator:
            result = result.model_copy(update=coordinator.public_result())
        result.technical_limits.append(f"{type(exc).__name__}: {exc}")
    finally:
        budget.save()
        if runtime:
            result.metrics_path = str(output / "metrics.json")
            Path(result.metrics_path).write_text(
                json.dumps(runtime.scheduler.metrics(), ensure_ascii=False, indent=2), "utf-8"
            )
        export(output, result)
        if literature:
            await literature.close()
        if evidence:
            await evidence.close()
        if runtime:
            await runtime.close()
    return result
