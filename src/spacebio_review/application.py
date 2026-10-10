"""Shared application material and scientific assessment vocabulary."""

from typing import Literal

from pydantic import BaseModel, Field

Dimension = Literal["scientific_value", "innovation", "rigor", "feasibility", "mission"]


class ApplicationSource(BaseModel):
    empty_text: bool = False
    load_error: str | None = None
    source_id: str
    title: str
    role: Literal["main", "attachment"]
    availability: str
    sections: list[str]
    limitations: list[str] = Field(default_factory=list)
    reading_gaps: list[str] = Field(default_factory=list)


class ApplicationBundle(BaseModel):
    sources: list[ApplicationSource]

    @property
    def source_ids(self) -> list[str]:
        return [source.source_id for source in self.sources]
