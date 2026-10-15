"""Write the concluded evaluation and evidence appendices without deciding scores."""

from spacebio_review.agents.context import TokenEstimator
from spacebio_review.agents.contracts import SubmissionContract
from spacebio_review.evidence.submissions import record

from .assessment import assessment_system, main_section, main_sections
from .assessment_input import review_materials, select_materials
from .publication_prose import cited_ids, prose_type
from .records import write_json

VERSION = "report-authorship-4"
PROMPT = """依据已经完成的各项评价及指定原始材料，写本次 units 指定的公开文字。
各项判断、作用范围和最终评价结论已经形成。本次只承担 units 说明的内容职责。
units 包含 overall 时，先解释六项评价中的主要贡献和限制如何形成当前综合判断，
然后在 summary 中压缩这些理由和既定结论。两段均解释已有评价，不重新评分，
不另设支持门槛或生成另一项支持决定。
专题附录展开相关论述的原始依据、实际比较、适用条件和保留的解释，写完整自然段。
研究材料的共同范围、未知和逐项 basis 随论述阅读；related_opinions 表示解释沿袭，
origin=delegated 是委派文献解释，公开论述的直接依据由相邻原文及其适用条件确定。
决策摘要依据主文简明说明贡献、主要限制和最终评价结论，保持确定性与否定的原有强度。
scoring 若存在，是程序已确定的总分、分数等级和安全否决状态；recommendation 是
最终结论。摘要准确使用这些结果，安全否决时不能把分数等级 A 写成最终获得支持。
只评价现有计划及证据，不提出补材料、补实验、改路线等整改建议或实施行动清单。
每个单元以 paragraphs 列自然段，每段 parts 的 text 表达完整论断和必要条件，
cites 选择直接支持相邻文字的 evidence 编号。用研究对象和具体内容指代论点。
程序负责标题、评分、引用编号、段间空行和目录；文字直接面向报告读者。
"""


def author_contract(keys, evidence):
    prose = prose_type(tuple(evidence))
    draft = record("AuthoredUnits", **{key: (prose, ...) for key in keys})
    return SubmissionContract(draft, draft, version=VERSION)


def writing_plan(assessment):
    topics = [f"topic_{i + 1}" for i in range(len(assessment["appendix_topics"]))]
    main = ["overall", "summary"] if assessment_system(assessment).score_grades else ["summary"]
    return [main, *[topics[i : i + 4] for i in range(0, len(topics), 4)]]


def author_materials(frozen, assessment, keys):
    units, opinions, sections = {}, [], []
    if "summary" in keys:
        if "overall" in keys:
            units["overall"] = {
                "role": "综合评价，约400-600字，整合六项主要贡献、限制及其对既定结论的影响。"
            }
        units["summary"] = {"role": "决策摘要，约250-350字，压缩贡献、限制与已确定的最终结论。"}
        sections.extend(main_sections(assessment_system(assessment)))
    else:
        for key in keys:
            topic = assessment["appendix_topics"][int(key.split("_")[1]) - 1]
            units[key] = {
                "role": "专题论证，约400-600字，解释具体证据、比较及适用范围。",
                "title": topic["title"],
                "focus": topic["focus"],
                "main_sections": topic["main_sections"],
            }
            opinions.extend(topic["opinion_ids"])
            sections.extend(topic["main_sections"])
    main = {key: main_section(assessment, key) for key in dict.fromkeys(sections)}
    materials = select_materials(
        frozen, opinions, evidence_ids=list(dict.fromkeys(cited_ids(main)))
    )
    result = {
        "evaluation_system": assessment_system(assessment).snapshot(),
        "main": main,
        "recommendation": assessment["recommendation"],
        "units": units,
        "materials": materials,
    }
    if "summary" in keys and assessment_system(assessment).score_grades:
        result["scoring"] = {
            "total_score": assessment["total_score"],
            "score_grade": assessment["score_grade"],
            "safety_veto": assessment["safety_veto"]["applies"],
        }
    return result


def author_request(caller, frozen, assessment, keys):
    materials = author_materials(frozen, assessment, keys)
    contract = author_contract(keys, materials["materials"]["evidence"])
    materials["materials"] = review_materials({"materials": materials["materials"]})
    tokens = caller.config.summary_tokens if "summary" in keys else caller.config.author_tokens
    return materials, contract, tokens


def sized_writing_plan(caller, frozen, assessment):
    def fit(keys):
        materials, contract, tokens = author_request(caller, frozen, assessment, keys)
        request = caller.request(PROMPT, materials, contract, tokens)
        # Size whole-topic prose as well as reserving the model's reasoning allowance.
        sample = {
            key: {"paragraphs": [{"parts": [{"text": "文" * 600, "cites": ["E1"]}]}]}
            for key in keys
        }
        output = TokenEstimator().measure([sample])["input_tokens"]
        return (
            caller.preview(request)["fits"]
            and output <= tokens - caller.config.model.reasoning_reserve
        )

    groups = []
    for keys in writing_plan(assessment):
        if "summary" in keys:
            if not fit(keys):
                raise ValueError("authored_unit_exceeds_capacity:" + "+".join(keys))
            groups.append(keys)
            continue
        batch = []
        for key in keys:
            if batch and not fit([*batch, key]):
                groups.append(batch)
                batch = []
            if not fit([key]):
                raise ValueError("authored_unit_exceeds_capacity:" + key)
            batch.append(key)
        if batch:
            groups.append(batch)
    return groups


async def author_report(caller, frozen_ref, assessment_ref):
    frozen, assessment = caller.records.value(frozen_ref), caller.records.value(assessment_ref)
    plan = sized_writing_plan(caller, frozen, assessment)
    written, refs = {}, []
    status_path = caller.records.root / "authorship.json"

    def checkpoint(error=None):
        missing = [key for keys in plan for key in keys if key not in written]
        write_json(
            status_path,
            {
                "assessment": assessment_ref,
                "frozen_review": frozen_ref,
                "plan": plan,
                "completed": list(written),
                "missing": missing,
                "status": "incomplete" if missing else "completed",
                "error": error,
            },
        )

    checkpoint()
    for keys in plan:
        materials, contract, tokens = author_request(caller, frozen, assessment, keys)
        try:
            value = await caller.submit(
                "writer/authorship/" + "+".join(keys),
                PROMPT,
                materials,
                contract,
                max_tokens=tokens,
            )
        except Exception as exc:
            checkpoint(str(exc))
            raise
        refs.append(caller.records.put("authored-unit", value, [assessment_ref, frozen_ref]))
        written.update(value)
        checkpoint()
    return caller.records.put(
        "authored-report",
        {
            "format_version": VERSION,
            "assessment": assessment_ref,
            "frozen_review": frozen_ref,
            "plan": plan,
            "sections": written,
        },
        [assessment_ref, frozen_ref, *refs],
    )
