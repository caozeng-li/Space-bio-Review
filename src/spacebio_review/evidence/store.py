from __future__ import annotations

import asyncio
import mimetypes
from hashlib import sha256
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4
from zipfile import ZipFile

import httpx
from lxml import html

from .discovery import identifiers, optional_secret, public_url
from .documents import add_material, add_passage, blocked_html, parse_document, render_public_page
from .normalization import ConversionError, normalize_doc
from .types import SourceCandidate, SourceDocument, Visual


class SourceStore:
    """Originals for ONE run. No opinions, conversation history or cross-project index."""

    def __init__(
        self,
        root: str | Path,
        client: httpx.AsyncClient,
        *,
        env_file: str | None = ".env",
        browser_fallback: bool = True,
        legacy_document=None,
        discovery=None,
    ):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.client = client
        self.env_file = env_file
        self.browser_fallback = browser_fallback
        self.legacy_document = legacy_document
        self.discovery = discovery
        self._located: set[tuple] = set()
        self._candidates: dict[str, SourceCandidate] = {}
        self._documents: dict[str, SourceDocument] = {}
        self._identities: dict[tuple[str, str, str], str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._attempted: dict[str, set[str]] = {}
        self._transient_failures: dict[tuple[str, str], int] = {}
        self._parse_lock = asyncio.Lock()

    def register(self, candidate: SourceCandidate) -> str:
        candidate = candidate.model_copy(deep=True)
        candidate.identifiers = identifiers(**candidate.identifiers)
        keys = [(candidate.kind, k, v) for k, v in candidate.identifiers.items()]
        if candidate.url:
            keys.append((candidate.kind, "url", candidate.url))
        source_id = next((self._identities[k] for k in keys if k in self._identities), None)
        if source_id is None:
            source_id = "src-" + uuid4().hex[:12]
            self._candidates[source_id] = candidate
            self._locks[source_id] = asyncio.Lock()
        else:
            current = self._candidates[source_id]
            current.identifiers.update(candidate.identifiers)
            current.fulltext_urls = list(
                dict.fromkeys(current.fulltext_urls + candidate.fulltext_urls)
            )
            current.discovered_by = list(
                dict.fromkeys(current.discovered_by + candidate.discovered_by)
            )
            current.abstract = candidate.abstract or current.abstract
            current.authors = current.authors or candidate.authors
            current.venue = current.venue or candidate.venue
            for field in ("volume", "issue", "pages", "article_number", "corporate_authors"):
                if not getattr(current, field):
                    setattr(current, field, getattr(candidate, field))
            current.version = current.version or candidate.version
            current.related_identifiers.update(candidate.related_identifiers)
            if candidate.version_date and (
                not current.version_date or candidate.version_date > current.version_date
            ):
                current.version_date = candidate.version_date
            if candidate.publication_date and (
                current.publication_date is None
                or candidate.publication_date > current.publication_date
            ):
                current.publication_date = candidate.publication_date
                current.date_precision = candidate.date_precision
        for key in keys:
            self._identities[key] = source_id
        folder = self.root / source_id
        folder.mkdir(exist_ok=True)
        (folder / "candidate.json").write_text(
            self._candidates[source_id].model_dump_json(indent=2), encoding="utf-8"
        )
        return source_id

    def related_versions(self, source_id: str) -> list[str]:
        """Explicit identity links only; title similarity is not research identity."""
        import re

        def keys_for(candidate):
            keys = set(candidate.identifiers.items()) | set(candidate.related_identifiers.items())
            for key, value in list(keys):
                if key in {"arxiv", "arxiv_work"}:
                    keys.add(("arxiv_work", re.sub(r"v\d+$", "", value.split("/abs/")[-1])))
            return keys

        candidate = self._candidates[source_id]
        keys = keys_for(candidate)
        return [
            sid
            for sid, other in self._candidates.items()
            if sid != source_id and keys & keys_for(other)
        ]

    def candidate(self, source_id: str) -> SourceCandidate:
        return self._candidates[source_id].model_copy(deep=True)

    def catalogue(self) -> list[dict]:
        """Discovered titles, without private retrieval sessions or agent judgments."""
        return [
            {
                "source_id": sid,
                "title": c.title,
                "url": c.url,
                "identifiers": c.identifiers,
                "acquired": sid in self._documents,
            }
            for sid, c in self._candidates.items()
        ]

    def retained(self, source_id: str) -> SourceDocument | None:
        """Read retained originals without making a new network request during reporting."""
        doc = self._documents.get(source_id)
        return doc.model_copy(deep=True) if doc else None

    def restore(self):
        """Load this run's retained sources without network work or rewriting originals."""
        for path in self.root.glob("*/candidate.json"):
            sid = path.parent.name
            candidate = SourceCandidate.model_validate_json(path.read_text("utf-8"))
            self._candidates[sid] = candidate
            self._locks[sid] = asyncio.Lock()
            for key, value in candidate.identifiers.items():
                self._identities[(candidate.kind, key, value)] = sid
            if candidate.url:
                self._identities[(candidate.kind, "url", candidate.url)] = sid
            retained = path.parent / "source.json"
            if retained.exists():
                doc = SourceDocument.model_validate_json(retained.read_text("utf-8"))
                if doc.source_id != sid:
                    raise ValueError("retained_source_identity_mismatch")
                self._documents[sid] = doc

    def _save(self, doc: SourceDocument) -> None:
        folder = self.root / doc.source_id
        folder.mkdir(exist_ok=True)
        (folder / "source.json").write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        self._documents[doc.source_id] = doc.model_copy(deep=True)

    async def add_local(self, path: str | Path, *, kind="application", title=None) -> str:
        path = Path(path).resolve()
        candidate = SourceCandidate(title=title or path.stem, kind=kind)
        source_id = self.register(candidate)
        folder = self.root / source_id
        folder.mkdir(exist_ok=True)
        saved = folder / ("original" + path.suffix.lower())
        saved.write_bytes(path.read_bytes())
        normalized = None
        if path.suffix.lower() == ".doc":
            try:
                normalized = await asyncio.to_thread(
                    normalize_doc, saved, folder, self.legacy_document
                )
            except ConversionError as exc:
                self._save(
                    SourceDocument(
                        source_id=source_id,
                        candidate=candidate,
                        original_path=str(saved),
                        reading_gaps=[str(exc)],
                        limitations=[str(exc)],
                    )
                )
                return source_id
        async with self._parse_lock:
            doc = await asyncio.to_thread(
                parse_document,
                source_id,
                candidate,
                normalized or saved,
                folder,
                legacy_config=self.legacy_document,
            )
        if normalized:
            doc.original_path, doc.normalized_path = str(saved), str(normalized)
            doc.limitations.append("local_DOC_conversion; layout may differ from original")
        self._save(doc)
        return source_id

    async def _get(self, url: str) -> httpx.Response:
        if not public_url(url):
            raise ValueError("unsupported_url")
        # The OpenAlex secret is sent only to its content host, never to publisher URLs.
        params = None
        if urlsplit(url).hostname == "content.openalex.org":
            key = optional_secret("OPENALEX_API_KEY", self.env_file)
            if key:
                params = {"api_key": key}
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        return response

    @staticmethod
    def _format_priority(material):
        url = material.url.lower()
        if "fulltextxml" in url or ".xml" in url or "grobid-xml" in url:
            return 0
        if ".pdf" in url or "pdf=render" in url:
            return 2
        return 1

    def _seed_materials(self, doc):
        candidate = self.candidate(doc.source_id)
        urls = list(candidate.fulltext_urls)
        if candidate.identifiers.get("pmcid"):
            urls.insert(
                0,
                "https://www.ebi.ac.uk/europepmc/webservices/rest/"
                + candidate.identifiers["pmcid"]
                + "/fullTextXML",
            )
        if candidate.url:
            urls.append(candidate.url)
        for url in dict.fromkeys(urls):
            add_material(doc, url=url, title="Article full text")

    @staticmethod
    def _suffix(content, content_type, url):
        suffix = Path(urlsplit(url).path).suffix.lower()
        if content.startswith(b"%PDF"):
            return ".pdf"
        if "html" in content_type:
            return ".html"
        if "wordprocessingml" in content_type:
            return ".docx"
        if "xml" in content_type:
            return ".xml"
        if content_type.startswith("image/"):
            return mimetypes.guess_extension(content_type.split(";")[0]) or ".png"
        if content_type.startswith("text/"):
            return suffix if suffix in {".csv", ".md", ".txt"} else ".txt"
        if suffix in {".pdf", ".docx", ".xml", ".png", ".jpg", ".csv", ".txt"}:
            return suffix
        raise ValueError("unsupported_response_type")

    async def _material_file(self, source_id, material):
        folder = self.root / source_id / "materials" / material.material_id
        folder.mkdir(parents=True, exist_ok=True)
        if material.package_member:
            package = (
                self.root
                / source_id
                / "packages"
                / (sha256(material.url.encode()).hexdigest()[:16] + ".zip")
            )
            if not package.exists():
                response = await self._get(material.url)
                package.parent.mkdir(exist_ok=True)
                package.write_bytes(response.content)
            with ZipFile(package) as archive:
                names = archive.namelist()
                member = material.package_member.lstrip("./")
                matches = [name for name in names if name == member]
                if not matches:
                    matches = [name for name in names if Path(name).name == Path(member).name]
                if len(matches) != 1:
                    raise ValueError("supplementary_member_not_identified")
                content = archive.read(matches[0])
            content_type = mimetypes.guess_type(member)[0] or "application/octet-stream"
            suffix = self._suffix(content, content_type, member)
            acquired_url = material.url
        else:
            response = await self._get(material.url)
            content = response.content
            content_type = response.headers.get("content-type", "").lower()
            acquired_url = str(response.url)
            suffix = self._suffix(content, content_type, acquired_url)
        path = folder / ("original" + suffix)
        path.write_bytes(content)
        material.original_path, material.acquired_url = str(path), acquired_url
        return path, content_type, acquired_url

    @staticmethod
    def _retain_original_dates(candidate, original):
        if original.publication_date and (
            not candidate.publication_date or original.publication_date > candidate.publication_date
        ):
            candidate.publication_date = original.publication_date
            candidate.date_precision = original.date_precision
        if original.version_date:
            candidate.version_date = max(
                candidate.version_date or original.version_date, original.version_date
            )
        return candidate

    def _append_document(self, doc, material, parsed):
        prefix = material.material_id + "-"
        label = material.title or material.material_id
        if material.role == "supplement":
            filename = Path(material.package_member or urlsplit(material.url).path).name
            if filename and filename not in label:
                label += f" ({filename})"
        for passage in parsed.passages:
            passage.passage_id = prefix + passage.passage_id
            if passage.unit_id:
                passage.unit_id = prefix + passage.unit_id
            passage.locator = f"{label} / {passage.locator}"
        for visual in parsed.visuals:
            visual.visual_id = prefix + visual.visual_id
            # Main-article XML locators are also used to resolve figure originals.
            if material.role == "supplement":
                visual.locator = f"{label} / {visual.locator}"
        material.passage_ids = [p.passage_id for p in parsed.passages]
        material.visual_ids = [v.visual_id for v in parsed.visuals]
        material.availability = parsed.availability
        material.limitations = list(dict.fromkeys(parsed.limitations + parsed.reading_gaps))
        doc.passages.extend(parsed.passages)
        doc.visuals.extend(parsed.visuals)
        existing = {m.material_id for m in doc.materials}
        doc.materials.extend(m for m in parsed.materials if m.material_id not in existing)
        doc.reading_gaps.extend(parsed.reading_gaps)
        doc.limitations.extend(parsed.limitations)
        self._retain_original_dates(doc.candidate, parsed.candidate)
        if material.role == "main":
            rank = {"unavailable": 0, "abstract": 1, "partial": 2, "full_text": 3}
            if rank[parsed.availability] > rank[doc.availability]:
                doc.availability = parsed.availability
                doc.original_path = parsed.original_path
                doc.acquired_url = parsed.acquired_url
                doc.normalized_path = parsed.normalized_path

    async def _obtain(self, doc, material, cutoff):
        if not material.url or material.original_path:
            return
        attempted = self._attempted.setdefault(doc.source_id, set())
        key = material.material_id
        if key in attempted:
            return
        attempted.add(key)
        try:
            path, content_type, acquired_url = await self._material_file(doc.source_id, material)
            async with self._parse_lock:
                parsed = await asyncio.to_thread(
                    parse_document,
                    doc.source_id,
                    doc.candidate,
                    path,
                    path.parent / "parsed",
                    content_type,
                    acquired_url,
                )
                if (
                    path.suffix == ".html"
                    and not parsed.passages
                    and not parsed.materials
                    and self.browser_fallback
                    and not blocked_html(path.read_text("utf-8"))
                ):
                    rendered, final_url = await render_public_page(acquired_url)
                    rendered_path = path.parent / "rendered.html"
                    rendered_path.write_bytes(rendered)
                    parsed = await asyncio.to_thread(
                        parse_document,
                        doc.source_id,
                        doc.candidate,
                        rendered_path,
                        path.parent / "parsed-rendered",
                        "text/html",
                        final_url,
                    )
                    parsed.limitations.append("browser_rendered_public_page")
            if not parsed.candidate.eligible(cutoff):
                material.limitations.append("original_after_cutoff")
                doc.limitations.append(f"{material.material_id}: original_after_cutoff")
                return
            self._append_document(doc, material, parsed)
        except Exception as exc:
            status = (
                f"HTTP_{exc.response.status_code}"
                if isinstance(exc, httpx.HTTPStatusError)
                else str(exc)
                if isinstance(exc, ValueError)
                else type(exc).__name__
            )
            doc.limitations.append(f"{material.material_id}: acquisition_failed:{status}")
            material.limitations.append(f"acquisition_failed:{status}")
            transient = isinstance(exc, httpx.TransportError) or (
                isinstance(exc, httpx.HTTPStatusError)
                and (exc.response.status_code in {408, 429} or exc.response.status_code >= 500)
            )
            if transient:
                failure_key = (doc.source_id, key)
                count = self._transient_failures.get(failure_key, 0) + 1
                self._transient_failures[failure_key] = count
                if count < 3:
                    attempted.discard(key)
        self._save(doc)

    async def _main_materials(self, doc, cutoff, visited):
        while doc.availability != "full_text":
            choices = sorted(
                [m for m in doc.materials if m.role == "main" and m.material_id not in visited],
                key=self._format_priority,
            )
            if not choices:
                break
            material = choices[0]
            visited.add(material.material_id)
            await self._obtain(doc, material, cutoff)

    async def acquire(self, source_id: str, *, material_id=None, cutoff=None) -> SourceDocument:
        async with self._locks[source_id]:
            candidate = self.candidate(source_id)
            if not candidate.eligible(cutoff):
                raise ValueError("source_after_cutoff")
            cached = self._documents.get(source_id)
            doc = (
                cached.model_copy(deep=True)
                if cached
                else SourceDocument(source_id=source_id, candidate=candidate)
            )
            if not doc.candidate.eligible(cutoff):
                raise ValueError("original_after_cutoff")
            self._seed_materials(doc)
            if material_id:
                material = next((m for m in doc.materials if m.material_id == material_id), None)
                if material is None:
                    raise ValueError("unknown_source_material")
                await self._obtain(doc, material, cutoff)
            else:
                visited = set()
                await self._main_materials(doc, cutoff, visited)
                if (
                    doc.availability != "full_text"
                    and self.discovery
                    and candidate.kind == "literature"
                ):
                    for provider in ("europe_pmc", "openalex"):
                        identity = (source_id, provider, cutoff)
                        if identity in self._located:
                            continue
                        self._located.add(identity)
                        result = await self.discovery.locate(
                            self.candidate(source_id), provider, cutoff
                        )
                        doc.limitations.extend(result.limitations)
                        for found in result.candidates:
                            self.register(found)
                        doc.candidate = self._retain_original_dates(
                            self.candidate(source_id), doc.candidate
                        )
                        self._seed_materials(doc)
                        await self._main_materials(doc, cutoff, visited)
                        if doc.availability == "full_text":
                            break
            if not doc.passages and doc.candidate.abstract:
                text = html.fromstring("<div>" + doc.candidate.abstract + "</div>").text_content()
                add_passage(doc, text, "Abstract", kind="abstract")
                doc.availability = "abstract"
                path = self.root / source_id / "abstract.txt"
                path.write_text(text, encoding="utf-8")
                doc.original_path = str(path)
            doc.limitations = list(dict.fromkeys(doc.limitations))
            doc.reading_gaps = list(dict.fromkeys(doc.reading_gaps))
            self._save(doc)
            return doc.model_copy(deep=True)

    async def visual_bytes(self, source_id: str, visual: Visual) -> tuple[bytes, str]:
        if visual.path:
            path = Path(visual.path)
            return path.read_bytes(), mimetypes.guess_type(path.name)[0] or "image/png"
        from io import BytesIO

        from PIL import Image

        async def download(url):
            response = await self._get(url)
            # Decode pixels; an HTTP 200 challenge must not reach the vision model.
            picture = Image.open(BytesIO(response.content))
            picture.load()
            target = self.root / source_id / f"{visual.visual_id}.png"
            picture.save(target, format="PNG")
            cached = self._documents[source_id].model_copy(deep=True)
            for item in cached.visuals:
                if item.visual_id == visual.visual_id:
                    item.path, item.url = str(target), url
            self._save(cached)
            return target.read_bytes(), "image/png"

        if visual.url:
            try:
                return await download(visual.url)
            except Exception:
                pass
        candidate = self.candidate(source_id)
        pmcid, doi = candidate.identifiers.get("pmcid"), candidate.identifiers.get("doi")
        pages = [f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"] if pmcid else []
        pages += ([f"https://doi.org/{doi}"] if doi else []) + [candidate.url]
        for page_url in dict.fromkeys(pages):
            if not page_url:
                continue
            try:
                page = await self._get(page_url)
                if blocked_html(page.text):
                    continue
                tree = html.fromstring(page.content)
                links = tree.xpath("//*[@id=$id]//img/@src", id=visual.locator)
                links += tree.xpath("//img[contains(@src, $id)]/@src", id=visual.locator)
                for link in dict.fromkeys(links):
                    try:
                        return await download(urljoin(str(page.url), link))
                    except Exception:
                        continue
            except Exception:
                continue
        raise ValueError("image_not_available; caption_only")
