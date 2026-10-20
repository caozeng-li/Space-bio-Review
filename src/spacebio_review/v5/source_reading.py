"""Question-specific reading: one source, an authored explanation, reusable originals."""

from __future__ import annotations

import asyncio
import json
from contextlib import nullcontext
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.agents.reading import (
    BrowseMaterials,
    ReadPassages,
)
from spacebio_review.evidence.access import ORIGINAL_PAGE_READING
from spacebio_review.evidence.knowledge import AcquireSource, RetrieveKnowledge
from spacebio_review.evidence.reading_render import render_originals
from spacebio_review.evidence.session import VisualRequest
from spacebio_review.persistence import plain, write_json
from spacebio_review.tools import Tool, ToolResult

from .records import SessionTask
from .scientific_handoff import delivery_readings, manuscript_tokens


def visible_note_refs(value):
    """Identify complete notes already present, by immutable identity rather than text."""
    found = set()
    if isinstance(value, dict):
        ref = value.get("note_ref") or value.get("contribution_ref")
        if ref and isinstance(value.get("note"), dict):
            if value["note"].get("body"):
                found.add(ref)
        for child in value.values():
            found.update(visible_note_refs(child))
    elif isinstance(value, list):
        for child in value:
            found.update(visible_note_refs(child))
    return found


class SourceQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    question: str = Field(
        min_length=1,
        description="需要这份来源解释的对象、条件、比较或功能关系；项目联系由专题作者建立。",
    )
    passage_ids: list[str] = Field(default_factory=list)
    material_id: str | None = None
    reading_id: str | None = Field(default=None, description="续问时提供本次运行已有阅读任务 ID。")


class ReadSourceNotes(BaseModel):
    reading_ids: list[str] = Field(min_length=1, max_length=8)


class SourceMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reading_purpose: str = ""
    reading_tokens: int | None = Field(default=None, gt=0)
    material_id: str | None = None
    start: int = Field(default=0, ge=0, description="原生阅读单元目录的起始位置。")
    count: int = Field(default=40, ge=1, le=100, description="本次展示的目录单元数量。")


