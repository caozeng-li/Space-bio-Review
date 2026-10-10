from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
from lxml import html

from .discovery import identifiers, optional_secret, public_url
from .documents import add_passage, blocked_html, parse_document, render_public_page
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
    ):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.client = client
        self.env_file = env_file
        self.browser_fallback = browser_fallback
        self.legacy_document = legacy_document
        self._candidates: dict[str, SourceCandidate] = {}
        self._documents: dict[str, SourceDocument] = {}
        self._identities: dict[tuple[str, str, str], str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._attempted: dict[str, set[str]] = {}
        self._transient_failures: dict[tuple[str, str], int] = {}
        self._acquisition_counts: dict[str, int] = {}
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

    async def acquire(self, source_id: str) -> SourceDocument:
        async with self._locks[source_id]:
            candidate = self.candidate(source_id)
            cached = self._documents.get(source_id)
            attempted = self._attempted.setdefault(source_id, set())
            urls = list(
                dict.fromkeys(candidate.fulltext_urls + ([candidate.url] if candidate.url else []))
            )
            if cached:
                cached = cached.model_copy(deep=True)
                original_date = cached.candidate.publication_date
                original_precision = cached.candidate.date_precision
                original_version_date = cached.candidate.version_date
                cached.candidate = candidate.model_copy(deep=True)
                if original_version_date:
                    cached.candidate.version_date = max(
                        candidate.version_date or original_version_date, original_version_date
                    )
                if original_date and (
                    not candidate.publication_date or original_date > candidate.publication_date
                ):
                    cached.candidate.publication_date = original_date
                    cached.candidate.date_precision = original_precision
                if cached.candidate.publication_date is None:
                    cached.candidate.publication_date = candidate.publication_date
                    cached.candidate.date_precision = candidate.date_precision
                if cached.availability == "full_text" or not any(u not in attempted for u in urls):
                    return cached
            folder = self.root / source_id
            folder.mkdir(exist_ok=True)
            best = cached or SourceDocument(source_id=source_id, candidate=candidate)
            notes = []
            for url in urls:
                if url in attempted:
                    continue
                index = self._acquisition_counts.get(source_id, 0)
                self._acquisition_counts[source_id] = index + 1
                attempted.add(url)
                try:
                    response = await self._get(url)
                    content = response.content
                    ctype = response.headers.get("content-type", "").lower()
                    suffix = (
                        ".pdf"
                        if content.startswith(b"%PDF")
                        else (
                            ".html"
                            if "html" in ctype
                            else ".xml"
                            if "xml" in ctype
                            else ".docx"
                            if "wordprocessingml" in ctype
                            else ".txt"
                        )
                    )
                    if suffix == ".txt" and not ctype.startswith("text/"):
                        notes.append("unsupported_response_type")
                        continue
                    path = folder / f"original-{index}{suffix}"
                    path.write_bytes(content)
                    async with self._parse_lock:
                        doc = await asyncio.to_thread(
                            parse_document,
                            source_id,
                            candidate,
                            path,
                            folder / f"parsed-{index}",
                            ctype,
                            url,
                        )
                    if suffix == ".html" and not doc.passages and self.browser_fallback:
                        if not blocked_html(content.decode(errors="replace")):
                            rendered, final_url = await render_public_page(url)
                            rendered_path = folder / f"rendered-{index}.html"
                            rendered_path.write_bytes(rendered)
                            doc = await asyncio.to_thread(
                                parse_document,
                                source_id,
                                candidate,
                                rendered_path,
                                folder / f"parsed-rendered-{index}",
                                "text/html",
                                final_url,
                            )
                            doc.limitations.append("browser_rendered_public_page")
                    rank = {"unavailable": 0, "abstract": 1, "partial": 2, "full_text": 3}
                    if rank[doc.availability] > rank[best.availability]:
                        if cached and cached.passages:
                            # Preserve earlier citations when another entry finds full text.
                            for p in doc.passages:
                                p.passage_id = f"r{index}-{p.passage_id}"
                                p.locator = f"original-{index}{suffix} / {p.locator}"
                            for v in doc.visuals:
                                v.visual_id = f"r{index}-{v.visual_id}"
                            doc.passages = cached.passages + doc.passages
                            doc.visuals = cached.visuals + doc.visuals
                        best = doc
                    notes.extend(doc.limitations)
                    if best.availability == "full_text":
                        break
                except Exception as exc:
                    notes.append(f"acquisition_failed:{type(exc).__name__}")
                    transient = isinstance(exc, httpx.TransportError) or (
                        isinstance(exc, httpx.HTTPStatusError)
                        and (
                            exc.response.status_code in {408, 429}
                            or exc.response.status_code >= 500
                        )
                    )
                    if transient:
                        key = (source_id, url)
                        failures = self._transient_failures.get(key, 0) + 1
                        self._transient_failures[key] = failures
                        if failures < 3:
                            attempted.discard(url)
                            notes.append("transient_failure; retry_available_on_next_read")
            if not best.passages and candidate.abstract:
                # Metadata abstracts are available even if every publisher denies access.
                text = html.fromstring("<div>" + candidate.abstract + "</div>").text_content()
                add_passage(best, text, "Abstract", kind="abstract")
                best.availability = "abstract"
                abstract_path = folder / "abstract.txt"
                abstract_path.write_text(text, encoding="utf-8")
                best.original_path = str(abstract_path)
            best.limitations = list(dict.fromkeys(best.limitations + notes))
            if best.availability != "full_text":
                best.limitations.append(
                    "Full text not confirmed; missing access is not negative evidence"
                )
            self._save(best)
            return best.model_copy(deep=True)

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
