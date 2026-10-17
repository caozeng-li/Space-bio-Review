"""Immutable original files shared by runs; no readings or scientific conclusions."""

from __future__ import annotations

import json
import mimetypes
import shutil
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from .discovery import identifiers
from .types import SourceCandidate, SourceDocument


class MaterialCacheConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: Path
    prefer_existing: bool = True


class MaterialSnapshot(BaseModel):
    snapshot_id: str
    source: SourceCandidate
    role: Literal["main", "supplement", "visual"]
    url: str
    acquired_url: str
    title: str = ""
    package_member: str = ""
    content_sha256: str
    file: str
    content_type: str = ""
    acquired_at: str | None = None
    stored_at: str
    origin_source: str = ""


def _identity_keys(candidate):
    values = identifiers(**candidate.identifiers)
    if candidate.url:
        values["url"] = candidate.url
    return [
        sha256(json.dumps([candidate.kind, key, value]).encode()).hexdigest()
        for key, value in values.items()
    ]


def _compatible(candidate, original, cutoff):
    if candidate.kind != original.kind or not original.eligible(cutoff):
        return False
    current, previous = identifiers(**candidate.identifiers), identifiers(**original.identifiers)
    if any(current[key] != previous[key] for key in current.keys() & previous.keys()):
        return False
    if candidate.version and candidate.version != original.version:
        return False
    return not (candidate.version_date and candidate.version_date != original.version_date)


class MaterialCache:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    @staticmethod
    def _atomic_write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        try:
            temporary.write_bytes(content)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def retain(
        self,
        candidate: SourceCandidate,
        path: str | Path,
        *,
        role,
        url,
        acquired_url="",
        title="",
        package_member="",
        acquired_at=None,
        content_type="",
        origin_source="",
    ) -> MaterialSnapshot:
        source = candidate.model_copy(deep=True)
        source.identifiers = identifiers(**source.identifiers)
        source.abstract = ""
        keys = _identity_keys(source)
        if not keys:
            raise ValueError("original_requires_source_identity")
        path = Path(path)
        content = path.read_bytes()
        digest = sha256(content).hexdigest()
        file = "files/" + digest + path.suffix.lower()
        identity = {
            "identifiers": source.identifiers,
            "kind": source.kind,
            "url": source.url if not source.identifiers else "",
            "version": source.version,
            "version_date": str(source.version_date or ""),
            "role": role,
            "material_url": url,
            "package_member": package_member,
            "content": digest,
        }
        snapshot_id = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        manifest = self.root / "snapshots" / (snapshot_id + ".json")
        if manifest.exists():
            snapshot = MaterialSnapshot.model_validate_json(manifest.read_text("utf-8"))
        else:
            snapshot = MaterialSnapshot(
                snapshot_id=snapshot_id,
                source=source,
                role=role,
                url=url,
                acquired_url=acquired_url or url,
                title=title,
                package_member=package_member,
                content_sha256=digest,
                file=file,
                content_type=content_type or mimetypes.guess_type(path.name)[0] or "",
                acquired_at=acquired_at,
                stored_at=datetime.now(timezone.utc).isoformat(),
                origin_source=str(origin_source),
            )
            self._atomic_write(self.root / file, content)
            self._atomic_write(manifest, snapshot.model_dump_json(indent=2).encode("utf-8"))
        # One immutable pointer per snapshot avoids a shared mutable catalogue.
        for key in keys:
            pointer = self.root / "identities" / key / snapshot_id
            pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer.touch(exist_ok=True)
        return snapshot

    def find(
        self, candidate: SourceCandidate, *, role=None, url=None, cutoff: date | None = None
    ) -> list[MaterialSnapshot]:
        ids = {
            path.name
            for key in _identity_keys(candidate)
            for path in (self.root / "identities" / key).glob("*")
        }
        snapshots = []
        for sid in ids:
            path = self.root / "snapshots" / (sid + ".json")
            snapshot = MaterialSnapshot.model_validate_json(path.read_text("utf-8"))
            if (
                (role is None or snapshot.role == role)
                and (url is None or snapshot.url == url)
                and _compatible(candidate, snapshot.source, cutoff)
            ):
                snapshots.append(snapshot)
        # Newest available bytes for each material; previous snapshots stay addressable.
        selected = {}
        for snapshot in sorted(snapshots, key=lambda item: item.stored_at, reverse=True):
            selected.setdefault((snapshot.role, snapshot.url, snapshot.package_member), snapshot)
        return list(selected.values())

    def copy_to(self, snapshot: MaterialSnapshot, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        original = self.root / snapshot.file
        target = folder / ("original" + original.suffix)
        shutil.copyfile(original, target)
        return target

    def import_source(self, folder: str | Path) -> list[MaterialSnapshot]:
        """Explicitly import one retained source, leaving its run untouched."""
        folder = Path(folder).resolve()
        doc = SourceDocument.model_validate_json((folder / "source.json").read_text("utf-8"))
        imported = []
        for material in doc.materials:
            if material.availability not in {"partial", "full_text"} or not (
                material.passage_ids or material.visual_ids
            ):
                continue
            local = folder / "materials" / material.material_id
            if not material.original_path:
                continue
            path = local / Path(material.original_path).name
            if not material.snapshot_ref and (local / "rendered.html").exists():
                path = local / "rendered.html"
            if not path.is_file():
                continue
            imported.append(
                self.retain(
                    doc.candidate,
                    path,
                    role=material.role,
                    url=material.url,
                    acquired_url=material.acquired_url or material.url,
                    title=material.title,
                    package_member=material.package_member,
                    acquired_at=material.acquired_at,
                    origin_source=str(folder),
                )
            )
        for visual in doc.visuals:
            if not visual.path or not visual.url:
                continue
            saved_path = Path(visual.path)
            try:
                relative = saved_path.relative_to(folder)
            except ValueError:
                relative = Path(saved_path.name)
            path = folder / relative
            if path.is_file():
                imported.append(
                    self.retain(
                        doc.candidate,
                        path,
                        role="visual",
                        url=visual.url,
                        title=visual.locator,
                        origin_source=str(folder),
                    )
                )
        return imported
