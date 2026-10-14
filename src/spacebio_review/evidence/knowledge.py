"""Literature questions, reading requests and sourced knowledge answers."""

from datetime import date
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializeAsAny,
    field_validator,
    model_serializer,
    model_validator,
)

from spacebio_review.agents.types import TaskResult
from spacebio_review.prose import Prose, prose_parts, render_parts
from spacebio_review.reasoning import EvidenceStatement, annotated_product

from .types import SourceCandidate

KnowledgePurpose = Literal["background", "method", "prior-art", "contribution", "comparison"]


class KnowledgeQuestion(BaseModel):
    question: str = Field(min_length=1)
    purpose: KnowledgePurpose = "background"
    background: str = ""
    conditions: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    cutoff_date: date | None = None
    existing_source_ids: list[str] = Field(default_factory=list)
    assessment_focus: str = ""
    known_project_facts: list[EvidenceStatement] = Field(default_factory=list)
    needed_information: list[str] = Field(default_factory=list)


class ReviewKnowledgeQuestion(KnowledgeQuestion):
    """Project delegation is specific; standalone research retains its simpler interface."""

    model_config = ConfigDict(str_strip_whitespace=True)

    input_version: Literal["review-knowledge-2"] = "review-knowledge-2"
    assessment_focus: str = Field(min_length=1)
    known_project_facts: list[EvidenceStatement] = Field(
        default_factory=list, exclude=True, max_length=0
    )
    application_statements: dict[str, dict] = Field(min_length=1)
    needed_information: list[str] = Field(min_length=1)


class SearchQuery(BaseModel):
    query: str = Field(min_length=1)
    provider: Literal[
        "europe_pmc",
        "openalex",
        "openalex_semantic",
        "semantic_scholar",
        "arxiv",
        "crossref",
        "tavily",
    ]


class ResearchPlan(BaseModel):
    concepts: list[str]
    queries: list[SearchQuery] = Field(min_length=1, max_length=6)
    rationale: str


class KnowledgeRef(BaseModel):
    source_id: str
    passage_id: str | None = Field(
        default=None, description="Actually read passage; not a concern ID."
    )
    visual_id: str | None = Field(
        default=None, description="Actually interpreted visual; not both locations."
    )


class BoundEvidenceStatement(EvidenceStatement):
    evidence_refs: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    explanation: str = Field(min_length=1)
    references: list[KnowledgeRef] = Field(default_factory=list)
    basis: list[BoundEvidenceStatement] = Field(default_factory=list)


class KnowledgeAnswer(BaseModel):
    contract_version: str = "legacy-3"
    evidence_resolution: dict = Field(default_factory=dict)
    answer: str
    findings: list[Finding | Prose] = Field(default_factory=list)
    scope: Prose | None = None
    applicability: list[str] | Prose = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    reading_scope: str = Field(min_length=1)
    stop_reason: str = Field(min_length=1)
    project_implications: list[str] = Field(default_factory=list)
    unresolved_application_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def project_canonical(cls, data):
        if isinstance(data, dict) and data.get("contract_version") in {
            "literature-4",
            "literature-5",
        }:
            data = dict(data)
            # These derived accessors serve legacy callers; persistence keeps only the prose.
            data["answer"] = "\n\n".join(
                render_parts(prose_parts(f)) for f in data["findings"]
            ) or "\n".join(data["unknowns"])
            data["project_implications"] = (
                [render_parts(prose_parts(data["applicability"]))]
                if data["contract_version"] == "literature-4"
                else []
            )
            data["unresolved_application_questions"] = data["unknowns"]
        if isinstance(data, dict) and data.get("contract_version") in {
            "scientific-2",
            "scientific-3",
        }:
            data = dict(data)
            data["answer"] = "\n".join(f["explanation"] for f in data.get("findings", []))
            data["project_implications"] = data.get("applicability", [])
            data["unresolved_application_questions"] = data.get("unknowns", [])
        return data

    @model_serializer(mode="wrap")
    def persist_canonical(self, handler):
        data = handler(self)
        if self.contract_version in {
            "scientific-2",
            "scientific-3",
            "literature-4",
            "literature-5",
        }:
            for key in ("answer", "project_implications", "unresolved_application_questions"):
                data.pop(key, None)
            for finding in data["findings"]:
                finding.pop("references", None)
        if self.contract_version == "literature-5":
            data.pop("applicability", None)
        else:
            data.pop("scope", None)
        return data


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
    limit: int = Field(default=6, ge=1, le=12)


class LiteratureConfig(BaseModel):
    max_actions: int = Field(default=12, ge=3)
    max_model_turns: int = Field(default=18, ge=5)
    stagnation_batches: int = Field(default=2, ge=1)
    context_soft_tokens: int = Field(default=64000, ge=1000)


class ReadingRecord(BaseModel):
    source: SourceCandidate
    passage_ids: list[str] = Field(default_factory=list)
    visual_ids: list[str] = Field(default_factory=list)
    availability: str = "metadata"
    related_versions: list[str] = Field(default_factory=list)


class KnowledgeRun(BaseModel):
    research_id: str
    question: SerializeAsAny[KnowledgeQuestion]
    answer: KnowledgeAnswer | None = None
    task_result: TaskResult
    plan_result: TaskResult | None = None
    sources: dict[str, ReadingRecord] = Field(default_factory=dict)
    channels: list[dict] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    reused: bool = False

    @field_validator("answer", mode="before")
    @classmethod
    def read_saved_answer(cls, value):
        # Saved answer_view adds display annotations; the formal prose stays canonical.
        if isinstance(value, dict) and value.get("contract_version") in {
            "literature-4",
            "literature-5",
        }:
            return {
                **value,
                "findings": [
                    {
                        k: v
                        for k, v in row.items()
                        if k not in {"citation_status", "citation_limits"}
                    }
                    if isinstance(row, dict)
                    else row
                    for row in value.get("findings", [])
                ],
            }
        return value

    @field_validator("question", mode="before")
    @classmethod
    def read_question(cls, value):
        if isinstance(value, dict) and value.get("input_version") == "review-knowledge-2":
            return ReviewKnowledgeQuestion.model_validate(value)
        return value

    def answer_view(self):
        return (
            annotated_product(self.answer, self.task_result.citation_issues)
            if self.answer
            else None
        )


class AcquireSource(BaseModel):
    source_id: str | None = None
    url: str | None = None
    start: int = Field(default=0, ge=0)
    count: int = Field(default=40, ge=1, le=100)
