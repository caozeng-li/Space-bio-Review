"""Task-local source access shared by application and literature reading."""

from copy import deepcopy
from pathlib import Path

from spacebio_review.reference_contract import SourceScope


class ReviewAccess:
    def __init__(self, store, reader, *, cutoff=None, whole_units=True, runtime=None):
        self.store, self.reader = store, reader
        self.references = set()
        self.metadata = {}
        self.cutoff = cutoff
        self.whole_units = whole_units
        self.runtime = runtime
        self.source_scope = SourceScope(
            store,
            self.visible_sources,
            self.read_locations,
            cutoff=cutoff,
        )
        self.source_scope.snapshot_providers.append(self.read_snapshots)

    def read_snapshots(self):
        return self.reader.references.snapshot_choices() if self.reader else {}

    def visible_sources(self):
        return {r[0] for r in self.references} | (set(self.reader.docs) if self.reader else set())

    def read_locations(self):
        return set(self.references) | (self.reader.read_locations() if self.reader else set())

    def register_knowledge(self, run):
        """Only originals actually delivered with the answer become parent reads."""
        answer = run.answer_view()
        for handle, item in (answer or {}).get("evidence_resolution", {}).items():
            ref = item["reference"]
            sid, pid, vid = (ref.get(key) for key in ("source_id", "passage_id", "visual_id"))
            self.references.add((sid, pid, vid))
            self.source_scope.snapshots[handle] = deepcopy(item)
            if item.get("reading_scope") == "metadata/abstract" and pid:
                self.metadata[sid + "/" + pid] = item["quote"]
        return answer

    def record_context_read(self, payload):
        sid = payload.get("source_id")
        if not sid or not self.source_scope.eligible(sid):
            return
        rows = payload.get("passages", [])
        self.references.update((sid, p["passage_id"], None) for p in rows)
        self.source_scope.capture_passages(sid, rows)

    def reading_records(self):
        from spacebio_review.evidence.knowledge import ReadingRecord

        records = {}
        for sid, pid, vid in sorted(self.references, key=str):
            candidate = self.store.candidate(sid)
            if candidate.kind == "application":
                continue
            if sid not in records:
                doc = self.store.retained(sid)
                records[sid] = ReadingRecord(
                    source=candidate,
                    availability=doc.availability if doc else "metadata",
                    related_versions=self.store.related_versions(sid),
                )
            if pid:
                records[sid].passage_ids.append(pid)
            if vid:
                records[sid].visual_ids.append(vid)
        return records

    async def read_visual(self, args):
        from spacebio_review.evidence.literature import read_literature_image
        from spacebio_review.tools import ToolResult

        if args.source_id not in self.visible_sources():
            return ToolResult(ok=False, error="source_not_visible")
        if not self.source_scope.eligible(args.source_id):
            return ToolResult(ok=False, error="source_after_cutoff")
        doc = self.store.retained(args.source_id)
        visual = (
            next((v for v in doc.visuals if v.visual_id == args.visual_id), None) if doc else None
        )
        if not visual or not visual.path or not Path(visual.path).is_file():
            return ToolResult(ok=False, error="retained_image_unavailable")
        result = await read_literature_image(self.runtime, self.store, doc, args, self.source_scope)
        if result.ok:
            self.references.add((args.source_id, None, args.visual_id))
        return result

    def tools(self):
        self.reader.references.cutoff = self.cutoff
        tools = self.reader.tools()
        self.source_scope.attach(tools, "read_review_references")
        if self.runtime is not None:
            from spacebio_review.evidence.session import VisualRequest
            from spacebio_review.tools import Tool

            tools.add(
                Tool(
                    "read_retained_visual",
                    "复核本任务已有资料中已保存在本地的图像。",
                    VisualRequest,
                    self.read_visual,
                    delegates=True,
                    exploration=True,
                    timeout_seconds=300,
                )
            )

        async def material(source_id):
            if source_id not in self.visible_sources():
                return None
            if not self.source_scope.eligible(source_id):
                return {"error": "source_after_cutoff"}
            doc = self.store.retained(source_id)
            from spacebio_review.evidence.documents import reading_passages

            rows = reading_passages(doc, whole_units=self.whole_units) if doc else []
            rows += [
                {"passage_id": key.split("/", 1)[1], "section": "Metadata abstract", "text": text}
                for key, text in self.metadata.items()
                if key.startswith(source_id + "/")
            ]
            return {"passages": rows} if rows else {"error": "source_unavailable"}

        tools.material_providers.insert(0, material)
        tools.read_observers.append(self.record_context_read)
        return tools
