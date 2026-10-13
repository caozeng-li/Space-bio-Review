"""A complete professional task: original reading, literature, argument and artifact."""

import re

from pydantic import BaseModel, Field

from spacebio_review.context import product_material
from spacebio_review.evidence.documents import reading_passages
from spacebio_review.evidence.knowledge import KnowledgeQuestion
from spacebio_review.review.access import ReviewAccess
from spacebio_review.tools import Tool, ToolResult

from .contracts import PUBLIC_PROSE, professional_contract
from .handoff import knowledge_artifact
from .preparation import section_refs


class LiteratureRequest(BaseModel):
    question: str = Field(min_length=1)
    needed_information: list[str] = Field(min_length=1)
    changes_judgment_if: str = Field(min_length=1)


def artifact_dependencies(value):
    refs = set()
    structured = bool(value.get("citation_content")) if isinstance(value, dict) else False

    def visit(node):
        if isinstance(node, dict):
            refs.update(r.split("::", 1)[0] for r in node.get("result_refs", []))
            refs.update(r.split("::", 1)[0] for r in node.get("scope_links", []) if "::" in r)
            if structured:
                refs.update(r.split("::", 1)[0] for r in node.get("cites", []) if "::" in r)
            for v in node.values():
                visit(v)
        elif isinstance(node, list):
            for v in node:
                visit(v)

        elif isinstance(node, str) and not structured:
            refs.update(
                k.split("::", 1)[0] for k in re.findall(r"(?<!\\)\[@([^]]+)\]", node) if "::" in k
            )

    visit(value)
    return sorted(refs)


def save_artifact(records, kind, product, *, task, author, receipt, dependencies=()):
    value = {
        **product,
        "author": author,
        "task_id": task,
        "input_version": receipt["input"],
        "reading": receipt["reading"],
    }
    value["blocks"] = [{**b, "block_id": f"b{i}"} for i, b in enumerate(value["blocks"], 1)]
    # Other paragraph lists (conditions/options) remain local, never independent arguments.
    from .artifacts import AssessmentArtifact, ProfessionalArtifact, ResearchArtifact
    from .records import plain

    if kind == "research":
        for outcome in value["scope_outcomes"]:
            indices = outcome.pop("block_indices")
            if any(i < 1 or i > len(value["blocks"]) for i in indices):
                raise ValueError("scope_outcome_block_out_of_range")
            outcome["block_ids"] = [f"b{i}" for i in dict.fromkeys(indices)]
    schema = (
        AssessmentArtifact
        if kind == "assessment"
        else ProfessionalArtifact
        if kind == "research"
        else ResearchArtifact
    )
    value = schema.model_validate(plain(value)).model_dump(mode="json")
    return records.put(kind, value, sorted(set(dependencies) | set(artifact_dependencies(value))))


