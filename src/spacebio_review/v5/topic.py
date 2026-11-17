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
    ReadPassages,
)
from spacebio_review.evidence.access import SourceAccess
from spacebio_review.persistence import plain, write_json
from spacebio_review.tools import Tool, ToolResult

from .records import SessionTask
from .scientific_handoff import delivery_readings
from .source_reading import SourceReading, visible_note_refs

_DEFAULT_CUTOFF = object()
INITIAL_FOCUS = (
    'Determine the research questions affecting judgment based on the complete application and '
    'review responsibilities.'
)


class ApplicationRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(description=(
        'The source_id of this project application; for external papers, pass using source_ids.'
    ))
    passage_id: str = Field(description=(
        'The passage_id of this application original text paragraph, paired with source_id.'
    ))


class TopicTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    responsibility: str = Field(
        min_length=1,
        description=(
            'Directly propose 1–3 review questions that need research. The application '
            'arrangement is passed via the original text in application_refs; do not write an '
            'application summary or pre-judge flaws here. The questions shall retain the '
            'objects, stages, and targets from the original text.'
        ),
    )
    impact: str = Field(
        default="",
        description=(
            'How the answer will affect the evaluation of which aspect of the application; do '
            'not pre-set judgment outcomes or expected score changes. Application claims are '
            'carried by application_refs; the final score is completed by the lead reviewer.'
        ),
    )
    expertise: list[str] = Field(
        default_factory=list,
        description=(
            'The id from the capability catalog, e.g., space-operations; not the Chinese '
            'expertise name.'
        ),
    )
    skills: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(
        default_factory=list, description=(
            'The source_id of relevant external materials, from the materials catalog.'
        )
    )
    research_ids: list[str] = Field(
        default_factory=list,
        description=(
            'The topic_id (stable inquiry_id) of the topic whose existing review opinion needs '
            'to be referenced; leave blank if none.'
        ),
    )
    application_refs: list[ApplicationRef] = Field(
        default_factory=list, description=(
            'The original text position of the application corresponding to the current '
            'question; the code accompanies the original text of the section it belongs to.'
        )
    )


