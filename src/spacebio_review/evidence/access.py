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
)
from spacebio_review.persistence import write_json
from spacebio_review.reference_contract import SourceScope
from spacebio_review.tools import Tool, ToolRegistry, ToolResult

from .discovery import DiscoveryResult
from .knowledge import (
    AcquireSource,
    ReadingRecord,
    ReadSource,
    ReadSources,
    RelatedPapers,
    RetrieveKnowledge,
    SearchSources,
)
from .retrieval import find_passages, hybrid_rank
from .session import VisualRequest
from .types import SourceCandidate


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
        completed_research=None,
        whole_units=True,
        state_path=None,
        external_research_enabled=True,
        local_source_ids=(),
    ):
        self.store, self.discovery = store, discovery
        self.objective, self.cutoff = objective, cutoff
        self.rerank_model, self.index = rerank_model, index
        self.visual_reader, self.completed_research = visual_reader, completed_research
        self.visible = set(visible)
        self.read: dict[str, set[str]] = {}
        self.images: dict[str, set[str]] = {}
        self.channels, self.limitations = [], []
        self._metadata: dict[str, str] = {}
        self._read_documents = {}
        self.whole_units = whole_units
        self.reading = ReadingWindow(whole_units=whole_units)
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
            "sections": list(dict.fromkeys(p.section for p in doc.passages)) if doc else [],
            "materials": [
                m.model_dump(include={"material_id", "role", "title", "availability"})
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
            doc = await self.store.acquire(source_id, material_id=material_id, cutoff=self.cutoff)
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
        ids = [sid for sid in dict.fromkeys(ids) if self.store.candidate(sid).eligible(self.cutoff)]
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
            return await self.discovery.search(
                query.query, query.provider, self.cutoff, max(12, args.limit * 3)
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
        result = await self.discovery.related(seeds, args.relation, self.cutoff, args.limit)
        purpose = args.information_need.strip() or self.objective
        return await self._candidates([result], [purpose], args.limit)

    async def _read(self, args: ReadSource):
        try:
            sid = args.source_id
            if sid is None:
                if not args.url or self.closing:
                    raise ValueError("source_id_or_public_url_required")
                from .discovery import public_url

                if not public_url(args.url):
                    raise ValueError("public_url_required")
                sid = self.store.register(_url_candidate(args.url))
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
            sid = self.store.register(_url_candidate(args.url))
            self.visible.add(sid)
        try:
            doc = await self._document(sid, args.material_id)
        except (KeyError, ValueError) as exc:
            return ToolResult(ok=False, error=str(exc))
        abstract_id = self._read_abstract(sid, doc.candidate, doc.candidate.abstract)
        self.limitations.extend(doc.limitations)
        result = ToolResult(
            ok=True,
            source_ids=[sid],
            content={
                "source_id": sid,
                "title": doc.candidate.title,
                "abstract": doc.candidate.abstract,
                "abstract_passage_id": abstract_id,
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
            has_passages = any(
                row.get("passages") for row in (reading.content or {}).get("sources", [])
            )
            result.content["instruction"] = (
                "已返回本次目的相关原文；按章节、图像或 continuation 继续阅读。"
                if has_passages
                else "未取得本次目的相关正文；查看 availability、limitations 及目录后选择下一步。"
            )
            result.reading_scope = reading.reading_scope
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
        if doc and not doc.candidate.eligible(self.cutoff):
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
                    "cutoff": str(self.cutoff),
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
                self.rerank_model,
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
        rows, remaining = [], self.reading.budget
        for selection in args.reads:
            material = await self.material(selection.source_id)
            if material is None:
                row = {"source_id": selection.source_id, "error": "source_not_available"}
            elif "error" in material:
                row = {"source_id": selection.source_id, **material}
            else:
                row = self.reading.read(selection, budget=remaining, **material)
                self.record_context_read(row)
            remaining -= row.get("input_tokens_estimate", 0)
            rows.append(row)
        return ToolResult(
            ok=any("error" not in row for row in rows),
            content=rows,
            source_ids=[row["source_id"] for row in rows if "error" not in row],
            reading_scope="selected_material",
        )

    async def visual(self, args: VisualRequest):
        if not self.visual_reader:
            return ToolResult(ok=False, error="visual_reader_not_configured")
        try:
            doc = await self._document(args.source_id)
        except (KeyError, ValueError) as exc:
            return ToolResult(ok=False, error=str(exc))
        if not self.external_research_enabled and not any(
            v.visual_id == args.visual_id and v.path for v in doc.visuals
        ):
            return ToolResult(ok=False, error="local_image_not_available")
        result = await self.visual_reader(doc, args, self.references, background=self.objective)
        if result.ok:
            self.images.setdefault(args.source_id, set()).add(args.visual_id)
        return result

    def _tool_handler(self, handler):
        async def invoke(args):
            try:
                return await handler(args)
            finally:
                self.save()

        return invoke

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
                "按科学问题定位所选来源原文及邻段；返回可继续阅读的位置。",
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
                    description,
                    arguments,
                    self._tool_handler(handler),
                    exploration=True,
                    delegates=name == "read_literature_visual",
                    timeout_seconds=300,
                )
            )
        if include_readers:
            for name, arguments, description in (
                ("read_materials", ReadPassages, "按原文段落 ID 阅读完整段落及其原始单元。"),
                (
                    "browse_materials",
                    BrowseMaterials,
                    "按来源、章节或起始段落继续阅读；continuation 接续上次未读内容。",
                ),
            ):
                registry.add(
                    Tool(name, description, arguments, self._tool_handler(self.read_materials))
                )
        registry.material_providers.append(self.material)
        registry.reading_windows.append(self.reading)
        registry.read_observers.append(self.record_context_read)
        if reference_tool:
            self.references.attach(registry, reference_tool)
        else:
            registry.evidence_views.append(self.references.snapshot_choices)
        return registry
