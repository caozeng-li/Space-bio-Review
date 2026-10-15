"""One deliberation writes the assessment; code derives national scoring outcomes."""

from typing import Literal

from pydantic import Field

from spacebio_review.agents.contracts import SubmissionContract
from spacebio_review.evidence.submissions import record, selection
from spacebio_review.review.evaluation import GENERAL, EvaluationSystem, system_from_context

from .assessment_input import review_materials
from .publication_prose import prose_type

VERSION = "global-assessment-5"
GRADE = Literal["A", "B", "C", "D", "E"]
RECOMMENDATION = Literal[
    "support", "conditional_support", "revise", "not_support", "insufficient_basis"
]


def main_sections(system=GENERAL):
    return (
        "project",
        "scope",
        *system.aspects,
        *(() if system.id == "gbt42048_2022" else ("overall",)),
        "safety_veto" if system.id == "gbt42048_2022" else "support_scope",
        "unknowns",
    )


MAIN_SECTIONS = main_sections()

PROMPT = """你是全项目综合评审人，依据申请原文、已取得的外部资料和专业意见，
直接写出面向申请方与决策者的最终评审主文，按本轮评价体系形成判断。

先理解实际研究类型及目标：研究对象、处理或比较、实际观测、拟获得的认识。
project 说明这条知识路径，scope 说明本次实际审议范围和材料边界。
然后逐项完成 dimensions 的最终 body：围绕本项评价对象，解释贡献、关键依据、
实质限制及其影响，形成完整自然段。贡献由实际可得认识及其与已有工作的关系决定，
描述性研究同样可以有价值；“重大”“领先”等判断需要贡献证据，不能只看承诺措辞。

application 是完整申请各段；research 按研究议题组织完整 arguments、共同 scope 与未知，
origin=professional 表示专家判断，origin=delegated 表示委派文献解释。
professional_status=not_formed 表示相关专项专业意见尚未形成；这份文献认识仍可结合
原文使用，但不是已获专家认可的项目判断。仅有 scope/unknowns 的研究如实限定已知范围。
论述附近的 originals 保留实际读到的原文、来源标题及阅读范围，see 定位本输入中
已经展示的同一原文；available_originals 保留其余可用材料，sources 为来源信息。
basis 保留每项依据的解释、定量条件和对应原文。related_opinions 表示论述沿袭，当前
论述的直接原文引用保留在相邻 parts.cites；两种关系分别理解。申请事实以申请原文
为准；采用外部结论时同时考虑其对象、条件和观测目的。
research_coverage 表示各项实际获得的专项研究，未研究的部分仍可依据计划书评价，
但要如实说明依据范围。admission 是已经执行或跳过的内容审查状态，仅用于范围说明。
系统未完成的研究不等于项目材料缺陷，分别说明执行范围和申请本身未说明的事实。
围绕每条路线可取得的认识审议不同意见，保留可得产出和真正约束它的限制。
将已知事实、专家解释和仍待明确的前提分别理解，再决定论述的强度及适用范围。

各项正文形成后，unknowns 按科学主题说明仍会改变
理解的未知及其影响。交付的是当前计划的评价：不足、适用前提和支持范围作为判断
依据表达，不给申请方布置补材料、补实验、修改路线或实施里程碑等整改任务。

主文包含影响判断的具体依据与适用范围，详细方法比较留给值得展开的 appendix_topics。
每个专题用科学主题命名，focus 交代展开内容，opinion_ids 选择完整专业材料，
main_sections 选择该专题需遵循的最终主文章节。附录只展开评价依据和专业细节。

所有公开文字用中文，各正文以 paragraphs 列出自然段，每段以 parts 保留局部引用。
每个 text 用研究对象和实际内容表述完整论断与必要前提；cites 选择直接支持相邻
文字的 evidence 编号，申请材料承担申请事实，外部材料承担其实际说明的认识。
例如原文 E1 支持一句事实时，写 {"text":"该事实的完整表述。","cites":["E1"]}。
text 保留自然语言，材料编号仅放在 cites；按实际支持相邻陈述的原文段落选编号。
项目事实、评价推断和一般背景分别表达，说明事实支持解释的关系。
程序负责标题、评分汇总、段间空行、引用编号和索引。只提交接口要求的最终稿。
"""

GENERAL_JUDGMENT = """
通用体系：每项 body 之后给出 grade 与 confidence。
grade 为 A优秀/B良好/C中等/D较弱/E很弱；confidence 的 A-E 表示依据对该判断的
支持由强到弱，不是项目成功概率。依据不足以评定时相应项为 null，正文说明缺口。
support_scope 写清当前判断成立的目标、路线或阶段及事实前提，然后形成 recommendation。
overall 综合主导项目判断的理由。
conditional_support 表示评价成立的事实前提；revise 表示当前方案尚不足以支持。
这些结论说明当前方案的依据与范围，不另外生成如何改进的行动要求。
"""

