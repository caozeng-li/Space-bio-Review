from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    base_url: str
    api_key_env: str = "API_KEY"
    error_profile: Literal["generic", "dashscope"] = "generic"
    quota_group: str | None = None
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
    image_patch_size: int | None = Field(default=None, gt=0)
    # An endpoint-specific capability declaration, not inferred from an SDK name.
    schema_supported: bool = False
    schema_with_images: bool = False
    schema_with_thinking: bool = False
    capability_source: str = ""
    capability_verified: bool = False
    reasoning_reserve: int = Field(default=0, ge=0)
    minimum_body_tokens: int = Field(default=256, gt=0)


class EmbeddingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    base_url: str
    api_key_env: str = "API_KEY"
    error_profile: Literal["generic", "dashscope"] = "generic"
    quota_group: str | None = None
    dimensions: int = Field(default=1024, gt=0)
    open_weights_url: str
    hosted_version_url: str
    batch_size: int = Field(default=10, ge=1, le=10)
    max_chars: int = Field(default=6000, ge=1)
    timeout_seconds: float = Field(default=60, gt=0)
    retries: int = Field(default=1, ge=0, le=3)
    cache_dir: Path | None = None


class TokenQuota(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tokens_per_minute: float = Field(gt=0, allow_inf_nan=False)
    token_basis: Literal["input", "input_and_output"] = "input_and_output"


class ConcurrencyConfig(BaseModel):
    model_requests: int = Field(default=4, gt=0)
    tools: int = Field(default=8, gt=0)
    per_model: dict[str, int] = Field(default_factory=dict)
    quotas: dict[str, TokenQuota] = Field(default_factory=dict)

    def validate_routes(self, models, embedding=None):
        for route in [*models.values(), *([embedding] if embedding else [])]:
            if route.quota_group is not None and route.quota_group not in self.quotas:
                raise ValueError(f"unknown_token_quota_group:{route.quota_group}")


def credential(name: str, env_file: str | Path | None = None) -> str:
    value = os.environ.get(name)
    if not value and env_file is not None:
        value = dotenv_values(env_file).get(name)
    if not value:
        raise ValueError(f"Missing credential environment variable: {name}")
    return value
