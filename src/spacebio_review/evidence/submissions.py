"""Task-specific scientific submissions with atomic, read-qualified evidence choices."""

from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, create_model

from spacebio_review.agents.contracts import SubmissionContract
from spacebio_review.reasoning import QuantitativeBasis


def record(name, **fields):
    return create_model(name, __config__=ConfigDict(extra="forbid"), **fields)


def selection(values):
    return Literal[tuple(values)]


class UnquotedQuantity(QuantitativeBasis):
    origin: Literal["unknown", "suggested"]


class DerivedQuantity(QuantitativeBasis):
    origin: Literal["derived"]
    assumptions: str = Field(min_length=1)


class QuotedQuantity(QuantitativeBasis):
    origin: Literal["quoted"]


class Interpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["interpretation", "recommendation", "unknown"]
    text: str = Field(min_length=1)
    support_explanation: str = Field(min_length=1)
    quantitative_basis: UnquotedQuantity | DerivedQuantity | None


def basis_type(choices):
    if not choices:
        return Interpretation
    sourced = record(
        "ReadEvidence",
        evidence_handle=(selection(choices), ...),
        text=(str, Field(min_length=1)),
        support_explanation=(str, Field(min_length=1)),
        quantitative_basis=(QuotedQuantity | DerivedQuantity | UnquotedQuantity | None, ...),
    )
    return Union[sourced, Interpretation]


def resolve_basis(items, choices):
    resolved = []
    for item in items:
        data = item.model_dump(mode="json") if isinstance(item, BaseModel) else dict(item)
        handle = data.pop("evidence_handle", None)
        if handle is not None:
            entry = choices[handle]
            data.update(kind=entry["kind"], source_refs=[entry["reference"]])
        else:
            data["source_refs"] = []
        resolved.append(data)
    return resolved


def used_choices(data, choices):
    handles = set()

    def visit(node):
        if isinstance(node, dict):
            if node.get("evidence_handle"):
                handles.add(node["evidence_handle"])
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(data)
    return {key: choices[key] for key in sorted(handles)}


def scientific_contract(scope, result_type, *, paths=(), concerns=(), session=None):
    """Freeze the current task's public read ledger immediately before formal generation."""
    contract = SubmissionContract(result_type, result_type, version="scientific-2")

    def refresh(current):
        choices = scope.submission_choices()
        basis = basis_type(choices)
        if session is not None:
            finding = record(
                "FindingDraft",
                explanation=(str, Field(min_length=1)),
                basis=(list[basis], Field(min_length=1)),
            )
            current.draft_type = record(
                "KnowledgeDraft",
                findings=(list[finding], Field(min_length=1)),
                applicability=(
                    list[str],
                    Field(min_length=1)
                    if getattr(getattr(session, "question", None), "assessment_focus", "")
                    else ...,
                ),
                open_questions=(list[str], ...),
            )

            def assemble(draft):
                data = draft.model_dump(mode="json")
                findings = [
                    {**f, "basis": resolve_basis(f["basis"], choices)} for f in data["findings"]
                ]
                return {
                    "contract_version": "scientific-2",
                    "findings": findings,
                    "applicability": data["applicability"],
                    "unknowns": data["open_questions"],
                    "reading_scope": "Actual read locations are recorded in KnowledgeRun.sources.",
                    "stop_reason": session.state.stop_reason or "formal_submission",
                    "evidence_resolution": used_choices(data, choices),
                }
        else:
            fields = dict(
                subject=(str, Field(min_length=1)),
                assessment=(str, Field(min_length=1)),
                basis=(list[basis], Field(min_length=1)),
                reasoning=(str, Field(min_length=1)),
                consequence=(str, Field(min_length=1)),
                conditions=(list[str], ...),
                alternatives=(list[str], ...),
                suggested_action=(str | None, ...),
                action_kind=(Literal["necessary_question", "possible_approach", "none"], ...),
            )
            if paths:
                fields["project_path_ids"] = (list[selection(paths)], Field(min_length=1))
            if concerns:
                fields["coverage_refs"] = (list[selection(concerns)], Field(min_length=1))
            judgment = record("JudgmentDraft", **fields)
            current.draft_type = record(
                "StudyDraft",
                scope=(str, Field(min_length=1)),
                judgments=(list[judgment], Field(min_length=1)),
                open_questions=(list[str], ...),
            )

            def assemble(draft):
                data = draft.model_dump(mode="json")
                return {
                    "contract_version": "scientific-2",
                    "scope": data["scope"],
                    "judgments": [resolve_judgment(j) for j in data["judgments"]],
                    "scientific_unknowns": data["open_questions"],
                    "evidence_resolution": used_choices(data, choices),
                }

            def resolve_judgment(data):
                data = judgment.model_validate(data).model_dump(mode="json")
                return {**data, "basis": resolve_basis(data["basis"], choices)}

            current.judgment_resolver = resolve_judgment
        current.assemble = assemble
        current.directory = lambda: choices

    contract.refresh = refresh
    refresh(contract)
    return contract
