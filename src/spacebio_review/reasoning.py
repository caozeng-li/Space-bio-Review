"""Shared public evidence vocabulary; references do not establish semantic support."""

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceRef(BaseModel):
    source_id: str
    passage_id: str | None = Field(
        default=None, description="Actually read passage; not a concern ID."
    )
    visual_id: str | None = Field(
        default=None, description="Actually interpreted visual; not both locations."
    )


class QuantitativeBasis(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    amount: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    origin: Literal["quoted", "derived", "suggested", "unknown"]
    assumptions: str = ""


class EvidenceStatement(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1)
    kind: Literal["application", "external", "interpretation", "recommendation", "unknown"]
    source_refs: list[EvidenceRef] = Field(default_factory=list)
    support_explanation: str = Field(min_length=1)
    quantitative_basis: QuantitativeBasis | None = None

    @model_validator(mode="before")
    @classmethod
    def ignore_handoff_annotations(cls, value):
        if isinstance(value, dict):
            return {
                k: v for k, v in value.items() if k not in {"citation_status", "citation_limits"}
            }
        return value


def evidence_errors(value):
    """Check declared provenance fields, never the truth or entailment of the statement."""
    data = value.model_dump() if hasattr(value, "model_dump") else value
    errors = []

    def visit(item, at):
        if isinstance(item, dict):
            if "support_explanation" in item and "kind" in item:
                quantitative = item.get("quantitative_basis") or {}
                if (
                    item["kind"] in {"application", "external"}
                    or quantitative.get("origin") == "quoted"
                ) and not item.get("source_refs"):
                    errors.append(
                        {"location": at + ["source_refs"], "type": "declared_source_required"}
                    )
                if quantitative.get("origin") == "derived" and not quantitative.get("assumptions"):
                    errors.append(
                        {
                            "location": at + ["quantitative_basis", "assumptions"],
                            "type": "derivation_premises_required",
                        }
                    )
            for key, child in item.items():
                visit(child, at + [key])
        elif isinstance(item, list):
            for i, child in enumerate(item):
                visit(child, at + [i])

    visit(data, [])
    return errors


def annotated_product(product, issues):
    """A code-owned handoff projection; diagnostics are not fields the model can clear."""
    data = deepcopy(product.model_dump(mode="json") if hasattr(product, "model_dump") else product)
    if not isinstance(data, dict):
        return data
    for group in ("judgments", "findings"):
        for i, item in enumerate(data.get(group, [])):
            related = [e for e in issues if e.get("location", [])[:2] == [group, i]]
            item["citation_status"] = "provisional" if related else "not_semantically_verified"
            item["citation_limits"] = [
                {k: e[k] for k in ("location", "type", "resolution") if k in e} for e in related
            ]
            for j, basis in enumerate(item.get("basis", [])):
                affected = [e for e in related if e.get("location", [])[2:4] == ["basis", j]]
                basis["citation_status"] = (
                    "provisional" if affected else "not_semantically_verified"
                )
    return data
