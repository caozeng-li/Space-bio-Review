"""Question-specific reading: one source, an authored explanation, reusable originals."""

from __future__ import annotations

import asyncio
import json
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.agents.reading import (
    BrowseMaterials,
    BrowseSelection,
    PassageSelection,
    ReadPassages,
)
from spacebio_review.evidence.knowledge import AcquireSource, RetrieveKnowledge
from spacebio_review.evidence.session import VisualRequest
from spacebio_review.persistence import plain, write_json
from spacebio_review.tools import Tool, ToolResult

from .records import SessionTask

READER_INSTRUCTION = """你负责围绕当前问题阅读一篇资料及其附件，形成可复用的科学解释。
从目录定位方法、实际比较、结果与图注，需要时继续同篇附件或图像。说明研究对象和
条件、各组/队列/任务之间的关系、实际观测及其支持的认识。研究者提出的假说与实验
已经观察到的结果按各自性质表达。对象和限制随具体结论一起写出，引用紧跟依据。
当前问题需要另一篇论文才能解决的部分，留给专题作者继续查找。你的产物解释这份资料，
专题作者负责跨论文及与申请的比较，主审最终评价。问题前提若与原文不符，按原文解释。
已有原文足以回答当前问题，或本次可读材料已经用尽时，调用 finish_research 说明理由；
随后在程序安排的写作阶段直接写完整科学说明，条件和未解问题保留在相应论述中。
"""


def visible_note_refs(value):
    """Identify complete notes already present, by immutable identity rather than text."""
    found = set()
    if isinstance(value, dict):
        if value.get("note_ref") and isinstance(value.get("note"), dict):
            if value["note"].get("body"):
                found.add(value["note_ref"])
        for child in value.values():
            found.update(visible_note_refs(child))
    elif isinstance(value, list):
        for child in value:
            found.update(visible_note_refs(child))
    return found


class SourceQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    question: str = Field(min_length=1)
    passage_ids: list[str] = Field(default_factory=list)
    material_id: str | None = None
    reading_id: str | None = Field(default=None, description="续问或恢复时提供已有阅读任务 ID。")


class ReadSourceNotes(BaseModel):
    reading_ids: list[str] = Field(min_length=1, max_length=8)


class SourceMaterial(BaseModel):
    reading_purpose: str = ""
    material_id: str | None = None
    start: int = Field(default=0, ge=0)
    count: int = Field(default=40, ge=1, le=100)


class SourcePassages(BaseModel):
    passage_ids: list[str] = Field(min_length=1)


class SourceBrowse(BaseModel):
    section: str | None = None
    continuation: str | None = None
    start_passage_id: str | None = None


class SourceSearch(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=4)
    limit: int = Field(default=5, ge=1, le=8)
    neighbors: int = Field(default=1, ge=0, le=2)


class SourceVisual(BaseModel):
    visual_id: str
    question: str = Field(min_length=1)


