"""The v5 public entry: continuing chief review and direct report delivery."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.persistence import plain, write_json
from spacebio_review.review.evaluation import get_evaluation_system
from spacebio_review.v5.entry import load_settings, prepare_review, validate_run


class ReviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime_config: Path = Path("config/runtime.yaml")
    review_config: Path = Path("config/review.yaml")
    evidence_config: Path | None = None
    admission_config: Path = Path("config/admission.yaml")
    reporting_config: Path = Path("config/reporting.yaml")
    env_file: str = ".env"
    output: Path = Path("runs/review")
    resume: bool = False


class ReviewResult(BaseModel):
    schema_version: str = "v5-review-result-1"
    evaluation_system: dict = Field(default_factory=dict)
    status: str = "failed"
    stage: str = "admission"
    scientific_status: str = "not_run"
    reporting_status: str = "not_run"
    research_status: str = "not_run"
    assessment_status: str = "not_run"
    admission: dict | None = None
    application: dict = Field(default_factory=dict)
    chief: dict = Field(default_factory=dict)
    topics: list[dict] = Field(default_factory=list)
    agenda: dict = Field(default_factory=dict)
    research_scope: dict = Field(default_factory=dict)
    scientific_unknowns: list[str | dict] = Field(default_factory=list)
    assessment: dict | None = None
    dimension_assessments: dict = Field(default_factory=dict)
    total_score: int | None = None
    score_grade: str | None = None
    safety_veto: dict | None = None
    recommendation: str | None = None
    support_scope: dict | None = None
    publication: dict = Field(default_factory=dict)
    delivery: dict = Field(default_factory=dict)
    usage: dict = Field(default_factory=dict)
    technical_limits: list[str] = Field(default_factory=list)
    result_path: str = "review.json"
    report_path: str = ""
    report_en_path: str = ""
    appendix_path: str = ""
    metrics_path: str = ""


def _resolve_paths(output, result):
    for field in ("result_path", "report_path", "report_en_path", "appendix_path", "metrics_path"):
        value = getattr(result, field)
        if value and not Path(value).is_absolute():
            setattr(result, field, str((Path(output) / value).resolve()))
    return result


def export(output, result):
    output = Path(output)
    paths = result.delivery.get("paths", {})
    result.result_path = "review.json"
    result.report_path = paths.get("zh", "")
    result.report_en_path = paths.get("en", "")
    result.appendix_path = paths.get("appendix", "")
    excluded = (
        {"total_score", "score_grade", "safety_veto"}
        if result.evaluation_system.get("id") == "general"
        else set()
    )
    write_json(output / result.result_path, result.model_dump(mode="json", exclude=excluded))
    return _resolve_paths(output, result)


def read_result(path):
    """Read current or historical public results without executing or migrating a run."""
    path = Path(path)
    data = json.loads(path.read_text("utf-8"))
    root = path.resolve().parent
    return {
        "schema_version": data.get("schema_version", 1),
        "data": data,
        "resolved_paths": {
            key: str((root / value).resolve())
            for key, value in data.items()
            if key
            in {"result_path", "report_path", "report_en_path", "appendix_path", "metrics_path"}
            and value
            and not Path(value).is_absolute()
        },
    }


def _record_chief(result, research):
    result.chief = research
    result.topics = research.get("topics", [])
    result.agenda = research.get("agenda", {})
    result.research_scope = research.get("reading_records", {})
    result.scientific_unknowns = (research.get("note") or {}).get("unknowns", [])
    result.research_status = research.get("status", "partial")
    result.assessment = research.get("assessment")
    if result.assessment:
        result.assessment_status = "completed"
        result.scientific_status = research.get("scientific_status", "partial")
        result.dimension_assessments = result.assessment.get("dimensions", {})
        result.scientific_unknowns = result.assessment.get("unknowns", [])
        for field in (
            "total_score",
            "score_grade",
            "safety_veto",
            "recommendation",
            "support_scope",
        ):
            if field in result.assessment:
                setattr(result, field, result.assessment[field])


async def run_review(application_paths, config=None):
    """The sole production engine. Local acceptance calls individual boundaries instead."""
    from spacebio_review.v5.delivery import deliver_assessment

    config = config or ReviewConfig()
    settings = load_settings(config)
    _, checkpoint = validate_run(application_paths, config, settings)
    output = config.output.resolve()
    if checkpoint.get("status") == "completed" and (output / "review.json").is_file():
        saved = read_result(output / "review.json")["data"]
        if saved.get("status") == "completed":
            return _resolve_paths(output, ReviewResult.model_validate(saved))

    result = ReviewResult(
        evaluation_system=get_evaluation_system(settings.review.evaluation_system).snapshot()
    )
    prepared = None
    try:
        prepared = await prepare_review(application_paths, config)
        result.application, result.admission = plain(prepared.bundle), plain(prepared.admission)
        if not prepared.admission.can_proceed:
            result.status = "rejected" if prepared.admission.status == "completed" else "failed"
            return result
        result.stage = "research"
        research = await prepared.chief.run(prepared.chief_id, mode="assessment")
        _record_chief(result, research)
        if not result.assessment:
            result.status = "partial"
            if research.get("reason"):
                result.technical_limits.append(research["reason"])
            return result
        if not research.get("report_ref"):
            result.stage, result.status = "assessment", "partial"
            if research.get("reason"):
                result.technical_limits.append(research["reason"])
            return result
        result.stage = "delivery"
        model_alias = prepared.settings.runtime.auxiliary.get("translation")
        result.delivery = await deliver_assessment(
            prepared.runtime,
            result.assessment,
            evidence=research.get("evidence", {}),
            topics=research.get("selected_topics", []),
            admission=result.admission,
            english=prepared.settings.reporting.english_enabled,
            translation_model=prepared.runtime.models.get(model_alias),
            batch_chars=prepared.settings.reporting.material_batch_chars,
            root=output,
        )
        result.publication = result.delivery.get("publication", {})
        result.reporting_status = result.delivery["status"]
        result.status = "completed" if result.reporting_status == "completed" else "partial"
        result.stage = "completed" if result.status == "completed" else "delivery"
    except Exception as exc:
        if prepared and prepared.chief_id:
            _record_chief(result, prepared.chief.result(prepared.chief_id))
        result.status = "partial" if result.chief or result.assessment else "failed"
        result.technical_limits.append(f"{type(exc).__name__}: {exc}")
    finally:
        if prepared:
            result.usage = prepared.runtime.budget.usage()
            result.metrics_path = "metrics.json"
            write_json(output / "metrics.json", prepared.runtime.scheduler.metrics())
            export(output, result)
            prepared.checkpoint.update(status=result.status, stage=result.stage)
            prepared.save()
            await prepared.close()
        else:
            export(output, result)
    return result
