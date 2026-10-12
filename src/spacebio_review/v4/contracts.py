"""Stable scientific drafts; all reference binding is task-local."""

import re
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from spacebio_review.agents.contracts import SubmissionContract
from spacebio_review.evidence.submissions import record, selection
from spacebio_review.review.project import ReviewAspect

from .context import TaskContext

GENERATION_VERSION = "v4.2-generation-9"


class Product(BaseModel):
    model_config = ConfigDict(extra="allow")


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1)
    importance: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    changes_judgment_if: str = Field(min_length=1)
    aspects: list[ReviewAspect] = Field(min_length=1)
    expertise_needed: list[str] = Field(default_factory=list)
    resolution_type: Literal["available_research", "applicant_action", "retain_unknown"]
    information_route: str


class Followup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    missing_information: str
    decision_impact: str
    information_route: str


class Block(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, description="完整论证：理由、局部条件、替代解释与项目影响。")
    evidence_refs: list[str]
    result_refs: list[str]


class DraftBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(
        min_length=1,
        description=(
            "完整论证，保留理由、条件、替代解释。引用在对应语句后写 [@E1] 或 [@R1]，"
            "每个来源单独一个标记；编号取当前目录。程序自动提取引用列表。"
        ),
    )


class ApplicationReading(BaseModel):
    # Reading consumes these two content fields. Root annotations remain in the
    # runtime's raw candidate archive, not in the assembled scientific product.
    # SubmissionContract.schema() still asks the model for only the known fields.
    model_config = ConfigDict(extra="ignore")
    observations: list[DraftBlock]
    reading_limits: list[str]


def reading_contract(*, scope=None, evidence=None):
    return grounded_contract(
        "reading", lambda ev, rs: ApplicationReading, scope=scope, evidence=evidence
    )


class ResearchDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    scope_note: str
    blocks: list[DraftBlock] = Field(min_length=1)
    followups: list[Followup]


def choices_field(choices, *, required=False):
    return (
        (list[selection(choices)], Field(min_length=1 if required else 0))
        if choices
        else (list[str], Field(max_length=0))
    )


def proposal_type(evidence):
    fields = {k: (v.annotation, v) for k, v in Proposal.model_fields.items()}
    fields["evidence_handles"] = choices_field(evidence)
    return record("QuestionProposal", **fields)


def work_fields(experts, members):
    task = record(
        "ExpertScope",
        expert_id=(selection(experts), ...),
        member_ids=choices_field(members, required=True),
        analysis_scope=(str, Field(min_length=1)),
    )
    return {
        "expert_tasks": (list[task], ...),
        "external_needs": (list[str], ...),
        "retained_unknowns": (list[str], ...),
    }


class ProfessionalDraft(ResearchDraft):
    scope_outcomes: list[dict]

    @model_validator(mode="after")
    def local_outcome_references(self):
        seen = set()
        for raw in self.scope_outcomes:
            outcome = raw.model_dump() if isinstance(raw, BaseModel) else raw
            if outcome["member_id"] in seen:
                raise ValueError("one_outcome_per_member")
            seen.add(outcome["member_id"])
            indices = outcome["block_indices"]
            if any(i < 1 or i > len(self.blocks) for i in indices):
                raise ValueError("scope_outcome_block_out_of_range")
            if outcome["status"] == "analyzed" and not indices:
                raise ValueError("analyzed_scope_requires_argument")
        return self


