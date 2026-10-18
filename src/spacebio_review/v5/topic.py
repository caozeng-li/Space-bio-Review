"""A scientific inquiry owns its current author, contribution, and source-reading scope."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
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
INITIAL_FOCUS = "依据完整申请独立理解知识职责，确定值得研究的当前科学问题。"

TOPIC_INSTRUCTION = """你是独立研究当前科学问题的专家，解释本计划可能产生什么知识及其价值。
先从 application_basis 和完整申请独立理解目标、对象、安排、预期数据和拟获得的认识。
初始委派提供知识职责与研究背景，不预设必须证明的结论。理解原文后用 contribute 表达
实际研究问题；先定题再研究可用 focus_only，已有完整认识可用 write 形成科学稿。
当前问题与原文不符时直接替换问题，使接下来的研究和稿件围绕新的科学关系展开。
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
新材料形成可用认识或实质修订时用 contribute(write) 更新完整稿，不必每轮改稿。
需要其他作者解释具体科学联系时，用 request_inquiry 说明问题、用途和已有材料引用；
已有相关专题可直接指定 inquiry_id。请求登记后继续独立工作，无须同步等待对方完成。
同伴答复是科学解释；申请的实际安排仍由申请原文确定。整合时把已安排实验、
由证据形成的判断与建议采用的方案分别表达。讨论读出能回答什么时，从实际测量量
与方法信息推导；尚未指定的方法能力可以作为可选择的研究路径，说明怎样获得该认识。
暂时只能等待咨询时，finish_research 的 wait_for 列出所需请求；回复到达后继续本问题。
已有依据足以回答当前问题时用 finish_research，沿用同一成稿入口结束本轮研究。
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
        default_factory=list,
        description="复用已有科学稿的专题 topic_id（稳定 inquiry_id）；没有则留空。",
    )
    application_refs: list[ApplicationRef] = Field(
        default_factory=list, description="当前问题对应的申请原文位置，代码将装配原句。"
    )