class TopicResult(BaseModel):
    topic_id: str = Field(description=(
        "The stable inquiry_id of the topic, not the author's execution session ID."
    ))
    inquiry_id: str
    current_owner_session_id: str
    responsibility: str
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
            exclude={"readings", "reading_records"},
        )
        view["source_role"] = "review_interpretation"
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
    reading_tokens: int | None = Field(default=None, gt=0)


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
        evidence_config=None,
    ):
        from spacebio_review.evidence.research import EvidenceConfig

        self.runtime, self.store, self.discovery = runtime, store, discovery
        self.evidence_config = evidence_config or EvidenceConfig()
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
                    config.embedding, runtime.scheduler, env_file=env_file, budget=runtime.budget
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
                evidence_config=resources.config,
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
        inquiry = self.inquiry(topic_id)
        return inquiry.current_focus or inquiry.responsibility

    def _new_access(self, objective, cutoff, visible, *, topic_id=None):
        return SourceAccess(
            self.store,
            self.discovery,
            scheduler=self.runtime.scheduler,
            objective=objective,
            cutoff=cutoff,
            visible=visible,
            rerank_model=self.rerank_model,
            index=self.index,
            visual_reader=self.visual_reader,
            completed_research=(
                None if self.runtime.ablations.fixed_research else
                lambda: [r for r in self.completed() if r["topic_id"] != topic_id]
            ),
            state_path=self.root / "source-access" / (topic_id + ".json") if topic_id else None,
            external_research_enabled=self.external_research_enabled,
            local_source_ids=self.application_ids,
            reading_tokens=self.evidence_config.reading_tokens,
            max_reading_tokens=self.evidence_config.max_reading_tokens,
        )

    def source_access(self, objective, *, session_id=None, source_ids=(), cutoff=_DEFAULT_CUTOFF):
        """The chief and topic authors use the same originals and reading operations."""
        return self._new_access(
            objective,
            self.cutoff if cutoff is _DEFAULT_CUTOFF else cutoff,
            [*self.application_ids, *source_ids],
            topic_id=session_id,
        )

    def configure_access(self, access, state):
        """The actual author's model decides whether selected pages are native inputs."""
        from .sources import OriginalVisualReader

        vision = self.runtime.models[state.model_alias].config.vision
        if vision and access.original_reader is None:
            access.original_reader = OriginalVisualReader(self.store)
        elif not vision:
            access.original_reader = None
        return access

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
            "purpose": (
                'The plan and basis provided by the applicant team; the specific arrangements '
                'are explained by these original texts.'
            ),
            "sources": sources,
            "full_text_in_context": full,
            "reading_instruction": (
                (
                    'The full text of the original proposal is as follows; it can be read back '
                    'by original position.'
                )
                if full
                else 'First use '
                + ("browse_materials" if role == "chief" else "read_application")
                + (
                    ' Read through the targets, objects, controls, implementation stages, and '
                    'readouts to form a complete experimental chain; then continue research '
                    'targeting the topic. This catalog has not yet delivered the main text; the'
                    ' full text can be read back.'
                )
            ),
        }

    def application_basis(self, refs, access):
        """Carry the selected passages in their contiguous original section."""
        selected = {}
        for ref in refs:
            if ref.source_id not in self.application_ids:
                raise ValueError("application_reference_requires_current_application")
            doc = self.store.retained(ref.source_id)
            passage = next((p for p in doc.passages if p.passage_id == ref.passage_id), None)
            if passage is None:
                raise ValueError("unknown_application_passage")
            selected.setdefault(ref.source_id, set()).add(ref.passage_id)
        rows = []
        for source_id, anchors in selected.items():
            doc = self.store.retained(source_id)
            positions = set()
            for index, passage in enumerate(doc.passages):
                if passage.passage_id not in anchors:
                    continue
                # Use document structure, not a generated paraphrase or keyword rule.
                start, end = index, index + 1
                while start and doc.passages[start - 1].heading_path == passage.heading_path:
                    start -= 1
                while end < len(doc.passages) and (
                    doc.passages[end].heading_path == passage.heading_path
                ):
                    end += 1
                positions.update(range(start, end))
            row = {
                "source_id": source_id,
                "title": doc.candidate.title,
                "selected_passage_ids": [
                    p.passage_id for p in doc.passages if p.passage_id in anchors
                ],
                "passages": [
                    p.model_dump(mode="json") for i, p in enumerate(doc.passages) if i in positions
                ],
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
        context = state.task.evaluation
        if context.get("application_basis") == basis:
            return False
        previous = {
            (row["source_id"], passage["passage_id"])
            for row in context.get("application_basis", [])
            for passage in row["passages"]
        }
        added_basis = [
            {**row, "passages": [
                passage for passage in row["passages"]
                if (row["source_id"], passage["passage_id"]) not in previous
            ]}
            for row in basis
        ]
        added_basis = [row for row in added_basis if row["passages"]]
        context["application_basis"] = basis
        added_readings = self.runtime._capture(
            state, access.references.snapshot_choices(selected=False)
        )
        self.runtime._observe(state, {
            "kind": "application_basis_updated", "output": {
                "result": ToolResult(ok=True, content={
                    "application_basis": added_basis,
                    "instruction": (
                        'These original application sentences supplement the planned background'
                        ' for the current question; please continue research incorporating '
                        'existing understanding.'
                    ),
                }).model_dump(mode="json"),
                "readings": added_readings,
            },
        })
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
        access = self._new_access(
            task.responsibility, cutoff, [*self.application_ids, *task.source_ids]
        )
        application = self.application_context(access)
        existing = []
        completed = {row["topic_id"]: row for row in self.completed()}
        for topic_id in ([] if self.runtime.ablations.fixed_research else task.research_ids):
            row = completed[topic_id]
            access.receive(row["readings"])
            access.visible.update(row["source_ids"])
            existing.append(row["view"])
        context = {
            "application_basis": self.application_basis(task.application_refs, access),
            "existing_research": existing,
            "available_sources": [self._material(sid) for sid in task.source_ids],
        }
        if evaluation is not None:
            context["review_context"] = deepcopy(evaluation)
        session_id = self.runtime.create(
            SessionTask(
                role="researcher",
                objective=INITIAL_FOCUS,
                application=application,
                evaluation=context,
                skills=list(dict.fromkeys(skills)),
            ),
            readings=access.references.snapshot_choices(selected=False),
            allowance=allowance,
            inquiry_assignment=task.model_dump(mode="json"),
            inquiry_responsibility=task.responsibility,
        )
        state = self.runtime.load(session_id)
        topic_id = state.inquiry_id
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
        if not self.runtime.ablations.fixed_research:
            access.completed_research = lambda: [
                r for r in self.completed() if r["topic_id"] != topic_id
            ]
        access.save()
        self._access[topic_id] = access
        return topic_id

    def access(self, topic_id):
        self._access[topic_id].objective = self.focus(topic_id)
        return self.configure_access(self._access[topic_id], self.state(topic_id))

    def transfer_owner(
        self, topic_id, *, reason, skills=None, model_role=None, responsibility=None,
    ):
        """Replace an idle owner; the coordinator has already stopped its active segment."""
        previous = self.state(topic_id)
        if responsibility is not None:
            inquiry = self.inquiry(topic_id)
            inquiry.responsibility = responsibility
            self.runtime.store.save_inquiry(inquiry)
        task = previous.task.model_copy(deep=True)
        if skills is not None:
            task.skills = list(dict.fromkeys(skills))
        # Keep public observations' existing E references, then append newer source reads.
        readings = dict(previous.evidence)
        readings.update(self.access(topic_id).references.snapshot_choices(selected=False))
        session_id = self.runtime.create(
            task, readings=readings, inquiry_id=topic_id, model_role=model_role
        )
        state = self.runtime.load(session_id)
        state.observations = deepcopy(previous.observations)
        state.observation_index = previous.observation_index
        self.runtime._observe(state, {
            "kind": "author_handoff", "reason": reason,
            "contribution_ref": self.inquiry(topic_id).current_contribution_ref,
        })
        self.runtime.release_author(previous.session_id)
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

    def source_reading_timeout(self, topic_id):
        budget = self.runtime.budget
        return budget.remaining_seconds(
            session_id=self.inquiry(topic_id).budget_account_id, purpose="research"
        ) if budget else 3600

    def tools(self, topic_id):
        access = self.access(topic_id)
        registry = access.tools()
        registry.include(self.collaboration.tools(topic_id))

        async def read_application(args):
            sid = args.source_id or self.application_ids[0]
            if sid not in self.application_ids:
                return ToolResult(ok=False, error="current_application_required")
            selection = (
                ReadPassages(
                    source_id=sid, passage_ids=args.passage_ids, reading_tokens=args.reading_tokens
                )
                if args.passage_ids
                else BrowseMaterials(
                    source_id=sid,
                    section=args.section,
                    continuation=args.continuation,
                    start_passage_id=args.start_passage_id,
                    reading_tokens=args.reading_tokens,
                )
            )
            return await access.read_materials(selection)

        registry.add(
            Tool(
                "read_application",
                (
                    'Continue reading this project proposal by position or chapter, retaining '
                    'the original application sentences.'
                ) + access.reading_hint,
                ReadApplication,
                access._tool_handler(read_application),
            )
        )
        if self.runtime.ablations.source_reading_enabled:
            return self.reader.attach(topic_id, registry)
        return registry

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
                (
                    'acquire_source(reading_purpose): obtain relevant original texts; '
                    'read_materials / browse_materials: continue reading;'
                )
                + ((
                    'read_source_for_question: delegate readers as needed; read_source_notes: '
                    'existing explanations'
                )
                   if self.runtime.ablations.source_reading_enabled else "")
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
        state, access = self.state(topic_id), self.access(topic_id)
        ref = note_ref or self._visible_contribution_ref(topic_id)
        artifact = self.runtime.store.contribution(topic_id, ref) if ref else None
        records = plain(access.reading_records())
        source_readings = self.reader.results(topic_id)
        readings = delivery_readings(
            access, artifact=artifact, additional=(row["readings"] for row in source_readings),
        )
        for row in source_readings:
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
            responsibility=inquiry.responsibility,
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
