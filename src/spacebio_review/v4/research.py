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

PROFESSIONAL_INPUT_VERSION = "professional-input-5"


class LiteratureRequest(BaseModel):
    previous_research_id: str | None = Field(
        default=None, description="继续本题此前返回的 research_id；首次研究留空。"
    )
    question: str = Field(min_length=1)
    purpose: KnowledgePurpose
    needed_information: list[str] = Field(min_length=1)
    changes_judgment_if: str = Field(min_length=1)
    evidence_handles: list[str] = Field(
        default_factory=list,
        description="首次选择已读申请原文 E 引用；续问沿用此前原文，可补充新的相关申请原文。",
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
        national = self.ex.system.id == "gbt42048_2022"
        if national:
            aspects = {aspect for member in members.values() for aspect in member["aspects"]}
            work["evaluation_criteria"] = self.ex.system.prompt(aspects)
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
            whole_units=True,
            runtime=self.ex.runtime,
        )
        tools = access.tools()
        available = self.records.result_blocks(
            sorted(set(related) | set(artifact_dependencies(work)))
        )
        delegated = []
        parent_input = self.records.state["tasks"].get(task["task_id"], {}).get("input")

        async def investigate(args):
            previous_question = None
            if args.previous_research_id:
                previous = self.records.knowledge_refs(
                    parent_task_id=task["task_id"], research_id=args.previous_research_id
                )
                if not previous:
                    return ToolResult(ok=False, error="research_not_in_current_professional_task")
                previous_question = self.records.value(previous[0])["delegation"]["question"]
            request = literature_question(
                args,
                question,
                access.source_scope.evidence_choices(),
                previous_question=previous_question,
            )
            scheduler = self.literature.runtime.scheduler
            pool = scheduler.research_pool.get()
            if (
                pool
                and pool.remaining - sum(pool.reserves.values())
                < self.literature.minimum_turns(single_submission=True) + 1
            ):
                return ToolResult(ok=False, error="shared_research_turns_reserved_for_completion")
            owner = scheduler.research_owner.get()
            # Keep a parent interpretation turn in the same shared call pool.
            if pool:
                pool.reserves[owner] += 1
            try:
                run = await self.literature.investigate(
                    request, whole_units=True, single_submission=True
                )
            finally:
                if pool:
                    pool.reserves[owner] -= 1
            ref = None
            if run.answer and run.task_result.status == "completed":
                artifact = knowledge_artifact(
                    run.answer_view(), run.research_id, args.question, run.limitations
                )
                artifact["delegation"] = {
                    "parent_task_id": task["task_id"],
                    "parent_input": parent_input,
                    "question_ids": list(members),
                    "research_id": run.research_id,
                    "study_id": run.study_id or run.research_id,
                    "previous_research_id": run.previous_research_id,
                    "question": request.model_dump(mode="json"),
                }
                ref = self.records.put("knowledge", artifact)
            delegated.append(
                {
                    "research_id": run.research_id,
                    "study_id": run.study_id or run.research_id,
                    "previous_research_id": run.previous_research_id,
                    "artifact": ref,
                    "status": run.task_result.status,
                    "reason": run.task_result.reason,
                    "sources": run.sources,
                    "limitations": run.limitations,
                }
            )
            answer = access.register_knowledge(run)
            added = {}
            if ref:
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
                    "research_id": run.research_id,
                    "study_id": run.study_id or run.research_id,
                    "previous_research_id": run.previous_research_id,
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

        if self.ex.config.literature_enabled:
            tools.add(
                Tool(
                    "research_knowledge",
                    "研究外部知识并返回原文；可用此前 research_id 续问，沿用已读资料与申请语境。",
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
            rows = reading_passages(doc, whole_units=True)
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
        focus = (
            "按 professional-work.evaluation_criteria 中本题涉及的指标形成评价依据。"
            "先确认计划书实际的研究类型与目标，围绕可得认识、数据或方法与已有工作的关系，"
            "分析新增贡献、重要程度及局限。描述性结果和机制解释各按实际贡献评价；"
            "国标中的重大、领先等要求由贡献证据判断，不由研究类型或承诺的宏大程度决定。"
            "结合本题分别判断科学意义与创新、方法及已有载荷实施条件、安全可靠性、"
            "已完成的研究基础或预期成果效益；论述深度由本题实际职责决定。"
            "研究基础区分已完成的空间/地面实验与计划工作，成果效益说明产出的意义与可获得性。"
            "安全判断说明受影响的航天员、平台或载荷、具体风险与已有安排，不把材料缺失直接当作危险事实。"
            "按现有材料形成判断，明确未说明事项对证据强度和判断的影响。"
            if national
            else "以本题涉及的知识贡献和观测解释为中心展开研究，再判断实现条件对这些认识的影响。"
        )
        comparisons = (
            "按本题需要比较先例、方法适用性、工程条件或风险依据，说明支持范围和项目影响。"
            if national
            else "比较最近先例、方法适用性、指标含义与替代解释，说明支持范围、贡献和项目影响。"
        )
        unresolved = (
            "followups 只说明未决信息及对判断的限制，information_route 说明有关证据的类型或来源。"
            "正文和未决信息不生成整改、改进方案或行动清单；不独立给指标分数，"
            "安全问题及其依据提交综合评价统一判断。"
            if national
            else "区分文献采用的方法与本项目需要满足的事实前提，评价现有方案的贡献、"
            "局限及其影响，不向申请方布置整改或改进任务。"
        )
        literature_guidance = (
            "需要外部知识时，用 research_knowledge 提出当前具体疑问、用途、所需信息及对判断"
            "的意义，并选择相关申请原文 E。application_statements 是申请陈述的精确原文，"
            "表示申请写了什么。已得认识留下具体新疑问时，用 previous_research_id 续问；"
            "沿用此前申请原文，必要时补入新原文，继续消耗本题现有预算。"
            "委派回答包含外部对象、条件、认识和原文，结合实际项目解释其意义；"
            "执行未完成表示本次研究范围受限，不是申请本身的缺陷。"
            if self.ex.config.literature_enabled
            else "本次依据已提供材料形成专业意见，外部知识尚未获得的部分如实保留为未知。"
        )
        result = await self.ex.call(
            key,
            "specialist",
            "围绕 members 定位的议题，依据申请原文理解实际方案并开展专业评审。"
            "问题文字是研究导航；申请原文描述申请人提出的目标、安排与主张。"
            "从原文辨认对象、处理、比较、观测与拟获得认识的关系，保留预研究、正式实验、"
            "观测和返回分析各自的角色。题干与原文有出入时，围绕实际方案继续分析。"
            "完整申请可回读，用于理解本题相关的跨项目关系。"
            + focus
            + "利用已有材料给出具体分析，并把需要进一步研究的外部信息与已知条件区分。"
            + comparisons
            + "申请的创新陈述属于自述；先例判断以实际搜读范围为限。数值保留来源和推导条件。"
            "正式提交是一份本专家最终认可的连贯回答：结合原文审议研究中的不同解释，"
            "明确保留的判断及其依据；仍成立的分歧以不确定性说明。"
            f"{prose_shape}是这份回答中的完整论证，段落间的判断与限定保持一致，"
            "数值的用途和局部条件就地说明；真正共同的范围写 scope_note。"
            "E 是材料位置，R 是带适用范围的已有论述；委派解释由本专家结合原文判断其意义。"
            + literature_guidance
            + "得到的原文可用 read_materials / browse_materials 复核。"
            "本题内完成当前可做的阅读、检索与分析，真正未决的信息和影响写 followups。"
            "没有取得有效文献回答时，依据已有原文形成有限分析，外部依赖保留为未知或条件。"
            + unresolved
            + f"正文应可直接作为专业报告；{citation_style}"
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