def professional_contract(
    scope, results, members, *, structured_refs=False, inherited=False, **kwargs
):
    kwargs.setdefault("compact_sources", True)

    def build(ev, rs):
        outcome = record(
            "MemberOutcome",
            member_id=(selection(members), ...),
            status=(Literal["partial", "analyzed", "blocked"], ...),
            explanation=(str, ...),
            block_indices=(list[int], Field(description="本次 blocks 的 1 起始下标；受阻可为空。")),
        )
        if structured_refs:
            from .generation import narrative_type

            narrative = narrative_type([*ev, *rs])
            followup = record(
                "CitedFollowup", **{k: (narrative, ...) for k in Followup.model_fields}
            )
            return create_model(
                "CitedProfessionalDraft",
                __base__=ProfessionalDraft,
                scope_note=(narrative, ...),
                blocks=(list[narrative], Field(min_length=1)),
                followups=(list[followup], ...),
                scope_outcomes=(list[outcome], Field(min_length=1)),
            )
        return create_model(
            "ScopedProfessionalDraft",
            __base__=ProfessionalDraft,
            blocks=(
                list[DraftBlock],
                Field(
                    min_length=1,
                    description="每项只输出 text，引用标记写在正文中。"
                    "evidence_refs/result_refs 由程序生成，"
                    "不复制输入材料的这些归档字段。",
                ),
            ),
            scope_outcomes=(list[outcome], Field(min_length=1)),
        )

    return grounded_contract(
        "professional",
        build,
        scope=scope,
        results=results,
        structured_refs=structured_refs or inherited,
        inherited=inherited,
        members=members,
        **kwargs,
    )


def evidence_catalogue(scope):
    return scope.snapshot_choices() if scope else {}


def grounded_contract(
    name,
    build,
    *,
    scope=None,
    results=None,
    evidence=None,
    compact_sources=False,
    structured_refs=False,
    inherited=False,
    members=None,
):
    context = TaskContext(
        compact_sources=compact_sources, structured_refs=structured_refs, inherited=inherited
    )
    context.members = members or {}
    contract = SubmissionContract(
        Product,
        Product,
        version="v4.2-statements-1"
        if inherited
        else "v4.2-cited-parts-1"
        if structured_refs
        else GENERATION_VERSION,
        task_context=context,
    )

    def refresh(current):
        context.add(evidence or {}, "E")
        context.add(evidence_catalogue(scope) if scope else {}, "E")
        context.add(results() if callable(results) else results or {}, "R")
        context.collect(list(context.results.values()))
        if context.book:
            context.book.import_results()
        choices = context.citation_choices()
        current.draft_type = build(
            {
                context.aliases[k]: v
                for k, v in context.evidence.items()
                if context.aliases[k] in choices
            },
            {
                context.aliases[k]: v
                for k, v in context.results.items()
                if context.aliases[k] in choices
            },
        )
        if context.book:
            current.draft_type = context.book.draft_type()
        frozen = deepcopy(context)

        def assemble(draft):
            if frozen.book:
                value = frozen.book.compile(draft.model_dump(mode="json"))
            elif structured_refs:
                from .generation import compile_draft

                value = compile_draft(draft.model_dump(mode="json"), frozen)
            else:
                value = frozen.restore(draft.model_dump(mode="json"))
            used = set()

            def visit(node):
                if isinstance(node, dict):
                    used.update(node.get("evidence_refs", []))
                    if structured_refs:
                        used.update(k for k in node.get("cites", []) if k in frozen.evidence)
                    used.update(node.get("evidence_handles", []))
                    used.update(k for k in node.get("scope_links", []) if k in frozen.evidence)
                    for child in node.values():
                        visit(child)
                elif isinstance(node, list):
                    for child in node:
                        visit(child)

                elif isinstance(node, str) and not structured_refs:
                    used.update(
                        k for k in re.findall(r"(?<!\\)\[@([^]]+)\]", node) if k in frozen.evidence
                    )

            visit(value)
            return {**value, "evidence": {k: frozen.evidence[k] for k in sorted(used)}}

        current.assemble = assemble
        if compact_sources:
            current.directory = lambda: {
                "evidence_refs": [a for a in choices if a.startswith("E")],
                "result_refs": [a for a in choices if a.startswith("R")],
                "read_from": "task_sources",
            }
            return
        current.directory = lambda: {
            "evidence_refs": {
                frozen.aliases[k]: {
                    "reference": v.get("reference"),
                    "locator": v.get("locator"),
                    "title": v.get("title"),
                    "kind": v.get("kind"),
                }
                for k, v in frozen.evidence.items()
            },
            "result_refs": {
                frozen.aliases[k]: {"title": v.get("title"), "block_id": v.get("block_id")}
                for k, v in frozen.results.items()
            },
        }

    contract.refresh = refresh
    refresh(contract)
    return contract


def research_contract(scope, results, **kwargs):
    return grounded_contract(
        "research", lambda ev, rs: ResearchDraft, scope=scope, results=results, **kwargs
    )
