"""A single deliberation produces the final main text and its decisions."""

from typing import Literal

from pydantic import Field

from spacebio_review.agents.contracts import SubmissionContract
from spacebio_review.evidence.submissions import record, selection
from spacebio_review.review.project import REVIEW_ASPECTS

from .assessment_input import review_materials
from .publication_prose import prose_type

VERSION = "global-assessment-2"
GRADE = Literal["A", "B", "C", "D", "E"]
RECOMMENDATION = Literal[
    "support", "conditional_support", "revise", "not_support", "insufficient_basis"
]
MAIN_SECTIONS = (
    "project",
    "scope",
    *REVIEW_ASPECTS,
    "overall",
    "support_scope",
    "conditions",
    "alternatives",
    "unknowns",
)

PROMPT = """你是全项目综合评审人，依据申请原文、已取得的外部资料和专业意见，
直接写出面向申请方与决策者的最终评审主文，并形成同一份主文的等级和整体建议。

先按申请的研究目标理解各条路线：研究对象、处理或比较、实际观测、拟获得的认识。
project 说明这条知识路径，scope 说明本次实际审议范围和材料边界。
然后逐维完成 dimensions 的最终 body：围绕本维度的评价对象，解释贡献、关键依据、
实质限制及影响范围，形成完整自然段，再给出 grade 与 confidence。
grade 为 A优秀/B良好/C中等/D较弱/E很弱；confidence 的 A-E 表示依据对该判断的
支持由强到弱，不是项目成功概率。依据不足以评定时相应项为 null，正文说明缺口。

application 是完整申请各段；research 按研究议题组织完整 arguments、共同 scope 与未知，
origin=professional 表示专家判断，origin=delegated 表示委派文献解释。
论述附近的 originals 保留实际读到的原文、来源标题及阅读范围，see 定位本输入中
已经展示的同一原文；available_originals 保留其余可用材料，sources 为来源信息。
basis 保留每项依据的解释、定量条件和对应原文。related_opinions 表示论述沿袭，当前
论述的直接原文引用保留在相邻 parts.cites；两种关系分别理解。申请事实以申请原文
为准；采用外部结论时同时考虑其对象、条件和观测目的。
围绕每条路线可取得的认识审议不同意见，保留有价值的可得产出和真正约束它的限制。
将已知事实、专家解释和仍待明确的前提分别理解，再决定论述的强度及适用范围。

六维正文形成后，overall 综合主导项目判断的理由，support_scope 写清可支持的
目标、路线或阶段。conditions 的每一项直接写可交付的完整条件和理由：
before_support 是形成支持决定前必须明确的条件；implementation_milestone 是
实施相应阶段的交付；recommendation 是一般改进。条件交代影响哪种比较或产出、
何时明确，以及未满足时的范围变化。alternatives 讨论相应替代产出；unknowns
按科学主题说明仍会改变理解的未知。最后根据以上论述形成 recommendation。

主文包含影响判断的具体依据与适用范围，详细方法比较留给值得展开的 appendix_topics。
每个专题用科学主题命名，focus 交代展开内容，opinion_ids 选择完整专业材料，
main_sections 选择该专题需遵循的最终主文章节。附录只展开这些论述的证据和细节。

所有公开文字用中文，各正文以 paragraphs 列出自然段，每段以 parts 保留局部引用。
每个 text 用研究对象和实际内容表述完整论断与必要条件；cites 选择直接支持相邻
文字的 evidence 编号，申请材料承担申请事实，外部材料承担其实际说明的认识。
程序负责标题、评分、条件分组、段间空行、引用编号和索引。只提交接口要求的最终稿。
"""


def assessment_contract(frozen):
    materials = frozen["materials"]
    prose = prose_type(tuple(materials["evidence"]))
    ids = (
        (list[selection(tuple(materials["opinions"]))], ...)
        if materials["opinions"]
        else (list[str], Field(max_length=0))
    )
    dimension = record(
        "DimensionAssessment",
        body=(prose, ...),
        grade=(GRADE | None, ...),
        confidence=(GRADE | None, ...),
    )
    dimensions = record("SixDimensions", **{key: (dimension, ...) for key in REVIEW_ASPECTS})
    condition = record(
        "SupportCondition",
        timing=(Literal["before_support", "implementation_milestone", "recommendation"], ...),
        text=(prose, ...),
    )
    topic = record(
        "AppendixTopic",
        title=(str, Field(min_length=1)),
        focus=(str, ...),
        main_sections=(list[selection(MAIN_SECTIONS)], Field(min_length=1)),
        opinion_ids=ids,
    )
    unknown = record("ScientificUnknown", title=(str, ...), text=(prose, ...))
    draft = record(
        "GlobalAssessment",
        project=(prose, ...),
        scope=(prose, ...),
        dimensions=(dimensions, ...),
        overall=(prose, ...),
        support_scope=(prose, ...),
        conditions=(list[condition], ...),
        alternatives=(prose, ...),
        unknowns=(list[unknown], ...),
        recommendation=(RECOMMENDATION, ...),
        appendix_topics=(list[topic], ...),
    )
    return SubmissionContract(draft, draft, version=VERSION)


def assessment_request(frozen):
    return PROMPT + "\n" + frozen["evaluation"]["prompt"], review_materials(frozen)


def main_section(assessment, key):
    return assessment["dimensions"][key] if key in REVIEW_ASPECTS else assessment[key]


async def synthesize(caller, frozen_ref):
    frozen = caller.records.value(frozen_ref)
    prompt, materials = assessment_request(frozen)
    value = await caller.submit(
        "assessment/global/" + frozen_ref,
        prompt,
        materials,
        assessment_contract(frozen),
        max_tokens=caller.config.assessment_tokens,
    )
    return caller.records.put(
        "global-assessment", {"format_version": VERSION, **value}, [frozen_ref]
    )
