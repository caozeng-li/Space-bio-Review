"""One globally ordered question list; selection and dispatch contain no semantic planning."""

from copy import deepcopy

from pydantic import Field

from spacebio_review.context import product_material
from spacebio_review.evidence.submissions import record, selection
from spacebio_review.review.project import ReviewAspect
from spacebio_review.tools import ToolRegistry

from .contracts import choices_field, grounded_contract

QUESTION_VERSION = "questions-5"

QUESTION_OBJECTIVE = """根据完整计划书原文和已激活专家的学科与方法能力，生成供专家评审的问题列表。
每项的正文仅为 question：一个明确、开放、可独立理解的评审问题，以疑问句表达。
必要的研究对象、实验阶段和条件直接写在问题中，保持简洁。

粒度：每题围绕一个对项目评价有实质影响的核心疑问，具体到可开展连贯专业分析的
对象或过程。同一核心疑问的指标与必要条件合在一题；各题的核心疑问应可区分。
问题集合以全项目为视角，覆盖激活专家能力范围内的主要研究目标及其关键依据：
研究希望获得的知识或能力、实验比较与解释、测量观测与目标现象的对应关系、实现条件。
沿每项主要研究目标关注“研究对象—实验处理或比较—实际测量—拟解释的现象”的联系，
将决定该目标能否得到有效研究的核心疑问纳入题单。测量的含义与有效性和设备运行
均应结合项目实际目标考虑。问题的数量由计划书内容和实际可区分的核心疑问决定。

依据：题目中的给定事实取自计划书原文，数值、设备配置、样本安排与原文对应。
保留“拟、预计、可能”等表述的原有确定程度，申请人对创新性的陈述按申请主张理解。
原文尚待明确的内容以开放问题表达。每个实验环节按原文中的目的、地点、时序和
比较对象理解：预研究用于探索或准备方案，设备调试用于确认运行条件，正式对照用于
相应实验比较，正式观测用于取得目标数据；具体角色以本申请的安排为准。
分别保留地面实验、地面模拟、在轨实验、返回后分析各自承担的研究任务，
围绕它们与研究目标的实际关系提问。

questions 按对项目的重要性从高到低全局排序，优先考虑核心知识价值、关键结论的
可研究性和实施条件。每题 expert_id 选择最匹配的一位已激活专家；aspects 选择
该问题对应的综合索引标签；evidence_handles 选择题目前提对应的计划书原文位置。
"""


def expert_view(experts):
    return {
        eid: {k: row[k] for k in ("purpose", "profile_labels", "method_skill_ids") if k in row}
        for eid, row in experts.items()
    }


def question_contract(experts, *, evidence=None, scope=None):
    def build(ev, _):
        question = record(
            "ReviewQuestion",
            expert_id=(selection(experts), ...),
            question=(
                str,
                Field(
                    min_length=1,
                    description="一个开放的评审问题，含必要对象、实验阶段与条件，以疑问句表达。",
                ),
            ),
            aspects=(list[ReviewAspect], ...),
            evidence_handles=choices_field(ev),
        )
        return record("QuestionBatch", questions=(list[question], ...))

    contract = grounded_contract(
        "questions", build, evidence=evidence, scope=scope, compact_sources=True
    )
    contract.version = QUESTION_VERSION
    contract.native_prompt = (
        "按接口给定的 JSON Schema 提交按重要性排序的问题列表。"
        "evidence_handles 从 task_sources 中选择对应原文的编号。"
    )
    return contract


class QuestionGeneration:
    def __init__(self, preparation, package):
        self.ex, self.package = preparation.ex, package
        self.reader = preparation.reader()
        self.sources = []
        for sid, doc in self.reader.docs.items():
            if doc is None:
                raise ValueError("question_source_unavailable")
            payload = {
                "source_id": sid,
                "passages": [p.model_dump(mode="json") for p in doc.passages],
            }
            self.reader.record_context_read(payload)
            self.sources.append({**payload, "reading_gaps": doc.reading_gaps})

    def materials(self):
        return [
            product_material("active-experts", expert_view(self.package["experts"])),
            product_material("application", self.sources),
        ]

    def contract(self, _=None):
        return question_contract(self.package["experts"], scope=self.reader.references)

    def preview(self):
        return self.ex.preview(
            "prepare/questions",
            "question_generation",
            QUESTION_OBJECTIVE,
            self.contract,
            materials=self.materials(),
        )

    async def run(self):
        if not self.preview()["fits"]:
            raise ValueError("question_context_exceeds_capacity")
        return await self.ex.required(
            "prepare/questions",
            "question_generation",
            QUESTION_OBJECTIVE,
            self.contract(),
            materials=self.materials(),
            tools=ToolRegistry(),
            read_actions=0,
            reading=lambda: {"application": self.reader.coverage()},
        )


def select_questions(batch, limit):
    if limit < 1:
        raise ValueError("max_questions_must_be_positive")
    return {
        f"q{rank}": {"proposal": deepcopy(row), "source_rank": rank}
        for rank, row in enumerate(batch["questions"][:limit], 1)
    }


def research_task(qid, question):
    proposal = question["proposal"]
    return {
        "task_id": f"research/{qid}/{proposal['expert_id']}/r0",
        "expert_id": proposal["expert_id"],
        "members": {qid: deepcopy(proposal)},
        "analysis_scope": proposal["question"],
        "external_needs": [],
        "retained_unknowns": [],
    }
