"""Resumable publication work: only public text enters summary and translation calls."""

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.agents.contracts import SubmissionContract
from spacebio_review.agents.diagnostics import digest
from spacebio_review.agents.types import TaskResult
from spacebio_review.context import product_material
from spacebio_review.evidence.submissions import record

from .publication import build_publication
from .publication_document import (
    PublicationDocument,
    Section,
    parse_text,
    summary_input,
    text_slots,
)
from .publication_render import Renderer
from .records import write_json

PATHS = {"zh": "report.md", "en": "report.en.md", "appendix": "appendix.md"}
SUMMARY_OBJECTIVE = (
    "根据给定公开决策内容写简短中文科学判断摘要，保留决定性条件、替代方案和科学未知。"
    "程序另行固定展示整体建议标签和实际覆盖状态；text 不写建议标签、已评/未评方面、"
    "评估完成情况或执行过程，不重复这些由程序负责的内容。"
    "不重新评审，不新增事实、条件或研究请求。实际覆盖仅供理解判断的适用边界，"
    "不能把未评当成申请缺少材料，不能自行认为未评不影响判断。不生成来源引用或内部编号。"
)
TRANSLATION_OBJECTIVE = (
    "将同一小节的有序文字单元翻译为英文，保留条件、否定、数字、判断强度及各单元语义归属。"
    "units 每个键只返回原单元的译文，不合并或移转内容。保持同段语境，title 返回英文标题。"
    "不生成引用、序号、Markdown 排版或链接，程序在原位置装配。"
    "原始数值和单位倍率必须保持：只翻译单位名称，不换算数量级、不拼接数字位数。"
    "例如金额以万元计时可译为 ten-thousand yuan，0 万元仍是 0 ten-thousand yuan，"
    "不能拼成 00,000 yuan。单元边缘标点由程序保留，译文不另补句首或句末标点。"
)
EDIT_OBJECTIVE = (
    "对指定旧稿做一次面向读者的局部出版编辑，将内部称谓和任务指令改为公开表达。"
    "保留科学条件、否定、数字、未知、判断强度以及各单元的原意，不做科学评审或补充事实。"
    "units 每个键只返回该单元编辑结果，不能合并或移动内容。"
    "引用由程序固定，不增删来源，不生成引用标记；无法区分科学限定和执行说明时保留原文。"
)


class TextDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)


def load_document(records, manifest):
    """Old manifests are read adapters, never another new-publication writing path."""
    if "document" in manifest:
        return PublicationDocument.model_validate(manifest["document"])
    frozen = records.value(manifest["delivery_input"]) if manifest.get("delivery_input") else None
    return build_publication(records, manifest["assessment"], frozen)[0]


def text_batches(section, limit):
    def sized_blocks(block):
        temporary = Section(section_id=section.section_id, title=section.title, blocks=[block])
        values, _ = text_slots(temporary)
        length = sum(len(v) for k, v in values.items() if k != "title")
        if length > limit and block.kind in {
            "block",
            "scope",
            "bullet_list",
            "ordered_list",
            "list_item",
            "blockquote",
        }:
            for child in block.children:
                yield from sized_blocks(child)
        else:
            yield block, length

    groups, group, size = [], [], 0
    for block, length in (pair for root in section.blocks for pair in sized_blocks(root)):
        if group and size + length > limit:
            groups.append(group)
            group, size = [], 0
        group.append(block)
        size += length
    if group:
        groups.append(group)
    return groups


EDGE_PUNCTUATION = "。，；：！？.,;:!?"


def translation_fragment(text):
    """Sentence-edge punctuation belongs to layout, like the citation between two runs."""
    leading = re.match(r"[\s。，；：！？.,;:!?]*", text)[0]
    if leading.endswith(".") and text[len(leading) : len(leading) + 1].isdigit():
        leading = leading[:-1]
    rest = text[len(leading) :]
    trailing = re.search(r"[\s。，；：！？.,;:!?]*$", rest)[0]
    core = rest[: len(rest) - len(trailing)] if trailing else rest
    table = str.maketrans("。，；：！？", ".,;:!?")
    prefix, suffix = leading.translate(table), trailing.translate(table)
    if prefix and prefix[-1] in EDGE_PUNCTUATION:
        prefix += " "
    return core, prefix, suffix