class Research:
    def __init__(self, preparation, package, literature):
        self.prep, self.package, self.literature = preparation, package, literature
        self.ex, self.records = preparation.ex, preparation.ex.records

    async def run(self, task, *, related=(), originals=()):
        expert = self.package["experts"][task["expert_id"]]
        members = task["members"]
        scope = task["analysis_scope"]
        route = self.ex.runtime.config.formal_routes.get("specialist")
        inherited = bool(route and route.protocol == "inherited")
        work = {
            "members": members,
            "requests": task.get("requests", []),
            "external_needs": task["external_needs"],
            "retained_unknowns": task["retained_unknowns"],
        }
        reader = self.prep.reader()
        question = KnowledgeQuestion(
            question=scope,
            background=self.package["project"]["objective"],
            cutoff_date=self.prep.config.evaluation.historical_as_of,
            existing_source_ids=list(dict.fromkeys(v["source_id"] for v in originals)),
        )
        session = self.literature.new_session(question, whole_units=inherited)
        access = ReviewAccess(
            self.prep.store, reader, cutoff=question.cutoff_date, whole_units=inherited
        )
        access.additional_sources.append(session.references)
        tools = access.tools()
        tools.include(session.tools())
        available = self.records.result_blocks(
            sorted(set(related) | set(artifact_dependencies(work)))
        )
        delegated = []
        remaining = self.ex.config.literature_tasks_per_question

        async def investigate(args):
            nonlocal remaining
            if remaining <= 0:
                return ToolResult(ok=False, error="task_literature_budget_exhausted")
            pool = self.literature.runtime.scheduler.research_pool.get()
            if (
                pool
                and pool.remaining - sum(pool.reserves.values())
                < self.literature.minimum_research_turns
            ):
                return ToolResult(ok=False, error="shared_research_turns_reserved_for_completion")
            remaining -= 1
            run = await self.literature.investigate(
                question.model_copy(
                    update={
                        "question": args.question,
                        "needed_information": args.needed_information,
                        "assessment_focus": args.changes_judgment_if,
                        "background": question.background + "\n本次专业问题：" + scope,
                    }
                ),
                whole_units=inherited,
            )
            delegated.append(
                {
                    "research_id": run.research_id,
                    "sources": run.sources,
                    "limitations": run.limitations,
                }
            )
            access.register_knowledge(run)
            added = {}
            if run.answer:
                ref = self.records.put(
                    "knowledge",
                    knowledge_artifact(
                        run.answer_view(), run.research_id, args.question, run.limitations
                    ),
                )
                added = self.records.result_blocks([ref])
                available.update(added)
            active = set(contract.task_context.active)
            contract.refresh(contract)
            contract.task_context.active = active
            if run.answer:
                contract.task_context.activate(run.answer_view().get("evidence_resolution", {}))
                contract.task_context.activate(added)
            return ToolResult(
                ok=bool(run.answer),
                content={
                    "result_refs": list(added),
                    "limitations": run.limitations,
                    "provenance": "delegated_material_not_expert_conclusion",
                },
            )

        if remaining:
            tools.add(
                Tool(
                    "research_knowledge",
                    "委派文献研究；返回资料，不替代专业判断。",
                    LiteratureRequest,
                    investigate,
                    delegates=True,
                    exploration=True,
                    timeout_seconds=3600,
                )
            )
        # Explicitly supplied originals are read through the real session observer.
        # They are retained source excerpts, not discovery results or a synthetic read ledger.
        provided = []
        for ref in originals:
            doc = self.prep.store.retained(ref["source_id"])
            rows = reading_passages(doc, whole_units=inherited)
            units = {
                p["unit_id"]
                for p in rows
                if p["passage_id"] in ref["passage_ids"] and p.get("unit_id")
            }
            rows = [
                p
                for p in rows
                if p["passage_id"] in ref["passage_ids"] or p.get("unit_id") in units
            ]
            payload = {"source_id": ref["source_id"], "passages": rows}
            session.record_context_read(payload)
            provided.append(payload)
        source_evidence = dict(self.package["project"].get("evidence", {}))
        source_evidence.update(self.package.get("question_evidence", {}))
        selected = [
            source_evidence[h]
            for member in members.values()
            for h in member.get("evidence_handles", [])
            if h in source_evidence
        ]
        refs = section_refs(reader, task.get("application_refs", selected))
        project_context = {k: v for k, v in self.package["project"].items() if k != "evidence"}
        key = task["task_id"]
        structured_refs = (
            inherited
            or bool(route and route.protocol == "cited_parts")
            or (
                self.ex.runtime.config.routed_model(
                    "specialist", expert.get("model_tier", "normal"), formal=True
                ).structured_output
                == "json_schema"
            )
        )
        contract = professional_contract(
            access.source_scope,
            lambda: available,
            members,
            evidence=source_evidence,
            structured_refs=structured_refs,
            inherited=inherited,
        )
        prose_shape = (
            "每个工作论述"
            if inherited
            else "每个 blocks 项"
            if structured_refs
            else "每个 blocks.text"
        )
        citation_style = (
            "研究中形成完整 content、scope、based_on；已有论述用 use 继承。"
            "交接后正式阶段只组织 C 论述，不重写科学内容。"
            if inherited
            else "正文由 parts 的 text 和 cites 组成，引用在 cites 选择已有编号，不手写引用标记。"
            if structured_refs
            else "引用在对应语句后写 [@E1] 或 [@R1]，程序提取引用列表。"
        )
        result = await self.ex.call(
            key,
            "specialist",
            "主答当前具体问题；只纳入会影响此问题的跨项目关系，不将每项任务写成全项目概述。"
            "以 members 中的问题为评审对象；专家只评审本题，不生成或重新分配评审题。"
            "将可分析内容与仍需外部提供的事实分开，不以索取材料代替已具备条件的分析。"
            "保留全项目视角，可回读完整申请、纠正问题预设。"
            "比较最接近先例、方法特异性、替代解释与条件，说明贡献和项目影响。"
            "申请首次是自述，有限未检出不证明全球首次；重要数值必须有出处或明确推导。"
            f"{prose_shape}是完整论证，局部条件同块保留；共同范围写 scope_note。"
            "E 为材料证据，R 为已有论证；没有依据的外部事实不能当作确定前提。"
            "当前能完成的阅读、检索与分析在本题内完成。真正未决的信息及影响写 followups；"
            "这只是科学局限记录，不会启动其他专家或补充派工，不能代替当前可做的分析。"
            "申请未来实验与现在可研究的路径分开说明。"
            f"正文应可直接作为专业报告；{citation_style}"
            "不从通用资料推出未检索的具体先例；候选物理解释须明确为假设，不能变成必需门槛。"
            "比较性判断必须有比较对象与依据（如成本、工作量或具体先例）；没有比较资料时，"
            "只说明需要核实的条件，不把金额大小或专业直觉写成已证实的资源不足。"
            "涉及仪器极限需实际光学及测量参数，不能拿通用文献尺度当设备测量结论。"
            + (
                "在工作交接逐项填写 scope_outcomes，状态及科学说明由正式产物继承。"
                if inherited
                else "按本专家指定成员逐项填写 scope_outcomes，引用 blocks 的 1 起始下标。"
            )
            + "结论不确定但分析已完成可标 analyzed；未分析或只完成一部分不能标完成。"
            "外部依赖保持条件，不编造事实。"
            "不写工具调用过程，不作资助表决。" + PUBLIC_PROSE,
            contract,
            reader=reader,
            refs=refs,
            tools=tools,
            skills=expert["skills"],
            tier=expert.get("model_tier", "normal"),
            research=True,
            materials=[
                product_material("project", project_context),
                product_material("professional-work", work),
                product_material("provided-originals", provided),
            ],
            reading=lambda: {
                "application": reader.coverage(),
                "literature": session.reading_records(),
                "delegated": delegated,
            },
        )
        if not result.product or result.status != "completed":
            raise ValueError(f"{key}: {result.reason or result.status}")
        return save_artifact(
            self.records,
            "research",
            result.product,
            task=key,
            author=task["expert_id"],
            receipt=self.ex.receipts[key],
            dependencies=sorted({v["artifact_ref"] for v in available.values()}),
        )
