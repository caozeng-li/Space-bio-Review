"""Model routes and settings for the current scientific review runtime."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from spacebio_review.models.config import ConcurrencyConfig, EmbeddingConfig, ModelConfig

from .budget import BudgetLimits, SessionAllowance
from .records import SessionOverrides, SessionSettings


class SessionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_system: Literal["general", "gbt42048_2022"] = "general"
    models: dict[str, ModelConfig]
    roles: dict[Literal["chief", "researcher", "source_reader"], str]
    auxiliary: dict[
        Literal["rerank", "visual", "admission", "translation"], str
    ] = Field(default_factory=dict)
    embedding: EmbeddingConfig | None = None
    skills_dir: Path = Path("../skills")
    expertise_config: Path = Path("expertise.yaml")
    evidence_config: Path = Path("evidence.yaml")
    session: SessionSettings = Field(default_factory=SessionSettings)
    session_overrides: dict[Literal["chief", "researcher", "source_reader"], SessionOverrides] = (
        Field(default_factory=dict)
    )
    budget: BudgetLimits = Field(default_factory=BudgetLimits)
    allowances: dict[Literal["chief", "researcher"], SessionAllowance] = Field(default_factory=dict)
    concurrency: ConcurrencyConfig = Field(default_factory=ConcurrencyConfig)

    @model_validator(mode="after")
    def tool_routes(self):
        self.concurrency.validate_routes(self.models, self.embedding)
        if not {"chief", "researcher"}.issubset(self.roles):
            raise ValueError("chief_and_researcher_routes_required")
        self.roles.setdefault("source_reader", self.roles["researcher"])
        for alias in self.roles.values():
            model = self.models[alias]
            if not model.tools:
                raise ValueError("v5_session_requires_tool_capable_chat_route")
            if model.max_tokens >= model.context_window:
                raise ValueError("session_model_requires_input_capacity")
        for kind, alias in self.auxiliary.items():
            model = self.models[alias]
            if kind == "visual" and not model.vision:
                raise ValueError("visual_route_requires_image_input")
        if self.session.completion_turns >= self.session.max_turns:
            raise ValueError("completion_turns_must_leave_research_turns")
        for override in self.session_overrides.values():
            settings = self.session.model_copy(update=override.model_dump(exclude_none=True))
            if settings.completion_turns >= settings.max_turns:
                raise ValueError("completion_turns_must_leave_research_turns")
        return self


def load_config(path):
    path = Path(path)
    config = SessionConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    config.skills_dir = (path.parent / config.skills_dir).resolve()
    config.expertise_config = (path.parent / config.expertise_config).resolve()
    config.evidence_config = (path.parent / config.evidence_config).resolve()
    if config.embedding and config.embedding.cache_dir is not None:
        config.embedding.cache_dir = (path.parent / config.embedding.cache_dir).resolve()
    return config
