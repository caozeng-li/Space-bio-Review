"""Shared source discovery, reading requests and actual read locations."""

from typing import Literal

from pydantic import BaseModel, Field

from .types import SourceCandidate


class SearchQuery(BaseModel):
    query: str = Field(
        min_length=1,
        description=(
            "交给所选检索源的查询式。Europe PMC 多个关键词默认 AND，宜用少量核心概念，"
            "支持布尔组合；TITLE_ABS:限定题名摘要，词尾 * 匹配单复数等词形。"
            "完整科学问题放 information_need，避免把长问题直接当作关键词交集。"
        ),
    )
    information_need: str = Field(
        default="", description="本条查询要回答的完整科学问题及阅读用途。"
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


class ReadSource(BaseModel):
    source_id: str | None = None
    url: str | None = None
    section: str | None = None
    passage_ids: list[str] = Field(default_factory=list)
    start: int = Field(default=0, ge=0)
    count: int = Field(default=6, ge=1, le=12)


class ReadSources(BaseModel):
    reads: list[ReadSource] = Field(min_length=1, max_length=6)


class SearchSources(BaseModel):
    queries: list[SearchQuery] = Field(min_length=1, max_length=6)
    limit: int = Field(default=6, ge=1, le=12)


class RetrieveKnowledge(BaseModel):
    source_ids: list[str] = Field(min_length=1, max_length=20)
    queries: list[str] = Field(min_length=1, max_length=4)
    limit: int = Field(default=5, ge=1, le=8)
    neighbors: int = Field(default=1, ge=0, le=2)


class RelatedPapers(BaseModel):
    source_ids: list[str] = Field(min_length=1, max_length=2)
    relation: Literal["recommendations", "citations", "references"] = "recommendations"
    information_need: str = Field(default="", description="此次沿文献关系追踪的具体知识问题。")
    limit: int = Field(default=6, ge=1, le=12)


class ReadingRecord(BaseModel):
    source: SourceCandidate
    passage_ids: list[str] = Field(default_factory=list)
    visual_ids: list[str] = Field(default_factory=list)
    availability: str = "metadata"
    related_versions: list[str] = Field(default_factory=list)


class AcquireSource(BaseModel):
    reading_purpose: str = Field(
        default="", description="此次希望从原文读出的科学信息；留空仅看目录。"
    )
    source_id: str | None = None
    url: str | None = None
    material_id: str | None = None
    start: int = Field(default=0, ge=0)
    count: int = Field(default=40, ge=1, le=100)
