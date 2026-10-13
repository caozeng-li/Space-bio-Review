from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class V4Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = "v4"
    preparation_config: Path = Path("preparation.yaml")
    max_model_calls: int = Field(default=600, ge=1)
    max_embedding_calls: int = Field(default=600, ge=0)
    completion_reserve_calls: int = Field(default=80, ge=0)
    completion_reserve_seconds: int = Field(default=1800, ge=0)
    max_input_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    max_seconds: int = Field(default=14400, ge=1)
    task_turns: int = Field(default=20, ge=3)
    task_actions: int = Field(default=14, ge=2)
    context_tokens: int = Field(default=64000, ge=1000)
    material_batch_chars: int = Field(default=24000, ge=1000)
    max_questions: int = Field(default=10, ge=1)
    question_attempts: int = Field(default=3, ge=1)
    literature_tasks_per_question: int = Field(default=1, ge=0)
    parallel_questions: int = Field(default=4, ge=1)


class ReportingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = "v4"
    context_soft_tokens: int = Field(default=64000, ge=1000)
    max_repairs: int = Field(default=1, ge=0, le=2)
    max_requests: int = Field(default=160, ge=1)
    material_batch_chars: int = Field(default=18000, ge=1000)


def load_reporting(path):
    data = yaml.safe_load(Path(path).read_text("utf-8"))
    if data.get("version") != "v4":
        raise ValueError("V4 reporting requires version: v4; legacy chapter allocation removed")
    return ReportingConfig.model_validate(data)


def load_config(path):
    path = Path(path)
    raw = yaml.safe_load(path.read_text("utf-8"))
    if "supplemental_rounds" in raw:
        raise ValueError(
            "supplemental_rounds is retired; configure max_questions for the fixed question list"
        )
    if raw.get("version") != "v4":
        raise ValueError("V4 requires version: v4; use config/review.yaml, not V3 stage options")
    config = V4Config.model_validate(raw)
    config.preparation_config = (path.parent / config.preparation_config).resolve()
    return config
