"""A final scientific manuscript followed by one whole-project decision."""

from copy import deepcopy
from typing import Literal

from pydantic import Field

from spacebio_review.agents.contracts import SubmissionContract
from spacebio_review.evidence.submissions import record, selection
from spacebio_review.review.project import REVIEW_ASPECTS

from .assessment import GRADE, RECOMMENDATION
from .assessment_input import review_materials
from .material_view import reading_view
from .publication_prose import cited_ids, prose_type

SCIENTIFIC_VERSION = "scientific-review-1"
DECISION_VERSION = "review-decision-1"
RESULT_VERSION = "review-result-1"

SCIENTIFIC_PROMPT = """你是统一科学主审，依据完整申请、外部原文与已有专业意见，
直接写出面向申请方和决策者的最终科学评审正文及专题论证。

project 说明研究对象、处理或比较、实际观测、拟获得的认识之间的知识路径；
scope 说明本次实际审议范围与材料边界。dimensions 按共同评价标准的六个维度
形成最终科学正文：解释有价值的贡献、支持解释的实际比较和读出、已有研究的
具体认识、实质限制，以及在这些限制下仍然成立的结论和研究价值。

application 保留完整申请原文。research 按任务呈现专业论述、共同范围、未知，
以及实际沿用的文献解释；originals 保留实际读到的相邻原文及阅读范围，see 指向
此前已经完整给出的同一原文。available_originals 保留其余已读材料。sources 为
来源信息。related_opinions 表达论述沿袭，parts.cites 表达直接原文依据。
专业意见与委派文献解释都是本次需要依据原文审议的解释。按申请中的实际实验
角色理解各条路线，并在完整科学论述中处理相反解释、数量的用途和条件的范围。
采用外部认识时，将其研究对象、处理、读出与当前论证需要放在一起比较。

topics 是直接交付的最终专题正文。选择值得展开的科学主题，具体比较证据、
方法、可得认识与适用范围；主文保持完整，专题承担有实质内容的论证展开。
title 用科学内容命名，body 是完整自然段。确无必要专题时 topics 可为空。

这一步承担科学解释与论证：科学局限、实施依据、可选择的研究路径都写入
对应正文。评分、支持范围与支持条件由随后阅读本次全文的全局决策环节形成。
正文使用中文，以 paragraphs 组织完整自然段，parts 保留局部引用。每个 text
表达完整论断及其必要条件，cites 选择直接支持相邻文字的 evidence 编号。
申请材料承担申请事实，外部材料承担其实际说明的认识。程序负责章节标题、
段落身份、引用编号和索引；提交接口要求的最终科学稿。
"""

DECISION_PROMPT = """你是全项目决策评审人，依据完整申请、已经完成的全部科学
评审正文、正文实际引用的原文和共同评价标准，形成最终全局判断与支持取舍。

scientific 是将直接交付的完整科学稿，包含项目理解、范围、六维正文及专题。
其中每个自然段的 paragraph_id 由程序赋予。application 是完整申请，
available_originals 是科学稿实际引用的其他原文，sources 保留来源信息。
围绕项目可取得的认识与贡献，综合各项依据、限制及其影响范围，确定支持程度。

grades 按六维正文及评价标准给出 grade 与 confidence。grade 为 A优秀/B良好/
C中等/D较弱/E很弱；confidence 的 A-E 表示依据对该判断的支持由强到弱，
不是项目成功概率。依据不足以评定时相应项为 null。summary 是可直接交付的
决策摘要；overall 说明主导整体判断的理由；support_scope 写清可以支持的
研究目标、路线或阶段。科学正文由第一步原样交付，本次提交这些全局决策内容。

conditions 的每项 text 写明完整事项、科学理由、影响哪种比较或产出、何时
明确，以及未满足时支持范围怎样变化。before_support 表示形成支持决定前
必须明确的条件；implementation_milestone 表示实施相应阶段的交付；
recommendation 表示一般改进。based_on 选择实际支撑本项处置的科学段落身份。
这项关联用于内部定位，text 的文献依据仍由相邻 cites 独立选择。

alternatives 讨论相应替代产出；unknowns 按科学主题说明仍会改变理解的未知；
recommendation 汇总为整体建议。对于尚未确定的参数，依据现有论证表达待确定
事项及其实际影响；支持条件的程度和时点与科学依据一致。

所有公开文字用中文，以 paragraphs 和 parts 写完整自然段。text 用科学对象、
实际内容和适用范围表达；cites 选择直接支持相邻文字的原文编号。程序负责评分
展示、条件分组、编号与索引。提交接口要求的最终决策稿。
"""


