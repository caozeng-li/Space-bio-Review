"""Explicit v5 tool-session routes, independent of the legacy formal-generation routes."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from spacebio_review.models.config import ConcurrencyConfig, EmbeddingConfig, ModelConfig

from .budget import BudgetLimits, SessionAllowance
from .records import SessionSettings


class SessionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["v5-session-1"] = "v5-session-1"
    evaluation_system: Literal["general", "gbt42048_2022"] = "general"
    models: dict[str, ModelConfig]
    roles: dict[Literal["chief", "researcher"], str]
    auxiliary: dict[
        Literal["rerank", "visual", "admission", "translation", "delivery_parser"], str
    ] = Field(default_factory=dict)
    embedding: EmbeddingConfig | None = None
    skills_dir: Path = Path("../skills")
    expertise_config: Path = Path("expertise.yaml")
    evidence_config: Path = Path("evidence.yaml")
    session: SessionSettings = Field(default_factory=SessionSettings)
    budget: BudgetLimits = Field(default_factory=BudgetLimits)
    allowances: dict[Literal["chief", "researcher"], SessionAllowance] = Field(default_factory=dict)
    concurrency: ConcurrencyConfig = Field(default_factory=ConcurrencyConfig)

    @model_validator(mode="after")
    def tool_routes(self):
        if set(self.roles) != {"chief", "researcher"}:
            raise ValueError("chief_and_researcher_routes_required")
        if "delivery_parser" not in self.auxiliary:
            raise ValueError("delivery_parser_route_required")
        for alias in self.roles.values():
            model = self.models[alias]
            if not model.tools or model.request_protocol != "chat_completions":
                raise ValueError("v5_session_requires_tool_capable_chat_route")
            if model.max_tokens >= model.context_window:
                raise ValueError("session_model_requires_input_capacity")
        for kind, alias in self.auxiliary.items():
            model = self.models[alias]
            if kind == "visual" and not model.vision:
                raise ValueError("visual_route_requires_image_input")
            if kind == "delivery_parser" and (
                model.tools
                or model.request_protocol != "chat_completions"
                or model.structured_output not in {"json_object", "json_schema"}
            ):
                raise ValueError("delivery_parser_requires_tool_free_json_route")
        if self.session.completion_turns >= self.session.max_turns:
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
