"""One globally ordered question list; selection and dispatch contain no semantic planning."""

from copy import deepcopy

from pydantic import Field

from spacebio_review.context import product_material
from spacebio_review.evidence.submissions import record, selection
from spacebio_review.review.evaluation import GENERAL
from spacebio_review.tools import ToolRegistry

from .contracts import choices_field, grounded_contract

QUESTION_VERSION = "questions-10"


def question_aspects(system):
    return {item.id: f"{item.label}：{item.description}" for item in system.criteria}


QUESTION_ASPECTS = question_aspects(GENERAL)

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

GBT_QUESTION_OBJECTIVE = """根据完整计划书、国标评价准则和已激活专家能力，生成供专家研究的排序题单。

对象为基于已有载荷实施的实验项目。先辨认项目是现象描述、机制研究、方法验证等哪类研究，
以及实际要达到的目标，再理解实验活动、比较与观测、预期认识和效益，
区分已有载荷能力、本项目拟完成的工作、已完成的前期实验和计划开展的实验。
预研究、地面模拟、在轨实验、回收与返回分析沿用原文的目的、条件和时序。

根据 evaluation-criteria 中六项指标的判断要求，找出影响评价结论、值得专业分析的核心疑问。
围绕实际可得的认识、数据或方法，开放地询问相对已有工作增加了什么、意义有多大、局限在哪里。
描述性结果和机制解释分别按其实际贡献评价；国标中的重大、领先等要求由贡献证据判断，
不能以未提出机制假设或未承诺重大成果为不足前提，也不因承诺宏大而推定价值较高。
研究基础关注既有工作实际支持了什么，成果效益关注预期产出的意义和可获得性；
工程实施与安全问题围绕计划书中的实际载荷、资源、接口、操作及风险安排展开。
题目数量取决于可区分的核心疑问，无需每项指标各设一题，也不按分值分配题数。
每题以一个对象或过程为中心，保留连贯分析所需的条件和相关指标，各题讨论中心可区分。

按对项目评价结论的影响从高到低全局排序，综合考虑科学目标、创新、工程可实现性、
安全、研究基础和成果效益。直接关系航天员或平台安全、实验能否开展的关键疑问可以优先；
具体排序以项目事实为依据。已明确的安排作为提问前提，未明确的信息用于分析判断限制。
问题的回答者是评审专家：请其依据原文和研究分析现有方案，而不是要求申请人设计保证措施。
例如“现有密封和操作安排支持怎样的安全判断，依据适用于哪些条件？”、
“计划产出相对已有知识增加了什么，其意义与可获得性有何依据？”、
“既有预实验验证了哪些环节，又有哪些推断尚无依据？”。
这些问题以当前方案的评价为目的，保留有据的优势与限制。本任务只生成问题，不评分。

question 写简洁、可独立理解的疑问句，保留必要对象、实验阶段和条件。
数值、设备配置、样本安排及确定程度均对应原文，创新表述按申请主张理解。
expert_id 选择最匹配的一位已激活专家；aspects 从 question-aspects 选择实际涉及的标签，
每个适用标签写一次，一个问题可涉及多个指标。evidence_handles 选择题目前提对应的原文位置。
"""


def expert_view(experts):
    return {
        eid: {k: row[k] for k in ("purpose", "profile_labels", "method_skill_ids") if k in row}
        for eid, row in experts.items()
    }


def question_contract(experts, *, system=GENERAL, evidence=None, scope=None):
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
            aspects=(
                list[selection(system.aspects)],
                Field(min_length=1) if system.id == "gbt42048_2022" else ...,
            ),
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
        self.system = self.ex.system
        self.objective = (
            GBT_QUESTION_OBJECTIVE if self.system.id == "gbt42048_2022" else QUESTION_OBJECTIVE
        )
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
        materials = [
            product_material("active-experts", expert_view(self.package["experts"])),
            product_material("question-aspects", question_aspects(self.system)),
        ]
        if self.system.id == "gbt42048_2022":
            materials.append(
                product_material(
                    "evaluation-criteria", {"prompt": self.system.prompt(self.system.aspects)}
                )
            )
        return [*materials, product_material("application", self.sources)]

    def contract(self, _=None):
        return question_contract(
            self.package["experts"], system=self.system, scope=self.reader.references
        )

    def preview(self):
        return self.ex.preview(
            "prepare/questions",
            "question_generation",
            self.objective,
            self.contract,
            materials=self.materials(),
        )

    async def run(self):
        if not self.preview()["fits"]:
            raise ValueError("question_context_exceeds_capacity")
        return await self.ex.required(
            "prepare/questions",
            "question_generation",
            self.objective,
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
