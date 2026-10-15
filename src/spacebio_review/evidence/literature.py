"""Question-directed discovery, reading and a sourced knowledge answer."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

from spacebio_review.agents.context import LITERATURE_INSTRUCTION, MaterialAccess
from spacebio_review.agents.reading import PassageSelection, ReadingWindow
from spacebio_review.agents.types import AgentTask, ContextView, Material, RoleProfile
from spacebio_review.models.embedding import DashScopeEmbedding, VectorIndex
from spacebio_review.reference_contract import SourceScope
from spacebio_review.tools import Tool, ToolRegistry, ToolResult

from .discovery import DiscoveryResult
from .knowledge import (
    AcquireSource,
    KnowledgeAnswer,
    KnowledgeQuestion,
    KnowledgeRun,
    LiteratureConfig,
    ReadingRecord,
    ReadSource,
    ReadSources,
    RelatedPapers,
    RetrieveKnowledge,
    SearchSources,
)
from .retrieval import find_passages, hybrid_rank
from .session import SessionModel, VisualRequest
from .submissions import (
    LITERATURE_CONTRACT_VERSION,
    LITERATURE_SUBMISSION_PROMPT,
    scientific_contract,
)
from .types import SourceCandidate


def literature_task(research_id, config):
    """The production knowledge task, also used by isolated formal-answer probes."""
    return (
        AgentTask(
            task_id=research_id,
            kind="literature",
            phase="knowledge_research",
            mode="research",
            objective=(
                "回答当前知识问题。围绕用途和所需信息搜读，说明资料的对象、方法、条件及认识。"
                "形成可交回的认识后，以普通响应结束研究，"
                "由正式阶段组织 findings、scope、unknowns。"
            ),
            expected_product="KnowledgeDraft：findings、scope、unknowns",
            max_actions=config.max_actions,
            max_model_turns=config.max_model_turns,
            reserve_actions=0,
            reserve_model_turns=0,
            max_repairs=0,
            max_truncation_recoveries=0,
            context_soft_tokens=config.context_soft_tokens,
        ),
        RoleProfile(
            agent_id=research_id,
            responsibility="理解外部研究的对象、方法、观测与条件，回答当前知识问题。",
            required_skills=["literature-understanding"],
        ),
    )


async def read_literature_image(runtime, store, doc, args, references, *, background=""):
    """Interpret provided source pixels with one read-qualified evidence snapshot."""
    from typing import Literal

    from pydantic import BaseModel

    class VisualAnswer(BaseModel):
        readability: Literal["readable", "partial", "unreadable"]
        interpretation: str

    visual = next((v for v in doc.visuals if v.visual_id == args.visual_id), None)
    if visual is None:
        return ToolResult(ok=False, error="visual_not_found")
    pixels, mime = await store.visual_bytes(args.source_id, visual)
    child_id = "literature-visual-" + uuid4().hex[:8]
    result = await runtime.run_task(
        AgentTask(
            task_id=child_id,
            kind="visual",
            phase="literature_visual",
            mode="direct",
            objective=args.question,
            expected_product="VisualAnswer",
            max_repairs=0,
            max_truncation_recoveries=0,
            max_model_turns=1,
        ),
        RoleProfile(
            agent_id=child_id,
            responsibility=(
                "先依据实际像素读取图例、坐标、单位与标记关系，再解释研究问题。"
                "比较括号按两个端点对应的组别读取；说明可辨读数与不能分辨的范围。"
                "将图像观察与科学解释分别说明。"
            ),
        ),
        ContextView(
            agent_id=child_id,
            phase="literature_visual",
            materials=[
                Material(
                    source_id=args.source_id,
                    text=(
                        f"研究问题：{background}\n图像位置：{visual.locator}\n图注：{visual.caption}"
                    ),
                    image_urls=[f"data:{mime};base64," + base64.b64encode(pixels).decode()],
                )
            ],
        ),
        ToolRegistry(),
        VisualAnswer,
    )
    ok = bool(
        result.product
        and result.status == "completed"
        and result.product["readability"] != "unreadable"
    )
    if ok:
        references.capture(
            args.source_id,
            visual_id=args.visual_id,
            text=json.dumps(result.product, ensure_ascii=False, sort_keys=True),
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
            "interpretation": result.product,
        },
        source_ids=[args.source_id],
        reading_scope="delegated_image_read" if ok else "image_not_read",
        error=None if ok else "image_unreadable",
    )


class KnowledgeSession:
    """Private read ledger and observations; no source/opinion leakage between questions."""

    def __init__(self, researcher, question, *, whole_units=True):
        self.researcher, self.question = researcher, question.model_copy(deep=True)
        self.store, self.discovery = researcher.store, researcher.discovery
        self.runtime, self.index = researcher.runtime, researcher.index
        self.model = self.runtime.models["task/rerank/normal"]
        self.visible = set(question.existing_source_ids)
        self.read: dict[str, set[str]] = {}
        self.images: dict[str, set[str]] = {}
        self.channels, self.limitations = [], []
        self._metadata: dict[str, str] = {}
        self._read_documents = {}
        self.whole_units = whole_units
        self.reading = ReadingWindow(whole_units=whole_units)
        self._retrievals = {}
        self.state = SimpleNamespace(stop_reason="")
        self.closing = False
        self.closing_reason = ""
        self.claim_model_turn = None
        self.actions = 0
        self.references = SourceScope(
            self.store, lambda: self.visible, self.read_locations, cutoff=self.question.cutoff_date
        )
        for handle, item in getattr(question, "application_statements", {}).items():
            ref = item["reference"]
            self.visible.add(ref["source_id"])
            if ref.get("passage_id"):
                self.read.setdefault(ref["source_id"], set()).add(ref["passage_id"])
            if ref.get("visual_id"):
                self.images.setdefault(ref["source_id"], set()).add(ref["visual_id"])
            self.references.snapshots[handle] = deepcopy(item)

    def read_locations(self):
        return {(sid, pid, None) for sid, pids in self.read.items() for pid in pids} | {
            (sid, None, vid) for sid, vids in self.images.items() for vid in vids
        }

    def receive_knowledge(self, run):
        """The delivered answer contains these exact originals; directories alone are not reads."""
        for handle, item in run.answer.evidence_resolution.items():
            ref = item["reference"]
            sid = ref["source_id"]
            self.visible.add(sid)
            self.references.snapshots[handle] = deepcopy(item)
            if ref.get("passage_id"):
                self.read.setdefault(sid, set()).add(ref["passage_id"])
                if item.get("reading_scope") == "metadata/abstract":
                    self._metadata[sid + "/" + ref["passage_id"]] = item["quote"]
            if ref.get("visual_id"):
                self.images.setdefault(sid, set()).add(ref["visual_id"])
            doc = self.store.retained(sid)
            if doc:
                self._read_documents[sid] = doc

    def progress(self):
        return {
            "read_sources": len(self.read),
            "stop_reason": self.state.stop_reason,
            "instruction": "回答本问题已有的认识、适用条件和未知；资料位置已自动保存。",
        }

    def record_context_read(self, payload):
        sid = payload.get("source_id")
        doc = self.store.retained(sid) if sid else None
        if not doc or not doc.candidate.eligible(self.question.cutoff_date):
            return
        valid = {p.passage_id for p in doc.passages}
        read = {
            p["passage_id"] for p in payload.get("passages", []) if p.get("passage_id") in valid
        }
        if read:
            self.visible.add(sid)
            self.read.setdefault(sid, set()).update(read)
            self._read_documents[sid] = doc
            self.references.capture_passages(
                sid,
                [p for p in payload.get("passages", []) if p.get("passage_id") in read],
                candidate=doc.candidate,
            )

    def before_batch(self):
        pass

    def after_batch(self, exploration):
        pass

    def _eligible(self, source_id):
        if source_id not in self.visible:
            raise ValueError("source_not_visible")
        if not self.store.candidate(source_id).eligible(self.question.cutoff_date):
            raise ValueError("source_after_cutoff")

    async def _document(self, source_id, material_id=None):
        self._eligible(source_id)
        doc = await self.store.acquire(
            source_id, material_id=material_id, cutoff=self.question.cutoff_date
        )
        # Dates from original content are checked again, including retained full text.
        if not doc.candidate.eligible(self.question.cutoff_date):
            raise ValueError("original_after_cutoff")
        self._read_documents[source_id] = doc
        return doc

    async def _candidates(
        self,
        results,
        queries,
        limit,
        search_origins=None,
        *,
        retained_ids=None,
        include_knowledge=False,
    ):
        ids = []
        provenance = {}
        for position, result in enumerate(results):
            self.limitations.extend(result.limitations)
            self.channels.append({"stage": "discovery", **result.channels})
            for candidate_index, candidate in enumerate(result.candidates):
                if candidate.eligible(self.question.cutoff_date):
                    sid = (
                        retained_ids[position][candidate_index]
                        if retained_ids and position in retained_ids
                        else self.store.register(candidate)
                    )
                    self.visible.add(sid)
                    ids.append(sid)
                    if search_origins:
                        origins = provenance.setdefault(sid, [])
                        origin = search_origins[position]
                        if origin not in origins:
                            origins.append(origin)
        ids = [
            sid
            for sid in dict.fromkeys(ids)
            if self.store.candidate(sid).eligible(self.question.cutoff_date)
        ]
        candidates = [self.store.candidate(sid) for sid in ids]
        completed = (
            [
                run
                for run in self.researcher.completed_runs().values()
                if self.researcher.eligible_run(run, self.question.cutoff_date)
            ]
            if include_knowledge
            else []
        )
        prior_text = {
            sid: "\n".join(
                json.dumps(
                    {
                        "question": run.question.question,
                        "findings": run.answer.answer,
                        "scope": run.answer.scope.model_dump() if run.answer.scope else None,
                        "unknowns": run.answer.unknowns,
                    },
                    ensure_ascii=False,
                )
                for run in completed
                if sid in run.sources
            )
            for sid in ids
        }
        order, channels, notes, origins = await hybrid_rank(
            [
                c.title + "\n" + c.abstract + "\n" + prior_text[sid]
                for sid, c in zip(ids, candidates)
            ],
            queries,
            SessionModel(self) if self.researcher.rerank else None,
            index=self.index,
            limit=limit,
            candidate_context=[{"search_origins": provenance.get(sid, [])} for sid in ids],
        )
        self.channels.append({"stage": "candidate_recall", **channels})
        self.limitations.extend(notes)
        rows = []
        for i in order:
            sid, c = ids[i], candidates[i]
            # Metadata is an explicit citable reading scope, never original full text.
            excerpt = c.abstract[:4000]
            metadata_id = "abstract-" + hashlib.sha256(excerpt.encode()).hexdigest()[:10]
            if excerpt:
                self._metadata[sid + "/" + metadata_id] = excerpt
                self.read.setdefault(sid, set()).add(metadata_id)
                self.references.capture(
                    sid,
                    metadata_id,
                    text=excerpt,
                    locator="Metadata abstract",
                    kind="metadata/abstract",
                    candidate=c,
                )
            rows.append(
                {
                    "source_id": sid,
                    **c.model_dump(mode="json", exclude={"abstract"}),
                    "abstract": excerpt,
                    "abstract_passage_id": metadata_id if excerpt else None,
                    "abstract_truncated": len(c.abstract) > len(excerpt),
                    "date_unknown": c.publication_date is None,
                    "related_versions": self.store.related_versions(sid),
                    "recall_channels": origins[i],
                    "search_origins": provenance.get(sid, []),
                    "acquire_with": "acquire_source",
                    "browse_with": "browse_materials",
                }
            )
        knowledge = []
        if include_knowledge:
            chosen = {ids[i] for i in order}
            for run in completed:
                if chosen.intersection(run.sources):
                    knowledge.append(self.researcher.knowledge_view(run))
                    self.receive_knowledge(run)
            for row in rows:
                row["retained_material"] = self.researcher.material_overview(row["source_id"])
        return ToolResult(
            ok=True,
            content={
                "candidates": rows,
                "channels": channels,
                "discovery_channels": [r.channels for r in results],
                "limitations": [n for r in results for n in r.limitations] + notes,
                "candidate_count": len(ids),
                **({"existing_research": knowledge} if include_knowledge else {}),
            },
            source_ids=[ids[i] for i in order],
            reading_scope="discovery_metadata_and_selected_abstracts",
        )

    async def search(self, args: SearchSources):
        if self.closing:
            return ToolResult(ok=False, error="research_closing")

        project_ids = (
            [
                row["source_id"]
                for row in self.store.catalogue()
                if self.store.candidate(row["source_id"]).kind != "application"
            ]
            if any(q.provider == "project" for q in args.queries)
            else []
        )

        async def discover(query):
            if query.provider == "project":
                return DiscoveryResult(
                    candidates=[self.store.candidate(sid) for sid in project_ids],
                    channels={"project": "retained_catalogue"},
                )
            return await self.discovery.search(
                query.query, query.provider, self.question.cutoff_date, args.limit
            )

        results = await asyncio.gather(*(discover(q) for q in args.queries))
        return await self._candidates(
            results,
            [q.query for q in args.queries],
            args.limit,
            [q.model_dump() for q in args.queries],
            retained_ids={
                i: project_ids for i, q in enumerate(args.queries) if q.provider == "project"
            },
            include_knowledge=any(q.provider == "project" for q in args.queries),
        )

    async def related(self, args: RelatedPapers):
        if self.closing:
            return ToolResult(ok=False, error="research_closing")
        seeds = []
        for sid in args.source_ids:
            self._eligible(sid)
            c = self.store.candidate(sid)
            seed = c.identifiers.get("semantic_scholar")
            if not seed and c.identifiers.get("doi"):
                seed = "DOI:" + c.identifiers["doi"]
            if not seed and c.identifiers.get("arxiv"):
                seed = "ARXIV:" + c.identifiers["arxiv"]
            if seed:
                seeds.append(seed)
        if not seeds:
            return ToolResult(ok=False, error="no_supported_seed_identifier")
        result = await self.discovery.related(
            seeds, args.relation, self.question.cutoff_date, args.limit
        )
        return await self._candidates([result], [self.question.question], args.limit)

    async def _read(self, args: ReadSource):
        try:
            sid = args.source_id
            if sid is None:
                if not args.url or self.closing:
                    raise ValueError("source_id_or_public_url_required")
                from .discovery import public_url

                if not public_url(args.url):
                    raise ValueError("public_url_required")
                sid = self.store.register(
                    SourceCandidate(
                        title=args.url, kind="web", url=args.url, fulltext_urls=[args.url]
                    )
                )
                self.visible.add(sid)
            if self.closing and sid not in self._read_documents:
                raise ValueError("research_closing")
            doc = await self._document(sid)
            passages = [
                p
                for p in doc.passages
                if (args.section is None or p.section == args.section)
                and (not args.passage_ids or p.passage_id in args.passage_ids)
            ]
            chosen = passages[args.start : args.start + args.count]
            self.read.setdefault(sid, set()).update(p.passage_id for p in chosen)
            self.references.capture_passages(sid, chosen, candidate=doc.candidate)
            self.limitations.extend(doc.limitations)
            return {
                "source_id": sid,
                "availability": doc.availability,
                "date_unknown": doc.candidate.publication_date is None,
                "passages": [p.model_dump() for p in chosen],
                "sections": list(dict.fromkeys(p.section for p in doc.passages)),
                "visuals": [v.model_dump(exclude={"path", "url"}) for v in doc.visuals],
                "next_start": args.start + len(chosen)
                if args.start + len(chosen) < len(passages)
                else None,
                "limitations": doc.limitations,
            }
        except (ValueError, KeyError) as exc:
            reason = str(exc)
            self.limitations.append(reason)
            return {"source_id": args.source_id, "error": reason}

    async def acquire_source(self, args):
        sid = args.source_id
        if not sid:
            from .discovery import public_url

            if not args.url or not public_url(args.url):
                return ToolResult(ok=False, error="public_url_required")
            sid = self.store.register(
                SourceCandidate(title=args.url, kind="web", url=args.url, fulltext_urls=[args.url])
            )
            self.visible.add(sid)
        try:
            doc = await self._document(sid, args.material_id)
        except (KeyError, ValueError) as exc:
            return ToolResult(ok=False, error=str(exc))
        result = ToolResult(
            ok=True,
            source_ids=[sid],
            content={
                "source_id": sid,
                "title": doc.candidate.title,
                "abstract": doc.candidate.abstract,
                "availability": doc.availability,
                "sections": list(dict.fromkeys(p.section for p in doc.passages)),
                "materials": [
                    {
                        "material_id": m.material_id,
                        "role": m.role,
                        "title": m.title,
                        "availability": m.availability,
                        "url": m.url,
                        "passage_count": len(m.passage_ids),
                        "visual_ids": m.visual_ids,
                        "limitations": m.limitations,
                    }
                    for m in doc.materials
                ],
                "passage_directory": [
                    {"passage_id": p.passage_id, "section": p.section}
                    for p in doc.passages[args.start : args.start + args.count]
                ],
                "total_passages": len(doc.passages),
                "next_start": args.start + args.count
                if args.start + args.count < len(doc.passages)
                else None,
                "visuals": [v.model_dump(exclude={"path", "url"}) for v in doc.visuals],
                "read_with": "read_materials",
                "browse_with": "browse_materials",
                "instruction": "仅取得材料和目录，正文尚未读取。",
            },
        )
        if args.reading_purpose:
            readable = doc
            if args.material_id:
                material = next(m for m in doc.materials if m.material_id == args.material_id)
                readable = doc.model_copy(
                    update={
                        "passages": [
                            p for p in doc.passages if p.passage_id in material.passage_ids
                        ]
                    }
                )
            reading = await self.retrieve(
                RetrieveKnowledge(source_ids=[sid], queries=[args.reading_purpose]),
                documents=[readable],
            )
            result.content["reading"] = reading.content
            result.content["instruction"] = (
                "已返回本次目的相关原文；按章节、图像或continuation继续阅读。"
            )
            result.reading_scope = reading.reading_scope
        return result

    async def material(self, source_id):
        try:
            self.store.candidate(source_id)
        except KeyError:
            return None  # Synthetic, task-visible question/plan products use the generic reader.
        try:
            self._eligible(source_id)
        except (KeyError, ValueError) as exc:
            return {"error": str(exc)}
        doc = self.store.retained(source_id)
        if doc and not doc.candidate.eligible(self.question.cutoff_date):
            return {"error": "original_after_cutoff"}
        from .documents import reading_passages

        rows = reading_passages(doc, self.whole_units) if doc else []
        rows += [
            {"passage_id": key.split("/", 1)[1], "section": "Metadata abstract", "text": text}
            for key, text in self._metadata.items()
            if key.startswith(source_id + "/")
        ]
        return {"passages": rows} if rows else {"error": "acquire_source_first"}

    async def read_sources(self, args: ReadSources):
        rows = await asyncio.gather(*(self._read(r) for r in args.reads))
        return ToolResult(
            ok=any("error" not in r for r in rows),
            content={"sources": rows},
            source_ids=[r["source_id"] for r in rows if "error" not in r],
            reading_scope="selected_passages",
        )

    async def retrieve(self, args: RetrieveKnowledge, *, documents=None):
        if self.closing:
            return ToolResult(ok=False, error="research_closing")
        docs, errors = list(documents or []), []
        for sid in () if documents is not None else dict.fromkeys(args.source_ids):
            try:
                docs.append(await self._document(sid))
            except (KeyError, ValueError) as exc:
                errors.append(f"{sid}: {exc}")
        key = hashlib.sha256(
            json.dumps(
                {
                    "request": args.model_dump(mode="json"),
                    "cutoff": str(self.question.cutoff_date),
                    "sources": [d.model_dump(mode="json") for d in docs],
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        reused = key in self._retrievals
        if not reused:
            self._retrievals[key] = await find_passages(
                docs,
                args.queries,
                SessionModel(self) if self.researcher.rerank else None,
                index=self.index,
                limit=args.limit,
                neighbors=args.neighbors,
            )
        result = deepcopy(self._retrievals[key])
        result["reused"] = reused
        positions = {}
        for hit in result.pop("hits"):
            positions.setdefault(hit["source_id"], []).append(hit["passage"]["passage_id"])
        pages, remaining = [], self.reading.budget
        for sid, ids in positions.items():
            material = await self.material(sid)
            page = self.reading.read(
                PassageSelection(source_id=sid, passage_ids=ids),
                **material,
                budget=remaining,
            )
            remaining -= page["input_tokens_estimate"]
            self.record_context_read(page)
            pages.append(page)
        result["sources"] = pages
        result["continue_with"] = (
            "browse_materials using source_id and continuation; no reranking needed"
        )
        result["limitations"].extend(errors)
        self.channels.append({"stage": "passage_recall", **result.get("channels", {})})
        self.limitations.extend(result["limitations"])
        return ToolResult(
            ok=True,
            content=result,
            source_ids=[d.source_id for d in docs],
            reading_scope="selected_passages",
        )

    async def visual(self, args: VisualRequest):
        if self.closing or (self.claim_model_turn and not self.claim_model_turn()):
            return ToolResult(ok=False, error="research_closing")
        doc = await self._document(args.source_id)
        result = await read_literature_image(
            self.runtime,
            self.store,
            doc,
            args,
            self.references,
            background=self.question.question,
        )
        if result.ok:
            self.images.setdefault(args.source_id, set()).add(args.visual_id)
        return result

    def tools(self):
        def bounded(handler):
            async def invoke(args):
                if self.closing or self.actions >= self.researcher.config.max_actions:
                    self.state.stop_reason = "action_budget_exhausted"
                    return ToolResult(ok=False, error="research_closing")
                self.actions += 1
                return await handler(args)

            return invoke

        registry = ToolRegistry(
            [
                Tool(
                    "search_sources",
                    "批量查询所选入口，返回相关题录摘要和阅读入口。",
                    SearchSources,
                    bounded(self.search),
                    exploration=True,
                    timeout_seconds=300,
                ),
                Tool(
                    "acquire_source",
                    "按 reading_purpose 取得并读原文，返回章节、图表及附件目录；留空仅看目录。",
                    AcquireSource,
                    bounded(self.acquire_source),
                    exploration=True,
                    timeout_seconds=300,
                ),
                Tool(
                    "retrieve_knowledge",
                    "对所选来源全部可读段落作独立词法/向量召回并返回邻段。",
                    RetrieveKnowledge,
                    bounded(self.retrieve),
                    exploration=True,
                    timeout_seconds=300,
                ),
                Tool(
                    "related_papers",
                    "从已选种子作一次推荐或引用关系扩展。",
                    RelatedPapers,
                    bounded(self.related),
                    exploration=True,
                    timeout_seconds=300,
                ),
                Tool(
                    "read_literature_visual",
                    "委派视觉模型读取实际图像，返回有范围解释。",
                    VisualRequest,
                    bounded(self.visual),
                    exploration=True,
                    delegates=True,
                    timeout_seconds=300,
                ),
            ],
            inquiry=self,
        )
        registry.material_providers.append(self.material)
        registry.reading_windows.append(self.reading)
        registry.read_observers.append(self.record_context_read)
        self.references.attach(registry, "read_knowledge_references")
        return registry

    def reading_records(self):
        return {
            sid: ReadingRecord(
                source=(
                    self._read_documents[sid].candidate
                    if sid in self._read_documents
                    else self.store.candidate(sid)
                ),
                passage_ids=sorted(self.read.get(sid, set())),
                visual_ids=sorted(self.images.get(sid, set())),
                availability=self._read_documents[sid].availability
                if sid in self._read_documents
                else "metadata",
                related_versions=self.store.related_versions(sid),
            )
            for sid in self.read.keys() | self.images.keys()
        }


class LiteratureResearcher:
    def __init__(self, runtime, store, discovery, *, config=None, index=None, rerank=True):
        if not runtime.config or "literature" not in runtime.config.task_routes:
            raise ValueError("Literature task routes required")
        from spacebio_review.agents.diagnostics import SubmissionRecorder

        self.runtime, self.store, self.discovery = runtime, store, discovery
        if runtime.recorder.root is None:
            runtime.recorder = SubmissionRecorder(store.root.parent, runtime.recorder.max_bytes)
        self.config = config or LiteratureConfig()
        self.index, self.rerank = index, rerank
        self._completed: dict[str, KnowledgeRun] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._owned = None

    @classmethod
    def from_resources(cls, evidence, *, env_file=".env", embedder=None):
        runtime = evidence.runtime
        owned = embedder is None and runtime.config.embedding is not None
        if owned:
            embedder = DashScopeEmbedding(
                runtime.config.embedding, runtime.scheduler, env_file=env_file
            )
        service = cls(
            runtime,
            evidence.store,
            evidence.discovery,
            config=evidence.config.literature,
            index=VectorIndex(embedder, cache_dir=getattr(embedder.config, "cache_dir", None))
            if embedder
            else None,
            rerank=evidence.config.model_rerank,
        )
        service._owned = embedder if owned else None
        return service

    async def close(self):
        if self._owned:
            await self._owned.close()

    def new_session(self, question, *, whole_units=True):
        return KnowledgeSession(self, question, whole_units=whole_units)

    def completed_runs(self):
        """Completed answers in this run, including work saved by another researcher."""
        runs = {}
        for path in sorted((self.store.root.parent / "knowledge").glob("*/answer.json")):
            run = KnowledgeRun.model_validate_json(path.read_text("utf-8"))
            if run.answer is not None and run.task_result.status == "completed":
                runs[run.research_id] = run
        return runs

    def history(self, research_id):
        if not research_id:
            return []
        runs, history, seen = self.completed_runs(), [], set()
        while research_id:
            if research_id in seen or research_id not in runs:
                raise ValueError("completed_previous_research_required")
            seen.add(research_id)
            run = runs[research_id]
            history.append(run)
            research_id = run.previous_research_id
        return list(reversed(history))

    def eligible_run(self, run, cutoff):
        return all(
            self.store.candidate(item["reference"]["source_id"]).eligible(cutoff)
            and (
                (doc := self.store.retained(item["reference"]["source_id"])) is None
                or doc.candidate.eligible(cutoff)
            )
            for item in run.answer.evidence_resolution.values()
        )

    def material_overview(self, source_id):
        doc = self.store.retained(source_id)
        return {
            "source_id": source_id,
            "title": self.store.candidate(source_id).title,
            "availability": doc.availability if doc else "metadata",
            "sections": list(dict.fromkeys(p.section for p in doc.passages)) if doc else [],
            "materials": [
                m.model_dump(include={"material_id", "role", "title", "availability"})
                for m in doc.materials
            ]
            if doc
            else [],
            "read_with": "acquire_source(reading_purpose), read_materials, browse_materials",
        }

    def knowledge_view(self, run):
        return {
            "research_id": run.research_id,
            "study_id": run.study_id or run.research_id,
            "question": run.question.model_dump(mode="json"),
            "answer": run.answer_view(),
            "reading_records": {
                sid: record.model_dump(exclude={"source"}) for sid, record in run.sources.items()
            },
            "materials": [self.material_overview(sid) for sid in run.sources],
            "provenance": "previous_research_with_originals; interpret_for_current_question",
        }

    async def investigate(
        self,
        question: KnowledgeQuestion,
        *,
        context: ContextView | None = None,
        whole_units=True,
        single_submission=False,
    ):
        question = question.model_copy(deep=True)
        history = self.history(question.previous_research_id)
        if history:
            previous = history[-1]
            if previous.question.cutoff_date:
                question.cutoff_date = min(
                    question.cutoff_date or previous.question.cutoff_date,
                    previous.question.cutoff_date,
                )
            if not all(self.eligible_run(run, question.cutoff_date) for run in history):
                raise ValueError("previous_research_after_cutoff")
            question.existing_source_ids = list(
                dict.fromkeys(
                    question.existing_source_ids + [sid for run in history for sid in run.sources]
                )
            )
        # Exact compatibility is intentionally conservative; no semantic answer cache.
        visible = context.visible_materials() if context else []
        snapshots = []
        for sid in question.existing_source_ids:
            try:
                doc = self.store.retained(sid)
                snapshots.append(
                    (
                        sid,
                        self.store.candidate(sid).model_dump(mode="json"),
                        doc.model_dump(mode="json") if doc else None,
                    )
                )
            except KeyError:
                snapshots.append((sid, "unavailable"))
        key = hashlib.sha256(
            json.dumps(
                {
                    "question": question.model_dump(mode="json"),
                    "answer_contract": LITERATURE_CONTRACT_VERSION,
                    "research_flow": "continuous-literature-1",
                    "history": [run.model_dump(mode="json") for run in history],
                    "whole_units": whole_units,
                    "instructions": {
                        "context": LITERATURE_INSTRUCTION,
                        "submission": LITERATURE_SUBMISSION_PROMPT,
                        "method": self.runtime.skills.get("literature-understanding").body,
                        "task": literature_task("knowledge", self.config)[0].objective,
                    },
                    "single_submission": single_submission,
                    "configuration": self.config.model_dump(mode="json"),
                    "runtime": self.runtime.config.model_dump(mode="json"),
                    "context": [m.model_dump(mode="json") for m in visible],
                    "sources": snapshots,
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        async with self._locks.setdefault(key, asyncio.Lock()):
            if key in self._completed:
                cached = self._completed[key].model_copy(deep=True)
                # Recheck dates after other tasks enrich shared metadata.
                if all(
                    self.store.candidate(sid).eligible(question.cutoff_date)
                    for sid in cached.sources
                ):
                    cached.reused = True
                    return cached
            run = await self._investigate(
                question, context, whole_units=whole_units, single_submission=single_submission
            )
            if run.answer is not None and run.task_result.status == "completed":
                self._completed[key] = run.model_copy(deep=True)
            return run

    @property
    def minimum_research_turns(self):
        return self.minimum_turns()

    def minimum_turns(self, *, single_submission=False):
        return 2  # One research turn and one formal answer.

    async def _investigate(self, question, context, *, whole_units=True, single_submission=False):
        research_id = "research-" + uuid4().hex[:12]
        reserve = 1
        with self.runtime.scheduler.research_calls(
            research_id, self.config.max_model_turns, reserve
        ):
            return await self._investigate_scoped(
                question,
                context,
                research_id,
                whole_units=whole_units,
                single_submission=single_submission,
            )

    async def _investigate_scoped(
        self, question, context, research_id, *, whole_units=True, single_submission=False
    ):
        session = self.new_session(question, whole_units=whole_units)
        history = self.history(question.previous_research_id)
        view = (
            MaterialAccess(context).delegate(
                [m.source_id for m in context.visible_materials()],
                research_id,
                "knowledge_research",
            )
            if context
            else ContextView(agent_id=research_id, phase="knowledge_research")
        )
        view.materials.append(
            Material(
                source_id="knowledge-question",
                data=question.model_dump(mode="json"),
                essential=True,
            )
        )
        view.materials.append(
            Material(
                source_id="discovery-options",
                data={"enabled": ["project", *self.discovery.config.enabled]},
                essential=True,
            )
        )
        for previous in history:
            session.receive_knowledge(previous)
            view.materials.append(
                Material(
                    source_id=previous.research_id,
                    data=self.knowledge_view(previous),
                    essential=True,
                )
            )
        tools = session.tools()

        task, profile = literature_task(research_id, self.config)
        profile.allowed_tools = [s["function"]["name"] for s in tools.schemas()]
        result = await self.runtime.run_task(
            task,
            profile,
            view,
            tools,
            scientific_contract(session.references, KnowledgeAnswer, session=session),
        )
        answer = (
            KnowledgeAnswer.model_validate(result.product)
            if result.status == "completed" and result.product
            else None
        )
        if result.citation_issues:
            session.limitations.append(
                f"{len(result.citation_issues)} citation links could not be verified; "
                "the associated explanations remain provisional, not source-confirmed."
            )
        run = KnowledgeRun(
            research_id=research_id,
            study_id=(history[0].study_id or history[0].research_id) if history else research_id,
            previous_research_id=question.previous_research_id,
            question=question,
            answer=answer,
            task_result=result,
            sources=session.reading_records(),
            channels=session.channels,
            limitations=list(dict.fromkeys(session.limitations)),
        )
        self._save_run(run, session)
        return run

    def _save_run(self, run, session):
        from spacebio_review.v4.records import write_json

        folder = self.store.root.parent / "knowledge" / run.research_id
        folder.mkdir(parents=True, exist_ok=True)
        payload = run.model_dump(mode="json")
        payload["answer"] = run.answer_view()
        (folder / "metadata-readings.json").write_text(
            json.dumps(session._metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (folder / "evidence-readings.json").write_text(
            json.dumps(session.references.snapshot_choices(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Publishing the answer last makes a completed attempt visible with all its records.
        write_json(folder / "answer.json", payload)

    def investigate_tool(self, *, context=None, cutoff_date=None):
        """Caller binds readable project material and the assessment's historical horizon."""

        async def investigate(question):
            question = question.model_copy(deep=True)
            if cutoff_date:
                question.cutoff_date = min(question.cutoff_date or cutoff_date, cutoff_date)
            run = await self.investigate(question, context=context)
            return ToolResult(
                ok=run.answer is not None,
                content={
                    "research_id": run.research_id,
                    "study_id": run.study_id,
                    "previous_research_id": run.previous_research_id,
                    "question": run.question.model_dump(mode="json"),
                    "answer": run.answer_view(),
                    "status": run.task_result.status,
                    "reason": run.task_result.reason,
                    "submission_diagnostics": run.task_result.submission_diagnostics,
                    "candidate_id": run.task_result.candidate_id,
                    "candidate_path": run.task_result.candidate_path,
                    "sources": {
                        sid: s.model_dump(
                            mode="json", exclude={"source": {"abstract", "fulltext_urls"}}
                        )
                        for sid, s in run.sources.items()
                    },
                    "channels": run.channels,
                    "limitations": run.limitations,
                    "reused": run.reused,
                },
                source_ids=list(run.sources),
                reading_scope="knowledge_answer",
                error=None if run.answer else run.task_result.reason,
            )

        return Tool(
            "research_knowledge",
            "委派独立文献研究，返回知识回答、适用条件与已读来源。",
            KnowledgeQuestion,
            investigate,
            exploration=True,
            delegates=True,
            timeout_seconds=3600,
        )