GBT_JUDGMENT = """
本次任务使用 GB/T 42048—2022 表 B.2。按以下顺序完成每项评价：
1. 依据计划书和已有专业意见，说明本项目实际得到支持的贡献或实施能力，以及证据局限。
2. 对照本项分档的完整含义确定档位，再给出该档内整数 score。档内高低说明依据强弱。
   分档的各项判断同时成立才支持该档；缺少关键依据时，评价现有材料实际支持的较低水平。
   例如：仅能确认获取新数据的潜力、未建立国内外比较，当前可确认的创新程度一般，
   中档所需的“国际先进、国内领先”尚无依据。这是对当前可证实水平的判断，
   与断言项目实际上没有创新不同。领域重要性与本项目的前沿贡献也分别判断。
3. 六项全部给分。依据薄弱时仍作有范围的实质判断，不机械记零；每项正文集中解释
   分档与得分的决定性理由。总分、等级、支持结论由代码形成；本任务只交付逐项评价。

研究类型按实际产出理解：描述数据、机制解释和方法能力均按具体增量评价；
研究基础仅计已完成工作，地面预实验或空间实验均可构成基础，深度与相关性决定强弱。
工程及安全评价分清已有载荷能力与本实验的适配、样品、密封、操作和处置依据。
使用已有载荷本身不能证明本实验安全；未说明的危险源、防护效果仍属未知。

safety_veto 按第 7.2 d 独立给出 applies 及 reason：对于影响航天员安全、平台安全的项目
可一票否决。依据具体事实及影响路径作出判断；标准并未将否决限定为必然发生的
致命或灾难性事故。没有具体否决依据时 applies=false，
这只说明本次证据不足以否决，与安全单项是否达到基本合理可行分别判断。

所有正文交付当前状态的评价。局限写成“缺少密封试验记录使防护效果的依据有限”，
而非给申请方安排验证工作；unknowns 和否决理由同样只写未知事实及其当前影响。
scope 根据实际覆盖和 admission 状态说明范围：计划书内容审查限表 A.1 的 b、c，
不含征集主题、行政手续、资格或十二份配套国标的逐条符合性核查。
"""


def assessment_system(assessment):
    snapshot = assessment.get("evaluation_system")
    return EvaluationSystem.model_validate(snapshot) if snapshot else GENERAL


def assessment_contract(frozen):
    system = system_from_context(frozen["evaluation"])
    national = system.id == "gbt42048_2022"
    materials = frozen["materials"]
    prose = prose_type(tuple(materials["evidence"]))
    ids = (
        (list[selection(tuple(materials["opinions"]))], ...)
        if materials["opinions"]
        else (list[str], Field(max_length=0))
    )
    dimension_fields = {}
    for criterion in system.criteria:
        fields = (
            {
                "score": (
                    int,
                    Field(
                        ge=0,
                        le=criterion.max_score,
                        strict=True,
                        description="；".join(
                            f"{b.minimum}—{b.maximum}分：{b.description}" for b in criterion.bands
                        )
                        + "。依据正文已建立的完整判断选档，再给档内整数分。",
                    ),
                )
            }
            if national
            else {"grade": (GRADE | None, ...), "confidence": (GRADE | None, ...)}
        )
        dimension = record(
            criterion.id + "Assessment",
            body=(
                prose,
                Field(
                    description=criterion.label
                    + "：依据、完整分档判断及档内得分理由；只评价当前计划。"
                )
                if national
                else ...,
            ),
            **fields,
        )
        dimension_fields[criterion.id] = (dimension, ...)
    dimensions = record("SixDimensions", **dimension_fields)
    topic = record(
        "AppendixTopic",
        title=(str, Field(min_length=1)),
        focus=(str, ...),
        main_sections=(list[selection(main_sections(system))], Field(min_length=1)),
        opinion_ids=ids,
    )
    unknown = record("ScientificUnknown", title=(str, ...), text=(prose, ...))
    judgment = (
        {
            "safety_veto": (
                record("SafetyVeto", applies=(bool, ...), reason=(prose, ...)),
                ...,
            )
        }
        if national
        else {
            "overall": (prose, ...),
            "support_scope": (prose, ...),
            "recommendation": (RECOMMENDATION, ...),
        }
    )
    draft = record(
        "GlobalAssessment",
        project=(prose, ...),
        scope=(prose, ...),
        dimensions=(dimensions, ...),
        **judgment,
        unknowns=(list[unknown], ...),
        appendix_topics=(list[topic], ...),
    )
    return SubmissionContract(draft, draft, version=VERSION)


def assessment_request(frozen):
    system = system_from_context(frozen["evaluation"])
    judgment = GBT_JUDGMENT if system.id == "gbt42048_2022" else GENERAL_JUDGMENT
    return frozen["evaluation"]["prompt"] + "\n" + PROMPT + judgment, review_materials(frozen)


def main_section(assessment, key):
    return assessment["dimensions"][key] if key in assessment["dimensions"] else assessment[key]


def finalize_assessment(value, system):
    result = {"format_version": VERSION, "evaluation_system": system.snapshot(), **value}
    if system.id == "gbt42048_2022":
        total = sum(value["dimensions"][key]["score"] for key in system.aspects)
        grade = system.grade_for(total)
        result.update(
            total_score=total,
            score_grade=grade.id,
            recommendation="not_support"
            if value["safety_veto"]["applies"]
            else grade.recommendation,
        )
    return result


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
        "global-assessment",
        finalize_assessment(value, system_from_context(frozen["evaluation"])),
        [frozen_ref],
    )
