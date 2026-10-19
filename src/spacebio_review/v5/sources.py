"""Single-request image reading used by the shared source tools."""

import base64
import hashlib
import json
from typing import Literal

from pydantic import BaseModel

from spacebio_review.agents.serialization import parse_output
from spacebio_review.evidence.reading_render import reading_groups, render_source
from spacebio_review.tools import ToolResult


class VisualReading(BaseModel):
    readability: Literal["readable", "partial", "unreadable"]
    interpretation: str


def _page_originals(doc, visual):
    materials = [item for item in doc.materials if visual.visual_id in item.visual_ids]
    if len(materials) != 1:
        return None, []
    material = materials[0]
    if visual.page is None:
        return material, []
    members = set(material.passage_ids)
    passages = [item for item in doc.passages if item.passage_id in members]
    wanted = [item.passage_id for item in passages if item.page == visual.page]
    selected = {
        passage_id
        for group, _ in reading_groups(passages, wanted, materials=[material])
        for passage_id in group
    }
    return material, [item for item in passages if item.passage_id in selected]


class OriginalVisualReader:
    """Return retained original pages directly to a scientific author's own model."""

    def __init__(self, store):
        self.store = store

    async def _image(self, doc, visual, references, material):
        pixels, mime = await self.store.visual_bytes(doc.source_id, visual)
        image_digest = hashlib.sha256(pixels).hexdigest()
        references.capture(
            doc.source_id, visual_id=visual.visual_id, text=visual.caption,
            locator=visual.locator, kind="original_visual", candidate=doc.candidate,
            image_digest=image_digest,
            reading_structure={"page": visual.page, "kind": "original_visual"},
            material=material.model_dump(include={"material_id", "title", "role", "url"})
            if material else None,
        )
        retained = self.store.retained(doc.source_id)
        original = next((v for v in retained.visuals if v.visual_id == visual.visual_id), visual)
        return {
            "source_id": doc.source_id, "visual_id": visual.visual_id,
            "locator": visual.locator, "original_image": original.model_dump(exclude_none=True),
            "image_digest": image_digest,
        }, f"data:{mime};base64," + base64.b64encode(pixels).decode()

    async def accompany(self, doc, result, references):
        """Attach original pages for positions actually returned by the shared reading window."""
        if not result.ok or not isinstance(result.content, dict):
            return result
        returned = set(result.content.get("returned_ids", []))
        if not returned:
            return result
        pages = {
            material.material_id: {
                passage.page for passage in doc.passages
                if passage.passage_id in returned and passage.passage_id in material.passage_ids
                and passage.page is not None
            }
            for material in doc.materials
        }
        images, urls = [], []
        for visual in doc.visuals:
            materials = [m for m in doc.materials if visual.visual_id in m.visual_ids]
            if (len(materials) != 1 or visual.page not in pages[materials[0].material_id]
                    or not (visual.path or visual.url)):
                continue
            try:
                image, url = await self._image(doc, visual, references, materials[0])
            except (ValueError, OSError):
                # Text remains readable when the retained page pixels are unavailable.
                continue
            images.append(image)
            urls.append(url)
        if images:
            result.content["original_visuals"] = images
            result.image_urls = urls
        return result

    async def from_readings(self, readings, references):
        """Load pages of incoming positioned originals, without rereading a whole source."""
        sources = {}
        for item in readings.values():
            ref = item["reference"]
            sources.setdefault(ref["source_id"], []).append(item)
        images, urls = [], []
        for source_id, snapshots in sources.items():
            doc = self.store.retained(source_id)
            if doc is None:
                continue
            requested = set()
            for item in snapshots:
                ref = item["reference"]
                if ref.get("visual_id"):
                    requested.add(ref["visual_id"])
                    continue
                structure = item.get("reading_structure", {})
                material_id = item.get("material", {}).get("material_id")
                for material in doc.materials:
                    if (material_id and material.material_id != material_id
                            or ref.get("passage_id") not in material.passage_ids):
                        continue
                    pages = {row.get("visual_id") for row in structure.get("original_pages", [])}
                    requested.update(
                        visual.visual_id for visual in doc.visuals
                        if visual.visual_id in material.visual_ids
                        and (visual.visual_id in pages or structure.get("page") is not None
                             and visual.page == structure["page"])
                    )
            for visual in doc.visuals:
                if visual.visual_id not in requested or not (visual.path or visual.url):
                    continue
                materials = [m for m in doc.materials if visual.visual_id in m.visual_ids]
                if len(materials) != 1:
                    continue
                try:
                    image, url = await self._image(doc, visual, references, materials[0])
                except (ValueError, OSError):
                    continue
                images.append(image)
                urls.append(url)
        return ToolResult(ok=True, content={"original_visuals": images}, image_urls=urls,
                          source_ids=list(sources), reading_scope="original_visual")

    async def __call__(self, doc, args, references, *, background=""):
        visual = next((v for v in doc.visuals if v.visual_id == args.visual_id), None)
        if visual is None:
            return ToolResult(ok=False, error="visual_not_found")
        material, passages = _page_originals(doc, visual)
        try:
            image, url = await self._image(doc, visual, references, material)
        except ValueError as exc:
            return ToolResult(ok=False, content={
                "source_id": args.source_id, "visual_id": args.visual_id,
                "locator": visual.locator, "caption": visual.caption,
            }, reading_scope="image_not_read", error=str(exc))
        references.capture_passages(args.source_id, passages, candidate=doc.candidate)
        return ToolResult(
            ok=True,
            content={
                **image, "original_visuals": [image],
                "originals_description": (
                    "同一材料中原页对应完整单元的自动解析原文；"
                    "辅助理解，行列归属与原页图像共同阅读。"
                ),
                "originals": [render_source(
                    args.source_id, passages, source=doc.candidate, materials=[material],
                )] if passages else [],
            },
            source_ids=[args.source_id],
            image_urls=[url],
            reading_scope="original_visual",
        )


