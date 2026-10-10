"""Task-local navigation over explicitly supplied application sources."""

import base64
import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from spacebio_review.agents import AgentTask, ContextView, Material, RoleProfile
from spacebio_review.agents.context import selected_passages
from spacebio_review.agents.reading import ReadingWindow
from spacebio_review.agents.types import MaterialPassage
from spacebio_review.reference_contract import SourceScope
from spacebio_review.tools import Tool, ToolRegistry, ToolResult


class VisualRequest(BaseModel):
    source_id: str
    visual_id: str
    question: str = Field(min_length=1)


class VisualUnderstanding(BaseModel):
    readability: Literal["readable", "partial", "unreadable"]
    interpretation: str
    limitations: list[str] = Field(default_factory=list)


class ApplicationReader:
    def __init__(self, bundle, store, runtime):
        self.bundle, self.store, self.runtime = bundle, store, runtime
        self.docs = {s.source_id: store.retained(s.source_id) for s in bundle.sources}
        self.read = {sid: set() for sid in self.docs}
        self.images = {sid: set() for sid in self.docs}
        self.references = SourceScope(store, lambda: set(self.docs), self.read_locations)

    def read_locations(self):
        return {(sid, pid, None) for sid, pids in self.read.items() for pid in pids} | {
            (sid, None, vid) for sid, vids in self.images.items() for vid in vids
        }

    def navigation(self, source_id):
        doc = self.docs[source_id]
        if doc is None:
            return {"source_id": source_id, "availability": "unavailable"}
        sections = list(dict.fromkeys(p.section for p in doc.passages))
        return {
            "source_id": source_id,
            "availability": doc.availability,
            "sections": [
                {"name": s, "passage_ids": [p.passage_id for p in doc.passages if p.section == s]}
                for s in sections
            ],
            "tables": [p.model_dump(exclude={"text"}) for p in doc.passages if p.kind == "table"],
            "visuals": [v.model_dump(exclude={"path", "url"}) for v in doc.visuals],
            "reading_gaps": doc.reading_gaps,
            "limitations": doc.limitations,
            "original_path": doc.original_path,
            "normalized_path": doc.normalized_path,
            "read_with": "read_materials",
            "browse_with": "browse_materials",
            "visual_read_with": "read_project_visual",
        }

    def view(self, agent_id, phase, *, refs=(), extra=()):
        selected = {r.passage_id for r in refs if r.passage_id}
        materials = []
        for source in self.bundle.sources:
            doc = self.docs[source.source_id]
            material = Material(
                source_id=source.source_id,
                data={
                    "source": source.model_dump(),
                    "navigation": self.navigation(source.source_id),
                },
                essential=True,
                passages=[
                    MaterialPassage(passage_id=p.passage_id, section=p.section, text=p.text)
                    for p in doc.passages
                ]
                if doc
                else [],
            )
            # Passage IDs are source-local; select references separately per document.
            wanted = {
                r.passage_id for r in refs if r.source_id == source.source_id and r.passage_id
            }
            overview = selected_passages(material, ContextView(agent_id=agent_id, phase=phase))
            chosen = (
                [p for p in material.passages if p.passage_id in wanted] if selected else overview
            )
            self.read[source.source_id].update(p.passage_id for p in chosen)
            self.references.capture_passages(
                source.source_id, chosen, candidate=self.docs[source.source_id].candidate
            )
            # Keep full original behind task-local read_materials; only selected text is injected.
            material.selected_passage_ids = [p.passage_id for p in chosen]
            materials.append(material)
        return ContextView(
            agent_id=agent_id,
            phase=phase,
            materials=materials + list(extra),
        )

    def coverage(self):
        return {
            sid: {
                "read_passage_ids": sorted(self.read[sid]),
                "read_visual_ids": sorted(self.images[sid]),
                "total_passages": len(doc.passages) if doc else 0,
                "reading_gaps": doc.reading_gaps if doc else ["source_unavailable"],
            }
            for sid, doc in self.docs.items()
        }

    def record_context_read(self, payload):
        sid = payload.get("source_id")
        doc = self.docs.get(sid)
        if doc:
            valid = {p.passage_id for p in doc.passages}
            self.references.capture_passages(
                sid,
                [p for p in payload.get("passages", []) if p.get("passage_id") in valid],
                candidate=doc.candidate,
            )
            self.read[sid].update(
                p["passage_id"] for p in payload.get("passages", []) if p.get("passage_id") in valid
            )

    def reference_error(self, ref, *, require_read=False):
        doc = self.docs.get(ref.source_id)
        if doc is None:
            return "source_not_in_application"
        if not ref.passage_id and not ref.visual_id:
            return "source_location_required"
        if ref.passage_id and (
            not any(p.passage_id == ref.passage_id for p in doc.passages)
            or (require_read and ref.passage_id not in self.read[ref.source_id])
        ):
            return "unknown_or_unread_passage"
        if ref.visual_id and ref.visual_id not in self.images[ref.source_id]:
            return "visual_not_interpreted"
        return None

    async def read_project(self, args, *, reading=None):
        reading = reading or ReadingWindow()
        rows = []
        remaining = reading.budget
        for selection in args.reads:
            doc = self.docs.get(selection.source_id)
            if not doc:
                rows.append(
                    {"source_id": selection.source_id, "error": "source_not_in_application"}
                )
                continue
            row = reading.read(selection, passages=doc.passages, budget=remaining)
            remaining -= row.get("input_tokens_estimate", 0)
            self.record_context_read(row)
            row.update(reading_gaps=doc.reading_gaps, availability=doc.availability)
            rows.append(row)
        return ToolResult(
            ok=True,
            content=rows,
            source_ids=[r["source_id"] for r in rows if "error" not in r],
            reading_scope="selected_application_passages",
        )

    async def read_visual(self, args):
        doc = self.docs.get(args.source_id)
        visual = (
            next((v for v in doc.visuals if v.visual_id == args.visual_id), None) if doc else None
        )
        if not visual or not visual.path or not Path(visual.path).is_file():
            return ToolResult(ok=False, error="retained_image_unavailable")
        media = mimetypes.guess_type(visual.path)[0] or "image/png"
        pixels = Path(visual.path).read_bytes()
        url = f"data:{media};base64," + base64.b64encode(pixels).decode()
        result = await self.runtime.run_task(
            AgentTask(
                task_id=f"visual/{args.source_id}/{args.visual_id}",
                kind="visual",
                mode="direct",
                phase="project_visual",
                objective=args.question,
                expected_product="VisualUnderstanding：原图解释、可读范围和限制",
                max_model_turns=2,
            ),
            RoleProfile(
                agent_id="project-visual", responsibility="解释原图可见内容与限制，标明推测。"
            ),
            ContextView(
                agent_id="project-visual",
                phase="project_visual",
                materials=[
                    Material(source_id=args.source_id, text=visual.locator, image_urls=[url])
                ],
            ),
            ToolRegistry(),
            VisualUnderstanding,
        )
        understood = (
            result.status == "completed"
            and result.product
            and result.product["readability"] != "unreadable"
        )
        if understood:
            self.images[args.source_id].add(args.visual_id)
            self.references.capture(
                args.source_id,
                visual_id=args.visual_id,
                text=json.dumps(result.product, ensure_ascii=False, sort_keys=True),
                locator=visual.locator,
                kind="interpreted_visual",
                candidate=doc.candidate,
                image_digest=hashlib.sha256(pixels).hexdigest(),
            )
        return ToolResult(
            ok=bool(understood),
            source_ids=[args.source_id],
            content={
                "source_id": args.source_id,
                "visual_id": args.visual_id,
                "locator": visual.locator,
                "interpretation": result.product,
                "model": result.model,
                "status": result.status,
            },
            reading_scope="delegated_image_read",
            error=None
            if understood
            else "image_unreadable"
            if result.status == "completed"
            else result.reason,
        )

    def tools(self):
        registry = ToolRegistry(
            [
                Tool(
                    "read_project_visual",
                    "用图像模型解释保留原图及可读范围",
                    VisualRequest,
                    self.read_visual,
                    delegates=True,
                    timeout_seconds=1200,
                ),
            ]
        )

        async def material(source_id):
            if source_id not in self.docs:
                return None
            if not self.references.eligible(source_id):
                return {"error": "source_after_cutoff"}
            doc = self.docs[source_id]
            return {"passages": doc.passages} if doc else {"error": "source_unavailable"}

        registry.material_providers.append(material)
        registry.read_observers.append(self.record_context_read)
        self.references.attach(registry, "read_application_references")
        return registry

    async def reference_errors(self, project):
        errors = self.references.check(project)

        def visit(value, location):
            if isinstance(value, dict):
                if "source_id" in value:
                    return
                else:
                    if value.get("basis") == "external_knowledge":
                        errors.append(
                            {"location": location, "type": "external_knowledge_not_acquired"}
                        )
                    for key, child in value.items():
                        visit(child, location + [key])
            elif isinstance(value, list):
                for i, child in enumerate(value):
                    visit(child, location + [i])

        visit(project.model_dump(), [])
        return errors
