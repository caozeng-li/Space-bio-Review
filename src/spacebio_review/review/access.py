"""Task-local source access shared by application and literature reading."""

import json

from spacebio_review.reference_contract import SourceScope


class ReviewAccess:
    def __init__(self, store, reader, *, cutoff=None, whole_units=False):
        self.store, self.reader = store, reader
        self.references = set()
        self.metadata = {}
        self.cutoff = cutoff
        self.whole_units = whole_units
        self.additional_sources = []
        self.source_scope = SourceScope(
            store,
            self.visible_sources,
            self.read_locations,
            cutoff=cutoff,
        )
        self.source_scope.snapshot_providers.append(self.read_snapshots)

    def read_snapshots(self):
        values = self.reader.references.snapshot_choices() if self.reader else {}
        for scope in self.additional_sources:
            values.update(scope.snapshot_choices())
        return values

    def visible_sources(self):
        return (
            {r[0] for r in self.references}
            | (set(self.reader.docs) if self.reader else set())
            | {sid for scope in self.additional_sources for sid in scope.visible()}
        )

    def read_locations(self):
        return (
            set(self.references)
            | (self.reader.read_locations() if self.reader else set())
            | {ref for scope in self.additional_sources for ref in scope.locations()}
        )

    def register_knowledge(self, run):
        for sid, record in run.sources.items():
            self.references.update((sid, pid, None) for pid in record.passage_ids)
            self.references.update((sid, None, vid) for vid in record.visual_ids)
        path = self.store.root.parent / "knowledge" / run.research_id / "metadata-readings.json"
        if path.is_file():
            self.metadata.update(json.loads(path.read_text("utf-8")))
        snapshots = path.with_name("evidence-readings.json")
        if snapshots.is_file():
            self.source_scope.snapshots.update(json.loads(snapshots.read_text("utf-8")))

    def tools(self):
        self.reader.references.cutoff = self.cutoff
        tools = self.reader.tools()
        self.source_scope.attach(tools, "read_review_references")

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

        def record(payload):
            self.references.update(
                (payload["source_id"], p["passage_id"], None) for p in payload.get("passages", [])
            )
            if payload.get("passages"):
                self.source_scope.capture_passages(payload["source_id"], payload["passages"])

        tools.material_providers.insert(0, material)
        tools.read_observers.append(record)
        return tools