class TopicResult(BaseModel):
    topic_id: str = Field(description="专题的稳定 inquiry_id，不是作者执行会话 ID。")
    inquiry_id: str
    current_owner_session_id: str
    current_focus: str | None
    contribution_focus: str | None
    author_session_id: str | None
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
        view = self.model_dump(
            mode="json",
            exclude={
                "readings", "reading_records", "current_owner_session_id", "author_session_id"
            },
        )
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
        from .collaboration import ScientificCollaboration

        self.collaboration = ScientificCollaboration(self)

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

        output = Path(root)
        if output.exists() and any(output.iterdir()):
            raise ValueError("Use an empty output directory for new research")
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
            application_ids = [await resources.store.add_local(application)]
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

    def _topic_ids(self):
        return sorted(self._access)

    def _task(self, topic_id):
        return json.loads(self._path(topic_id).read_text("utf-8"))

    def inquiry(self, topic_id):
        return self.runtime.store.inquiry(topic_id)

    def state(self, topic_id):
        return self.runtime.load(self.inquiry(topic_id).current_owner_session_id)

    def focus(self, topic_id):
        return self.inquiry(topic_id).current_focus or INITIAL_FOCUS

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
        inquiry = self.inquiry(topic_id)
        combined = [ApplicationRef.model_validate(ref) for ref in inquiry.application_anchors]
        combined.extend(ref for ref in refs if ref not in combined)
        access = self.access(topic_id)
        self.application_basis(combined, access)
        inquiry.application_anchors = [ref.model_dump(mode="json") for ref in combined]
        self.runtime.store.save_inquiry(inquiry)
        access.save()

    def refresh_application_basis(self, topic_id, state):
        """Apply added anchors to the actual running state at its next model boundary."""
        access = self.access(topic_id)
        basis = self.application_basis(
            [ApplicationRef.model_validate(ref)
             for ref in self.inquiry(topic_id).application_anchors],
            access,
        )
        context = json.loads(state.task.evaluation)
        if context.get("application_basis") == basis:
            return False
        context["application_basis"] = basis
        state.task.evaluation = json.dumps(context, ensure_ascii=False)
        self.runtime._capture(state, access.references.snapshot_choices(selected=False))
        return True

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
        access = self._new_access(INITIAL_FOCUS, cutoff, [*self.application_ids, *task.source_ids])
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
            "existing_research": existing,
            "available_sources": [self._material(sid) for sid in task.source_ids],
        }
        session_id = self.runtime.create(
            SessionTask(
                role="researcher",
                objective=INITIAL_FOCUS,
                application=application,
                evaluation=json.dumps(context, ensure_ascii=False),
                skills=list(dict.fromkeys(skills)),
            ),
            readings=access.references.snapshot_choices(selected=False),
            allowance=allowance,
            inquiry_assignment=task.model_dump(mode="json"),
        )
        state = self.runtime.load(session_id)
        topic_id = state.inquiry_id
        if existing:
            aliases = {handle: alias for alias, handle in state.aliases.items()}
            for row in existing:
                row["body"] = bind_prose(row["body"], aliases)
            state.task.evaluation = json.dumps(context, ensure_ascii=False)
            self.runtime.store.save(state)
        write_json(
            self._path(topic_id),
            {
                "inquiry_id": topic_id,
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
        self._access[topic_id].objective = self.focus(topic_id)
        return self._access[topic_id]

    def transfer_owner(self, topic_id, *, reason, skills=None, model_role=None):
        """Replace an idle owner; the coordinator has already stopped its active segment."""
        previous = self.state(topic_id)
        task = previous.task.model_copy(deep=True)
        if skills is not None:
            task.skills = list(dict.fromkeys(skills))
        evaluation = json.loads(task.evaluation)
        for row in evaluation.get("existing_research", []):
            row["body"] = bind_prose(row["body"], previous.aliases)
        # Keep public observations' existing E references, then append newer source reads.
        readings = {handle: previous.evidence[handle] for handle in previous.aliases.values()}
        readings.update(self.access(topic_id).references.snapshot_choices(selected=False))
        session_id = self.runtime.create(
            task, readings=readings, inquiry_id=topic_id, model_role=model_role
        )
        state = self.runtime.load(session_id)
        aliases = {handle: alias for alias, handle in state.aliases.items()}
        for row in evaluation.get("existing_research", []):
            row["body"] = bind_prose(row["body"], aliases)
        state.task.evaluation = json.dumps(evaluation, ensure_ascii=False)
        state.observations = deepcopy(previous.observations)
        state.observation_index = previous.observation_index
        self.runtime._observe(state, {
            "kind": "author_handoff", "reason": reason,
            "contribution_ref": self.inquiry(topic_id).current_contribution_ref,
        })
        self.runtime._messages.pop(previous.session_id, None)
        return session_id

    async def run(self, topic_id, *, followup=None):
        return await self.collaboration.run_topic(topic_id, followup=followup)

    async def _run_segment(self, topic_id, *, collaboration, followup=None):
        async with self._locks.setdefault(topic_id, asyncio.Lock()):
            access = self.access(topic_id)
            for row in self.reader.results(topic_id):
                access.receive(row["readings"])
            state = self.state(topic_id)
            self.runtime._capture(state, access.references.snapshot_choices(selected=False))
            self.runtime.store.save(state)
            try:
                async with self._parallel:
                    await self.runtime.run(
                        state.session_id,
                        tools=self.tools(topic_id),
                        followup=followup,
                        context=lambda: self._context(topic_id),
                        collaboration=collaboration,
                    )
            finally:
                access.save()
                delivery = self.result(
                    topic_id, note_ref=self.inquiry(topic_id).current_contribution_ref
                )
                write_json(self.root / "deliveries" / (topic_id + ".json"), delivery)
                if delivery.status == "submitted":
                    version = self.root / "deliveries" / (delivery.note_ref + ".json")
                    if not version.exists():
                        write_json(version, delivery)
            return self.result(topic_id)

    def _context(self, topic_id):
        self.access(topic_id)  # Refresh the existing tool scope after a focus change.
        seen = visible_note_refs(self.state(topic_id).observations)
        return {
            "source_readings": [
                self.reader.view(row, seen=seen) for row in self.reader.results(topic_id)
            ],
            "collaboration": self.collaboration.context(topic_id),
        }

    def tools(self, topic_id):
        access = self.access(topic_id)
        registry = access.tools()
        registry.include(self.collaboration.tools(topic_id))

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

    def _visible_contribution_ref(self, topic_id):
        if self.collaboration.closed:
            return self.collaboration.selected_contributions.get(topic_id)
        return self.inquiry(topic_id).current_contribution_ref

    def contribution_refs(self, topic_id):
        """Exact manuscript versions belong to the inquiry, across all of its authors."""
        refs, ref = [], self._visible_contribution_ref(topic_id)
        while ref:
            refs.append(ref)
            ref = self.runtime.store.contribution(topic_id, ref)["previous_ref"]
        return refs

    def result(self, topic_id, *, note_ref=None):
        inquiry = self.inquiry(topic_id)
        if note_ref:
            self.runtime.store.contribution(topic_id, note_ref)
            version = self.runtime.store._path("deliveries", note_ref)
            if version.exists():
                return TopicResult.model_validate_json(version.read_text("utf-8"))
        state, access = self.state(topic_id), self.access(topic_id)
        ref = note_ref or self._visible_contribution_ref(topic_id)
        artifact = self.runtime.store.contribution(topic_id, ref) if ref else None
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
            inquiry_id=topic_id,
            current_owner_session_id=inquiry.current_owner_session_id,
            current_focus=inquiry.current_focus,
            contribution_focus=artifact["focus"] if artifact else None,
            author_session_id=artifact["author_session_id"] if artifact else None,
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
        """Expose each inquiry's current contribution, including a saved intermediate draft."""
        rows = []
        for topic_id in self._topic_ids():
            if not self._visible_contribution_ref(topic_id):
                continue
            result = self.result(topic_id)
            rows.append(
                {
                    "topic_id": result.topic_id,
                    "inquiry_id": result.inquiry_id,
                    "note_ref": result.note_ref,
                    "status": result.status,
                    "title": result.note["title"],
                    "body": result.note["body"],
                    "objective": result.contribution_focus,
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
                        "objective": result.contribution_focus,
                        "source_ids": list(result.reading_records),
                        **result.note,
                    },
                }
            )
        return rows

    async def close(self):
        await self.collaboration.close()
        for resource in self._owned:
            await resource.close()
