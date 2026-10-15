from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_serializer

Availability = Literal["full_text", "abstract", "partial", "unavailable"]


class SourceCandidate(BaseModel):
    title: str
    kind: Literal["literature", "web", "application"] = "literature"
    identifiers: dict[str, str] = Field(default_factory=dict)
    publication_date: date | None = None
    date_precision: Literal["day", "month", "year", "unknown"] = "unknown"
    url: str = ""
    fulltext_urls: list[str] = Field(default_factory=list)
    abstract: str = ""
    discovered_by: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    venue: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    article_number: str = ""
    corporate_authors: list[str] = Field(default_factory=list)
    version: str = ""
    related_identifiers: dict[str, str] = Field(default_factory=dict)
    version_date: date | None = None

    def eligible(self, cutoff: date | None) -> bool:
        return cutoff is None or all(
            value is None or value <= cutoff for value in (self.publication_date, self.version_date)
        )


class Passage(BaseModel):
    passage_id: str
    locator: str
    text: str
    section: str = ""
    page: int | None = None
    kind: Literal["text", "table", "caption", "abstract"] = "text"
    unit_id: str | None = None

    @model_serializer(mode="wrap")
    def stored_fields(self, handler):
        data = handler(self)
        if self.unit_id is None:
            data.pop("unit_id", None)
        return data


class Visual(BaseModel):
    visual_id: str
    locator: str
    caption: str = ""
    path: str | None = None
    url: str | None = None
    page: int | None = None
    section: str = ""


class SourceMaterial(BaseModel):
    """One readable file belonging to a source; citations keep source/position IDs."""

    material_id: str
    role: Literal["main", "supplement"] = "main"
    title: str = ""
    url: str = ""
    package_member: str = ""
    original_path: str | None = None
    acquired_url: str | None = None
    availability: Availability = "unavailable"
    passage_ids: list[str] = Field(default_factory=list)
    visual_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class SourceDocument(BaseModel):
    source_id: str
    candidate: SourceCandidate
    availability: Availability = "unavailable"
    passages: list[Passage] = Field(default_factory=list)
    visuals: list[Visual] = Field(default_factory=list)
    original_path: str | None = None
    acquired_url: str | None = None
    limitations: list[str] = Field(default_factory=list)
    reading_gaps: list[str] = Field(default_factory=list)
    normalized_path: str | None = None
    materials: list[SourceMaterial] = Field(default_factory=list)