def scientific_contract(frozen):
    prose = prose_type(tuple(frozen["materials"]["evidence"]))
    dimensions = record("ScientificDimensions", **{key: (prose, ...) for key in REVIEW_ASPECTS})
    topic = record("ScientificTopic", title=(str, Field(min_length=1)), body=(prose, ...))
    draft = record(
        "ScientificReview",
        project=(prose, ...),
        scope=(prose, ...),
        dimensions=(dimensions, ...),
        topics=(list[topic], ...),
    )
    return SubmissionContract(draft, draft, version=SCIENTIFIC_VERSION)


def scientific_request(frozen):
    return SCIENTIFIC_PROMPT + "\n" + frozen["evaluation"]["prompt"], review_materials(frozen)


def scientific_paragraphs(scientific):
    """Assign stable local identities without altering the publishable scientific prose."""
    result = deepcopy(scientific)

    def identify(prose, key):
        for i, paragraph in enumerate(prose["paragraphs"], 1):
            paragraph["paragraph_id"] = f"science.{key}.p{i}"

    for key in ("project", "scope"):
        identify(result[key], key)
    for key in REVIEW_ASPECTS:
        identify(result["dimensions"][key], key)
    for i, topic in enumerate(result["topics"], 1):
        identify(topic["body"], f"topic{i}")
    return result


def paragraph_ids(scientific):
    indexed = scientific_paragraphs(scientific)
    prose = [indexed["project"], indexed["scope"], *indexed["dimensions"].values()]
    prose.extend(topic["body"] for topic in indexed["topics"])
    return tuple(row["paragraph_id"] for value in prose for row in value["paragraphs"])


def decision_evidence(frozen, scientific):
    materials = frozen["materials"]
    selected = dict.fromkeys([*materials["application"], *cited_ids(scientific)])
    return {key: materials["evidence"][key] for key in selected}


def decision_contract(frozen, scientific):
    prose = prose_type(tuple(decision_evidence(frozen, scientific)))
    grade = record("DimensionGrade", grade=(GRADE | None, ...), confidence=(GRADE | None, ...))
    grades = record("DecisionGrades", **{key: (grade, ...) for key in REVIEW_ASPECTS})
    condition = record(
        "ScientificSupportCondition",
        timing=(Literal["before_support", "implementation_milestone", "recommendation"], ...),
        text=(prose, ...),
        based_on=(list[selection(paragraph_ids(scientific))], ...),
    )
    unknown = record("DecisionUnknown", title=(str, ...), text=(prose, ...))
    draft = record(
        "ReviewDecision",
        grades=(grades, ...),
        summary=(prose, ...),
        overall=(prose, ...),
        support_scope=(prose, ...),
        conditions=(list[condition], ...),
        alternatives=(prose, ...),
        unknowns=(list[unknown], ...),
        recommendation=(RECOMMENDATION, ...),
    )
    return SubmissionContract(draft, draft, version=DECISION_VERSION)


def decision_request(frozen, scientific):
    materials = frozen["materials"]
    evidence = decision_evidence(frozen, scientific)
    sources = {row["source"]: materials["sources"][row["source"]] for row in evidence.values()}
    view = reading_view(evidence, sources, {}, application=materials["application"])
    view.pop("research")
    view["scientific"] = scientific_paragraphs(scientific)
    view["material_limits"] = deepcopy(materials.get("material_limits", []))
    return DECISION_PROMPT + "\n" + frozen["evaluation"]["prompt"], view


async def synthesize_scientific(caller, frozen_ref):
    """Return a complete bound result only after both durable submissions succeed."""
    frozen = caller.records.value(frozen_ref)
    prompt, materials = scientific_request(frozen)
    scientific = await caller.submit(
        "assessment/scientific/" + frozen_ref,
        prompt,
        materials,
        scientific_contract(frozen),
        max_tokens=caller.config.scientific_tokens,
    )
    scientific_ref = caller.records.put(
        "scientific-review", {"format_version": SCIENTIFIC_VERSION, **scientific}, [frozen_ref]
    )
    prompt, materials = decision_request(frozen, scientific)
    decision = await caller.submit(
        "assessment/decision/" + scientific_ref,
        prompt,
        materials,
        decision_contract(frozen, scientific),
        max_tokens=caller.config.decision_tokens,
    )
    decision_ref = caller.records.put(
        "review-decision",
        {"format_version": DECISION_VERSION, **decision},
        [frozen_ref, scientific_ref],
    )
    return caller.records.put(
        "review-result",
        {
            "format_version": RESULT_VERSION,
            "status": "completed",
            "scientific": scientific_ref,
            "decision": decision_ref,
            "frozen_review": frozen_ref,
        },
        [frozen_ref, scientific_ref, decision_ref],
    )
