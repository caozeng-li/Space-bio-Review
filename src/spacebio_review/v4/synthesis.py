"""Bounded aspect reduction over complete arguments and immutable source versions."""

import re
from typing import Literal

from pydantic import BaseModel

from spacebio_review.agents.diagnostics import digest
from spacebio_review.context import product_material
from spacebio_review.review.project import REVIEW_ASPECTS
from spacebio_review.tools import Tool, ToolRegistry, ToolResult

from .contracts import DraftBlock, ResearchDraft, grounded_contract
from .records import statement_registry, statement_subset
from .research import save_artifact


class Condition(DraftBlock):
    timing: Literal["before_support", "implementation_milestone", "recommendation"]


class AssessmentDraft(ResearchDraft):
    recommendation: Literal[
        "support", "conditional_support", "revise", "not_support", "insufficient_basis"
    ]
    conditions: list[Condition]
    options: list[DraftBlock]
    unresolved: list[str]


class ReadArgument(BaseModel):
    argument_id: str


class ArgumentAccess:
    """Only the immutable versions explicitly supplied to this synthesis are readable."""

    def __init__(self, records, allowed, initial):
        self.records = records
        self.allowed = (
            dict(allowed)
            if isinstance(allowed, dict)
            else records.result_blocks(sorted(set(allowed)))
        )
        self.available = dict(initial)
        self.read_refs = set()
        self.contract = None

    def index(self):
        rows = {}
        for key, value in self.allowed.items():
            if key not in self.available:
                row = rows.setdefault(
                    value["artifact_ref"],
                    {
                        "argument_id": value["artifact_ref"],
                        "title": value["title"],
                        "block_ids": [],
                    },
                )
                row["block_ids"].append(value["block_id"])
        return rows

    def tools(self, reader):
        tools = reader.tools() if reader else ToolRegistry()

        async def read(args):
            selected = {
                k: v for k, v in self.allowed.items() if args.argument_id in {k, v["artifact_ref"]}
            }
            if not selected:
                return ToolResult(ok=False, error="argument_outside_selected_versions")
            self.available.update(selected)
            self.read_refs.update(v["artifact_ref"] for v in selected.values())
            if self.contract:
                self.contract.refresh(self.contract)
                return ToolResult(
                    ok=True,
                    content={"read_arguments": list(selected), "available_in": "task_sources"},
                )
            return ToolResult(ok=True, content={"arguments": selected})

        tools.add(
            Tool(
                "read_argument",
                "用产物 ID 读取整份原意见，或用产物ID::块ID读取完整单块及范围、证据。",
                ReadArgument,
                read,
            )
        )
        return tools


ASPECT_OBJECTIVE = (
    "在指定输入范围内评价 {aspect}。{purpose}"
    "解释贡献、条件、替代解释和决策影响；合并重复论点，保留独特证据与不同专家的分歧。"
    "同题意见冲突时说明依据和取舍，不能把可能性升级成事实，不能将未评写成申请缺失。"
    "保留支持证据与现有设计优点；可能的筛选偏差不能写成已经选出不敏感模型，"
    "本轮未核实不等于申请未设计；未知仪器参数不能推出不可测。"
    "已展开的论述包含范围，无需重复读取；需核对未展开的索引原意见时使用 read_argument。"
    "每个 block 保持完整理由和限定。"
    "使用当前接口的引用或论述选择格式；新实质缺口写 followups，不重复抄录上游请求。"
)
OVERALL_OBJECTIVE = (
    "根据各方面最终论证和实际覆盖形成项目整体建议，聚焦跨方面取舍、贡献、共享资源、"
    "失败后剩余价值、替代方案和决定性支持条件，不重复各方面全文。"
    "保留未知、未评范围和开放缺口，不把局部批评累加成整体否定。"
    "条件区分支持前、实施里程碑和一般建议；比较判断须有依据，金额不能证明经费不足。"
    "需要核对原意见时用 read_argument，不能自行补造事实或重新启动研究。"
    "使用当前接口的引用或论述选择格式，发现新的事实缺口写 followups。"
)
OVERALL_PARTIAL_OBJECTIVE = (
    "整合本批跨方面论证，形成供总体综合使用的内部论证。保留贡献、决定性条件、"
    "反例、专家分歧和尚未回答的缺口，删除重复背景。不要作全项目资助表决，"
    "不要宣称覆盖本批之外的方面。每块保留完整理由及其限定，选择本批论述为依据。"
    "尚未解决的冲突留给总体综合，不能通过省略反方取得一致。"
)


