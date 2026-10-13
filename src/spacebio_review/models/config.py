from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    base_url: str
    request_protocol: Literal["chat_completions", "messages"] = "chat_completions"
    api_key_env: str = "API_KEY"
    vision: bool = False
    tools: bool = True
    structured_output: Literal["tool_call", "json_schema", "json_object", "yaml"] = "tool_call"
    context_window: int = Field(default=262144, gt=0)
    max_tokens: int = Field(default=8192, gt=0)
    timeout_seconds: float = Field(default=180, gt=0)
    retries: int = Field(default=2, ge=0, le=5)
    parameters: dict[str, Any] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    open_weights_url: str = ""
    hosted_version_url: str = ""
    image_tokens: int = Field(default=16384, ge=0)
    formal_parameters: dict[str, Any] = Field(default_factory=dict)
    formal_extra_body: dict[str, Any] = Field(default_factory=dict)
    # An endpoint-specific capability declaration, not inferred from an SDK name.
    schema_supported: bool = False
    schema_with_images: bool = False
    schema_with_thinking: bool = False
    capability_source: str = ""
    capability_verified: bool = False
    reasoning_reserve: int = Field(default=0, ge=0)
    minimum_body_tokens: int = Field(default=256, gt=0)


class TaskRoute(BaseModel):
    max_truncation_recoveries: int = Field(default=0, ge=0, le=1)
    model_config = ConfigDict(extra="forbid")
    model: str
    mode: Literal["research", "direct"] = "direct"
    max_tokens: int | None = Field(default=None, gt=0)
    retries: int | None = Field(default=None, ge=0, le=5)
    read_actions: int = Field(default=0, ge=0)
    parameters: dict[str, Any] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)


class FormalRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = None
    allow_hosted_model: bool = False
    protocol: Literal["legacy", "cited_parts", "inherited"] = "legacy"
    max_tokens: int | None = Field(default=None, gt=0)
    structured_output: Literal["json_schema", "json_object", "yaml"] | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    reasoning_reserve: int | None = Field(default=None, ge=0)
    minimum_body_tokens: int | None = Field(default=None, gt=0)


class EmbeddingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    base_url: str
    api_key_env: str = "API_KEY"
    dimensions: int = Field(default=1024, gt=0)
    open_weights_url: str
    hosted_version_url: str
    batch_size: int = Field(default=10, ge=1, le=10)
    max_chars: int = Field(default=6000, ge=1)
    timeout_seconds: float = Field(default=60, gt=0)
    retries: int = Field(default=1, ge=0, le=3)
    cache_dir: Path | None = None


class ConcurrencyConfig(BaseModel):
    model_requests: int = Field(default=4, gt=0)
    tools: int = Field(default=8, gt=0)
    per_model: dict[str, int] = Field(default_factory=dict)


class RuntimeConfig(BaseModel):
    submission_max_bytes: int = Field(default=1048576, ge=1024)
    model_config = ConfigDict(extra="forbid")
    models: dict[str, ModelConfig]
    skills_dir: Path = Path("skills")
    evaluation_skill: str = "exploration-review"
    evaluation_core: str = ""
    task_routes: dict[str, TaskRoute] = Field(default_factory=dict)
    formal_routes: dict[str, FormalRoute] = Field(default_factory=dict)
    strong_model: str = "strong"
    embedding: EmbeddingConfig | None = None
    concurrency: ConcurrencyConfig = Field(default_factory=ConcurrencyConfig)

    def routed_model(self, kind: str, tier: str = "normal", *, formal=False) -> ModelConfig:
        route = self.task_routes[kind]
        if tier not in {"normal", "strong"}:
            raise ValueError("Unknown model tier")
        model = self.models[self.strong_model if tier == "strong" else route.model]
        if not model.open_weights_url:
            raise ValueError("Task model requires an official open-weights reference")
        updates = {
            "parameters": model.parameters | route.parameters,
            "extra_body": model.extra_body | route.extra_body,
        }
        if route.max_tokens is not None:
            updates["max_tokens"] = route.max_tokens
        if route.retries is not None:
            updates["retries"] = route.retries
        result = ModelConfig.model_validate(model.model_dump() | updates)
        if not formal:
            return result
        output = self.formal_routes.get(kind, FormalRoute())
        if output.model is not None:
            result = self.models[output.model].model_copy(deep=True)
            if not result.open_weights_url and not (
                output.allow_hosted_model and result.hosted_version_url
            ):
                raise ValueError("Formal model requires an official open-weights reference")
        settings = {
            "parameters": result.parameters | result.formal_parameters | output.parameters,
            "extra_body": result.extra_body | result.formal_extra_body | output.extra_body,
        }
        for key in ("max_tokens", "structured_output", "reasoning_reserve", "minimum_body_tokens"):
            if getattr(output, key) is not None:
                settings[key] = getattr(output, key)
        return ModelConfig.model_validate(result.model_dump() | settings)


def load_config(path: str | Path) -> RuntimeConfig:
    path = Path(path)
    config = RuntimeConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    # Paths are relative to the configuration file, independent of the caller's cwd.
    config.skills_dir = (path.parent / config.skills_dir).resolve()
    if config.embedding and config.embedding.cache_dir is not None:
        config.embedding.cache_dir = (path.parent / config.embedding.cache_dir).resolve()
    return config


def credential(name: str, env_file: str | Path | None = None) -> str:
    value = os.environ.get(name)
    if not value and env_file is not None:
        value = dotenv_values(env_file).get(name)
    if not value:
        raise ValueError(f"Missing credential environment variable: {name}")
    return value
