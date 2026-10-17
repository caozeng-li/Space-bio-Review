"""Production configuration and the material/admission boundary before chief research."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.admission import (
    AdmissionConfig,
    AdmissionRun,
    ProposalAdmission,
    admission_report,
    load_admission_config,
)
from spacebio_review.agents import AgentRuntime
from spacebio_review.agents.skills import SkillLibrary
from spacebio_review.application import ApplicationBundle
from spacebio_review.context import load_application
from spacebio_review.evidence.research import (
    EvidenceConfig,
    EvidenceResources,
    load_evidence_config,
)
from spacebio_review.models.embedding import DashScopeEmbedding, VectorIndex
from spacebio_review.models.provider import model_class
from spacebio_review.models.scheduling import RequestScheduler, ScheduledModel
from spacebio_review.persistence import plain, write_json
from spacebio_review.review.evaluation import get_evaluation_system
from spacebio_review.review.expertise import ExpertiseCatalogue

from .budget import Budget, SessionAllowance
from .chief import ChiefReview
from .config import SessionConfig, load_config
from .session import ResearchRuntime
from .sources import VisualReader
from .topic import TopicResearch


class ReviewSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_system: Literal["general", "gbt42048_2022"] = "general"
    external_research_enabled: bool = True
    historical_as_of: date | None = None
    parallel_topics: int = Field(default=4, ge=1)


class DeliverySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    english_enabled: bool = True
    material_batch_chars: int = Field(default=18000, ge=1000)


@dataclass
class EntrySettings:
    runtime: SessionConfig
    review: ReviewSettings
    reporting: DeliverySettings
    evidence: EvidenceConfig
    admission: AdmissionConfig
    skills: SkillLibrary


def load_settings(options):
    runtime = load_config(options.runtime_config)
    review = ReviewSettings.model_validate(
        yaml.safe_load(Path(options.review_config).read_text("utf-8"))
    )
    reporting = DeliverySettings.model_validate(
        yaml.safe_load(Path(options.reporting_config).read_text("utf-8"))
    )
    admission = load_admission_config(options.admission_config)
    if review.evaluation_system == "general" or admission.formal_review_enabled:
        if "admission" not in runtime.auxiliary:
            raise ValueError("v5 runtime auxiliary.admission is required for enabled admission")
    if reporting.english_enabled and "translation" not in runtime.auxiliary:
        raise ValueError("v5 runtime auxiliary.translation is required for English delivery")
    return EntrySettings(
        runtime,
        review,
        reporting,
        load_evidence_config(options.evidence_config or runtime.evidence_config),
        admission,
        SkillLibrary(runtime.skills_dir),
    )


def input_manifest(paths, settings):
    """Fingerprint material and effective configuration; never read credential values."""
    configuration = {
        key: plain(getattr(settings, key))
        for key in ("runtime", "review", "reporting", "evidence", "admission")
    }
    configuration["expertise"] = yaml.safe_load(
        settings.runtime.expertise_config.read_text("utf-8")
    )
    configuration["skills"] = {
        path.relative_to(settings.runtime.skills_dir).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(settings.runtime.skills_dir.rglob("*.md"))
    }
    configuration["evaluation"] = get_evaluation_system(
        settings.review.evaluation_system
    ).snapshot()
    return {
        "files": [
            {"path": path.as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in paths
        ],
        "configuration": hashlib.sha256(
            json.dumps(configuration, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
    }


def validate_run(application_paths, options, settings):
    paths = [Path(path).resolve() for path in application_paths]
    if not paths:
        raise ValueError("A main application is required")
    output = options.output.resolve()
    state_path = output / "v5" / "review-state.json"
    if options.resume:
        if not state_path.is_file():
            raise ValueError("Resume requires an existing review checkpoint")
        checkpoint = json.loads(state_path.read_text("utf-8"))
    else:
        if output.exists() and any(output.iterdir()):
            raise ValueError("Use an empty output directory or --resume for a v5 run")
        checkpoint = {}
    manifest = input_manifest(paths, settings)
    if options.resume and checkpoint.get("manifest") != manifest:
        raise ValueError("Resume input/configuration differs; use a new output directory")
    checkpoint["manifest"] = manifest
    return paths, checkpoint


class _AdmissionScheduler:
    """Admission keeps its existing direct executor, with v5 accounting at the provider."""

    request_budget = None

    def __init__(self, shared):
        self.shared = shared

    def __getattr__(self, name):
        return getattr(self.shared, name)


@dataclass
class PreparedReview:
    runtime: ResearchRuntime
    topics: TopicResearch
    chief: ChiefReview | None
    chief_id: str | None
    bundle: ApplicationBundle
    admission: AdmissionRun
    settings: EntrySettings
    checkpoint: dict

    def save(self):
        write_json(self.runtime.store.root / "review-state.json", self.checkpoint)

    async def close(self):
        self.runtime.budget.save()
        await self.topics.close()


async def prepare_review(
    application_paths,
    options,
    *,
    model_factory=None,
    admission_factory=ProposalAdmission,
):
    """Load materials and actual admission, create/resume the chief, then stop.

    This boundary does not execute research, scoring, or report delivery. Local
    wiring probes can replace models or admission while retaining real assembly.
    """
    settings = load_settings(options)
    paths, checkpoint = validate_run(application_paths, options, settings)
    output, config = options.output.resolve(), settings.runtime
    write_json(output / "v5" / "review-state.json", checkpoint)
    budget = Budget(output, config.budget)
    models, owned = {}, []
    try:
        aliases = set(config.roles.values())
        auxiliaries = {"visual", "delivery_parser"}
        if settings.review.external_research_enabled and settings.evidence.model_rerank:
            auxiliaries.add("rerank")
        if (
            settings.review.evaluation_system == "general"
            or settings.admission.formal_review_enabled
        ):
            auxiliaries.add("admission")
        if settings.reporting.english_enabled:
            auxiliaries.add("translation")
        aliases.update(config.auxiliary[kind] for kind in auxiliaries if kind in config.auxiliary)
        for alias, model_config in config.models.items():
            if alias not in aliases:
                continue
            model = (
                model_factory(model_config, budget=budget)
                if model_factory
                else model_class(model_config)(
                    model_config, env_file=options.env_file, budget=budget
                )
            )
            models[alias] = model
            owned.append(model)
        runtime = ResearchRuntime(
            output,
            models,
            config.roles,
            scheduler=RequestScheduler(config.concurrency),
            budget=budget,
            settings=config.session,
            session_overrides=config.session_overrides,
            skills=settings.skills,
            allowances=config.allowances,
            delivery_parser=models[config.auxiliary["delivery_parser"]],
        )
        runtime._owned_models = owned
        owned = [runtime]
        runtime.scheduler.request_budget = budget
        resources = EvidenceResources.create(
            runtime, output, settings.evidence, env_file=options.env_file
        )
        owned.insert(0, resources)
        if checkpoint.get("application"):
            bundle = ApplicationBundle.model_validate(checkpoint["application"])
        else:
            bundle = await load_application(resources.store, paths[0], paths[1:])
            checkpoint["application"] = plain(bundle)
            write_json(runtime.store.root / "review-state.json", checkpoint)

        previous_admission = checkpoint.get("admission", {})
        if previous_admission.get("status") in {"completed", "skipped"}:
            admission = AdmissionRun.model_validate(previous_admission)
        else:
            alias = config.auxiliary.get("admission", config.roles["chief"])
            admission_runtime = AgentRuntime(
                {"primary": models.get(alias, models[config.roles["chief"]])},
                settings.skills,
                get_evaluation_system(settings.review.evaluation_system).skill,
                scheduler=_AdmissionScheduler(runtime.scheduler),
            )
            budget.register("admission", SessionAllowance(completion_calls=0))
            budget.activate("admission")
            with budget.scope("admission", purpose="auxiliary"):
                async with asyncio.timeout(budget.remaining_seconds()):
                    admission = await admission_factory(
                        admission_runtime,
                        resources.store,
                        settings.admission,
                        evaluation_system=settings.review.evaluation_system,
                    ).check(bundle)
            budget.finish("admission")
            checkpoint["admission"] = plain(admission)
            write_json(runtime.store.root / "review-state.json", checkpoint)
            write_json(output / "admission.json", admission)
            (output / "admission.md").write_text(admission_report(admission), "utf-8")

        auxiliary = {
            kind: ScheduledModel(models[alias], runtime.scheduler, alias)
            for kind, alias in config.auxiliary.items()
            if alias in models
        }
        index = None
        if config.embedding and settings.review.external_research_enabled:
            embedder = DashScopeEmbedding(
                config.embedding, runtime.scheduler, env_file=options.env_file
            )
            owned.insert(0, embedder)
            index = VectorIndex(embedder, cache_dir=config.embedding.cache_dir)
        topics = TopicResearch(
            runtime,
            resources.store,
            resources.discovery,
            application_ids=bundle.source_ids,
            cutoff=settings.review.historical_as_of,
            rerank_model=auxiliary.get("rerank") if resources.config.model_rerank else None,
            index=index,
            visual_reader=VisualReader(auxiliary["visual"], resources.store)
            if "visual" in auxiliary
            else None,
            catalogue=ExpertiseCatalogue(config.expertise_config, settings.skills),
            external_research_enabled=settings.review.external_research_enabled,
            parallel_topics=settings.review.parallel_topics,
        )
        topics._owned = owned
        chief, chief_id = None, None
        if admission.can_proceed:
            chief = ChiefReview(
                topics,
                evaluation_system=settings.review.evaluation_system,
                admission=plain(admission),
            )
            chief_id = checkpoint.get("chief_id")
            if chief_id:
                runtime.load(chief_id)
            else:
                chief_id = chief.create(
                    "阅读完整计划书，围绕会改变科学判断的问题开展研究；"
                    "按所选评价体系形成最终评价及中文摘要。"
                )
                checkpoint["chief_id"] = chief_id
        prepared = PreparedReview(
            runtime, topics, chief, chief_id, bundle, admission, settings, checkpoint
        )
        prepared.save()
        return prepared
    except BaseException:
        budget.save()
        for resource in owned:
            await resource.close()
        raise
