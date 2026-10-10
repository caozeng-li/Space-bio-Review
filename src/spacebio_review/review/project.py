"""Shared source references, review aspects and preparation configuration."""

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class SourceRef(BaseModel):
    source_id: str
    passage_id: str | None = Field(
        default=None, description="Actually read passage; not a concern ID."
    )
    visual_id: str | None = Field(
        default=None, description="Actually interpreted visual; not both locations."
    )


ReviewAspect = Literal[
    "scientific_value", "innovation", "rigor", "feasibility", "safety", "management"
]
REVIEW_ASPECTS = ("scientific_value", "innovation", "rigor", "feasibility", "safety", "management")


class EvaluationContext(BaseModel):
    core: str = (
        "评价空间科学与技术研究可获得的知识、方法或材料能力及其价值，"
        "解释研究路径、可得认识和实施条件。未知不等于失败。"
    )
    mission_context: str = ""
    historical_as_of: date | None = None
    supplements: list[str] = Field(default_factory=list)

    def prompt(self):
        when = (
            f"按 {self.historical_as_of.isoformat()} 的知识时点评价。"
            if self.historical_as_of
            else "未指定历史知识截止日。"
        )
        return (
            self.core
            + "\n"
            + self.mission_context
            + "\n"
            + when
            + ("历史申请年度用于理解原计划，不因当前日期自动判为逾期。")
        )


class PreparationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalogue: Path = Path("config/expertise.yaml")
    # An abnormal-run guard, independent of scientific team size and concurrency.
    max_experts: int = Field(default=32, ge=1)
    read_actions: int = Field(default=6, ge=0)
    max_model_turns: int = Field(default=10, ge=2)
    max_repairs: int = Field(default=1, ge=0)
    evaluation: EvaluationContext = Field(default_factory=EvaluationContext)


def load_preparation_config(path):
    path = Path(path)
    config = PreparationConfig.model_validate(yaml.safe_load(path.read_text("utf-8")))
    config.catalogue = (path.parent / config.catalogue).resolve()
    return config
