"""Understand the application and configure expertise, without specialist proposals."""

from typing import Literal

from pydantic import Field

from spacebio_review.context import product_material
from spacebio_review.evidence.submissions import record
from spacebio_review.review.expertise import ExpertiseCatalogue
from spacebio_review.review.materials import ApplicationReader
from spacebio_review.review.project import SourceRef

from .contracts import choices_field, grounded_contract, reading_contract
from .questions import QuestionGeneration


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

    async def read(self):
        reader = self.reader()
        objective = (
            "读取本批完整申请段落，辨认目标、活动、观测产物、申请主张与依赖。"
            "所有首次/领先表述只是申请自述；观察段须选择原文依据。"
            "不做资助判断，不生成评审题；保留单位、条件、表格含义和读取限制。"
        )
        if self.ex.system.id == "gbt42048_2022":
            objective += (
                "保留已有载荷及实验模块、单元或样品的事实，区分载荷既有能力与本项目工作。"
                "区分已完成的空间实验或地面预实验与拟开展工作；保留实际结果及支持范围。"
                "记录资源、接口、在轨操作、回收、地面支持和安全可靠性安排及原文缺口。"
            )
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
            result = await self.ex.call(
                f"prepare/read/{i}",
                "preparation",
                objective,
                reading_contract(scope=reader.references),
                reader=reader,
                refs=refs,
            )
            if result.product:
                notes.append(result.product)
            else:
                limits.append(f"material_batch/{i}: {result.reason}")
        if not notes:
            raise ValueError("no_application_reading_results")
        return {"notes": notes, "limits": limits, "coverage": reader.coverage()}

    async def map_project(self, readings):
        notes = readings["notes"]
        evidence = {k: v for n in notes for k, v in n["evidence"].items()}

        def project_type(ev, _):
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
            context = {}
            if self.ex.system.id == "gbt42048_2022":
                fact = record(
                    "ProjectContextFact",
                    kind=(
                        Literal[
                            "existing_payload", "research_foundation", "implementation", "safety"
                        ],
                        ...,
                    ),
                    statement=(str, Field(description="计划书中已说明的事实及其确定程度。")),
                    evidence_handles=choices_field(ev, required=True),
                )
                context["review_context"] = (
                    list[fact],
                    Field(
                        description="已有载荷、前期基础、实施依赖和安全可靠性事实；未写明的内容记入理解缺口。"
                    ),
                )
            return record(
                "ProjectMapDraft",
                title=(str, ...),
                project_objective=(
                    str,
                    Field(description="申请希望获得的科学认识或技术能力，不是评审任务。"),
                ),
                paths=(list[path], Field(min_length=1)),
                understanding_limits=(list[str], ...),
                **context,
            )

        objective = (
            "根据已读申请记录建立整体路径：科学目标→活动→产物→申请声称的认识→价值。"
            "这是待评价的项目结构，不确认首创主张，不提出或分配评审题目。"
            "路径 question 是申请自身的研究问题，不是审查问题。保留跨路径依赖及地面/在轨分工。"
        )
        if self.ex.system.id == "gbt42048_2022":
            objective += (
                "review_context 保存已有载荷及其既有能力、已完成的前期实验及实际进展、"
                "工程资源与在轨操作/回收/地面支持依赖、安全可靠性安排。每条事实选取原文依据。"
                "计划开展的实验不计为已完成的研究基础，载荷能力不计为本项目已经取得的成果。"
                "原文未说明的事项记入 understanding_limits；不补齐申请内容，不提前评分。"
                "路径中保留预期科学成果或应用效益，以及地面、在轨和返回分析的角色与时序。"
            )
        project = await self.ex.required(
            "prepare/map",
            "preparation",
            objective,
            grounded_contract("map", project_type, evidence=evidence),
            materials=[product_material("application-readings", notes)],
        )
        project["objective"] = project["project_objective"]
        for i, path in enumerate(project["paths"], 1):
            path["path_id"] = f"path-{i}"
        project["reading_coverage"] = readings["coverage"]
        project["understanding_limits"].extend(readings["limits"])
        return project

    async def team(self, project):
        def team_type(_ev, _rs):
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

        objective = (
            "按实际项目配置互补专业能力，人数由需要决定。能力目录不是事实知识库。"
            "一个专家可负责多个相关问题；六方面无需各设一个席位。"
            "只配置评审能力和职责，不提出评审题；专家仅在后续对分配的题目开展研究。"
        )
        if self.ex.system.id == "gbt42048_2022":
            objective += (
                "结合项目路径和 review_context 配置科学领域、实验方法、已有载荷工程实施及安全能力。"
                "研究基础和成果效益由能分析相关事实的专家承担；资源管理能力服务于实施条件判断。"
                "能力组合服务国标六项评价，不另设项目管理评分维度。"
            )
        team = await self.ex.required(
            "prepare/team",
            "preparation",
            objective,
            grounded_contract("team", team_type),
            materials=[
                product_material("project", project),
                product_material("catalogue", self.catalogue.brief()),
            ],
        )
        experts = {}
        for i, expert in enumerate(team["experts"], 1):
            expert["profile_labels"] = [
                self.catalogue.entries[p].expertise for p in expert["profile_ids"]
            ]
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
            experts[f"expert-{i}"] = expert
        return experts

    def questions(self, package):
        return QuestionGeneration(self, package)