class SourceReading:
    def __init__(self, topics):
        self.topics, self.runtime = topics, topics.runtime
        self.root = self.runtime.store.root
        self._access, self._locks = {}, {}

    def _path(self, reading_id):
        return self.runtime.store._path("source-readings", reading_id)

    def manifest(self, reading_id):
        return json.loads(self._path(reading_id).read_text("utf-8"))

    def create(self, topic_id, request):
        from .topic import TopicTask

        parent = self.topics._task(topic_id)
        parent_access = self.topics.access(topic_id)
        parent_access._eligible(request.source_id)
        cutoff = date.fromisoformat(parent["cutoff"]) if parent["cutoff"] else None
        access = self.topics._new_access(request.question, cutoff, [request.source_id])
        access.completed_research = None
        basis = self.topics.application_basis(
            TopicTask.model_validate(parent["task"]).application_refs, access
        )
        candidate = self.topics.store.candidate(request.source_id)
        access._read_abstract(request.source_id, candidate, candidate.abstract)
        selected = []
        if request.passage_ids:
            doc = self.topics.store.retained(request.source_id)
            positions = {p.passage_id: p for p in doc.passages} if doc else {}
            selected = [positions[key].model_dump(mode="json") for key in request.passage_ids]
            access.record_context_read({"source_id": request.source_id, "passages": selected})
        source = {**access.material_overview(request.source_id), "abstract": candidate.abstract}
        reading_id = self.runtime.create(
            SessionTask(
                role="source_reader",
                objective=request.question,
                application={"application_basis": basis},
                evaluation=json.dumps(
                    {
                        "scientific_role": READER_INSTRUCTION,
                        "source": source,
                        "selected_passages": selected,
                        "material_id": request.material_id,
                    },
                    ensure_ascii=False,
                ),
                skills=["scientific-source-reading"],
            ),
            readings=access.references.snapshot_choices(selected=False),
            budget_owner=topic_id,
        )
        write_json(
            self._path(reading_id),
            {
                "reading_id": reading_id,
                "topic_id": topic_id,
                "source_id": request.source_id,
                "question": request.question,
                "cutoff": parent["cutoff"],
            },
        )
        # Register the identity before awaiting the child: saved work survives parent failure.
        parent["reading_ids"].append(reading_id)
        write_json(self.topics._path(topic_id), parent)
        access.state_path = self.root / "source-access" / (reading_id + ".json")
        access.save()
        self._access[reading_id] = access
        return reading_id

    def access(self, reading_id):
        if reading_id not in self._access:
            manifest = self.manifest(reading_id)
            access = self.topics._new_access(
                manifest["question"],
                date.fromisoformat(manifest["cutoff"]) if manifest["cutoff"] else None,
                [manifest["source_id"]],
                topic_id=reading_id,
            )
            access.completed_research = None
            if access.state_path.exists():
                access.restore_state(json.loads(access.state_path.read_text("utf-8")))
            access.receive(self.runtime.load(reading_id).evidence)
            self._access[reading_id] = access
        return self._access[reading_id]

    def tools(self, reading_id):
        access = self.access(reading_id)
        sid = self.manifest(reading_id)["source_id"]
        registry = access.tools().select([])

        async def acquire(args):
            return await access.acquire_source(AcquireSource(source_id=sid, **args.model_dump()))

        async def read(args):
            return await access.read_materials(
                ReadPassages(reads=[PassageSelection(source_id=sid, passage_ids=args.passage_ids)])
            )

        async def browse(args):
            return await access.read_materials(
                BrowseMaterials(reads=[BrowseSelection(source_id=sid, **args.model_dump())])
            )

        async def retrieve(args):
            return await access.retrieve(RetrieveKnowledge(source_ids=[sid], **args.model_dump()))

        async def visual(args):
            return await access.visual(VisualRequest(source_id=sid, **args.model_dump()))

        for name, description, schema, handler in (
            (
                "acquire_source",
                "取得当前论文正文或指定同篇附件，按问题定位原文。",
                SourceMaterial,
                acquire,
            ),
            ("read_materials", "读取当前论文的指定原文段落。", SourcePassages, read),
            (
                "browse_materials",
                "沿当前论文的章节或 continuation 继续阅读。",
                SourceBrowse,
                browse,
            ),
            ("retrieve_knowledge", "在当前论文中按问题定位原文和邻段。", SourceSearch, retrieve),
            (
                "read_literature_visual",
                "解读当前论文图像及图注，回答明确问题。",
                SourceVisual,
                visual,
            ),
        ):
            registry.add(
                Tool(
                    name,
                    description,
                    schema,
                    access._tool_handler(handler),
                    delegates=name == "read_literature_visual",
                    timeout_seconds=300,
                )
            )
        return registry

    def result(self, reading_id):
        manifest, state = self.manifest(reading_id), self.runtime.load(reading_id)
        artifact = self.runtime.store.note(state.note_ref) if state.note_ref else None
        return {
            "reading_id": reading_id,
            "source_id": manifest["source_id"],
            "question": manifest["question"],
            "status": state.status,
            "reason": state.reason,
            "note_ref": state.note_ref,
            "note": artifact["note"] if artifact else None,
            "readings": artifact["evidence"] if artifact else {},
            "reading_records": plain(self.access(reading_id).reading_records()),
        }

    def results(self, topic_id):
        return [self.result(rid) for rid in self.topics._task(topic_id)["reading_ids"]]

    @staticmethod
    def view(result, *, full=True, seen=()):
        view = {k: v for k, v in result.items() if k not in {"readings", "reading_records"}}
        if not full or result.get("note_ref") in seen:
            view.pop("note", None)
        return view

    def receive(self, access, result):
        access.receive(result["readings"])
        access.visible.update(result["reading_records"])
        access.save()

    async def research(self, topic_id, args):
        reading_id = args.reading_id or self.create(topic_id, args)
        manifest = self.manifest(reading_id)
        if manifest["topic_id"] != topic_id or manifest["source_id"] != args.source_id:
            return ToolResult(ok=False, error="reading_task_mismatch")
        async with self._locks.setdefault(reading_id, asyncio.Lock()):
            manifest = self.manifest(reading_id)
            state = self.runtime.load(reading_id)
            followup = args.question if args.question != manifest["question"] else None
            if state.status != "submitted" or followup:
                try:
                    await self.runtime.run(
                        reading_id,
                        tools=self.tools(reading_id),
                        followup=followup,
                        context=lambda: {
                            "source": self.access(reading_id).material_overview(args.source_id)
                        },
                    )
                    if followup:
                        manifest["question"] = followup
                        write_json(self._path(reading_id), manifest)
                finally:
                    self.access(reading_id).save()
                    result = self.result(reading_id)
                    write_json(
                        self.root / "source-reading-results" / (reading_id + ".json"), result
                    )
                    self.receive(self.topics.access(topic_id), result)
            result = self.result(reading_id)
            self.receive(self.topics.access(topic_id), result)
            return ToolResult(
                ok=result["note"] is not None,
                content=self.view(result),
                source_ids=[args.source_id],
                reading_scope="scientific_source_reading",
                error=None if result["note"] else result["reason"] or "source_reading_incomplete",
            )

    def attach_notes(self, registry, access):
        async def read_notes(args):
            results = [self.result(rid) for rid in args.reading_ids]
            for result in results:
                self.receive(access, result)
            return ToolResult(ok=True, content={"source_readings": [self.view(r) for r in results]})

        registry.add(
            Tool(
                "read_source_notes",
                "回读已完成文献阅读说明及其关键原文，复用已有科学解释。",
                ReadSourceNotes,
                read_notes,
            )
        )

    def attach(self, topic_id, registry):
        async def research(args):
            return await self.research(topic_id, args)

        self.attach_notes(registry, self.topics.access(topic_id))
        registry.add(
            Tool(
                "read_source_for_question",
                "针对一篇论文及附件深入解释具体科学问题，返回已保存说明及原文。"
                "续问或恢复提供 reading_id；已有完成说明用 read_source_notes 回读。",
                SourceQuestion,
                research,
                read_only=False,
                delegates=True,
                timeout_seconds=self.runtime.budget.remaining_seconds()
                if self.runtime.budget
                else 3600,
            )
        )
        return registry
