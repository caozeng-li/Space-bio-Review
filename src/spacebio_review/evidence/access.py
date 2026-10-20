"""Shared source discovery and reading with one session-local reading ledger.

Stores are shared within a run. A SourceAccess instance tracks only originals actually
returned to its author; downloading or discovering a source does not read its body.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote, urlsplit

from spacebio_review.agents.reading import (
    BrowseMaterials,
    PassageSelection,
    ReadingWindow,
    ReadPassages,
    reading_output,
)
from spacebio_review.persistence import write_json
from spacebio_review.reference_contract import SourceScope
from spacebio_review.tools import Tool, ToolRegistry, ToolResult

from .discovery import DiscoveryResult
from .knowledge import (
    AcquireSource,
    ReadingRecord,
    RelatedPapers,
    RetrieveKnowledge,
    SearchSources,
)
from .navigation import select_reading_start, source_navigation
from .reading_render import reading_units
from .retrieval import find_passages, hybrid_rank
from .session import VisualRequest
from .types import SourceCandidate

ORIGINAL_PAGE_READING = (
    "本次实际读取的完整单元有对应原页时，同时交付原页图像与自动解析文字，"
    "结合两者理解对象、行列与条件关系。"
)


def _url_candidate(url):
    """Keep explicit scholarly URL identities on the existing acquisition path."""
    parts = urlsplit(url)
    host, path = (parts.hostname or "").lower(), unquote(parts.path)
    ids = {}
    if host in {"doi.org", "dx.doi.org", "www.doi.org"}:
        if re.fullmatch(r"/10\.\d{4,9}/\S+", path, re.I):
            ids["doi"] = path[1:]
    elif host == "pmc.ncbi.nlm.nih.gov":
        if match := re.fullmatch(r"/articles/(PMC\d+)(?:/.*)?", path, re.I):
            ids["pmcid"] = match[1]
    elif host in {"europepmc.org", "www.europepmc.org"}:
        if match := re.fullmatch(r"/(?:articles|article/PMC)/(PMC\d+)/?", path, re.I):
            ids["pmcid"] = match[1]
        elif match := re.fullmatch(r"/article/MED/(\d+)/?", path, re.I):
            ids["pmid"] = match[1]
    elif host == "pubmed.ncbi.nlm.nih.gov":
        if match := re.fullmatch(r"/(\d+)/?", path):
            ids["pmid"] = match[1]
    elif host in {"ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov"}:
        if match := re.fullmatch(r"/pmc/articles/(PMC\d+)(?:/.*)?", path, re.I):
            ids["pmcid"] = match[1]
        elif match := re.fullmatch(r"/pubmed/(\d+)/?", path):
            ids["pmid"] = match[1]
    return SourceCandidate(
        title=url,
        kind="literature" if ids else "web",
        identifiers=ids,
        url=url,
        fulltext_urls=[url],
    )


class SourceAccess:
    """Application and external originals, independent of any research-agent lifecycle.

    completed_research is a synchronous callback returning current project research
    dictionaries with title, objective, body, source_ids and readings (exact originals).
    visual_reader is an async callable(doc, args, references, *, background).
    """

    def __init__(
        self,
        store,
        discovery,
        *,
        objective="",
        cutoff=None,
        visible=(),
        rerank_model=None,
        index=None,
        visual_reader=None,
        original_reader=None,
        completed_research=None,
        reading_tokens=None,
        max_reading_tokens=None,
        state_path=None,
        external_research_enabled=True,
        local_source_ids=(),
        scheduler=None,
    ):
        self.store, self.discovery = store, discovery
        self.scheduler = scheduler
        self.objective, self.cutoff = objective, cutoff
        self.rerank_model, self.index = rerank_model, index
        self.visual_reader, self.completed_research = visual_reader, completed_research
        self.original_reader = original_reader
        self.visible = set(visible)
        self.read: dict[str, set[str]] = {}
        self.images: dict[str, set[str]] = {}
        self.channels, self.limitations = [], []
        self._metadata: dict[str, str] = {}
        self._read_documents = {}
        if reading_tokens is None or max_reading_tokens is None:
            from .research import EvidenceConfig

            config = EvidenceConfig()
            reading_tokens = config.reading_tokens if reading_tokens is None else reading_tokens
            max_reading_tokens = (
                config.max_reading_tokens if max_reading_tokens is None else max_reading_tokens
            )
        self.reading = ReadingWindow(reading_tokens, max_budget=max_reading_tokens)
        self._retrievals = {}
        self.closing = False
        self.state_path = Path(state_path) if state_path else None
        self.external_research_enabled = external_research_enabled
        self.local_source_ids = set(local_source_ids)
        self.references = SourceScope(
            store, lambda: self.visible, self.read_locations, cutoff=cutoff
        )

    def receive(self, readings):
        """Record exactly the originals delivered with application or prior research."""
        for handle, item in readings.items():
            ref = item["reference"]
            sid = ref["source_id"]
            if not self.references.eligible(sid):
                continue
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
        self.save()

    def export_state(self):
        """Public reading progress, including continuations; never private reasoning."""
        return {
            "visible": sorted(self.visible),
            "read": {sid: sorted(ids) for sid, ids in self.read.items()},
            "images": {sid: sorted(ids) for sid, ids in self.images.items()},
            "read_documents": sorted(self._read_documents),
            "metadata": dict(self._metadata),
            "readings": self.references.snapshot_choices(selected=False),
            "cursors": deepcopy(self.reading.cursors),
            "channels": deepcopy(self.channels),
            "limitations": list(dict.fromkeys(self.limitations)),
        }

    def save(self):
        if self.state_path:
            write_json(self.state_path, self.export_state())

    def _research(self):
        rows = self.completed_research() if self.completed_research else []
        return [
            row
            for row in rows
            if all(
                self.references.eligible(item["reference"]["source_id"])
                for item in row.get("readings", {}).values()
            )
        ]

    @staticmethod
    def _adopted_sources(research):
        return {item["reference"]["source_id"] for item in research.get("readings", {}).values()}

    def material_overview(self, source_id):
        doc = self.store.retained(source_id)
        return {
            "source_id": source_id,
            "title": self.store.candidate(source_id).title,
            "availability": doc.availability if doc else "metadata",
            "limitations": list(doc.limitations) if doc else [],
            "reading_gaps": list(doc.reading_gaps) if doc else [],
            "sections": list(dict.fromkeys(p.section for p in doc.passages)) if doc else [],
            "materials": [
                m.model_dump(include={
                    "material_id", "role", "title", "availability", "limitations",
                })
                for m in doc.materials
            ]
            if doc
            else [],
            "read_with": "acquire_source(reading_purpose), read_materials, browse_materials",
        }

    def _read_abstract(self, source_id, candidate, text):
        if not text:
            return None
        passage_id = "abstract-" + hashlib.sha256(text.encode()).hexdigest()[:10]
        self._metadata[source_id + "/" + passage_id] = text
        self.read.setdefault(source_id, set()).add(passage_id)
        self.references.capture(
            source_id,
            passage_id,
            text=text,
            locator="Metadata abstract",
            kind="metadata/abstract",
            candidate=candidate,
        )
        return passage_id

    def read_locations(self):
        return {(sid, pid, None) for sid, pids in self.read.items() for pid in pids} | {
            (sid, None, vid) for sid, vids in self.images.items() for vid in vids
        }

    def record_context_read(self, payload):
        sid = payload.get("source_id")
        doc = self.store.retained(sid) if sid else None
        if not doc or not doc.candidate.eligible(self.cutoff):
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

    def _eligible(self, source_id):
        if not self.external_research_enabled and source_id not in self.local_source_ids:
            raise ValueError("external_research_disabled")
        if source_id not in self.visible:
            raise ValueError("source_not_visible")
        if not self.store.candidate(source_id).eligible(self.cutoff):
            raise ValueError("source_after_cutoff")

    async def _document(self, source_id, material_id=None):
        self._eligible(source_id)
        if self.external_research_enabled:
            doc = await self._source_operation(
                lambda: self.store.acquire(
                    source_id, material_id=material_id, cutoff=self.cutoff
                ),
                name="source_acquisition",
            )
        else:
            # Supplied sources remain readable without following their remote attachments.
            doc = self.store.retained(source_id)
            if doc is None:
                raise ValueError("local_material_not_retained")
            if material_id and not any(
                m.material_id == material_id and m.original_path for m in doc.materials
            ):
                raise ValueError("external_research_disabled")
        # Dates from original content are checked again, including retained full text.
        if not doc.candidate.eligible(self.cutoff):
            raise ValueError("original_after_cutoff")
        self.store.ensure_accepted(doc, material_id)
        self._read_documents[source_id] = doc
        return doc

    async def _source_operation(self, operation, *, name):
        """Only source I/O holds a tool slot; subsequent model ranking has its own admission."""
        if self.scheduler is None:
            return await operation()
        budget = getattr(self.scheduler, "request_budget", None)
        remaining = budget.current_remaining_seconds() if budget else None
        deadline = self.scheduler.clock() + remaining if remaining is not None else None
        async with asyncio.timeout(remaining):
            return await self.scheduler.call(operation, kind="tool", name=name, deadline=deadline)

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
        query_ids = [set() for _ in results]
        for position, result in enumerate(results):
            self.limitations.extend(result.limitations)
            self.channels.append({"stage": "discovery", **result.channels})
            for candidate_index, candidate in enumerate(result.candidates):
                if candidate.eligible(self.cutoff):
                    sid = (
                        retained_ids[position][candidate_index]
                        if retained_ids and position in retained_ids
                        else self.store.register(candidate)
                    )
                    self.visible.add(sid)
                    ids.append(sid)
                    query_ids[position].add(sid)
                    if search_origins:
                        origins = provenance.setdefault(sid, [])
                        origin = search_origins[position]
                        if origin not in origins:
                            origins.append(origin)
        ids = [sid for sid in dict.fromkeys(ids)
               if self.store.candidate(sid).eligible(self.cutoff) and not self.store.excluded(sid)]
        candidates = [self.store.candidate(sid) for sid in ids]
        completed = self._research() if include_knowledge else []
        prior_text = {
            sid: "\n".join(
                json.dumps(
                    {k: run.get(k) for k in ("title", "objective", "body", "unknowns")},
                    ensure_ascii=False,
                )
                for run in completed
                if sid in self._adopted_sources(run)
            )
            for sid in ids
        }
        order, channels, notes, origins = await hybrid_rank(
            [
                c.title + "\n" + c.abstract + "\n" + prior_text[sid]
                for sid, c in zip(ids, candidates)
            ],
            queries,
            self.rerank_model,
            index=self.index,
            limit=limit,
            candidate_context=[{"search_origins": provenance.get(sid, [])} for sid in ids],
        )
        self.channels.append({"stage": "candidate_recall", **channels})
        self.limitations.extend(notes)
        chosen = {ids[i] for i in order}
        rows = []
        for i in order:
            sid, c = ids[i], candidates[i]
            # Metadata is an explicit citable reading scope, never original full text.
            excerpt = c.abstract[:4000]
            metadata_id = self._read_abstract(sid, c, excerpt)
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
            relevant, _, _, _ = await hybrid_rank(
                [
                    json.dumps(
                        {k: run.get(k) for k in ("title", "objective", "body", "unknowns")},
                        ensure_ascii=False,
                    )
                    for run in completed
                ],
                queries,
                limit=limit,
            )
            for i, run in enumerate(completed):
                if i in relevant or chosen.intersection(self._adopted_sources(run)):
                    knowledge.append(deepcopy(run.get("view", run)))
                    self.receive(run.get("readings", {}))
            for row in rows:
                row["retained_material"] = self.material_overview(row["source_id"])
        return ToolResult(
            ok=True,
            content={
                "candidates": rows,
                "channels": channels,
                "discovery_channels": [r.channels for r in results],
                "limitations": [n for r in results for n in r.limitations] + notes,
                "candidate_count": len(ids),
                **(
                    {
                        "query_feedback": [
                            {
                                **origin,
                                "returned_count": len(results[position].candidates),
                                "eligible_count": len(query_ids[position]),
                                "selected_count": len(query_ids[position] & chosen),
                                "channels": results[position].channels,
                                "limitations": results[position].limitations,
                            }
                            for position, origin in enumerate(search_origins)
                        ]
                    }
                    if search_origins
                    else {}
                ),
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
            return await self._source_operation(
                lambda: self.discovery.search(
                    query.query, query.provider, self.cutoff, max(12, args.limit * 3)
                ),
                name="source_search",
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
        result = await self._source_operation(
            lambda: self.discovery.related(seeds, args.relation, self.cutoff, args.limit),
            name="related_source_search",
        )
        purpose = args.information_need.strip() or self.objective
        return await self._candidates([result], [purpose], args.limit)

    async def acquire_source(self, args):
        sid = args.source_id
        if not sid:
            from .discovery import public_url

            if not args.url or not public_url(args.url):
                return ToolResult(ok=False, error="public_url_required")
            sid = self.store.register(_url_candidate(args.url))
            self.visible.add(sid)
        try:
            doc = await self._document(sid, args.material_id)
        except (KeyError, ValueError) as exc:
            return ToolResult(ok=False, error=str(exc))
        self.limitations.extend(doc.limitations)
        units = reading_units(doc.passages)
        directory = []
        for positions in units[args.start : args.start + args.count]:
            first, last = doc.passages[positions[0]], doc.passages[positions[-1]]
            directory.append({
                "unit_id": first.unit_id or first.passage_id,
                "heading_path": first.heading_path,
                "section": first.section,
                "kind": "table" if any(doc.passages[i].kind == "table" for i in positions)
                else first.kind,
                "pages": list(dict.fromkeys(
                    doc.passages[i].page for i in positions if doc.passages[i].page is not None
                )),
                "start_passage_id": first.passage_id,
                "end_passage_id": last.passage_id,
                "passage_count": len(positions),
            })
        result = ToolResult(
            ok=True,
            source_ids=[sid],
            content={
                "source_id": sid,
                "title": doc.candidate.title,
                "abstract": doc.candidate.abstract,
                "abstract_passage_id": None,
                "availability": doc.availability,
                "limitations": doc.limitations,
                "reading_gaps": doc.reading_gaps,
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
                "reading_directory": directory,
                "navigation": source_navigation(doc),
                "total_units": len(units),
                "total_passages": len(doc.passages),
                "next_start": args.start + args.count
                if args.start + args.count < len(units)
                else None,
                "visuals": [v.model_dump(exclude={"path", "url"}) for v in doc.visuals],
                "read_with": "read_materials",
                "browse_with": "browse_materials",
                "instruction": "仅取得材料和目录，正文尚未读取。",
            },
        )
        if args.reading_purpose:
            reading = await self.open_question(
                sid, args.reading_purpose, material_id=args.material_id,
                reading_tokens=args.reading_tokens,
                document=doc,
            )
            has_passages = bool((reading.content or {}).get("returned_ids"))
            result.reading_scope = reading.reading_scope
            result.image_urls = reading.image_urls
            if has_passages:
                result.content = {
                    **reading.content,
                    "acquisition": {
                        "availability": doc.availability,
                        "reading_gaps": doc.reading_gaps,
                    },
                    "limitations": list(dict.fromkeys(
                        [*doc.limitations, *reading.content.get("limitations", [])]
                    )),
                }
                return result
            result.content["reading"] = reading.content or {
                "returned_ids": [], "error": reading.error,
            }
            result.content["instruction"] = (
                "未取得本次目的相关正文；查看 availability、limitations 及目录后选择下一步。"
            )
        result.content["abstract_passage_id"] = self._read_abstract(
            sid, doc.candidate, doc.candidate.abstract
        )
        return result

    async def material(self, source_id):
        try:
            self.store.candidate(source_id)
        except KeyError:
            return None  # Other task-visible material may be supplied by another reader.
        try:
            self._eligible(source_id)
        except (KeyError, ValueError) as exc:
            return {"error": str(exc)}
        doc = self.store.retained(source_id)
        if doc:
            try:
                self.store.ensure_accepted(doc)
            except ValueError as exc:
                return {"error": str(exc)}
        if doc and not doc.candidate.eligible(self.cutoff):
            return {"error": "original_after_cutoff"}
        from .documents import reading_passages

        rows = reading_passages(doc) if doc else []
        rows += [
            {"passage_id": key.split("/", 1)[1], "section": "Metadata abstract", "text": text}
            for key, text in self._metadata.items()
            if key.startswith(source_id + "/")
        ]
        return (
            {
                "passages": rows,
                "source": self.store.candidate(source_id).model_dump(
                    mode="json", exclude_none=True
                ),
                "materials": [m.model_dump(mode="json") for m in doc.materials] if doc else [],
            }
            if rows
            else {"error": "acquire_source_first"}
        )

    async def retrieve(self, args: RetrieveKnowledge, *, document=None):
        if self.closing:
            return ToolResult(ok=False, error="research_closing")
        try:
            doc = document if document is not None else await self._document(args.source_id)
        except (KeyError, ValueError) as exc:
            return ToolResult(ok=False, error=str(exc))
        key = hashlib.sha256(
            json.dumps(
                {
                    "source_id": args.source_id,
                    "queries": args.queries,
                    "limit": args.limit,
                    "cutoff": str(self.cutoff),
                    "source": doc.model_dump(mode="json"),
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        reused = key in self._retrievals
        if not reused:
            self._retrievals[key] = await find_passages(
                [doc], args.queries, self.rerank_model, index=self.index, limit=args.limit
            )
        result = deepcopy(self._retrievals[key])
        ids = [hit["passage"]["passage_id"] for hit in result.pop("hits")]
        from .documents import reading_passages

        if ids:
            page = self.reading.read(
                PassageSelection(
                    source_id=args.source_id,
                    passage_ids=ids,
                    reading_tokens=args.reading_tokens,
                ),
                passages=reading_passages(doc),
                source=doc.candidate.model_dump(mode="json", exclude_none=True),
                materials=[m.model_dump(mode="json") for m in doc.materials],
            )
            self.record_context_read(page)
            result.update(reading_output(page))
        else:
            result.update(source_id=args.source_id, returned_ids=[], complete=True)
        result["reused"] = reused
        result["continue_with"] = "browse_materials using source_id and continuation"
        self.channels.append({"stage": "passage_recall", **result.get("channels", {})})
        self.limitations.extend(result["limitations"])
        return await self._accompany(args.source_id, ToolResult(
            ok=True,
            content=result,
            source_ids=[args.source_id],
            reading_scope="selected_passages",
        ))

    async def open_question(self, source_id, question, *, material_id=None,
                            reading_tokens=None, document=None):
        """Start source understanding with actual question-relevant originals."""
        doc = document if document is not None else await self._document(source_id, material_id)
        if self.rerank_model is None:
            if material_id:
                material = next(m for m in doc.materials if m.material_id == material_id)
                doc = doc.model_copy(update={
                    "passages": [p for p in doc.passages if p.passage_id in material.passage_ids],
                })
            return await self.retrieve(RetrieveKnowledge(
                source_id=source_id, queries=[question], reading_tokens=reading_tokens,
            ), document=doc)
        ids = await select_reading_start(doc, question, self.rerank_model, material_id=material_id)
        if not ids:
            return ToolResult(ok=False, error="no_readable_passages")
        result = await self.read_materials(ReadPassages(
            source_id=source_id, passage_ids=ids, reading_tokens=reading_tokens,
        ))
        result.content["reading_question"] = question
        result.content["selected_starts"] = ids
        self.channels.append({"stage": "reading_start", "selection": "source_navigation"})
        return result

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

    async def read_materials(self, args):
        material = await self.material(args.source_id)
        if material is None:
            row = {"source_id": args.source_id, "error": "source_not_available"}
        elif "error" in material:
            row = {"source_id": args.source_id, **material}
        else:
            row = self.reading.read(args, **material)
            self.record_context_read(row)
        return await self._accompany(args.source_id, ToolResult(
            ok="error" not in row,
            content=reading_output(row),
            source_ids=[args.source_id] if "error" not in row else [],
            reading_scope="selected_material",
        ))

    async def _accompany(self, source_id, result):
        if self.original_reader and result.ok and (doc := self.store.retained(source_id)):
            result = await self.original_reader.accompany(doc, result, self.references)
            for visual in result.content.get("original_visuals", []):
                self.images.setdefault(source_id, set()).add(visual["visual_id"])
        return result

    async def original_pages(self, readings):
        """Open only retained pages located by this incoming set of originals."""
        if not self.original_reader:
            return ToolResult(ok=True, content={"original_visuals": []})
        eligible = {key: item for key, item in readings.items()
                    if self.references.eligible(item["reference"]["source_id"])}
        self.receive(eligible)
        result = await self.original_reader.from_readings(eligible, self.references)
        for visual in result.content["original_visuals"]:
            self.images.setdefault(visual["source_id"], set()).add(visual["visual_id"])
        return result

    async def visual(self, args: VisualRequest):
        reader = self.original_reader or self.visual_reader
        if not reader:
            return ToolResult(ok=False, error="visual_reader_not_configured")
        try:
            doc = await self._document(args.source_id)
        except (KeyError, ValueError) as exc:
            return ToolResult(ok=False, error=str(exc))
        if not self.external_research_enabled and not any(
            v.visual_id == args.visual_id and v.path for v in doc.visuals
        ):
            return ToolResult(ok=False, error="local_image_not_available")
        result = await reader(doc, args, self.references, background=self.objective)
        if result.ok:
            self.images.setdefault(args.source_id, set()).add(args.visual_id)
            if self.original_reader:
                self.receive(self.references.snapshot_choices(selected=False))
        return result

    def _tool_handler(self, handler):
        async def invoke(args):
            try:
                return await handler(args)
            finally:
                self.save()

        return invoke

    @property
    def reading_hint(self):
        return (
            f" 默认阅读窗口 {self.reading.budget} Token；完整长范围可用 reading_tokens "
            f"提高至 {self.reading.max_budget} Token。"
        )

    @property
    def page_reading_hint(self):
        return ORIGINAL_PAGE_READING if self.original_reader else ""

    def tools(self, *, include_readers=True, reference_tool=None, inquiry=None):
        registry = ToolRegistry(inquiry=inquiry)
        definitions = [
            (
                "search_sources",
                "按具体信息需要批量检索题录摘要；project 入口同时发现本次项目资料和完成研究。",
                SearchSources,
                self.search,
            ),
            (
                "acquire_source",
                "按 reading_purpose 取得并阅读原文，返回章节、图表和附件入口；"
                "material_id 指定附件，留空 reading_purpose 仅看目录。",
                AcquireSource,
                self.acquire_source,
            ),
            (
                "retrieve_knowledge",
                "在一个来源内按科学问题检索并阅读完整原生单元；reading_tokens 选择阅读窗口。",
                RetrieveKnowledge,
                self.retrieve,
            ),
            (
                "related_papers",
                "从已选论文沿参考文献、被引关系或相关论文扩展一次，"
                "information_need 指定这次追踪要回答的问题。",
                RelatedPapers,
                self.related,
            ),
            (
                "read_literature_visual",
                "读取选定图像的实际像素，结合图注解释坐标、条件、组别及可辨读结果。",
                VisualRequest,
                self.visual,
            ),
        ]
        for name, description, arguments, handler in definitions:
            if not self.external_research_enabled and name in {"search_sources", "related_papers"}:
                continue
            registry.add(
                Tool(
                    name,
                    description + self.page_reading_hint + self.reading_hint
                    if name in {"acquire_source", "retrieve_knowledge"}
                    else "读取指定原页像素及对应完整解析文字。"
                    if name == "read_literature_visual" and self.original_reader else description,
                    arguments,
                    self._tool_handler(handler),
                    exploration=True,
                    delegates=True,
                    timeout_seconds=300,
                )
            )
        if include_readers:
            for name, arguments, description in (
                ("read_materials", ReadPassages, "在一个来源内按原文位置阅读所属完整单元。"),
                (
                    "browse_materials",
                    BrowseMaterials,
                    "在一个来源内按章节或起始位置阅读；continuation 接续未读单元。",
                ),
            ):
                registry.add(
                    Tool(
                        name, description + self.page_reading_hint + self.reading_hint,
                        arguments, self._tool_handler(self.read_materials),
                    )
                )
        registry.material_providers.append(self.material)
        if self.original_reader:
            registry.original_page_readers.append(self.original_pages)
        registry.reading_windows.append(self.reading)
        registry.read_observers.append(self.record_context_read)
        if reference_tool:
            self.references.attach(registry, reference_tool)
        else:
            registry.evidence_views.append(self.references.snapshot_choices)
        return registry
