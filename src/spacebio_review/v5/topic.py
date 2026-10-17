"""A scientific topic owns one persistent author and one source-reading scope."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.agents.context import TokenEstimator
from spacebio_review.agents.reading import (
    BrowseMaterials,
    BrowseSelection,
    PassageSelection,
    ReadPassages,
)
from spacebio_review.evidence.access import SourceAccess
from spacebio_review.persistence import plain, write_json
from spacebio_review.prose import bind_prose
from spacebio_review.tools import Tool, ToolResult

from .records import SessionTask
from .source_reading import SourceReading, visible_note_refs

_DEFAULT_CUTOFF = object()

TOPIC_INSTRUCTION = """你是独立研究当前科学问题的专家，解释本计划可能产生什么知识及其价值。
从 application_basis 和完整申请理解目标、对象、安排、预期数据和拟获得的认识；
objective 提出问题，impact 说明答案的用途。可根据材料修正问题前提并调整研究方向。
把申请的实际安排与外部研究的对象、条件、比较和观察联系起来，形成专业判断。
计划中的方法优化、参数筛选和适配可以是合理研究工作，不要求申请已经完成全部研究。
有支持时明确肯定贡献与实现可能；某项方法解释受限时，继续判断其他数据能够带来的价值。
创新比较最近工作已经获得什么，本项目新增什么；描述、探索和新环境中的方法应用均可
有科学价值。合理推断说明来由，项目平台与阶段按申请原句理解，检索未找到不代表不存在。
围绕影响当前判断的信息需要检索和取得资料。可以直接使用 acquire_source、
read_materials、browse_materials、retrieve_knowledge 和 read_literature_visual 阅读。
复杂方法、比较、图表或较长论文需要独立投入时，可用 read_source_for_question 委派
论文阅读助手；不是每篇资料都需委派。已完成说明可用 read_source_notes 回读，原文可
用 read_references 继续理解。按问题选择直接阅读、委派或续问，不以篇数决定研究是否完成。
方法研究从具体读数怎样支持目标认识出发，阅读信号形成、比较和验证实验。定量结果保留
实际比较基准；观测支持哪些解释、哪些因素仍共同变化，按原实验关系说明。
已有依据足以回答问题时用 finish_research，随后直接写连贯的完整专业意见。
论述连接申请安排、研究证据、可作出的判断和真正影响它的条件，引用紧随论述，使用
[@E1]。重要未解问题写在相应论证中，不用大量不影响判断的保留意见稀释已有认识。
可提出值得主审继续研究的问题，最终项目评分与支持结论由主审形成。
"""


class ApplicationRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(description="本项目申请的 source_id；外部论文用 source_ids 传递。")
    passage_id: str = Field(description="该申请原文段落的 passage_id，与 source_id 配对。")


class TopicTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective: str = Field(
        min_length=1,
        description="会影响项目知识产出与价值判断的科学问题，例如实际读出能提供哪些互补认识。",
    )
    impact: str = Field(
        default="",
        description="答案帮助理解哪段科学关系或贡献，不预设查不到即首次或预期升降分。"
        "申请主张由 application_refs 携带，最终评分由主审完成。",
    )
    expertise: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(
        default_factory=list, description="相关外部资料的 source_id，来自资料目录。"
    )
    research_ids: list[str] = Field(
        default_factory=list, description="复用的已提交专题 topic_id；没有已完成专题则留空。"
    )
    application_refs: list[ApplicationRef] = Field(
        default_factory=list, description="当前问题对应的申请原文位置，代码将装配原句。"
    )


class TopicResult(BaseModel):
    topic_id: str
    status: str
    note_ref: str | None
    note: dict | None
    readings: dict = Field(default_factory=dict)
    reading_records: dict = Field(default_factory=dict)
    materials: list[dict] = Field(default_factory=list)
    source_readings: list[dict] = Field(default_factory=list)
    reason: str | None = None
    turns_used: int = 0

    def model_view(self, *, seen=()):
        """Scientific handoff; the full reading archive remains in the saved delivery."""
        view = self.model_dump(mode="json", exclude={"readings", "reading_records"})
        if self.note_ref in seen:
            view.pop("note", None)
        view["materials"] = [
            {
                key: value
                for key, value in item.items()
                if key in {"source_id", "title", "availability", "read_with"}
            }
            for item in self.materials
        ]
        view["source_readings"] = [
            SourceReading.view(row, seen=seen) for row in self.source_readings
        ]
        return view


class ReadApplication(BaseModel):
    source_id: str | None = None
    passage_ids: list[str] = Field(default_factory=list)
    section: str | None = None
    continuation: str | None = None
    start_passage_id: str | None = None


class TopicResearch:
    def __init__(
        self,
        runtime,
        store,
        discovery,
        *,
        application_ids,
        evaluation="",
        cutoff=None,
        rerank_model=None,
        index=None,
        visual_reader=None,
        catalogue=None,
        external_research_enabled=True,
        parallel_topics=4,
    ):
        self.runtime, self.store, self.discovery = runtime, store, discovery
        self.application_ids = list(application_ids)
        self.evaluation, self.cutoff = evaluation, cutoff
        self.rerank_model, self.index = rerank_model, index
        self.visual_reader, self.catalogue = visual_reader, catalogue
        self.external_research_enabled = external_research_enabled
        self._parallel = asyncio.Semaphore(parallel_topics)
        self.root = runtime.store.root
        self._access, self._locks, self._owned = {}, {}, []
        self.reader = SourceReading(self)

    @classmethod
    async def from_config(
        cls, path, root, application, *, env_file=".env", evaluation="", cutoff=None
    ):
        from spacebio_review.evidence.research import EvidenceResources, load_evidence_config
        from spacebio_review.models.embedding import DashScopeEmbedding, VectorIndex
        from spacebio_review.models.scheduling import ScheduledModel
        from spacebio_review.review.expertise import ExpertiseCatalogue

        from .config import load_config
        from .session import ResearchRuntime
        from .sources import VisualReader

        config = load_config(path)
        runtime = ResearchRuntime.from_config(path, root, env_file=env_file)
        resources = EvidenceResources.create(
            runtime,
            root,
            load_evidence_config(config.evidence_config),
            env_file=env_file,
        )
        owned = [resources, runtime]
        try:
            auxiliary = {
                kind: ScheduledModel(runtime.models[alias], runtime.scheduler, alias)
                for kind, alias in config.auxiliary.items()
            }
            index = None
            if config.embedding:
                embedder = DashScopeEmbedding(
                    config.embedding, runtime.scheduler, env_file=env_file
                )
                owned.insert(0, embedder)
                index = VectorIndex(embedder, cache_dir=config.embedding.cache_dir)
            # Reopening this run uses its retained application and existing source identity.
            project_path = runtime.store.root / "project.json"
            if project_path.exists():
                project = json.loads(project_path.read_text("utf-8"))
                if Path(application).resolve().as_posix() != project["application_path"]:
                    raise ValueError("run_application_changed")
                application_ids = project["application_ids"]
            else:
                application_ids = [await resources.store.add_local(application)]
                write_json(
                    project_path,
                    {
                        "application_path": Path(application).resolve().as_posix(),
                        "application_ids": application_ids,
                    },
                )
            service = cls(
                runtime,
                resources.store,
                resources.discovery,
                application_ids=application_ids,
                evaluation=evaluation,
                cutoff=cutoff,
                rerank_model=auxiliary.get("rerank") if resources.config.model_rerank else None,
                index=index,
                visual_reader=VisualReader(auxiliary["visual"], resources.store)
                if "visual" in auxiliary
                else None,
                catalogue=ExpertiseCatalogue(config.expertise_config, runtime.skills),
            )
            service._owned = owned
            return service
        except BaseException:
            for resource in owned:
                await resource.close()
            raise

    def _path(self, topic_id):
        return self.runtime.store._path("topics", topic_id)

    def _task(self, topic_id):
        return json.loads(self._path(topic_id).read_text("utf-8"))

    def _new_access(self, objective, cutoff, visible, *, topic_id=None):
        return SourceAccess(
            self.store,
            self.discovery,
            objective=objective,
            cutoff=cutoff,
            visible=visible,
            rerank_model=self.rerank_model,
            index=self.index,
            visual_reader=self.visual_reader,
            completed_research=lambda: [r for r in self.completed() if r["topic_id"] != topic_id],
            state_path=self.root / "source-access" / (topic_id + ".json") if topic_id else None,
            external_research_enabled=self.external_research_enabled,
            local_source_ids=self.application_ids,
        )

    def source_access(self, objective, *, session_id=None, source_ids=(), cutoff=_DEFAULT_CUTOFF):
        """The chief and topic authors use the same originals and reading operations."""
        return self._new_access(
            objective,
            self.cutoff if cutoff is _DEFAULT_CUTOFF else cutoff,
            [*self.application_ids, *source_ids],
            topic_id=session_id,
        )

    def application_context(self, access, *, role="researcher"):
        sources, size = [], 0
        estimator = TokenEstimator()
        for sid in self.application_ids:
            doc = self.store.retained(sid)
            if doc is None:
                raise ValueError("application_must_be_retained")
            passages = [p.model_dump(mode="json") for p in doc.passages]
            size += estimator.measure([{"role": "user", "content": json.dumps(passages)}])[
                "input_tokens"
            ]
            sources.append(
                {
                    "source_id": sid,
                    "title": doc.candidate.title,
                    "passages": passages,
                    "sections": list(dict.fromkeys(p.section for p in doc.passages)),
                    "visuals": [v.model_dump(exclude={"url", "path"}) for v in doc.visuals],
                    "limitations": doc.limitations,
                    "reading_gaps": doc.reading_gaps,
                }
            )
        full = size <= self.runtime.settings_for(role).context_tokens // 3
        for source in sources:
            if full:
                access.record_context_read(source)
            else:
                source.pop("passages")
        return {
            "sources": sources,
            "full_text_in_context": full,
            "reading_instruction": (
                "原计划书全文如下，可按原位置回读。"
                if full
                else "先用 "
                + ("browse_materials" if role == "chief" else "read_application")
                + " 通读目标、对象、对照、实施阶段与读出，形成完整实验链；"
                "再针对专题继续研究。此目录尚未交付正文，全文均可回读。"
            ),
        }

    def application_basis(self, refs, access):
        """Resolve application positions into originals, independently of session E aliases."""
        rows = []
        for ref in refs:
            if ref.source_id not in self.application_ids:
                raise ValueError("application_reference_requires_current_application")
            doc = self.store.retained(ref.source_id)
            passage = next((p for p in doc.passages if p.passage_id == ref.passage_id), None)
            if passage is None:
                raise ValueError("unknown_application_passage")
            row = {
                "source_id": ref.source_id,
                "title": doc.candidate.title,
                "passages": [passage.model_dump(mode="json")],
            }
            access.record_context_read(row)
            rows.append(row)
        return rows

    def supplement_application(self, topic_id, refs):
        """Keep the original task anchors when a continuation adds another experiment step."""
        if not refs:
            return
        manifest = self._task(topic_id)
        task = TopicTask.model_validate(manifest["task"])
        combined = list(task.application_refs)
        combined.extend(ref for ref in refs if ref not in combined)
        access = self.access(topic_id)
        basis = self.application_basis(combined, access)
        state = self.runtime.load(topic_id)
        context = json.loads(state.task.evaluation)
        context["application_basis"] = basis
        state.task.evaluation = json.dumps(context, ensure_ascii=False)
        self.runtime._capture(state, access.references.snapshot_choices(selected=False))
        self.runtime.store.save(state)
        task.application_refs = combined
        manifest["task"] = task.model_dump(mode="json")
        write_json(self._path(topic_id), manifest)
        access.save()
        # Continuation must receive the supplemented context, not an older in-memory view.
        self.runtime._messages.pop(topic_id, None)

    def create(self, task: TopicTask, *, allowance=None, evaluation=None, cutoff=_DEFAULT_CUTOFF):
        cutoff = self.cutoff if cutoff is _DEFAULT_CUTOFF else cutoff
        skills = ["literature-understanding", *task.skills]
        for key in task.expertise:
            entry = self.catalogue.entries[key]
            skills.append(entry.skill)
            skills.extend(entry.method_skills)
        allowed = {"literature-understanding"}
        if self.catalogue:
            allowed.update(self.catalogue.methods)
            allowed.update(entry.skill for entry in self.catalogue.entries.values())
        skills = [name for name in dict.fromkeys(skills) if name in allowed]
        access = self._new_access(task.objective, cutoff, [*self.application_ids, *task.source_ids])
        application = self.application_context(access)
        existing = []
        completed = {row["topic_id"]: row for row in self.completed()}
        for topic_id in task.research_ids:
            row = completed[topic_id]
            access.receive(row["readings"])
            access.visible.update(row["source_ids"])
            existing.append(row["view"])
        context = {
            "scientific_role": TOPIC_INSTRUCTION,
            "application_basis": self.application_basis(task.application_refs, access),
            "scientific_use": task.impact,
            "existing_research": existing,
            "available_sources": [self._material(sid) for sid in task.source_ids],
        }
        topic_id = self.runtime.create(
            SessionTask(
                role="researcher",
                objective=task.objective,
                application=application,
                evaluation=json.dumps(context, ensure_ascii=False),
                skills=list(dict.fromkeys(skills)),
            ),
            readings=access.references.snapshot_choices(selected=False),
            allowance=allowance,
        )
        if existing:
            state = self.runtime.load(topic_id)
            aliases = {handle: alias for alias, handle in state.aliases.items()}
            for row in existing:
                row["body"] = bind_prose(row["body"], aliases)
            state.task.evaluation = json.dumps(context, ensure_ascii=False)
            self.runtime.store.save(state)
        write_json(
            self._path(topic_id),
            {
                "topic_id": topic_id,
                "task": task.model_dump(mode="json"),
                "application_ids": self.application_ids,
                "cutoff": cutoff.isoformat() if cutoff else None,
                "reading_ids": [],
            },
        )
        access.state_path = self.root / "source-access" / (topic_id + ".json")
        access.completed_research = lambda: [
            r for r in self.completed() if r["topic_id"] != topic_id
        ]
        access.save()
        self._access[topic_id] = access
        return topic_id

    def access(self, topic_id):
        if topic_id not in self._access:
            manifest = self._task(topic_id)
            task = TopicTask.model_validate(manifest["task"])
            cutoff = date.fromisoformat(manifest["cutoff"]) if manifest["cutoff"] else None
            access = self._new_access(
                task.objective,
                cutoff,
                [*manifest["application_ids"], *task.source_ids],
                topic_id=topic_id,
            )
            if access.state_path.exists():
                access.restore_state(json.loads(access.state_path.read_text("utf-8")))
            access.receive(self.runtime.load(topic_id).evidence)
            self._access[topic_id] = access
        return self._access[topic_id]

    async def run(self, topic_id, *, followup=None):
        async with self._locks.setdefault(topic_id, asyncio.Lock()):
            access = self.access(topic_id)
            for row in self.reader.results(topic_id):
                access.receive(row["readings"])
            state = self.runtime.load(topic_id)
            self.runtime._capture(state, access.references.snapshot_choices(selected=False))
            self.runtime.store.save(state)
            try:
                async with self._parallel:
                    await self.runtime.run(
                        topic_id,
                        tools=self.tools(topic_id),
                        followup=followup,
                        context=lambda: self._context(topic_id),
                    )
            finally:
                access.save()
                delivery = self.result(topic_id)
                write_json(self.root / "deliveries" / (topic_id + ".json"), delivery)
                if delivery.status == "submitted":
                    version = self.root / "deliveries" / (delivery.note_ref + ".json")
                    if not version.exists():
                        write_json(version, delivery)
            return self.result(topic_id)

    def _context(self, topic_id):
        seen = visible_note_refs(self.runtime.load(topic_id).observations)
        return {
            "source_readings": [
                self.reader.view(row, seen=seen) for row in self.reader.results(topic_id)
            ]
        }

    def tools(self, topic_id):
        access = self.access(topic_id)
        registry = access.tools()

        async def read_application(args):
            sid = args.source_id or self.application_ids[0]
            if sid not in self.application_ids:
                return ToolResult(ok=False, error="current_application_required")
            selection = (
                ReadPassages(reads=[PassageSelection(source_id=sid, passage_ids=args.passage_ids)])
                if args.passage_ids
                else BrowseMaterials(
                    reads=[
                        BrowseSelection(
                            source_id=sid,
                            section=args.section,
                            continuation=args.continuation,
                            start_passage_id=args.start_passage_id,
                        )
                    ]
                )
            )
            return await access.read_materials(selection)

        registry.add(
            Tool(
                "read_application",
                "按位置或章节继续阅读本项目计划书，保留申请原句。",
                ReadApplication,
                access._tool_handler(read_application),
            )
        )
        return self.reader.attach(topic_id, registry)

    def _material(self, sid):
        doc = self.store.retained(sid)
        return {
            "source_id": sid,
            "title": self.store.candidate(sid).title,
            "availability": doc.availability if doc else "metadata",
            "sections": list(dict.fromkeys(p.section for p in doc.passages)) if doc else [],
            "materials": [m.model_dump(exclude={"original_path"}) for m in doc.materials]
            if doc
            else [],
            "read_with": (
                "read_materials; browse_materials; read_source_for_question; read_source_notes"
            ),
        }

    def result(self, topic_id, *, note_ref=None):
        if note_ref:
            version = self.runtime.store._path("deliveries", note_ref)
            if version.exists():
                return TopicResult.model_validate_json(version.read_text("utf-8"))
        state, access = self.runtime.load(topic_id), self.access(topic_id)
        ref = note_ref or state.note_ref
        artifact = self.runtime.store.note(ref) if ref else None
        records = plain(access.reading_records())
        source_readings = self.reader.results(topic_id)
        readings = dict(artifact["evidence"]) if artifact else {}
        for row in source_readings:
            readings.update(row["readings"])
            for sid, record in row["reading_records"].items():
                if sid not in records:
                    records[sid] = record
                else:
                    for key in ("passage_ids", "visual_ids"):
                        records[sid][key] = list(
                            dict.fromkeys([*records[sid].get(key, []), *record.get(key, [])])
                        )
        return TopicResult(
            topic_id=topic_id,
            status=state.status,
            note_ref=ref,
            note=artifact["note"] if artifact else None,
            readings=readings,
            reading_records=records,
            materials=[self._material(sid) for sid in records],
            source_readings=source_readings,
            reason=state.reason,
            turns_used=state.turns_used,
        )

    def completed(self):
        """Only v5 completed notes in this run; a failed continuation retains prior submissions."""
        rows = []
        for path in sorted((self.root / "topics").glob("*.json")):
            manifest = json.loads(path.read_text("utf-8"))
            state = self.runtime.load(manifest["topic_id"])
            if not state.submissions:
                continue
            result = self.result(state.session_id, note_ref=state.submissions[-1])
            rows.append(
                {
                    "topic_id": result.topic_id,
                    "note_ref": result.note_ref,
                    "status": "submitted",
                    "title": result.note["title"],
                    "body": result.note["body"],
                    "objective": manifest["task"]["objective"],
                    "source_ids": list(result.reading_records),
                    "readings": result.readings,
                    "reading_records": result.reading_records,
                    "materials": result.materials,
                    "view": {
                        **{
                            key: value
                            for key, value in result.model_view().items()
                            if key != "note"
                        },
                        "objective": manifest["task"]["objective"],
                        "source_ids": list(result.reading_records),
                        **result.note,
                    },
                }
            )
        return rows

    async def close(self):
        for resource in self._owned:
            await resource.close()