class SourcePassages(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reading_tokens: int | None = Field(default=None, gt=0)
    passage_ids: list[str] = Field(min_length=1)


class SourceBrowse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reading_tokens: int | None = Field(default=None, gt=0)
    section: str | None = None
    pages: list[int] = Field(default_factory=list, description="来源导航中的PDF物理页码。")
    material_id: str | None = None
    continuation: str | None = None
    start_passage_id: str | None = None


class SourceSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reading_tokens: int | None = Field(default=None, gt=0)
    queries: list[str] = Field(min_length=1, max_length=4, description=(
        "结合来源导航中的实际对象名称和术语，每条查询表达一个需要读取的科学关系。"
        "比较不同对象或查找方法与结果时，可分别查询。"))
    limit: int = Field(default=5, ge=1, le=8)


class SourceVisual(BaseModel):
    visual_id: str
    question: str = Field(min_length=1)


class SourceReading:
    def __init__(self, topics):
        self.topics, self.runtime = topics, topics.runtime
        self.root = self.runtime.store.root
        self._access, self._locks = {}, {}
        self._question_starts = {}

    def _path(self, reading_id):
        return self.runtime.store._path("source-readings", reading_id)

    def manifest(self, reading_id):
        return json.loads(self._path(reading_id).read_text("utf-8"))

    def create(self, topic_id, request):
        parent = self.topics._task(topic_id)
        inquiry = self.topics.inquiry(topic_id)
        parent_access = self.topics.access(topic_id)
        parent_access._eligible(request.source_id)
        cutoff = date.fromisoformat(parent["cutoff"]) if parent["cutoff"] else None
        access = self.topics._new_access(request.question, cutoff, [request.source_id])
        access.completed_research = None
        candidate = self.topics.store.candidate(request.source_id)
        access._read_abstract(request.source_id, candidate, candidate.abstract)
        source = {**access.material_overview(request.source_id), "abstract": candidate.abstract}
        reading_id = self.runtime.create(
            SessionTask(
                role="source_reader",
                objective=request.question,
                application={},
                evaluation={
                    "source": source,
                    "material_id": request.material_id,
                },
                skills=["scientific-source-reading"],
            ),
            readings=access.references.snapshot_choices(selected=False),
            budget_owner=inquiry.budget_account_id,
        )
        reader_state = self.runtime.load(reading_id)
        self.topics.configure_access(access, reader_state)
        if access.original_reader:
            reader_state.task.evaluation["source"]["description"] = ORIGINAL_PAGE_READING
            self.runtime.store.save(reader_state)
        write_json(
            self._path(reading_id),
            {
                "reading_id": reading_id,
                "inquiry_id": topic_id,
                "source_id": request.source_id,
                "question": request.question,
                "cutoff": parent["cutoff"],
            },
        )
        # Register the reading so its result remains available to this research run.
        parent["reading_ids"].append(reading_id)
        write_json(self.topics._path(topic_id), parent)
        access.state_path = self.root / "source-access" / (reading_id + ".json")
        access.save()
        self._access[reading_id] = access
        return reading_id

    def access(self, reading_id):
        return self._access[reading_id]

    def _existing(self, topic_id, request):
        if request.reading_id:
            return request.reading_id
        return next((
            reading_id for reading_id in self.topics._task(topic_id)["reading_ids"]
            if self.manifest(reading_id)["source_id"] == request.source_id
        ), None)

    def _needs_followup(self, reading_id, request):
        """New questions or unread explicit starting positions continue this author."""
        if request.question != self.manifest(reading_id)["question"]:
            return True
        conversation = self.runtime._conversations.get(reading_id)
        references = [conversation.readings[alias]["reference"]
                      for alias in conversation.presented_handles] if conversation else []
        read = {ref["passage_id"] for ref in references
                if ref.get("passage_id") and ref["source_id"] == request.source_id}
        if set(request.passage_ids) - read:
            return True
        if request.material_id:
            doc = self.topics.store.retained(request.source_id)
            material = next((item for item in doc.materials
                             if item.material_id == request.material_id), None) if doc else None
            visuals = {ref["visual_id"] for ref in references
                       if ref.get("visual_id") and ref["source_id"] == request.source_id}
            return material is None or not (
                read.intersection(material.passage_ids)
                or visuals.intersection(material.visual_ids)
            )
        return False

    def estimate_manuscript(
        self, topic_id, *, question, source_id=None, reading_id=None,
        passage_ids=(), material_id=None,
    ):
        """Reserve the known explanation or one new reader manuscript before delegation."""
        request = SourceQuestion(
            source_id=source_id, question=question, reading_id=reading_id,
            passage_ids=list(passage_ids), material_id=material_id,
        ) if source_id else None
        existing = self._existing(topic_id, request) if request else None
        view = self.view(self.result(existing)) if existing else {}
        if existing:
            state = self.runtime.load(existing)
            if state.status != "ready" and (
                state.status != "submitted" or not self._needs_followup(existing, request)
            ):
                return manuscript_tokens(view)
            alias = state.model_alias
        else:
            alias = self.runtime.routes.get("source_reader") or self.runtime.routes["researcher"]
        view.pop("note", None)
        return self.runtime.models[alias].config.max_tokens + manuscript_tokens(view)

    async def _starting_material(self, reading_id, request):
        """Deliver requested starting positions through the ordinary reading tools.

        An attachment is an initial place to look, not a restriction on subsequent
        reading of the source. Text and original pages enter the same initial delivery.
        """
        access = self.access(reading_id)
        results = []
        if request.material_id:
            result = await access.acquire_source(AcquireSource(
                source_id=request.source_id, material_id=request.material_id,
                reading_purpose="",
            ))
            if result.ok:
                result.content["selected_material_id"] = request.material_id
                doc = self.topics.store.retained(request.source_id)
                material = next(m for m in doc.materials if m.material_id == request.material_id)
                result.content["starting_passage_ids"] = material.passage_ids[:1]
            results.append(("acquire_source", result))
        if request.passage_ids:
            results.append(("read_materials", await access.read_materials(ReadPassages(
                source_id=request.source_id, passage_ids=request.passage_ids,
            ))))
        else:
            # Location selection precedes the scientific author's first request.
            # Charge it to the same inquiry as that author's reading and writing.
            state = self.runtime.load(reading_id)
            scope = self.runtime.budget.scope(state.budget_owner or state.session_id) \
                if self.runtime.budget else nullcontext()
            with scope:
                results.append(("open_source_question", await access.open_question(
                    request.source_id, request.question, material_id=request.material_id,
                )))
        for name, result in results:
            self.runtime.receive_tool_result(
                reading_id, result, access.references.snapshot_choices(selected=False), name=name,
            )

    def tools(self, reading_id):
        access = self.access(reading_id)
        sid = self.manifest(reading_id)["source_id"]
        registry = access.tools().select([])

        async def acquire(args):
            return await access.acquire_source(
                AcquireSource(source_id=sid, **args.model_dump())
            )

        async def read(args):
            return await access.read_materials(
                ReadPassages(source_id=sid, **args.model_dump())
            )

        async def browse(args):
            return await access.read_materials(
                BrowseMaterials(source_id=sid, **args.model_dump())
            )

        async def retrieve(args):
            return await access.retrieve(
                RetrieveKnowledge(source_id=sid, **args.model_dump())
            )

        async def visual(args):
            return await access.visual(VisualRequest(source_id=sid, **args.model_dump()))

        for name, description, schema, handler in (
            (
                "acquire_source",
                "取得当前来源正文或指定附件，查看完整单元目录或按问题定位原文。",
                SourceMaterial,
                acquire,
            ),
            ("read_materials", "读取当前来源指定位置所属完整单元。", SourcePassages, read),
            (
                "browse_materials",
                "按来源导航的 pages/material_id 或章节阅读完整原页范围；continuation 继续读取。",
                SourceBrowse,
                browse,
            ),
            (
                "retrieve_knowledge", "在当前来源中按问题定位并阅读完整原生单元。",
                SourceSearch, retrieve,
            ),
            (
                "read_literature_visual",
                "查看当前来源原页、表格或图像及图注，解释文字未充分呈现的对应关系。",
                SourceVisual,
                visual,
            ),
        ):
            if access.original_reader:
                description = (
                    "重新查看当前来源指定原页及其完整解析文字。"
                    if name == "read_literature_visual"
                    else description + ORIGINAL_PAGE_READING
                )
            registry.add(
                Tool(
                    name,
                    description + access.reading_hint
                    if name != "read_literature_visual"
                    else description,
                    schema,
                    access._tool_handler(handler),
                    delegates=name in {
                        "acquire_source", "retrieve_knowledge", "read_literature_visual"
                    },
                    timeout_seconds=300,
                )
            )
        return registry

    def result(self, reading_id, *, note_ref=None):
        """Reusable explanation and cumulative actual originals for any recipient."""
        manifest, state = self.manifest(reading_id), self.runtime.load(reading_id)
        note_ref = note_ref or state.note_ref
        artifact = self.runtime.store.note(note_ref) if note_ref else None
        authored = (
            self.runtime.store.author_text(artifact["author_text_ref"])
            if artifact and artifact.get("author_text_ref") else None
        )
        readings = delivery_readings(
            self.access(reading_id), artifact=artifact, source_id=manifest["source_id"],
        )
        document = self.topics.store.retained(manifest["source_id"])
        visuals = {visual.visual_id: visual for visual in document.visuals} if document else {}
        visual_readings = []
        for snapshot in readings.values():
            ref = snapshot["reference"]
            if ref.get("visual_id") not in visuals:
                continue
            visual_reading = {
                "source_id": manifest["source_id"], "visual_id": ref["visual_id"],
                "locator": snapshot["locator"],
                "image_digest": snapshot.get("image_digest"),
                "original_image": visuals[ref["visual_id"]].model_dump(exclude_none=True),
            }
            if snapshot["reading_scope"] == "original_visual":
                visual_reading.update({
                    "source_role": "external_research", "reading_scope": "original_visual",
                    "reference": ref, "material_version": snapshot["material_version"],
                    "description": "来源原页的回读入口；本次来源解释见阅读作者的稿件。",
                })
            else:
                visual_reading["source_role"] = "review_interpretation"
                visual_reading["interpretation"] = {
                    "reference": ref, "quote": snapshot["quote"],
                    "material_version": snapshot["material_version"],
                }
            visual_readings.append(visual_reading)
        return {
            "reading_id": reading_id,
            "author_session_id": reading_id,
            "source_role": "review_interpretation",
            "source_id": manifest["source_id"],
            "source": self.access(reading_id).material_overview(manifest["source_id"]),
            "question": manifest["question"],
            "status": state.status,
            "reason": state.reason,
            "note_ref": note_ref,
            "note_question": authored.get("focus") if authored else None,
            "note": artifact["note"] if artifact else None,
            "readings": readings,
            "visual_readings": visual_readings,
            "reading_records": plain(self.access(reading_id).reading_records()),
        }

    def _question_result(self, reading_id):
        """The existing parent already has previous questions' originals."""
        result = self.result(reading_id)
        conversation = self.runtime._conversations.get(reading_id)
        if conversation is not None:
            current = {
                alias for alias in conversation.readings_since(
                    self._question_starts.get(reading_id, 0)
                )
            }
            result["readings"] = {
                handle: snapshot for handle, snapshot in result["readings"].items()
                if handle in current
            }
            visuals = {snapshot["reference"].get("visual_id")
                       for snapshot in result["readings"].values()}
            result["visual_readings"] = [row for row in result["visual_readings"]
                                         if row["visual_id"] in visuals]
        return result

    def results(self, topic_id):
        return [self.result(rid) for rid in self.topics._task(topic_id)["reading_ids"]]

    @staticmethod
    def view(result, *, full=True, seen=(), originals=False):
        # reading_id addresses follow-ups; note_ref identifies this immutable explanation
        # across handoffs. Neither is a literature citation.
        view = {k: v for k, v in result.items() if k not in {
            "readings", "reading_records", "author_session_id"
        }}
        if not full or result.get("note_ref") in seen:
            view.pop("note", None)
        if originals:
            view["originals"] = render_originals(result["readings"])
        return view

    def receive(self, access, result):
        access.receive(result["readings"])
        access.visible.update(result["reading_records"])
        access.save()

    async def research(self, topic_id, args):
        try:
            document = await self.topics.access(topic_id)._document(
                args.source_id, args.material_id,
            )
        except (KeyError, ValueError) as exc:
            return ToolResult(ok=False, error=str(exc))
        if not document.passages and not document.visuals and not document.candidate.abstract:
            return ToolResult(
                ok=False,
                error="source_body_unavailable",
                source_ids=[args.source_id],
                content={
                    "source": self.topics.access(topic_id).material_overview(args.source_id),
                    "instruction": "本次未取得可读内容。换用其他来源继续研究；没有生成阅读稿。",
                },
            )
        reading_id = self._existing(topic_id, args) or self.create(topic_id, args)
        manifest = self.manifest(reading_id)
        if manifest["inquiry_id"] != topic_id or manifest["source_id"] != args.source_id:
            return ToolResult(ok=False, error="reading_task_mismatch")
        async with self._locks.setdefault(reading_id, asyncio.Lock()):
            manifest = self.manifest(reading_id)
            state = self.runtime.load(reading_id)
            followup = args.question if args.question != manifest["question"] else None
            if state.status == "submitted" and self._needs_followup(reading_id, args):
                followup = args.question
            if state.status == "ready" or (state.status == "submitted" and followup):
                conversation = self.runtime._conversations.get(reading_id)
                self._question_starts[reading_id] = (
                    conversation.reading_index if conversation else 0
                )
                if followup:
                    self.access(reading_id).objective = followup
                    state.task.objective = followup
                    self.runtime.store.save(state)
                    manifest["question"] = followup
                    write_json(self._path(reading_id), manifest)
                try:
                    await self._starting_material(reading_id, args)
                    await self.runtime.run(
                        reading_id,
                        tools=self.tools(reading_id),
                        followup=followup,
                        context=lambda: {
                            "source": self.access(reading_id).material_overview(args.source_id)
                        },
                    )
                finally:
                    self.access(reading_id).save()
                    result = self.result(reading_id)
                    write_json(
                        self.root / "source-reading-results" / (reading_id + ".json"), result
                    )
                    self.receive(self.topics.access(topic_id), self._question_result(reading_id))
            result = self._question_result(reading_id)
            self.receive(self.topics.access(topic_id), result)
            return ToolResult(
                ok=result["status"] == "submitted" and result["note"] is not None,
                content=self.view(result, originals=True),
                source_ids=[args.source_id],
                reading_scope="scientific_source_reading",
                error=None if result["status"] == "submitted" and result["note"]
                else result["reason"] or "source_reading_incomplete",
            )

    def attach_notes(self, registry, access):
        def estimate(arguments):
            args = ReadSourceNotes.model_validate_json(arguments)
            return manuscript_tokens({
                "source_readings": [self.view(self.result(rid)) for rid in args.reading_ids]
            })

        async def read_notes(args):
            results = [self.result(rid) for rid in args.reading_ids]
            for result in results:
                self.receive(access, result)
            return ToolResult(ok=True, content={
                "source_readings": [self.view(r, originals=True) for r in results]
            })

        registry.add(
            Tool(
                "read_source_notes",
                "回读已完成文献阅读说明及其关键原文，复用已有科学解释。",
                ReadSourceNotes,
                read_notes,
                manuscript_tokens=estimate,
            )
        )

    def attach(self, topic_id, registry):
        def estimate(arguments):
            args = SourceQuestion.model_validate_json(arguments)
            return self.estimate_manuscript(
                topic_id, question=args.question, source_id=args.source_id,
                reading_id=args.reading_id, passage_ids=args.passage_ids,
                material_id=args.material_id,
            )

        async def research(args):
            return await self.research(topic_id, args)

        self.attach_notes(registry, self.topics.access(topic_id))
        registry.add(
            Tool(
                "read_source_for_question",
                "针对当前来源及附件深入解释具体科学关系，返回阅读说明及实际读到的完整原文。"
                "论文、技术手册均适用；passage_ids/material_id 是阅读起点。"
                "同来源自动沿用当前专题已有读者，也可用 reading_id 明确续问；"
                "已有说明用 read_source_notes 回读。",
                SourceQuestion,
                research,
                read_only=False,
                delegates=True,
                manuscript_tokens=estimate,
                timeout_seconds=self.topics.source_reading_timeout(topic_id),
            )
        )
        return registry
