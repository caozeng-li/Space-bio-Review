"""One globally ordered question list; selection and dispatch contain no semantic planning."""

from copy import deepcopy

from pydantic import Field

from spacebio_review.context import product_material
from spacebio_review.evidence.submissions import record, selection
from spacebio_review.review.project import REVIEW_ASPECTS, ReviewAspect
from spacebio_review.tools import ToolRegistry

from .contracts import choices_field, grounded_contract

QUESTION_VERSION = "questions-9"

QUESTION_ASPECTS = dict(
    zip(
        REVIEW_ASPECTS,
        (
            "科学价值：问题及预期认识的价值。",
            "创新性：相对已有研究的新意。",
            "研究严谨性：设计、测量、分析与推断的关系。",
            "实施可行性：实施条件与技术可实现性。",
            "安全风险：人员、样本、环境与操作安全。",
            "项目管理：分工、资源、进度与关键依赖。",
        ),
        strict=True,
    )
)

QUESTION_OBJECTIVE = """根据完整计划书原文和已激活专家的学科与方法能力，生成供专家评审的问题列表。

先明确计划书要增加什么认识、相对已有研究的推进在哪里，再理解每项主要目标中
研究对象、处理或比较、实际观测与拟获得认识的联系。
预研究、模拟、设备调试、正式对照、正式观测及返回分析沿用原文各自的目的、地点、
时序和比较对象。围绕知识价值、指标的测量含义、比较支持的解释强度和相应实现条件，
找出值得开展专业研究的核心疑问。

每题以一个具体对象或过程为中心，细度以能够开展一次连贯专业分析为准；同一核心
疑问的必要指标和条件合在一题，各题讨论中心可区分。题单覆盖激活专家能力范围内的
主要研究目标，题数由计划书内容与实际可区分的核心疑问决定。

题目给定的数值、设备配置、样本安排与确定程度均对应原文，创新性陈述按申请主张
理解。已明确安排作为提问前提，尚待明确的安排用开放询问表达。
例如，某计划结合现场观测与受控实验研究一个机制，问题可以分别围绕
“所提出机制相对最接近的已有解释增加了什么可研究的认识？”、
“现场关联与受控实验的干预结果能共同区分哪些机制解释？”、
“采样与测量安排如何支持计划中的关键比较？”展开。
这几个问题分别以知识贡献、推断关系和必要实现条件为中心，具体内容取决于原计划。
question 写一个简洁、可独立理解的疑问句，保留必要对象、实验阶段和条件。

questions 按对项目的重要性从高到低全局排序，优先考虑核心知识目标、关键结论的
可研究性，再考虑相应实现条件。expert_id 选择最匹配的一位已激活专家；aspects 从
question-aspects 中选择题目实际涉及的归类标签，每个适用标签写一次。标签用于索引，
题目仍由计划书的核心疑问与专家能力决定。evidence_handles 选择题目前提对应的计划书原文位置。
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
        "questions", build, evidence=evidence, scope=scope, compact_sources=True, source_only=True
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
            product_material("question-aspects", QUESTION_ASPECTS),
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
