"""Production configuration and the material/admission boundary before chief research."""

from __future__ import annotations

import asyncio
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
from spacebio_review.models.provider import DashScopeModel
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


def validate_run(application_paths, options):
    paths = [Path(path).resolve() for path in application_paths]
    if not paths:
        raise ValueError("A main application is required")
    output = options.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty output directory for a new review")
    return paths


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
    """Load materials and actual admission, create the chief, then stop.

    This boundary does not execute research, scoring, or report delivery. Local
    wiring probes can replace models or admission while retaining real assembly.
    """
    settings = load_settings(options)
    paths = validate_run(application_paths, options)
    output, config = options.output.resolve(), settings.runtime
    budget = Budget(output, config.budget)
    scheduler = RequestScheduler(config.concurrency)
    scheduler.request_budget = budget
    models, owned = {}, []
    try:
        aliases = set(config.roles.values())
        auxiliaries = {"visual", "assembly"}
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
                model_factory(model_config, budget=budget, scheduler=scheduler)
                if model_factory
                else DashScopeModel(
                    model_config, env_file=options.env_file, budget=budget, scheduler=scheduler
                )
            )
            models[alias] = model
            owned.append(model)
        runtime = ResearchRuntime(
            output,
            models,
            config.roles,
            scheduler=scheduler,
            budget=budget,
            settings=config.session,
            session_overrides=config.session_overrides,
            skills=settings.skills,
            allowances=config.allowances,
            assembly_route=config.auxiliary["assembly"],
        )
        runtime._owned_models = owned
        owned = [runtime]
        resources = EvidenceResources.create(
            runtime, output, settings.evidence, env_file=options.env_file
        )
        owned.insert(0, resources)
        bundle = await load_application(resources.store, paths[0], paths[1:])
        write_json(output / "application.json", bundle)
        alias = config.auxiliary.get("admission", config.roles["chief"])
        admission_runtime = AgentRuntime(
            {"primary": models.get(alias, models[config.roles["chief"]])},
            settings.skills,
            get_evaluation_system(settings.review.evaluation_system).skill,
            scheduler=_AdmissionScheduler(runtime.scheduler),
            evidence_config=settings.evidence,
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
                config.embedding, runtime.scheduler, env_file=options.env_file, budget=budget
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
            evidence_config=resources.config,
        )
        topics._owned = owned
        chief, chief_id = None, None
        if admission.can_proceed:
            chief = ChiefReview(
                topics,
                evaluation_system=settings.review.evaluation_system,
                admission=plain(admission),
            )
            chief_id = chief.create(
                "阅读完整计划书，围绕会改变科学判断的问题开展研究；"
                "理解具体知识贡献及其实现依据，形成当前项目判断并决定下一步研究。"
            )
        return PreparedReview(
            runtime, topics, chief, chief_id, bundle, admission, settings
        )
    except BaseException:
        budget.save()
        for resource in owned:
            await resource.close()
        raise
