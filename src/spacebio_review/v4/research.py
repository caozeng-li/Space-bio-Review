"""A complete professional task: original reading, literature, argument and artifact."""

import re

from pydantic import BaseModel, Field

from spacebio_review.context import product_material
from spacebio_review.evidence.documents import reading_passages
from spacebio_review.evidence.knowledge import KnowledgePurpose, KnowledgeQuestion
from spacebio_review.review.access import ReviewAccess
from spacebio_review.tools import Tool, ToolResult

from .contracts import PUBLIC_PROSE, professional_contract
from .handoff import knowledge_artifact, literature_question
from .preparation import section_refs

PROFESSIONAL_INPUT_VERSION = "professional-input-3"


class LiteratureRequest(BaseModel):
    question: str = Field(min_length=1)
    purpose: KnowledgePurpose
    needed_information: list[str] = Field(min_length=1)
    changes_judgment_if: str = Field(min_length=1)
    evidence_handles: list[str] = Field(
        min_length=1, description="已读的相关申请原文 E 引用，连同必要的对象、方法及条件。"
    )


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
    from .artifacts import ProfessionalArtifact
    from .records import plain

    if kind == "research":
        for outcome in value["scope_outcomes"]:
            indices = outcome.pop("block_indices")
            if any(i < 1 or i > len(value["blocks"]) for i in indices):
                raise ValueError("scope_outcome_block_out_of_range")
            outcome["block_ids"] = [f"b{i}" for i in dict.fromkeys(indices)]
    value = ProfessionalArtifact.model_validate(plain(value)).model_dump(mode="json")
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
            "input_version": PROFESSIONAL_INPUT_VERSION,
            "members": members,
            "requests": task.get("requests", []),
            "external_needs": task["external_needs"],
            "retained_unknowns": task["retained_unknowns"],
        }
        reader = self.prep.reader()
        question = KnowledgeQuestion(
            question=scope,
            cutoff_date=self.prep.config.evaluation.historical_as_of,
            existing_source_ids=list(dict.fromkeys(v["source_id"] for v in originals)),
        )
        access = ReviewAccess(
            self.prep.store,
            reader,
            cutoff=question.cutoff_date,
            whole_units=inherited,
            runtime=self.ex.runtime,
        )
        tools = access.tools()
        available = self.records.result_blocks(
            sorted(set(related) | set(artifact_dependencies(work)))
        )
        delegated = []
        remaining = self.ex.config.literature_tasks_per_question

        async def investigate(args):
            nonlocal remaining
            if remaining <= 0:
                return ToolResult(ok=False, error="task_literature_budget_exhausted")
            request = literature_question(args, question, access.source_scope.evidence_choices())
            scheduler = self.literature.runtime.scheduler
            pool = scheduler.research_pool.get()
            if (
                pool
                and pool.remaining - sum(pool.reserves.values())
                < self.literature.minimum_turns(single_submission=True) + 1
            ):
                return ToolResult(ok=False, error="shared_research_turns_reserved_for_completion")
            remaining -= 1
            owner = scheduler.research_owner.get()
            # Keep a parent interpretation turn in the same shared call pool.
            if pool:
                pool.reserves[owner] += 1
            try:
                run = await self.literature.investigate(
                    request, whole_units=inherited, single_submission=True
                )
            finally:
                if pool:
                    pool.reserves[owner] -= 1
            delegated.append(
                {
                    "research_id": run.research_id,
                    "sources": run.sources,
                    "limitations": run.limitations,
                }
            )
            answer = access.register_knowledge(run)
            added = {}
            if run.answer:
                ref = self.records.put(
                    "knowledge",
                    knowledge_artifact(answer, run.research_id, args.question, run.limitations),
                )
                added = self.records.result_blocks([ref])
                available.update(added)
            active = set(contract.task_context.active)
            contract.refresh(contract)
            contract.task_context.active = active
            if run.answer:
                contract.task_context.activate(answer.get("evidence_resolution", {}))
                contract.task_context.activate(added)
            return ToolResult(
                ok=bool(run.answer),
                content={
                    "result_refs": list(added),
                    "answer": answer,
                    "limitations": run.limitations,
                    "provenance": "delegated_material_not_expert_conclusion",
                },
                source_ids=sorted(
                    {
                        item["reference"]["source_id"]
                        for item in (answer or {}).get("evidence_resolution", {}).values()
                    }
                ),
                reading_scope="selected_originals_and_knowledge_answer",
                error=None if answer else run.task_result.reason,
            )

        if remaining:
            tools.add(
                Tool(
                    "research_knowledge",
                    "提出外部知识问题并选择已读申请原文；返回适用资料及其原文，供专业判断。",
                    LiteratureRequest,
                    investigate,
                    delegates=True,
                    exploration=True,
                    timeout_seconds=3600,
                )
            )
        # Explicitly supplied originals are read through the real access observer.
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
            access.record_context_read(payload)
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
            "围绕 members 定位的议题，依据申请原文理解实际方案并开展专业评审。"
            "问题文字是研究导航；申请原文描述申请人提出的目标、安排与主张。"
            "从原文辨认对象、处理、比较、观测与拟获得认识的关系，保留预研究、正式实验、"
            "观测和返回分析各自的角色。题干与原文有出入时，围绕实际方案继续分析。"
            "完整申请可回读，用于理解本题相关的跨项目关系。"
            "以本题涉及的知识贡献和观测解释为中心展开研究，再判断实现条件对这些认识的影响。"
            "利用已有材料给出具体分析，并把需要进一步研究的外部信息与已知条件区分。"
            "比较最近先例、方法适用性、指标含义与替代解释，说明支持范围、贡献和项目影响。"
            "申请的创新陈述属于自述；先例判断以实际搜读范围为限。数值保留来源和推导条件。"
            "正式提交是一份本专家最终认可的连贯回答：结合原文审议研究中的不同解释，"
            "明确保留的判断及其依据；仍成立的分歧以不确定性说明。"
            f"{prose_shape}是这份回答中的完整论证，段落间的判断与限定保持一致，"
            "数值的用途和局部条件就地说明；真正共同的范围写 scope_note。"
            "E 是材料位置，R 是带适用范围的已有论述；委派解释由本专家结合原文判断其意义。"
            "需要外部知识时，用 research_knowledge 提出当前具体疑问、用途、所需信息及对判断"
            "的意义，并选择相关申请原文 E。application_statements 是申请陈述的精确原文，"
            "表示申请写了什么。得到的原文可用 read_materials / browse_materials 复核。"
            "本题内完成当前可做的阅读、检索与分析，真正未决的信息和影响写 followups。"
            "没有取得有效文献回答时，依据已有原文形成有限分析，外部依赖保留为未知或条件。"
            "区分文献采用的方法与本项目需要满足的条件，建议强度以实际适用依据为准。"
            f"正文应可直接作为专业报告；{citation_style}"
            "比较性判断说明比较对象与依据；测量能力结合实际设备及操作条件理解。"
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
                product_material("professional-work", work),
                product_material("provided-originals", provided),
            ],
            reading=lambda: {
                "application": reader.coverage(),
                "literature": access.reading_records(),
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
