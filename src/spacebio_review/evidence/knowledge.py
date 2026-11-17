"""Shared source discovery, reading requests and actual read locations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .types import SourceCandidate


class SearchQuery(BaseModel):
    query: str = Field(
        min_length=1,
        description=(
            "The query submitted to the selected search source. For Europe PMC, multiple "
            "keywords default to AND, preferably using a few core concepts; Boolean "
            "combinations are supported. Use TITLE_ABS: to limit to title and abstract; use"
            " a trailing * on a word to match singular/plural and other word forms. Place "
            "the complete scientific question in information_need, avoiding treating a long"
            " question directly as a set of keyword intersections."
        ),
    )
    information_need: str = Field(
        default="",
        description=(
            "The complete scientific question this specific query aims to answer, and its "
            "reading purpose."
        ),
    )
    provider: Literal[
        "europe_pmc",
        "openalex",
        "openalex_semantic",
        "semantic_scholar",
        "arxiv",
        "crossref",
        "tavily",
        "project",
    ]


class SearchSources(BaseModel):
    queries: list[SearchQuery] = Field(min_length=1, max_length=6)
    limit: int = Field(default=6, ge=1, le=12)


class RetrieveKnowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    queries: list[str] = Field(min_length=1, max_length=4)
    limit: int = Field(default=5, ge=1, le=8)
    reading_tokens: int | None = Field(default=None, gt=0)


class RelatedPapers(BaseModel):
    source_ids: list[str] = Field(min_length=1, max_length=2)
    relation: Literal["recommendations", "citations", "references"] = "recommendations"
    information_need: str = Field(
        default="",
        description=(
            "The specific knowledge question for this step of tracking along literature "
            "relationships."
        ),
    )
    limit: int = Field(default=6, ge=1, le=12)


class ReadingRecord(BaseModel):
    source: SourceCandidate
    passage_ids: list[str] = Field(default_factory=list)
    visual_ids: list[str] = Field(default_factory=list)
    availability: str = "metadata"
    related_versions: list[str] = Field(default_factory=list)


class AcquireSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reading_purpose: str = Field(
        default="",
        description=(
            "The scientific information hoped to be gleaned from the original text this time; "
            "leave blank to view only the catalog."
        ),
    )
    source_id: str | None = None
    url: str | None = None
    material_id: str | None = None
    reading_tokens: int | None = Field(default=None, gt=0)
    start: int = Field(
        default=0, ge=0, description="The starting position of the native reading unit catalog."
    )
    count: int = Field(
        default=40, ge=1, le=100, description="The number of catalog units displayed this time."
    )