def coverage_view(coverage):
    if "status" not in coverage:
        return {aspect: coverage_view(item) for aspect, item in coverage.items()}
    value = {
        k: coverage[k]
        for k in (
            "status",
            "applicability",
            "assessment_status",
            "synthesis_limit",
            "external_needs",
            "reason",
            "unfinished",
        )
        if k in coverage
    }
    # Retain the substantive qualification, without repeated artifact and expert bookkeeping.
    value["analysis_limits"] = {
        member: [
            {k: row[k] for k in ("status", "explanation") if k in row}
            for row in rows
            if row.get("status") != "analyzed"
        ]
        for member, rows in coverage.get("member_outcomes", {}).items()
        if any(row.get("status") != "analyzed" for row in rows)
    }
    return value


def request_view(requests):
    result = []
    for row in requests:
        value = {
            k: row[k]
            for k in ("request_id", "request", "status", "reason", "origins", "request_forms")
            if k in row
        }
        if row.get("outcome"):
            value["outcome"] = {
                k: row["outcome"][k]
                for k in (
                    "action",
                    "remaining_gap",
                    "dependency",
                    "uncertainty",
                    "impact",
                    "result_refs",
                    "proposal",
                    "expert_tasks",
                    "external_needs",
                    "retained_unknowns",
                    "question_id",
                )
                if k in row["outcome"]
            }
        links = set()

        def project(node):
            if isinstance(node, dict):
                return {k: project(v) for k, v in node.items()}
            if isinstance(node, list):
                return [project(v) for v in node]
            if isinstance(node, str):
                links.update(re.findall(r"(?<!\\)\[@(statement-[^]]+)\]", node))
                return re.sub(r"(?<!\\)\[@statement-[^]]+\]", "", node)
            return node

        value = project(value)
        if links:
            value["argument_links"] = sorted(links)
        result.append(value)
    return result


def request_evidence(requests):
    return {k: v for row in requests for k, v in row.get("evidence", {}).items()}


