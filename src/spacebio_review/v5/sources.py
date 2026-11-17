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
            doc.source_id,
            visual_id=visual.visual_id,
            text=visual.caption,
            locator=visual.locator,
            kind="original_visual",
            candidate=doc.candidate,
            image_digest=image_digest,
            reading_structure={"page": visual.page, "kind": "original_visual"},
            material=material.model_dump(include={"material_id", "title", "role", "url"})
            if material
            else None,
        )
        retained = self.store.retained(doc.source_id)
        original = next((v for v in retained.visuals if v.visual_id == visual.visual_id), visual)
        return {
            "source_id": doc.source_id,
            "visual_id": visual.visual_id,
            "locator": visual.locator,
            "original_image": original.model_dump(exclude_none=True),
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
                passage.page
                for passage in doc.passages
                if passage.passage_id in returned
                and passage.passage_id in material.passage_ids
                and passage.page is not None
            }
            for material in doc.materials
        }
        images, urls = [], []
        for visual in doc.visuals:
            materials = [m for m in doc.materials if visual.visual_id in m.visual_ids]
            if (
                len(materials) != 1
                or visual.page not in pages[materials[0].material_id]
                or not (visual.path or visual.url)
            ):
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
                    if (
                        material_id
                        and material.material_id != material_id
                        or ref.get("passage_id") not in material.passage_ids
                    ):
                        continue
                    pages = {row.get("visual_id") for row in structure.get("original_pages", [])}
                    requested.update(
                        visual.visual_id
                        for visual in doc.visuals
                        if visual.visual_id in material.visual_ids
                        and (
                            visual.visual_id in pages
                            or structure.get("page") is not None
                            and visual.page == structure["page"]
                        )
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
        return ToolResult(
            ok=True,
            content={"original_visuals": images},
            image_urls=urls,
            source_ids=list(sources),
            reading_scope="original_visual",
        )

    async def __call__(self, doc, args, references, *, background=""):
        visual = next((v for v in doc.visuals if v.visual_id == args.visual_id), None)
        if visual is None:
            return ToolResult(ok=False, error="visual_not_found")
        material, passages = _page_originals(doc, visual)
        try:
            image, url = await self._image(doc, visual, references, material)
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
        references.capture_passages(args.source_id, passages, candidate=doc.candidate)
        return ToolResult(
            ok=True,
            content={
                **image,
                "original_visuals": [image],
                "originals_description": (
                    "Auto-parsed text of complete units corresponding to the original page "
                    "within the same material; aiding understanding, row and column "
                    "attribution should be read together with the original page image."
                ),
                "originals": [
                    render_source(
                        args.source_id,
                        passages,
                        source=doc.candidate,
                        materials=[material],
                    )
                ]
                if passages
                else [],
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
        content = [
            {
                "type": "text",
                "text": (
                    f"Topic: {background}\nQuestion: {args.question}\n"
                    f"Source: {doc.candidate.title}\nLocation: {visual.locator}\n"
                    f"Caption: {visual.caption}"
                ),
            }
        ]
        material, page_passages = _page_originals(doc, visual)
        if page_passages:
            content.append(
                {
                    "type": "text",
                    "text": (
                        (
                            "Auto-parsed text of complete units corresponding to the original page "
                            "within the same material (auxiliary reader, understand row and column "
                            "attribution in conjunction with the original page image):\n"
                        )
                        + json.dumps(
                            render_source(
                                args.source_id,
                                page_passages,
                                source=doc.candidate,
                                materials=[material],
                            ),
                            ensure_ascii=False,
                        )
                    ),
                }
            )
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64," + base64.b64encode(pixels).decode(),
                },
            }
        )
        schema = VisualReading.model_json_schema()
        reply = await self.model.complete(
            [
                {
                    "role": "system",
                    "content": (
                        (
                            "Organize an original page reader for the scientific reading author"
                            " around the question, accurately delivering objects, identifiers, "
                            "and relationships visible on the page. In manuals, tables, and "
                            "equipment diagrams, combined with titles, headers, merged cells, "
                            "and notes, explain the equipment, component, applicable object, "
                            "function, and operational phase corresponding to parameters; "
                            "retain values, units, and conditions according to page rows and "
                            "columns. Present entry names together with the content printed "
                            "beside them, preserving blanks as blanks in that position. When "
                            "the name printed on the original page appears incongruent with the"
                            " content, specifically describe their respective positions and the"
                            " actual connections on the page, allowing the reading author to "
                            "understand in conjunction with the main text; the reader preserves"
                            " the identity of each entry. The accompanied auto-parsed text is "
                            "an auxiliary reader; there may be row-column misalignments; "
                            "determine cell attribution in conjunction with the original page, "
                            "preserving the information conveyed by original characters, null "
                            "values, and qualifiers. In experimental figures, explain the "
                            "horizontal axis conditions, legend encodings, and vertical axis "
                            "readouts for each panel; interpret markers according to the figure"
                            " caption, clearly stating the names and conditions of the two "
                            "groups for each comparison. Brackets correspond to specific groups"
                            " based on endpoints, and for non-bracket symbols, determine the "
                            "comparison rule according to the figure caption; describe height "
                            "differences and statistical significance separately. Answer page "
                            "reading questions with these visible relationships; scientific "
                            "meaning is interpreted by the reading author in conjunction with "
                            "the source main text. Faithfully retain any quantities or "
                            "relationships that cannot be resolved. Return a JSON object with "
                            "readability being readable, partial, or unreadable, and "
                            "interpretation being the organized original page reader. The "
                            "following is the output object schema; fill in an instance:"
                        )
                        + json.dumps(schema)
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
