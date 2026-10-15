"""A scientific topic owns one persistent author and one source-reading scope."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.agents.context import TokenEstimator
from spacebio_review.evidence.access import SourceAccess
from spacebio_review.persistence import plain, write_json
from spacebio_review.prose import bind_prose

from .records import SessionTask

_DEFAULT_CUTOFF = object()

TOPIC_INSTRUCTION = """你负责一个完整科学专题，可调整研究方向并指出任务前提不成立之处。
先理解原计划书的对象、比较、实施阶段、实际读出及拟解释的问题。申请、外部原文和
已有研究是不同来源：在轨观察与返回后检测按申请实际安排理解。
围绕会改变判断的信息需要，直接使用资料工具搜索、阅读方法和结果，必要时读取图表
或补充材料。将外部研究的对象、条件、比较基准、观测与本项目联系起来，解释可比性
和差异；对关键倍数或效应量读清实际比较，再应用其科学含义。
研究测量时从具体方法名称和拟解释的对象出发，优先查找信号形成、定位或特异性的
直接实验依据，读清验证用了什么比较、得到什么结果，再说明本项目的读出能支持什么。
得到足以回答专题的关键原文后及时形成研究说明；扩大搜索应服务于尚未解决的具体问题。
研究说明是你直接交付的连贯科学意见，说明已弄清什么、为何影响本项目及仍未知什么。
可以提出值得主审另行研究的问题；最终六项评分和项目支持结论由主审形成。
已有研究用于提供可阅读的认识与原文入口，其项目判断需结合当前任务重新理解。
引用放在正文片段的 cites 中，正文用论文题名或作者自然说明来源。
"""


class TopicTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective: str = Field(min_length=1)
    impact: str = ""
    expertise: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    research_ids: list[str] = Field(default_factory=list)


class TopicResult(BaseModel):
    topic_id: str
    status: str
    note_ref: str | None
    note: dict | None
    readings: dict = Field(default_factory=dict)
    reading_records: dict = Field(default_factory=dict)
    materials: list[dict] = Field(default_factory=list)
    reason: str | None = None
    turns_used: int = 0


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

    def application_context(self, access):
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
        full = size <= self.runtime.settings.context_tokens // 3
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
                else "先用 browse_materials 通读目标、对象、对照、实施阶段与读出，形成完整实验链；"
                "再针对专题继续研究。此目录尚未交付正文，全文均可回读。"
            ),
        }

    def create(self, task: TopicTask, *, allowance=None, evaluation=None, cutoff=_DEFAULT_CUTOFF):
        cutoff = self.cutoff if cutoff is _DEFAULT_CUTOFF else cutoff
        skills = ["literature-understanding", *task.skills]
        for key in task.expertise:
            entry = self.catalogue.entries[key]
            skills.extend([entry.skill, *entry.method_skills])
        access = self._new_access(task.objective, cutoff, [*self.application_ids, *task.source_ids])
        application = self.application_context(access)
        existing = []
        completed = {row["topic_id"]: row for row in self.completed()}
        for topic_id in task.research_ids:
            row = completed[topic_id]
            access.receive(row["readings"])
            access.visible.update(row["source_ids"])
            existing.append({key: value for key, value in row.items() if key != "readings"})
        context = {
            "scientific_role": TOPIC_INSTRUCTION,
            "evaluation_context": self.evaluation if evaluation is None else evaluation,
            "potential_impact": task.impact,
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
                "schema_version": "v5-topic-1",
                "topic_id": topic_id,
                "task": task.model_dump(mode="json"),
                "application_ids": self.application_ids,
                "cutoff": cutoff.isoformat() if cutoff else None,
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
            try:
                async with self._parallel:
                    await self.runtime.run(topic_id, tools=access.tools(), followup=followup)
            finally:
                access.save()
                delivery = self.result(topic_id)
                write_json(self.root / "deliveries" / (topic_id + ".json"), delivery)
                if delivery.status == "submitted":
                    version = self.root / "deliveries" / (delivery.note_ref + ".json")
                    if not version.exists():
                        write_json(version, delivery)
            return self.result(topic_id)

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
            "read_with": "acquire_source(reading_purpose), read_materials, browse_materials",
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
        return TopicResult(
            topic_id=topic_id,
            status=state.status,
            note_ref=ref,
            note=artifact["note"] if artifact else None,
            readings=artifact["evidence"] if artifact else {},
            reading_records=records,
            materials=[self._material(sid) for sid in records],
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
                    "unknowns": result.note["unknowns"],
                    "objective": manifest["task"]["objective"],
                    "source_ids": list(result.reading_records),
                    "readings": result.readings,
                    "reading_records": result.reading_records,
                    "materials": result.materials,
                }
            )
        return rows

    async def close(self):
        for resource in self._owned:
            await resource.close()
