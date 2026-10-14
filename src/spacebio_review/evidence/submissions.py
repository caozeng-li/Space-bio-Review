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


def basis_type(choices, *, multiple=False):
    if not choices:
        return Interpretation
    sourced = record(
        "ReadEvidence",
        **(
            {"evidence_handles": (list[selection(choices)], Field(min_length=1))}
            if multiple
            else {"evidence_handle": (selection(choices), ...)}
        ),
        text=(str, Field(min_length=1)),
        support_explanation=(str, Field(min_length=1)),
        quantitative_basis=(QuotedQuantity | DerivedQuantity | UnquotedQuantity | None, ...),
    )
    return Union[sourced, Interpretation]


def resolve_basis(items, choices, *, identities=None):
    resolved = []
    for item in items:
        data = item.model_dump(mode="json") if isinstance(item, BaseModel) else dict(item)
        handle = data.pop("evidence_handle", None)
        handles = data.pop("evidence_handles", []) or ([handle] if handle is not None else [])
        if handles:
            entries = [choices[k] for k in dict.fromkeys(handles)]
            kinds = {entry["kind"] for entry in entries}
            data.update(
                kind=entries[0]["kind"] if len(kinds) == 1 else "interpretation",
                source_refs=[entry["reference"] for entry in entries],
            )
            if identities is not None:
                data["evidence_refs"] = list(dict.fromkeys(identities[k] for k in handles))
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
            handles.update(node.get("evidence_handles", []))
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(data)
    return {key: choices[key] for key in sorted(handles)}


def scientific_contract(scope, result_type, *, paths=(), concerns=(), session=None):
    """Freeze the current task's public read ledger immediately before formal generation."""
    if session is not None:
        return literature_contract(scope, result_type, session)

    contract = SubmissionContract(result_type, result_type, version="scientific-2")

    def refresh(current):
        choices = scope.submission_choices()
        context = current.task_context
        if context:
            context.add(choices, "E")
            choices = {context.aliases[k]: v for k, v in choices.items()}
        basis = basis_type(choices)
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
        current.directory = (
            (lambda: {"evidence_refs": list(choices), "read_from": "task_sources"})
            if context
            else (lambda: choices)
        )

    contract.refresh = refresh
    refresh(contract)
    return contract


LITERATURE_CONTRACT_VERSION = "literature-5"
LITERATURE_SUBMISSION_PROMPT = (
    "依据本次已读资料，以接口给定的 JSON Schema 交回知识回答。"
    "findings 的每条正文解释外部研究的认识，将对象、方法、观测、数值用途及局部条件一起说明；"
    "scope 说明这些外部认识共同覆盖的对象、方法和条件，没有共同范围时为 null；"
    "unknowns 列尚未回答的知识问题，可为空。尚无发现时 findings 可为空。"
    "每段 parts 按引用归属组织，cites 选择 task_sources 的原文编号，"
    "支持关系发生变化时再分 part；无来源的解释使用空 cites。"
)


def literature_contract(scope, result_type, session):
    from spacebio_review.prose import bind_prose, cited_ids, prose_type
    from spacebio_review.v4.context import TaskContext

    context = TaskContext(compact_sources=True, formal_cites=True)
    contract = SubmissionContract(
        result_type,
        result_type,
        version=LITERATURE_CONTRACT_VERSION,
        task_context=context,
        completion="natural",
        native_prompt=LITERATURE_SUBMISSION_PROMPT,
    )

    def refresh(current):
        originals = scope.submission_choices()
        context.add(originals, "E")
        choices = {context.aliases[k]: k for k in originals}
        prose = prose_type(choices)
        current.draft_type = record(
            "KnowledgeDraft",
            findings=(list[prose], ...),
            scope=(prose | None, ...),
            unknowns=(list[str], ...),
        )

        def assemble(draft):
            data = draft.model_dump(mode="json")
            findings = [bind_prose(f, choices) for f in data["findings"]]
            shared_scope = bind_prose(data["scope"], choices) if data["scope"] else None
            used = dict.fromkeys(cited_ids([*findings, shared_scope]))
            return {
                "contract_version": current.version,
                "findings": findings,
                "scope": shared_scope,
                "unknowns": data["unknowns"],
                "reading_scope": "Actual read locations are recorded in KnowledgeRun.sources.",
                "stop_reason": session.state.stop_reason or "formal_submission",
                "evidence_resolution": {key: originals[key] for key in used},
            }

        current.assemble = assemble
        current.directory = lambda: {"evidence_refs": list(choices), "read_from": "task_sources"}

    contract.refresh = refresh
    refresh(contract)
    return contract
