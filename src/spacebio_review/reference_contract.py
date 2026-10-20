"""Task-local reference locations, mechanical completion and public diagnostics."""

from copy import deepcopy

from pydantic import BaseModel, Field

from spacebio_review.agents.diagnostics import digest
from spacebio_review.tools import Tool, ToolResult


class ReferenceQuery(BaseModel):
    source_ids: list[str] = Field(default_factory=list)
    evidence_handles: list[str] = Field(default_factory=list)
    start: int = Field(default=0, ge=0)
    count: int = Field(default=20, ge=1, le=50)
    select_for_submission: bool = False


def reference_objects(value, path=()):
    """Yield actual model objects so a mechanical completion reaches the submitted product."""
    if isinstance(value, BaseModel):
        if hasattr(value, "source_id") and hasattr(value, "passage_id"):
            yield value, list(path)
        else:
            for name in type(value).model_fields:
                yield from reference_objects(getattr(value, name), (*path, name))
    elif isinstance(value, dict):
        for name, child in value.items():
            yield from reference_objects(child, (*path, name))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from reference_objects(child, (*path, i))


class SourceScope:
    def __init__(self, store, visible, locations, *, cutoff=None, categories=None):
        self.store, self.visible, self.locations = store, visible, locations
        self.cutoff, self.categories = cutoff, categories or (lambda: {})
        self.resolutions = []
        self.selected_sources = None
        self.selected_handles = None
        self.snapshots = {}
        self.snapshot_providers = []

    def capture(
        self,
        sid,
        passage_id=None,
        *,
        text="",
        locator="",
        kind="text",
        visual_id=None,
        candidate=None,
        image_digest=None,
        reading_structure=None,
        material=None,
    ):
        """Bind the content actually delivered to this reader, not mutable store state."""
        candidate = candidate or self.store.candidate(sid)
        if not candidate.eligible(self.cutoff):
            return
        document = self.store.retained(sid)
        reference = {"source_id": sid, "passage_id": passage_id, "visual_id": visual_id}
        version = digest({"content": text, "reading_scope": kind, "image_digest": image_digest})
        key = "evidence-" + digest({"reference": reference, "version": version})[:16]
        self.snapshots.setdefault(
            key,
            {
                "reference": reference,
                "title": candidate.title,
                "kind": "application" if candidate.kind == "application" else "external",
                "locator": locator or passage_id or visual_id or "",
                "quote": text,
                "material_version": version,
                "reading_scope": kind,
                "provided_via": ["task_read"],
                "citation_status": "not_semantically_verified",
                "url": candidate.url or (document.original_path if document else "") or "",
                "authors": list(candidate.authors),
                "publication_date": str(candidate.publication_date or ""),
                "image_digest": image_digest,
                **({"availability": document.availability,
                    "limitations": list(document.limitations)} if document else {}),
                **({"reading_structure": deepcopy(reading_structure)} if reading_structure else {}),
                **({"material": deepcopy(material)} if material else {}),
            },
        )

    def capture_passages(self, sid, passages, *, candidate=None):
        document = self.store.retained(sid)
        positions = {
            passage.passage_id: i for i, passage in enumerate(document.passages)
        } if document else {}
        materials = {
            pid: material.model_dump(include={
                "material_id", "title", "role", "url", "availability", "limitations",
            })
            for material in (document.materials if document else [])
            for pid in material.passage_ids
        }
        for passage in passages:
            row = passage.model_dump() if hasattr(passage, "model_dump") else passage
            structure = {
                key: value for key, value in row.items()
                if key not in {"text", "passage_id", "evidence_handle", "citation"}
            }
            if row["passage_id"] in positions:
                structure["order"] = positions[row["passage_id"]]
            self.capture(
                sid,
                row["passage_id"],
                text=row["text"],
                locator=row.get("locator") or row.get("section", ""),
                kind="metadata/abstract"
                if row.get("section") == "Metadata abstract"
                else row.get("kind", "text"),
                candidate=candidate,
                reading_structure=structure,
                material=materials.get(row["passage_id"]),
            )

    def snapshot_choices(self, *, selected=True):
        values = dict(self.snapshots)
        for provider in self.snapshot_providers:
            values.update(provider())
        visible = set(self.visible())
        return deepcopy(
            {
                k: v
                for k, v in values.items()
                if v["reference"]["source_id"] in visible
                and (
                    not selected
                    or self.selected_sources is None
                    or v["reference"]["source_id"] in self.selected_sources
                )
                and (
                    not selected
                    or self.selected_handles is None
                    or k in self.selected_handles
                    or self.handle(v["reference"]) in self.selected_handles
                )
            }
        )

    def candidate(self, sid):
        try:
            return self.store.candidate(sid) if self.store else None
        except (KeyError, FileNotFoundError):
            return None

    def eligible(self, sid):
        candidate = self.candidate(sid)
        if candidate is None:
            return False
        doc = self.store.retained(sid)
        return candidate.eligible(self.cutoff) and (
            doc is None or doc.candidate.eligible(self.cutoff)
        )

    def read_locations(self, sid):
        return [
            {"source_id": source, "passage_id": pid, "visual_id": vid}
            for source, pid, vid in sorted(set(self.locations()), key=str)
            if source == sid and bool(pid) != bool(vid)
        ]

    def problem(self, ref):
        sid = ref.source_id
        if self.candidate(sid) is None:
            return "unknown_source"
        if sid not in self.visible():
            return "source_outside_task"
        if not self.eligible(sid):
            return "source_after_cutoff"
        if ref.passage_id and ref.visual_id:
            return "conflicting_source_locations"
        if not ref.passage_id and not ref.visual_id:
            return "source_location_required"
        key = (sid, ref.passage_id, ref.visual_id)
        if key not in set(self.locations()):
            return "source_location_not_read"
        return None

    def check(self, product):
        errors = []
        for ref, path in reference_objects(product):
            reason = self.problem(ref)
            choices = self.read_locations(ref.source_id) if ref.source_id in self.visible() else []
            if reason == "source_location_required" and len(choices) == 1:
                original = ref.model_dump()
                ref.passage_id = choices[0]["passage_id"]
                ref.visual_id = choices[0]["visual_id"]
                self.resolutions.append(
                    {
                        "location": path,
                        "product_type": type(product).__name__,
                        "original_reference": original,
                        "resolved_reference": ref.model_dump(),
                        "resolution": "unique_task_read_location",
                        "citation_status": "not_semantically_verified",
                    }
                )
                reason = self.problem(ref)
            if reason:
                errors.append(
                    {
                        "location": path,
                        "type": reason,
                        "category": "citation",
                        "invalid_value": ref.model_dump(),
                        "allowed_locations": choices if self.eligible(ref.source_id) else [],
                        "instruction": "只引用本任务实际读到的位置；位置有效不等于支持结论。",
                    }
                )
        return errors

    def directory(self, args=None):
        args = args or ReferenceQuery()
        ids = sorted(set(self.visible()))
        if args.source_ids:
            ids = [sid for sid in ids if sid in args.source_ids]
        # Page locations as well as sources; a single heavily read source can be large.
        rows = []
        snapshots = self.snapshot_choices(selected=False)
        for sid in ids:
            candidate = self.candidate(sid)
            doc = self.store.retained(sid) if self.store else None
            eligible = self.eligible(sid)
            for ref in self.read_locations(sid) or [None]:
                position = (
                    next(
                        (
                            item
                            for item in (doc.passages if ref and ref["passage_id"] else doc.visuals)
                            if (
                                getattr(item, "passage_id", None)
                                or getattr(item, "visual_id", None)
                            )
                            == (ref["passage_id"] or ref["visual_id"])
                        ),
                        None,
                    )
                    if doc and ref
                    else None
                )
                versions = [
                    (key, value) for key, value in snapshots.items() if value["reference"] == ref
                ]
                for key, value in versions or [(self.handle(ref) if ref else None, {})]:
                    rows.append(
                        {
                            "source_id": sid,
                            "title": value.get(
                                "title", candidate.title if candidate else "Unknown source"
                            ),
                            "eligible": eligible,
                            "read_location": ref,
                            "locator": value.get("locator", position.locator if position else None),
                            "read_status": "read" if ref else "not_read",
                            "evidence_handle": key if ref and (value or eligible) else None,
                            "material_version": value.get("material_version"),
                        }
                    )
        rows.sort(
            key=lambda row: (
                row["read_location"] is None,
                row["source_id"],
                str(row["read_location"]),
            )
        )
        return {
            **self.categories(),
            "source_locations": rows[args.start : args.start + args.count],
            "next_start": args.start + args.count if args.start + args.count < len(rows) else None,
            "total_locations": len(rows),
            "citation_status": "not_semantically_verified",
            "instruction": "coverage_refs 引用事项，source_refs 引用来源及已读位置；二者不可混用。",
        }

    @staticmethod
    def handle(ref):
        return "evidence-" + digest(ref)[:16]

    def evidence_choices(self):
        """Only exact locations made public to this task, never the shared store inventory."""
        choices = {}
        for sid in sorted(set(self.visible())):
            if not self.eligible(sid):
                continue
            candidate = self.candidate(sid)
            document = self.store.retained(sid)
            for ref in self.read_locations(sid):
                location = (
                    next((p for p in document.passages if p.passage_id == ref["passage_id"]), None)
                    if document
                    else None
                )
                choices[self.handle(ref)] = {
                    "reference": ref,
                    "title": candidate.title,
                    "kind": "application" if candidate.kind == "application" else "external",
                    "locator": location.locator
                    if location
                    else ref["visual_id"] or "metadata/abstract",
                    "reading_scope": "interpreted_visual"
                    if ref["visual_id"]
                    else location.kind
                    if location
                    else "metadata/abstract",
                    "citation_status": "not_semantically_verified",
                }
        snapshots = self.snapshot_choices(selected=False)
        captured = {self.handle(v["reference"]) for v in snapshots.values()}
        return {**{k: v for k, v in choices.items() if k not in captured}, **snapshots}

    def submission_choices(self):
        choices = self.evidence_choices()
        if self.selected_handles is not None:
            return {k: v for k, v in choices.items() if k in self.selected_handles}
        if self.selected_sources is None:
            return choices
        return {
            k: v for k, v in choices.items() if v["reference"]["source_id"] in self.selected_sources
        }

    def attach(self, registry, name):
        def view():
            return {**self.directory(), "read_with": name}

        async def read(args):
            if args.select_for_submission:
                if args.evidence_handles:
                    if not set(args.evidence_handles) <= set(self.evidence_choices()):
                        return ToolResult(ok=False, error="select_read_handles_explicitly")
                    self.selected_handles = set(args.evidence_handles)
                    self.selected_sources = None
                elif not args.source_ids or not set(args.source_ids) <= set(self.visible()):
                    return ToolResult(ok=False, error="select_visible_sources_explicitly")
                else:
                    self.selected_sources = set(args.source_ids)
                    self.selected_handles = None
            return ToolResult(
                ok=True, content=self.directory(args), reading_scope="reference_directory"
            )

        registry.reference_views.append(view)
        registry.reference_audits.append(lambda: self.resolutions)
        registry.evidence_views.append(self.evidence_choices)
        registry.add(
            Tool(
                name,
                "分页查看已读位置；大量材料时用 source_ids 或 evidence_handles，"
                "配合 select_for_submission "
                "明确选择正式证据范围，可再次修改。",
                ReferenceQuery,
                read,
            )
        )