class Synthesis:
    def __init__(self, execution, preparation):
        self.ex, self.prep, self.records = execution, preparation, execution.records

    def contract(self, reader, access, *, assessment=False, open_requests=()):
        route = self.ex.runtime.config.formal_routes.get("assessment")
        inherited = bool(route and route.protocol == "inherited")
        contract = grounded_contract(
            "assessment" if assessment else "synthesis",
            lambda ev, rs: AssessmentDraft if assessment else ResearchDraft,
            scope=reader.references,
            results=lambda: access.available,
            evidence=request_evidence(open_requests),
            compact_sources=True,
            inherited=inherited,
        )
        access.contract = contract
        return contract

    def materials(self, project, coverage, access, open_requests=()):
        return [
            product_material("project", {k: v for k, v in project.items() if k != "evidence"}),
            product_material("actual-coverage", coverage_view(coverage)),
            product_material("original-argument-index", access.index()),
            product_material("open-requests", request_view(open_requests)),
        ]

    def fits(
        self, blocks, project, item, allowed, objective, *, assessment=False, open_requests=()
    ):
        access = ArgumentAccess(self.records, allowed, blocks)

        def contract(reader):
            return self.contract(reader, access, assessment=assessment, open_requests=open_requests)

        return self.ex.preview(
            "synthesis/capacity-preview",
            "assessment",
            objective,
            contract,
            reader_factory=self.prep.reader,
            tools_factory=access.tools,
            materials=self.materials(project, item, access, open_requests),
        )["fits"]

    async def generate(self, blocks, project, item, allowed, aspect, kind, *, open_requests=()):
        overall = kind == "assessment"
        purpose = (
            "这是本方面唯一最终论证，整合全部给定输入。"
            if kind == "aspect-final"
            else "这是内部批次，只分析本批关系与条件，不宣称完成整个方面、不写全项目背景。"
        )
        objective = (
            OVERALL_OBJECTIVE
            if overall
            else OVERALL_PARTIAL_OBJECTIVE
            if kind == "overall-partial"
            else ASPECT_OBJECTIVE.format(aspect=aspect, purpose=purpose)
        )
        access = ArgumentAccess(self.records, allowed, blocks)
        key = (
            f"synthesis/{aspect}/{kind}/"
            + digest([blocks, project, item, allowed, open_requests])[:16]
        )
        reader = self.prep.reader()
        contract = self.contract(reader, access, assessment=overall, open_requests=open_requests)
        result = await self.ex.required(
            key,
            "assessment",
            objective,
            contract,
            reader=reader,
            read_actions=4,
            tools=access.tools(reader),
            materials=self.materials(project, item, access, open_requests),
            reading=lambda: {
                "application": reader.coverage(),
                "arguments": sorted(access.read_refs),
            },
        )
        result["input_refs"] = sorted(
            {b["artifact_ref"] for b in blocks.values()} | access.read_refs
        )
        if overall:
            return result, key
        return save_artifact(
            self.records,
            kind,
            result,
            task=key,
            author="assessment/" + aspect,
            receipt=self.ex.receipts[key],
            dependencies=result["input_refs"],
        )

    def split(self, blocks, project, item, allowed, objective, *, open_requests=()):
        # Keep opinions about a question together, splitting only at whole block boundaries.
        units = {}
        for key, block in sorted(blocks.items()):
            task = self.records.value(block["artifact_ref"])["task_id"].split("/")
            question = task[1] if len(task) > 2 and task[0] == "research" else block["artifact_ref"]
            units.setdefault(question, {})[key] = block
        groups, group = [], {}
        for unit in units.values():
            pieces = (
                [unit]
                if self.fits(unit, project, item, allowed, objective, open_requests=open_requests)
                else [{k: v} for k, v in unit.items()]
            )
            for piece in pieces:
                if not self.fits(
                    piece, project, item, allowed, objective, open_requests=open_requests
                ):
                    raise ValueError("complete_argument_exceeds_context")
                if group and not self.fits(
                    group | piece, project, item, allowed, objective, open_requests=open_requests
                ):
                    groups.append(group)
                    group = {}
                group.update(piece)
        if group:
            groups.append(group)
        return groups

    async def aspect(self, aspect, related, coverage, project, originals, internal):
        objective = ASPECT_OBJECTIVE.format(
            aspect=aspect, purpose="这是本方面唯一最终论证，整合全部给定输入。"
        )
        if self.fits(related, project, coverage, originals, objective):
            return await self.generate(
                related, project, coverage, originals, aspect, "aspect-final"
            )
        groups = self.split(related, project, coverage, originals, objective)
        parts = []
        for group in groups:
            ref = await self.generate(group, project, coverage, originals, aspect, "aspect-partial")
            parts.append(ref)
            internal.append(ref)
        # Every reduction consumes >=2 complete units; at most n-1 further calls.
        for _ in range(len(parts) - 1):
            arguments = self.records.result_blocks(parts)
            allowed = sorted(set(originals + internal))
            if self.fits(arguments, project, coverage, allowed, objective):
                return await self.generate(
                    arguments, project, coverage, allowed, aspect, "aspect-final"
                )
            for i in range(len(parts) - 1):
                pair = self.records.result_blocks(parts[i : i + 2])
                if self.fits(pair, project, coverage, allowed, objective):
                    ref = await self.generate(
                        pair, project, coverage, allowed, aspect, "aspect-partial"
                    )
                    internal.append(ref)
                    parts[i : i + 2] = [ref]
                    break
            else:
                raise ValueError("two_complete_partial_arguments_exceed_context")
        raise ValueError("aspect-final_unavailable_within_reduction_bound")

    async def run(self, artifacts, coverage, project, *, selected_aspects=None, open_requests=()):
        selected = set(REVIEW_ASPECTS if selected_aspects is None else selected_aspects)
        if selected - set(REVIEW_ASPECTS):
            raise ValueError("unknown_assessment_aspect")
        aspect_refs, internal, actual = {}, [], {k: dict(v) for k, v in coverage.items()}
        all_blocks = self.records.result_blocks(sorted(artifacts))
        for aspect in REVIEW_ASPECTS:
            item = coverage[aspect]
            if aspect not in selected or not item["result_refs"]:
                continue
            related = {
                k: v for k, v in all_blocks.items() if v["artifact_ref"] in item["result_refs"]
            }
            try:
                final = await self.aspect(
                    aspect, related, item, project, sorted(item["result_refs"]), internal
                )
                aspect_refs[aspect] = [final]
            except ValueError as exc:
                actual[aspect]["synthesis_limit"] = str(exc)
        if not aspect_refs and artifacts:
            reasons = "; ".join(
                f"{a}: {v['synthesis_limit']}"
                for a, v in actual.items()
                if v.get("synthesis_limit")
            )
            raise ValueError(
                f"aspect_synthesis_unavailable ({len(artifacts)} research artifacts): "
                + (reasons or "no_selected_aspect_inputs")
            )
        return await self.overall(
            artifacts,
            aspect_refs,
            actual,
            project,
            internal_refs=internal,
            open_requests=open_requests,
        )

    async def overall(
        self, artifacts, aspect_refs, coverage, project, *, internal_refs=(), open_requests=()
    ):
        if not aspect_refs:
            raise ValueError("no_professional_basis_for_assessment")
        actual = {
            a: {
                **coverage[a],
                "assessment_status": "completed" if aspect_refs.get(a) else "unassessed",
            }
            for a in REVIEW_ASPECTS
        }
        refs = [r for a in REVIEW_ASPECTS for r in aspect_refs.get(a, [])]
        if any(len(rows) != 1 for rows in aspect_refs.values()):
            raise ValueError("one_final_per_aspect_required")
        internal_refs = list(internal_refs)
        allowed = sorted(set([*artifacts, *internal_refs, *refs]))
        arguments = self.records.result_blocks(refs)
        if not self.fits(
            arguments,
            project,
            actual,
            allowed,
            OVERALL_OBJECTIVE,
            assessment=True,
            open_requests=open_requests,
        ):
            arguments = await self.reduce_overall(
                arguments, project, actual, allowed, internal_refs, open_requests
            )
            allowed = sorted(set([*allowed, *internal_refs]))
        result, key = await self.generate(
            arguments,
            project,
            actual,
            allowed,
            "overall",
            "assessment",
            open_requests=open_requests,
        )
        result.update(
            project={k: v for k, v in project.items() if k != "evidence"},
            coverage=actual,
            aspect_refs=aspect_refs,
            professional_refs=sorted(artifacts),
            internal_refs=sorted(set(internal_refs)),
        )
        followups = [
            f for r in [*internal_refs, *refs] for f in self.records.value(r).get("followups", [])
        ]
        result["followups"] = list({digest(f): f for f in followups + result["followups"]}.values())
        # Appended upstream gaps retain their original source bindings as well as their text.
        cited = {
            k
            for f in result["followups"]
            for text in f.values()
            for k in re.findall(r"\[@([^]]+)\]", text)
        }
        for ref in [*internal_refs, *refs]:
            source = self.records.value(ref)
            statements = statement_subset(statement_registry(source), cited)
            if statements:
                result.setdefault("statement_content", {}).setdefault(
                    "/registry", {"version": 2, "statements": {}}
                )["statements"].update(statements)
                cited.update(
                    k
                    for s in statements.values()
                    for p in s.get("parts", []) + s.get("scope", [])
                    for k in p["cites"]
                )
            result["evidence"].update({k: v for k, v in source["evidence"].items() if k in cited})
        return save_artifact(
            self.records,
            "assessment",
            result,
            task=key,
            author="project-assessment",
            receipt=self.ex.receipts[key],
            dependencies=allowed,
        )

    async def reduce_overall(self, arguments, project, coverage, allowed, internal, requests):
        """One map pass, then at most n-1 merges; originals remain available for comparison."""
        groups = self.split(
            arguments,
            project,
            coverage,
            allowed,
            OVERALL_PARTIAL_OBJECTIVE,
            open_requests=requests,
        )
        parts = []
        for group in groups:
            ref = await self.generate(
                group,
                project,
                coverage,
                allowed,
                "overall",
                "overall-partial",
                open_requests=requests,
            )
            parts.append(ref)
            internal.append(ref)
        while parts:
            selected = self.records.result_blocks(parts)
            current = sorted(set([*allowed, *internal]))
            if self.fits(
                selected,
                project,
                coverage,
                current,
                OVERALL_OBJECTIVE,
                assessment=True,
                open_requests=requests,
            ):
                return selected
            # Each successful merge strictly reduces the number of frontier artifacts.
            for i in range(len(parts) - 1):
                pair = self.records.result_blocks(parts[i : i + 2])
                if not self.fits(
                    pair,
                    project,
                    coverage,
                    current,
                    OVERALL_PARTIAL_OBJECTIVE,
                    open_requests=requests,
                ):
                    continue
                ref = await self.generate(
                    pair,
                    project,
                    coverage,
                    current,
                    "overall",
                    "overall-partial",
                    open_requests=requests,
                )
                internal.append(ref)
                parts[i : i + 2] = [ref]
                break
            else:
                raise ValueError("overall_reduction_cannot_fit_complete_arguments")
