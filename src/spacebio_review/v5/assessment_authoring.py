"""Short assessment mappings: retain author prose and extract only explicit decisions."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, create_model

from .authoring import prose_from_ids, text_of

GRADE = Literal["A", "B", "C", "D", "E"]
RECOMMENDATION = Literal[
    "support", "conditional_support", "revise", "not_support", "insufficient_basis"
]

INSTRUCTION = """将主审的评价原稿定位为结构映射，分值和判断只提取作者明示的决定。
provided_fields 列出本稿明确提供的顶层部分。固定结构中其余字段用空列表或 null 占位，
并在 missing_fields 指出缺项。dimensions 中每个指标须有完整论述及分数，或通用体系
的等级与置信表达；原稿未写出的指标设 null 并报告缺项，不从其他稿件补齐。
project、scope、各指标 paragraph_ids、安全否决 paragraph_ids、support_scope
均只选择科学论述段落，不包含章节标题、指标名称、评分行或其他结构提示。
评分行的整数只提取为 score，禁止根据论述重新评分或将上限误作实得分。
安全否决 applies 只提取作者明示的适用与否，未知不能变成 false；对应 paragraph_ids
指向否决判断的理由。通用体系 grade/confidence 的明确 null 或无法判定是合法值，
没有谈及该决定则是缺项，须在 missing_fields 说明；recommendation 只提取明示建议。
provided_fields 表示原稿是否表达了对应内容，不要求原稿出现同名章节或 JSON 字段名。
例如安全性指标正文中明确写了不予否决和理由，就已提供 safety_veto，应列入
provided_fields 并映射相应原文；不能因为没有独立 safety_veto 标题而报告缺项。
unknowns 的每项 title_paragraph_id 指向作者已有的独立标题，paragraph_ids 指向说明。
作者未给独立标题时 title_paragraph_id 为 null，由代码提供中性展示标题，这不是内容缺项。
列表形式的未知事项保留原文所在段落，无需为了拆成多项而改写或遗漏列表内容。
明确“未知事项：无”时，provided_fields 包含 unknowns 且 unknowns=[]；若作者没写
未知事项，不把它当空列表。科学稿由已保存的选编记录直接装配，不映射为主审正文。
"""

MANUSCRIPT_INSTRUCTION = """overall 选择作者已写成的完整综合科学判断段落。
summary 从本次项目理解或综合判断中选择少量可独立阅读的核心判语，保留原稿顺序；
可与 project、overall 使用同一个段落。摘要选择不生成新文字、不截取句子、不缩写。
核心判语须在原稿中已经说明项目对象、可获得的认识、主导限制和适用范围；不能仅用
评分行、标题、空泛结语或依赖前文才能理解的零散句子充当摘要。没有可独立阅读的
核心判语时在 missing_fields 明确指出未找到的内容。无需另有“摘要”章节。
overall 与 summary 均列入 provided_fields，保留对象、测量阶段、条件和未知。
"""


def parsing_instruction(*, selection_from_manuscript=False):
    selection = (
        "\n本次尚未选文。main_sections、appendix_sections 只提取主审原稿明确选中的"
        "候选短标识（如 S1、S2），严格保留作者安排的顺序。主文至少一篇；明确无附录"
        "时 appendix_sections=[]。未表达选择则记录 missing_fields，不自动推荐或全选。"
        if selection_from_manuscript else ""
    )
    return INSTRUCTION + "\n" + MANUSCRIPT_INSTRUCTION + selection


def record(name, **fields):
    return create_model(name, __config__=ConfigDict(extra="forbid"), **fields)


def assessment_mapping_type(system, *, selection_from_manuscript=False):
    dimensions = {}
    for criterion in system.criteria:
        fields = {"paragraph_ids": (list[str], ...)}
        if system.score_grades:
            score = Annotated[int, Field(ge=0, le=criterion.max_score, strict=True)]
            fields["score"] = (score | None, ...)
        else:
            fields.update(grade=(GRADE | None, ...), confidence=(GRADE | None, ...))
        dimensions[criterion.id] = (
            record(criterion.id + "Mapping", **fields) | None,
            ...,
        )
    unknown = record(
        "UnknownMapping", title_paragraph_id=(str | None, ...), paragraph_ids=(list[str], ...)
    )
    fields = {
        "project": (list[str], ...),
        "scope": (list[str], ...),
        "dimensions": (record("DimensionMappings", **dimensions), ...),
        "unknowns": (list[unknown], ...),
        "overall": (list[str], ...),
        "summary": (list[str], ...),
    }
    if system.score_grades:
        fields["safety_veto"] = (
            record("VetoMapping", applies=(bool | None, ...), paragraph_ids=(list[str], ...))
            | None,
            ...,
        )
    else:
        fields.update(support_scope=(list[str], ...), recommendation=(RECOMMENDATION | None, ...))
    selection_fields = {
        "main_sections": (list[str], ...), "appendix_sections": (list[str], ...),
    } if selection_from_manuscript else {}
    return record(
        "AssessmentManuscriptMapping",
        provided_fields=(list[Literal[tuple(fields)]], ...),
        **fields,
        **selection_fields,
        missing_fields=(list[str], ...),
    )


def assessment_values(raw, mapping):
    """Copy selected original paragraphs; no model-authored body is regenerated here."""
    data = mapping.model_dump(mode="json")
    values = {}
    for key in mapping.provided_fields:
        value = data[key]
        if key in {"project", "scope", "support_scope", "overall", "summary"}:
            values[key] = prose_from_ids(raw, value)
        elif key == "dimensions":
            values[key] = {
                criterion: {"body": prose_from_ids(raw, item["paragraph_ids"])}
                | {name: scalar for name, scalar in item.items() if name != "paragraph_ids"}
                for criterion, item in value.items()
                if item is not None
            }
        elif key == "unknowns":
            values[key] = [
                {
                    "title": re.sub(
                        r"^ {0,3}#{1,6}[ \t]+", "", text_of(raw, item["title_paragraph_id"])
                    )
                    if item["title_paragraph_id"] is not None
                    else "未确定事项",
                    "text": prose_from_ids(raw, item["paragraph_ids"]),
                }
                for item in value
            ]
        elif key == "safety_veto":
            values[key] = (
                {
                    "applies": value["applies"],
                    "reason": prose_from_ids(raw, value["paragraph_ids"]),
                }
                if value is not None
                else None
            )
        else:
            values[key] = value
    return values