class VisualReader:
    def __init__(self, model, store):
        self.model, self.store = model, store

    async def __call__(self, doc, args, references, *, background=""):
        visual = next((v for v in doc.visuals if v.visual_id == args.visual_id), None)
        if visual is None:
            return ToolResult(ok=False, error="visual_not_found")
        try:
            pixels, mime = await self.store.visual_bytes(args.source_id, visual)
        except ValueError as exc:
            return ToolResult(
                ok=False,
                content={
                    "source_id": args.source_id,
                    "visual_id": args.visual_id,
                    "locator": visual.locator,
                    "caption": visual.caption,
                },
                reading_scope="image_not_read",
                error=str(exc),
            )
        content = [{
            "type": "text",
            "text": (
                f"专题：{background}\n问题：{args.question}\n"
                f"来源：{doc.candidate.title}\n位置：{visual.locator}\n图注：{visual.caption}"
            ),
        }]
        material, page_passages = _page_originals(doc, visual)
        if page_passages:
            content.append({
                "type": "text",
                "text": (
                    "同一材料中原页对应完整单元的自动解析原文（辅助读本，行列归属结合原页图像理解）：\n"
                    + json.dumps(render_source(
                        args.source_id, page_passages, source=doc.candidate,
                        materials=[material],
                    ), ensure_ascii=False)
                ),
            })
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime};base64," + base64.b64encode(pixels).decode(),
            },
        })
        schema = VisualReading.model_json_schema()
        reply = await self.model.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "围绕问题为科学阅读作者整理原页读本，准确交付页面可见的对象、标识与关系。"
                        "手册、表格和设备图中，结合标题、表头、合并单元格与注释，说明参数对应的"
                        "设备、部件、适用对象、功能和操作阶段；按页面行列保留数值、单位及条件。"
                        "条目名称与其旁边所印的内容一起呈现，空白保持为该位置的空白。"
                        "原页印出的名称与内容看起来不相称时，具体描述各自位置及页面实际连接，"
                        "让阅读作者据此结合正文理解，读本保留每个条目的身份。"
                        "随附自动解析文字是辅助读本，可能存在行列错位；结合原页确定单元格归属，"
                        "保留原字符、空值与限定语所表达的信息。"
                        "实验图中，交代各 panel 的横轴条件、图例编码与纵轴读出；按图注解释标记，"
                        "每项比较写清两组的名称和条件。括号按端点落到具体组别，"
                        "非括号符号按图注确定比较规则；图上高度差与统计显著性分别说明。"
                        "以这些可见关系回答页面阅读问题；科学意义由阅读作者结合来源正文解释。"
                        "分辨不了的量值或关系如实保留。返回 JSON 对象，"
                        "readability 为 readable、partial 或 unreadable，"
                        "interpretation 为整理出的原页读本。"
                        "以下是输出对象的 schema，填写实例：" + json.dumps(schema)
                    ),
                },
                {
                    "role": "user",
                    "content": content,
                },
            ],
            response_schema=schema,
        )
        if reply.finish_reason != "stop":
            return ToolResult(ok=False, error="visual_response_" + reply.finish_reason)
        answer = VisualReading.model_validate(parse_output(reply.message.get("content") or ""))
        ok = answer.readability != "unreadable"
        if ok:
            references.capture(
                args.source_id,
                visual_id=args.visual_id,
                text=json.dumps(answer.model_dump(), ensure_ascii=False),
                locator=visual.locator,
                kind="interpreted_visual",
                candidate=doc.candidate,
                image_digest=hashlib.sha256(pixels).hexdigest(),
            )
        return ToolResult(
            ok=ok,
            content={
                "source_id": args.source_id,
                "visual_id": args.visual_id,
                "locator": visual.locator,
                "original_image": visual.model_dump(exclude_none=True),
                "interpretation": answer.model_dump(),
            },
            source_ids=[args.source_id],
            reading_scope="delegated_image_read" if ok else "image_not_read",
            error=None if ok else "image_unreadable",
        )
