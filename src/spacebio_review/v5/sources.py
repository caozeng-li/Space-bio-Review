"""Single-request image reading used by the shared source tools."""

import base64
import hashlib
import json
from typing import Literal

from pydantic import BaseModel

from spacebio_review.agents.serialization import parse_output
from spacebio_review.tools import ToolResult


class VisualReading(BaseModel):
    readability: Literal["readable", "partial", "unreadable"]
    interpretation: str


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
        schema = VisualReading.model_json_schema()
        reply = await self.model.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "解读依次写出与问题有关的图形布局、统计标记对应关系、科学解释。"
                        "先交代各 panel 的横轴条件、图例编码与纵轴读出；再按图注逐种解释标记，"
                        "每一项比较写清两组的名称和条件。括号按两个端点落到具体组别，"
                        "重复的非括号符号按图注确定比较规则。图上高度差与统计显著性分别说明；"
                        "没有提供某项比较的检验时，说明该项检验未提供。随后联系方法语境回答问题。"
                        "分辨不了的量值或关系如实保留。返回 JSON 对象，"
                        "readability 为 readable、partial 或 unreadable，"
                        "interpretation 为你的解读文字。"
                        "以下是输出对象的 schema，填写实例：" + json.dumps(schema)
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"专题：{background}\n问题：{args.question}\n"
                                f"论文：{doc.candidate.title}\n位置：{visual.locator}\n图注：{visual.caption}"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64," + base64.b64encode(pixels).decode(),
                            },
                        },
                    ],
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
                "interpretation": answer.model_dump(),
            },
            source_ids=[args.source_id],
            reading_scope="delegated_image_read" if ok else "image_not_read",
            error=None if ok else "image_unreadable",
        )
