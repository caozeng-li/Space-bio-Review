"""Read the application, then let specialists revise questions before research."""

from typing import Annotated, Literal, Union

from pydantic import Field

from spacebio_review.context import product_material
from spacebio_review.evidence.submissions import record, selection
from spacebio_review.review.expertise import ExpertiseCatalogue
from spacebio_review.review.materials import ApplicationReader
from spacebio_review.review.project import REVIEW_ASPECTS, SourceRef

from .contracts import (
    DraftBlock,
    choices_field,
    grounded_contract,
    proposal_type,
    reading_contract,
    work_fields,
)


def section_refs(reader, evidence=()):
    """Expand selected locations to whole natural sections, including neighbouring limits."""
    sections = {}
    for item in evidence:
        ref = item.get("reference", item)
        doc = reader.docs.get(ref["source_id"])
        if doc:
            for p in doc.passages:
                if p.passage_id == ref.get("passage_id"):
                    sections.setdefault(ref["source_id"], set()).add(p.section)
    return [
        SourceRef(source_id=sid, passage_id=p.passage_id)
        for sid, doc in reader.docs.items()
        if doc
        for p in doc.passages
        if p.section in sections.get(sid, set())
    ]


class Preparation:
    def __init__(self, execution, store, bundle, config):
        self.ex, self.store, self.bundle, self.config = execution, store, bundle, config
        self.ex.preparation_limits = config
        self.catalogue = ExpertiseCatalogue(config.catalogue, execution.runtime.skills)

    def reader(self):
        reader = ApplicationReader(self.bundle, self.store, self.ex.runtime)
        reader.references.cutoff = self.config.evaluation.historical_as_of
        return reader

    async def run(self):
        reader = self.reader()
        # Every passage gets an opportunity in a bounded, whole-passage batch.
        batches, batch, size = [], [], 0
        for sid, doc in reader.docs.items():
            for p in doc.passages if doc else []:
                if batch and size + len(p.text) > self.ex.config.material_batch_chars:
                    batches.append(batch)
                    batch, size = [], 0
                batch.append(SourceRef(source_id=sid, passage_id=p.passage_id))
                size += len(p.text)
        if batch:
            batches.append(batch)
        notes, limits = [], []
        for i, refs in enumerate(batches):
            contract = reading_contract(scope=reader.references)
            result = await self.ex.call(
                f"prepare/read/{i}",
                "preparation",
                "读取本批完整申请段落，辨认目标、活动、观测产物、申请主张与依赖。"
                "所有首次/领先表述只是申请自述；观察段须选择原文依据。"
                "不做资助判断，保留单位、条件、表格含义和读取限制。",
                contract,
                reader=reader,
                refs=refs,
            )
            if result.product:
                notes.append(result.product)
            else:
                limits.append(f"material_batch/{i}: {result.reason}")
        if not notes:
            raise ValueError("no_application_reading_results")
        evidence = {k: v for n in notes for k, v in n["evidence"].items()}

        project = await self.map_project(notes, evidence)
        for i, path in enumerate(project["paths"], 1):
            path["path_id"] = f"path-{i}"
        project["reading_coverage"] = reader.coverage()
        project["understanding_limits"].extend(limits)
        proposals = list(project.pop("questions"))

        def team_type(ev, rs):
            expert = record(
                "ExpertCapability",
                purpose=(str, ...),
                model_tier=(Literal["normal", "strong"], ...),
                profile_ids=choices_field(self.catalogue.entries, required=True),
                method_skill_ids=choices_field(self.catalogue.methods),
            )
            return record(
                "ExpertTeam",
                experts=(list[expert], Field(min_length=1, max_length=self.config.max_experts)),
            )

        team = await self.ex.required(
            "prepare/team",
            "preparation",
            "按实际项目配置互补专业能力，人数由需要决定。能力目录不是事实知识库。"
            "一个专家可负责多个相关问题；六方面无需各设一个席位。",
            grounded_contract("team", team_type),
            materials=[
                product_material("project", project),
                product_material("catalogue", self.catalogue.brief()),
            ],
        )
        experts = {}
        for i, expert in enumerate(team["experts"], 1):
            eid = f"expert-{i}"
            expert["skills"] = list(
                dict.fromkeys(
                    [self.catalogue.entries[p].skill for p in expert["profile_ids"]]
                    + [
                        m
                        for p in expert["profile_ids"]
                        for m in self.catalogue.entries[p].method_skills
                    ]
                    + expert["method_skill_ids"]
                    + self.config.evaluation.supplements
                )
            )
            experts[eid] = expert
            orientation, specialist = await self.orient(project, expert, eid)
            expert["orientation_status"] = orientation.status
            expert["orientation_reason"] = orientation.reason
            expert["orientation"] = orientation.product
            expert["reading_coverage"] = specialist.coverage()
            if orientation.product:
                proposals.extend(orientation.product["proposals"])
            else:
                project["understanding_limits"].append(f"orientation/{eid}: {orientation.reason}")
        return {"project": project, "experts": experts, "proposals": proposals}

    async def map_project(self, notes, evidence):
        def project_type(ev, rs):
            path = record(
                "ProjectPath",
                question=(str, ...),
                activity=(str, ...),
                observation_or_artifact=(str, ...),
                claimed_knowledge=(str, ...),
                claimed_value=(str, ...),
                dependencies=(list[str], ...),
                evidence_handles=choices_field(ev, required=True),
            )
            return record(
                "ProjectMapDraft",
                title=(str, ...),
                project_objective=(
                    str,
                    Field(
                        description="申请项目要获得的科学认识或技术能力；不是梳理、评审或分配任务。"
                    ),
                ),
                paths=(list[path], Field(min_length=1)),
                understanding_limits=(list[str], ...),
                questions=(list[proposal_type(ev)], Field(min_length=1)),
            )

        project = await self.ex.required(
            "prepare/map",
            "preparation",
            "根据已读申请记录建立整体路径：活动→产物→申请声称的认识→价值。"
            "这是待评价的项目结构，不能确认申请的首创主张。提出具体且影响判断的问题，"
            "跨路径依赖保留，覆盖价值、创新、严谨、可行、安全、管理。",
            grounded_contract("map", project_type, evidence=evidence),
            materials=[product_material("application-readings", notes)],
        )
        project["objective"] = project["project_objective"]
        return project

    async def orient(self, project, expert, eid):
        specialist = self.reader()

        def orientation_type(ev, rs):
            return record(
                "ProfessionalOrientation",
                understanding=(str, ...),
                corrections=(list[DraftBlock], ...),
                proposals=(list[proposal_type(ev)], ...),
            )

        orientation = await self.ex.call(
            f"prepare/orient/{eid}",
            "specialist",
            "先看全项目结构和完整材料目录，主动补读本专业相关完整方案。"
            "纠正项目结构，提出遗漏或带错误预设的问题；可以跨既有问题扩展。"
            "相关实验、样品链不要切碎；基于完整方案独立发现问题，不预设必须批评。"
            "区分直接观察、推断和未核实的专业前提，同时说明潜在贡献。",
            grounded_contract("orientation", orientation_type, scope=specialist.references),
            reader=specialist,
            skills=expert["skills"],
            tier=expert["model_tier"],
            materials=[
                product_material("project", project),
                product_material("expert-purpose", expert),
            ],
        )
        return orientation, specialist

    async def allocate(
        self, project, experts, questions, *, suffix="initial", remaining_calls=0, gaps=None
    ):
        active = questions
        evidence = dict(project["evidence"])
        for expert in experts.values():
            evidence.update((expert.get("orientation") or {}).get("evidence", {}))

        def plan_type(ev, rs):
            proposal = proposal_type(ev)
            proposed_task = record(
                "NewQuestionAssignment",
                proposal=(proposal, ...),
                **work_fields(experts, ["self"]),
                disposition=(Literal["research", "defer"], ...),
                reason=(str, ...),
            )
            entry = record(
                "QuestionAllocation",
                question_id=(selection(active) if active else str, ...),
                disposition=(
                    Literal["research", "defer", "withdraw"],
                    Field(
                        description=(
                            "调度决定：research=交给专家研究，defer=本轮暂不研究，withdraw=问题不成立。"
                            "仅无当前分析范围时延期，外部依赖不取消可分析部分。"
                        )
                    ),
                ),
                **work_fields(experts, active),
                related=choices_field(active),
                merge_into=choices_field(active),
                reason=(str, ...),
            )
            coverage = record(
                "AspectCoverage",
                applicability=(Literal["applicable", "not_applicable", "undetermined"], ...),
                explanation=(str, ...),
                no_work_reason=(
                    str,
                    Field(description="该方面无当前分析工作时的具体原因，否则为空。"),
                ),
            )
            return record(
                "QuestionPlan",
                assignments=(list[entry], ...),
                new_questions=(list[proposed_task], ...),
                coverage=(record("Coverage", **{k: (coverage, ...) for k in REVIEW_ASPECTS}), ...),
                project_corrections=(list[DraftBlock], ...),
            )

        return await self.ex.required(
            f"prepare/allocate/{suffix}",
            "preparation",
            "结合专家的原始理解和纠正分配问题。每题确定负责专家、关联问题，同题专家独立提出意见，"
            "同一主张跨方面时可合并，merge_into 至多一个且不能互相合并。"
            "不同科学观点不能因合并删除。没有能力或预算用 defer 并说明；"
            "问题前提不成立或不适用用 withdraw 并说明，保留记录。"
            "related 表示共同研究的强关联，非所有背景联系。落实专家指出的项目结构修正。"
            "先检查适用方面是否有实质任务；有能力及预算但遗漏时用 new_questions 新建并分配，"
            "不能只说以后覆盖，也不能把所有遗漏一律延期。专家定位失败不等于该专业已评。"
            "比较新增与既有提案的对象、已有证据和新增信息路径；同一未知无新材料不要重开。"
            "proposal.resolution_type 描述信息路径，assignment.disposition 描述调度决定。"
            "旧分类只是历史意见，不能取消当前可做的分析。"
            "expert_tasks 是唯一责任来源：填写专家、负责的成员 ID 和具体 analysis_scope；"
            "合并后根任务必须包含可分析成员。external_needs/retained_unknowns 独立保留，"
            "不要求编造事实。新问题 member_ids 使用 self，程序分配正式编号。若收到 planning-gaps，"
            "只返回补充差量，不重排无关工作。"
            "coverage applicability 用 applicable/not_applicable/undetermined。",
            grounded_contract("allocation", plan_type, evidence=evidence),
            materials=[
                product_material("project", project),
                product_material("experts", experts),
                product_material("questions", active),
                product_material("planning-gaps", gaps or {}),
                product_material(
                    "downstream-research-capabilities",
                    {
                        "professional_tasks": "专家可读取完整申请、检索和读取外部文献、解释原图。",
                        "literature_access": (
                            "检索渠道按 evidence 配置启用，实际服务可用性在研究时确定。"
                        ),
                        "delegated_literature_tasks_per_question": (
                            self.ex.config.literature_tasks_per_question
                        ),
                        "allocation_scope": (
                            "本任务只分配工作；当前没有检索工具不代表下游专家无法检索。"
                        ),
                        "orientation_scope": (
                            "定位提交失败表示尚未取得该意见，不表示专家能力或研究工具不可用。"
                        ),
                    },
                ),
                product_material(
                    "remaining-work",
                    {
                        "model_calls_remaining": remaining_calls,
                        "attempts_per_question": self.ex.config.question_attempts,
                        "literature_tasks_per_question": (
                            self.ex.config.literature_tasks_per_question
                        ),
                    },
                ),
            ],
        )

    async def route_requests(
        self, pending, questions, package, *, work_state=None, results=None, previous_requests=()
    ):
        """One semantic decision per gap, with real current work and argument versions."""
        ids = [r["request_id"] for r in pending]
        all_ids = sorted(set(ids + [r["request_id"] for r in previous_requests]))
        roots = {k: q for k, q in questions.items() if not q.get("merged_into")}
        route = self.ex.runtime.config.formal_routes.get("preparation")
        compact = bool(route and route.protocol == "inherited")
        access = None
        if compact:
            from .synthesis import ArgumentAccess, request_view

            supplied = {k for row in pending for k in row.get("results", {})}
            origins = {ref for row in pending for ref in row.get("origins", [])}
            access = ArgumentAccess(
                self.ex.records,
                results or {},
                {
                    k: v
                    for k, v in (results or {}).items()
                    if k in supplied or v.get("artifact_ref") in origins
                },
            )

        def build(ev, rs):
            common = {
                "request_ids": choices_field(ids, required=True),
                "canonical_id": (selection(all_ids), ...),
                "reason": (str, ...),
            }
            variants = []

            def action(name, **fields):
                variants.append(record(name, action=(Literal[name], ...), **common, **fields))

            if rs:
                action(
                    "reuse_result",
                    result_refs=choices_field(rs, required=True),
                    remaining_gap=(
                        str,
                        Field(description="已回答范围之外的缺口；完全回答时为空。"),
                    ),
                )
            pending_work = {k: v for k, v in (work_state or {}).items() if v["pending"]}
            if pending_work:
                action("attach_to_work", question_id=(selection(pending_work), ...))
            if roots:
                action(
                    "revise_work",
                    question_id=(selection(roots), ...),
                    **work_fields(package["experts"], questions),
                )
            action(
                "new_work",
                proposal=(proposal_type(ev), ...),
                **work_fields(package["experts"], ["self"]),
            )
            action("external_dependency", dependency=(str, ...), impact=(str, ...))
            action("retain_unknown", uncertainty=(str, ...), impact=(str, ...))
            decision = Annotated[Union[tuple(variants)], Field(discriminator="action")]
            return record("ResearchRequestRouting", decisions=(list[decision], ...))

        objective = (
            "按实际知识缺口归并请求，同义中英表述放入一个 request_ids，指定 canonical_id，"
            "保留不同对象和条件。参考已处理请求，同一材料和范围不因改写重新研究。"
            "reuse_result 只能引用本轮真实 R 论证，说明回答范围，部分回答保留 remaining_gap；"
            "问题存在不代表有答案。attach_to_work 仅指向 work-state 中已获准且仍待执行的工作。"
            "延期、失败或不足的旧范围需要 revise_work，新分析用 new_work；新问题成员用 self。"
            "同一根的新增范围汇入一次 revise_work。当前可分析内容与外部依赖分开。"
            "仅外部事实用 external_dependency，无可执行分析的未知用 retain_unknown。"
            "每条 pending 处理一次，不为非研究动作编造 proposal。用中文说明处置与剩余缺口。"
            "先前 status=routing_proposal 的处置尚未派工。同一缺口跨批时沿用 canonical_id，"
            "新处置须覆盖该规范缺口的完整范围，不能丢弃前批已列出的分析任务。"
        )
        materials = [
            product_material("pending-requests", pending),
            product_material("previous-requests", previous_requests),
            product_material("existing-questions", questions),
            product_material("work-state", work_state or {}),
            product_material("project-and-experts", package),
        ]
        if access:
            materials = [
                product_material(
                    "pending-requests",
                    [
                        {**v, "origins": row.get("origins", [])}
                        for v, row in zip(request_view(pending), pending, strict=True)
                    ],
                ),
                product_material("previous-requests", request_view(previous_requests)),
                product_material(
                    "existing-questions",
                    {
                        k: {
                            f: v[f]
                            for f in (
                                "proposal",
                                "disposition",
                                "merged_into",
                                "reason",
                                "expert_tasks",
                                "external_needs",
                                "retained_unknowns",
                            )
                            if f in v
                        }
                        for k, v in questions.items()
                    },
                ),
                product_material("work-state", work_state or {}),
                product_material(
                    "project-and-experts",
                    {
                        "project": {k: v for k, v in package["project"].items() if k != "evidence"},
                        "experts": {
                            k: {
                                f: v[f]
                                for f in ("purpose", "profile_ids", "model_tier", "skills")
                                if f in v
                            }
                            for k, v in package["experts"].items()
                        },
                    },
                ),
                product_material("argument-index", access.index()),
            ]
            objective += (
                "task_sources 中已展开的 R 可直接判断是否复用，不必重复读取。"
                "目录只是其他可读分析的索引，不表示已经回答问题；"
                "只有需要未展开的论述时才使用 read_argument。"
                "argument_links/origins 是请求的论证关联，不是直接材料证据或可手写引用编号。"
            )

        evidence = dict(package["project"].get("evidence", {}))
        for row in [*previous_requests, *pending]:
            evidence.update(row.get("evidence", {}))

        def contract(_=None):
            value = grounded_contract(
                "routing",
                build,
                evidence=evidence,
                results=(lambda: access.available) if access else results or {},
                compact_sources=compact,
            )
            if access:
                access.contract = value
            return value

        capacity = self.ex.preview(
            "prepare/route/capacity",
            "preparation",
            objective,
            contract,
            materials=materials,
            tools_factory=access.tools if access else None,
        )
        if not capacity["fits"]:
            if len(pending) < 2:
                raise ValueError("routing_context_exceeds_capacity")
            middle = len(pending) // 2
            first = await self.route_requests(
                pending[:middle],
                questions,
                package,
                work_state=work_state,
                results=results,
                previous_requests=previous_requests,
            )
            original = {r["request_id"]: r for r in [*previous_requests, *pending]}
            proposed = [
                {
                    **original[row["canonical_id"]],
                    "status": "routing_proposal",
                    "outcome": row,
                    "request_forms": [original[i]["request"] for i in row["request_ids"]],
                }
                for row in first["decisions"]
            ]
            second = await self.route_requests(
                pending[middle:],
                questions,
                package,
                work_state=work_state,
                results=results,
                previous_requests=[*previous_requests, *proposed],
            )
            combined, aliases = {}, {}
            for row in [*first["decisions"], *second["decisions"]]:
                canonical = aliases.get(row["canonical_id"], row["canonical_id"])
                prior = combined.get(canonical, {})
                ids = sorted(set(prior.get("request_ids", []) + row["request_ids"]))
                combined[canonical] = {**row, "canonical_id": canonical, "request_ids": ids}
                aliases.update({i: canonical for i in ids})
            return {
                "decisions": list(combined.values()),
                "evidence": {**first.get("evidence", {}), **second.get("evidence", {})},
            }
        return await self.ex.required(
            "prepare/route/" + __import__("hashlib").sha256(str(pending).encode()).hexdigest()[:16],
            "preparation",
            objective,
            contract(),
            materials=materials,
            tools=access.tools(None) if access else None,
            read_actions=4 if access else 0,
        )