class Delivery:
    def __init__(self, execution, output):
        self.ex, self.records, self.output = execution, execution.records, Path(output)

    def assemble(self, assessment_ref, *, delivery_input=None):
        frozen = self.records.value(delivery_input) if delivery_input else None
        document, audit = build_publication(self.records, assessment_ref, frozen)
        manifest = {
            "format_version": document.format_version,
            "assessment": assessment_ref,
            "delivery_input": delivery_input,
            "document": document.model_dump(),
            "audit": audit,
        }
        return self.records.put(
            "manifest", manifest, [assessment_ref] + ([delivery_input] if delivery_input else [])
        )

    async def summarize(self, manifest_ref):
        document = load_document(self.records, self.records.value(manifest_ref))
        data = summary_input(document)
        return await self.ex.call(
            "writer/public-summary/" + digest(data),
            "writer",
            SUMMARY_OBJECTIVE,
            SubmissionContract(TextDraft, TextDraft, version="publication-summary-2"),
            materials=[product_material("public-decision", data)],
        )

    async def transform(self, section, *, edit=False):
        section = section.model_copy(deep=True)
        original_title = section.title
        for blocks in text_batches(section, self.ex.reporting.material_batch_chars):
            chunk = Section(section_id="chunk", title=original_title, blocks=blocks)
            slots, nodes = text_slots(chunk)
            units, targets, edges = {}, {}, {}
            for i, (key, value) in enumerate(slots.items()):
                if key == "title":
                    continue
                local = f"U{i}"
                if edit:
                    body = value.strip()
                    prefix = value[: len(value) - len(value.lstrip())]
                    suffix = value[len(value.rstrip()) :]
                else:
                    body, prefix, suffix = translation_fragment(value)
                if body:
                    units[local], targets[local], edges[local] = body, nodes[key], (prefix, suffix)
                else:
                    nodes[key].text = prefix + suffix
            if not units:
                continue
            fields = record("PublicationTextUnits", **{key: (str, ...) for key in units})
            draft = record("PublicationTextDraft", title=(str, ...), units=(fields, ...))
            data = {"title": original_title, "units": units}
            mode = "edit" if edit else "en"
            result = await self.ex.call(
                "writer/public-" + mode + "/" + digest(data),
                "writer",
                EDIT_OBJECTIVE if edit else TRANSLATION_OBJECTIVE,
                SubmissionContract(draft, draft, version="publication-" + mode + "-2"),
                materials=[product_material("public-text", data)],
            )
            if result.status != "completed" or not result.product:
                return result
            for key, node in targets.items():
                prefix, suffix = edges[key]
                body = result.product["units"][key].strip()
                if not edit:
                    body = translation_fragment(body)[0].strip()
                node.text = prefix + body + suffix
            section.title = result.product["title"]
        if not edit and section.title_en:
            section.title = section.title_en
        if not edit:
            section.title_en = section.title
        return TaskResult(
            task_id="writer/public-text/" + digest(section.model_dump()),
            agent_id="writer",
            status="completed",
            reason="public_text_transformed",
            product={"section": section.model_dump()},
        )

    async def translate(self, section):
        return await self.transform(
            Section.model_validate(section) if isinstance(section, dict) else section
        )

    async def edit(self, section):
        return await self.transform(
            Section.model_validate(section) if isinstance(section, dict) else section, edit=True
        )

    def receipt(self, state, document):
        missing = [s.section_id for s in document.main if s.section_id not in state["translations"]]
        if "summary" not in state["translations"]:
            missing.insert(0, "summary")
        return {
            "format_version": document.format_version,
            "status": state["status"],
            "documents": [
                {
                    "role": role,
                    "language": "en" if role == "en" else "zh",
                    "path": path,
                    "status": state[role]["status"],
                    "missing_sections": missing
                    if role == "en"
                    else ["summary"]
                    if role == "zh" and not state.get("summary")
                    else [],
                }
                for role, path in PATHS.items()
            ],
            "missing_sections": missing,
        }

    def export(self, state, document):
        self.output.mkdir(parents=True, exist_ok=True)
        receipt = self.receipt(state, document)
        state["publication"] = receipt
        state["paths"] = {item["role"]: item["path"] for item in receipt["documents"]}
        translated = [
            Section.model_validate(state["translations"][s.section_id])
            for s in document.main
            if s.section_id in state["translations"]
        ]
        titles = {s.section_id: s.title_en or s.title for s in document.main}
        zh_missing = [] if state.get("summary") else ["决策摘要尚未完成。"]
        en_missing = [
            "Translation unavailable: " + titles.get(key, "Decision summary")
            for key in receipt["missing_sections"]
        ]
        english_summary = state["translations"].get("summary")
        for role in PATHS:
            renderer = Renderer(
                document, "en" if role == "en" else "zh", available_main=set(state["translations"])
            )
            summary = (
                renderer.blocks(Section.model_validate(english_summary).blocks, role)
                if role == "en" and english_summary
                else state.get("summary", "")
                if role == "zh"
                else ""
            )
            text = renderer.render(
                role,
                translated if role == "en" else None,
                summary=summary,
                missing=en_missing if role == "en" else zh_missing if role == "zh" else [],
            )
            path = self.output / PATHS[role]
            temp = path.with_suffix(".tmp")
            temp.write_text(text, "utf-8")
            temp.replace(path)
        write_json(self.output / "delivery.json", state)

    async def run(self, manifest_ref, *, selected_sections=None):
        document = load_document(self.records, self.records.value(manifest_ref))
        fingerprint = digest([document.model_dump(), getattr(self.ex, "configuration", "")])
        state = {
            "manifest": manifest_ref,
            "status": "partial",
            "zh": {"status": "body_ready"},
            "en": {"status": "not_run"},
            "appendix": {"status": "completed"},
            "translations": {},
            "technical_limits": [],
            "work_fingerprint": fingerprint,
        }
        saved = self.output / "delivery.json"
        if saved.exists():
            previous = json.loads(saved.read_text("utf-8"))
            if previous.get("work_fingerprint") == fingerprint:
                state["translations"] = previous.get("translations", {})
                if previous.get("summary"):
                    state["summary"] = previous["summary"]
        self.export(state, document)
        summary = await self.summarize(manifest_ref)
        if summary.status == "completed" and summary.product:
            state["summary"] = summary.product["text"]
            state["zh"]["status"] = "completed"
        else:
            state["technical_limits"].append({"stage": "summary", "reason": summary.reason})
        sections = [
            s
            for s in document.main
            if selected_sections is None or s.section_id in selected_sections
        ]
        if state.get("summary"):
            sections.insert(
                0,
                Section(
                    section_id="summary",
                    title="决策摘要",
                    title_en="Decision summary",
                    blocks=parse_text(state["summary"]),
                ),
            )
        state["en"]["status"] = "partial"
        self.export(state, document)
        for section in sections:
            result = await self.translate(section)
            if result.status == "completed" and result.product:
                state["translations"][section.section_id] = result.product["section"]
            else:
                state["technical_limits"].append(
                    {"stage": section.section_id, "reason": result.reason}
                )
            self.export(state, document)
        if state.get("summary") and all(s.section_id in state["translations"] for s in sections):
            state["en"]["status"] = (
                "completed" if selected_sections is None else "selected_completed"
            )
        if all(state[key]["status"] == "completed" for key in PATHS):
            state["status"] = "completed"
        self.export(state, document)
        return self.records.put("delivery", state, [manifest_ref])
