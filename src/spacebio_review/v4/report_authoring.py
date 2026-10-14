"""Only the summary and evidence appendices remain separate writing tasks."""

from spacebio_review.agents.context import TokenEstimator
from spacebio_review.agents.contracts import SubmissionContract
from spacebio_review.evidence.submissions import record

from .assessment import MAIN_SECTIONS, main_section
from .assessment_input import review_materials, select_materials
from .publication_prose import cited_ids, prose_type
from .records import write_json

VERSION = "report-authorship-2"
PROMPT = """依据已经完成的评审主文及指定原始材料，写本次 units 指定的公开文字。
主文的判断、条件、作用范围和建议已经形成。本次只承担 units 说明的内容职责。
专题附录展开相关论述的原始依据、实际比较、适用条件和保留的解释，写完整自然段。
研究材料的共同范围、未知和逐项 basis 随论述阅读；related_opinions 表示解释沿袭，
origin=delegated 是委派文献解释，公开论述的直接依据由相邻原文及其适用条件确定。
决策摘要依据主文简明说明贡献、主要限制和支持范围，保持确定性与否定的原有强度。
每个单元以 paragraphs 列自然段，每段 parts 的 text 表达完整论断和必要条件，
cites 选择直接支持相邻文字的 evidence 编号。用研究对象和具体内容指代论点。
程序负责标题、评分、条件分组、引用编号、段间空行和目录；文字直接面向报告读者。
"""


def author_contract(keys, evidence):
    prose = prose_type(tuple(evidence))
    draft = record("AuthoredUnits", **{key: (prose, ...) for key in keys})
    return SubmissionContract(draft, draft, version=VERSION)


def writing_plan(assessment):
    topics = [f"topic_{i + 1}" for i in range(len(assessment["appendix_topics"]))]
    return [["summary"], *[topics[i : i + 4] for i in range(0, len(topics), 4)]]


def author_materials(frozen, assessment, keys):
    units, opinions, sections = {}, [], []
    if keys == ["summary"]:
        units["summary"] = {"role": "决策摘要，约250-350字，压缩最终主文的贡献、限制与支持范围。"}
        sections.extend(MAIN_SECTIONS)
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
    return {
        "main": main,
        "recommendation": assessment["recommendation"],
        "units": units,
        "materials": materials,
    }


def author_request(caller, frozen, assessment, keys):
    materials = author_materials(frozen, assessment, keys)
    contract = author_contract(keys, materials["materials"]["evidence"])
    materials["materials"] = review_materials({"materials": materials["materials"]})
    tokens = caller.config.summary_tokens if keys == ["summary"] else caller.config.author_tokens
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
