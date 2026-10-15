from __future__ import annotations

import asyncio
import re
from datetime import date
from time import monotonic
from urllib.parse import quote, urlsplit

import httpx
from lxml import etree
from pydantic import BaseModel, Field

from spacebio_review.models.config import credential

from .types import SourceCandidate


class DiscoveryResult(BaseModel):
    candidates: list[SourceCandidate] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    channels: dict[str, str] = Field(default_factory=dict)


class DiscoveryConfig(BaseModel):
    enabled: list[str] = Field(
        default_factory=lambda: [
            "europe_pmc",
            "openalex",
            "openalex_semantic",
            "semantic_scholar",
            "arxiv",
            "crossref",
            "tavily",
        ]
    )
    intervals: dict[str, float] = Field(
        default_factory=lambda: {
            "europe_pmc": 0.2,
            "openalex": 0.1,
            "openalex_semantic": 1.0,
            "semantic_scholar": 1.0,
            "arxiv": 3.0,
            "crossref": 0.2,
            "tavily": 0.2,
        }
    )
    cache_seconds: float = Field(default=300, ge=0)
    max_relation_seeds: int = Field(default=2, ge=1, le=4)


def optional_secret(name: str, env_file: str | None = ".env") -> str | None:
    try:
        return credential(name, env_file)
    except ValueError:
        return None


def parse_date(value: str | None) -> tuple[date | None, str]:
    if not value:
        return None, "unknown"
    match = re.match(r"^(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", value)
    if not match:
        return None, "unknown"
    year, month, day = match.groups()
    try:
        # An incomplete date is conservatively placed at the END of its period for cutoffs.
        import calendar

        m = int(month or 12)
        d = int(day) if day else calendar.monthrange(int(year), m)[1]
        return date(int(year), m, d), "day" if day else "month" if month else "year"
    except ValueError:
        return None, "unknown"


def identifiers(**values: str | None) -> dict[str, str]:
    result = {}
    for key, value in values.items():
        if not value:
            continue
        value = str(value).strip()
        if key == "doi":
            value = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I)
            value = value.lower()
        if key == "pmcid":
            value = value.upper()
        result[key] = value
    return result


def public_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and bool(parts.hostname) and not parts.username


class Discovery:
    """Discovery only: search snippets never become acquired original passages."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        env_file: str | None = ".env",
        config: DiscoveryConfig | None = None,
    ):
        self.client = client
        self.env_file = env_file
        self.config = config or DiscoveryConfig()
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}
        self._cache: dict[tuple, tuple[float, DiscoveryResult]] = {}
        self.cache_hits = 0

    async def _pace(self, provider):
        async with self._locks.setdefault(provider, asyncio.Lock()):
            delay = self.config.intervals.get(provider, 0) - (
                monotonic() - self._last.get(provider, 0)
            )
            if delay > 0:
                await asyncio.sleep(delay)
            self._last[provider] = monotonic()

    async def search(
        self, query: str, provider: str, cutoff: date | None, limit: int = 6
    ) -> DiscoveryResult:
        providers = ["europe_pmc", "openalex"] if provider == "both" else [provider]
        results = await asyncio.gather(
            *(self._search_one(query, p, cutoff, limit) for p in providers)
        )
        return DiscoveryResult(
            candidates=[c for r in results for c in r.candidates],
            limitations=[n for r in results for n in r.limitations],
            channels={p: s for r in results for p, s in r.channels.items()},
        )

    async def _search_one(self, query, provider, cutoff, limit) -> DiscoveryResult:
        if provider not in self.config.enabled:
            return DiscoveryResult(
                limitations=[f"{provider}: disabled"], channels={provider: "disabled"}
            )
        key = (query, provider, cutoff, limit)
        cached = self._cache.get(key)
        if cached and monotonic() - cached[0] < self.config.cache_seconds:
            self.cache_hits += 1
            return cached[1].model_copy(deep=True)
        try:
            await self._pace(provider)
            if provider == "europe_pmc":
                result = await self._europe_pmc(query, cutoff, limit)
            elif provider == "openalex":
                result = await self._openalex(query, cutoff, limit)
            elif provider == "openalex_semantic":
                if not optional_secret("OPENALEX_API_KEY", self.env_file):
                    return DiscoveryResult(
                        limitations=[f"{provider}: not_configured"],
                        channels={provider: "not_configured"},
                    )
                result = await self._openalex(query[:2000], cutoff, min(limit, 50), semantic=True)
                if len(query) > 2000:
                    result.limitations.append("openalex_semantic: query_truncated_to_2000_chars")
            elif provider == "semantic_scholar":
                result = await self._scholar(query, limit)
            elif provider == "arxiv":
                result = await self._arxiv(query, limit)
            elif provider == "crossref":
                result = await self._crossref(query, limit)
            elif provider == "tavily":
                result = await self._tavily(query, limit)
            else:
                return DiscoveryResult(limitations=[f"{provider}: unsupported_provider"])
            if cutoff:
                before = len(result.candidates)
                result.candidates = [c for c in result.candidates if c.eligible(cutoff)]
                if before != len(result.candidates):
                    result.limitations.append(f"{provider}: excluded_after_cutoff")
            result.channels.setdefault(provider, "ok" if result.candidates else "empty")
            if result.channels[provider] in {"ok", "empty"}:
                self._cache[key] = monotonic(), result.model_copy(deep=True)
            return result
        except (httpx.HTTPError, ValueError, KeyError, TypeError, etree.XMLSyntaxError) as exc:
            status = self._failure(exc)
            return DiscoveryResult(
                limitations=[f"{provider}: {status}"], channels={provider: status}
            )

    @staticmethod
    def _failure(exc):
        if isinstance(exc, httpx.HTTPStatusError):
            if exc.response.status_code == 429:
                return "rate_limited"
            if exc.response.status_code in {401, 403}:
                return "authorization_failed"
        return "request_failed"

    async def _europe_pmc(self, query, cutoff, limit):
        if cutoff:
            query = f"({query}) AND FIRST_PDATE:[0001-01-01 TO {cutoff.isoformat()}]"
        response = await self.client.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": query, "format": "json", "resultType": "core", "pageSize": limit},
        )
        response.raise_for_status()
        candidates = []
        for item in response.json().get("resultList", {}).get("result", []):
            ids = identifiers(
                doi=item.get("doi"),
                pmcid=item.get("pmcid"),
                pmid=item.get("id") if item.get("source") == "MED" else None,
            )
            urls = []
            if ids.get("pmcid"):
                urls.append(
                    "https://www.ebi.ac.uk/europepmc/webservices/rest/"
                    + ids["pmcid"]
                    + "/fullTextXML"
                )
            for link in item.get("fullTextUrlList", {}).get("fullTextUrl", []):
                if link.get("availability") in {"Open access", "Free"} and link.get("url"):
                    urls.append(link["url"])
            published, precision = parse_date(item.get("firstPublicationDate"))
            candidates.append(
                SourceCandidate(
                    title=item.get("title", "Untitled"),
                    identifiers=ids,
                    publication_date=published,
                    date_precision=precision,
                    url=f"https://europepmc.org/article/{item.get('source', 'MED')}/{item['id']}",
                    fulltext_urls=list(dict.fromkeys(urls)),
                    abstract=item.get("abstractText", ""),
                    discovered_by=["europe_pmc"],
                    authors=[
                        a.get("fullName", "") for a in item.get("authorList", {}).get("author", [])
                    ],
                    venue=item.get("journalInfo", {}).get("journal", {}).get("title", ""),
                    volume=item.get("journalInfo", {}).get("volume") or "",
                    issue=item.get("journalInfo", {}).get("issue") or "",
                    pages=item.get("pageInfo") or "",
                )
            )
        return DiscoveryResult(candidates=candidates)

    async def locate(self, candidate: SourceCandidate, provider: str, cutoff=None):
        """Locate one selected work by identity, without doing a topic search."""
        ids = candidate.identifiers
        if provider not in self.config.enabled:
            return DiscoveryResult(channels={provider: "disabled"})
        if provider == "europe_pmc":
            if ids.get("pmcid"):
                query = f"PMCID:{ids['pmcid']}"
            elif ids.get("doi"):
                query = f'DOI:"{ids["doi"]}"'
            elif ids.get("pmid"):
                query = f"EXT_ID:{ids['pmid']} AND SRC:MED"
            else:
                return DiscoveryResult(channels={provider: "no_identifier"})
            result = await self.search(query, provider, cutoff, limit=2)
        elif provider == "openalex":
            identifier = (ids["openalex"].rsplit("/", 1)[-1] if ids.get("openalex") else None) or (
                "https://doi.org/" + ids["doi"] if ids.get("doi") else None
            )
            if not identifier:
                return DiscoveryResult(channels={provider: "no_identifier"})
            key = ("locate", provider, identifier, cutoff)
            cached = self._cache.get(key)
            if cached and monotonic() - cached[0] < self.config.cache_seconds:
                self.cache_hits += 1
                return cached[1].model_copy(deep=True)
            try:
                await self._pace(provider)
                result = await self._openalex("", cutoff, 1, identifier=identifier)
                result.candidates = [c for c in result.candidates if c.eligible(cutoff)]
                result.channels[provider] = "ok" if result.candidates else "empty"
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                status = self._failure(exc)
                return DiscoveryResult(
                    channels={provider: status}, limitations=[f"{provider}: {status}"]
                )
        else:
            return DiscoveryResult(channels={provider: "unsupported_provider"})
        # Identity agreement, not title similarity, determines the source being enriched.
        result.candidates = [
            c for c in result.candidates if any(c.identifiers.get(k) == v for k, v in ids.items())
        ]
        if provider == "openalex":
            result.channels[provider] = "ok" if result.candidates else "empty"
            self._cache[key] = monotonic(), result.model_copy(deep=True)
        return result

    async def _openalex(self, query, cutoff, limit, semantic=False, *, identifier=None):
        params = (
            {}
            if identifier
            else {"search.semantic" if semantic else "search": query, "per-page": limit}
        )
        key = optional_secret("OPENALEX_API_KEY", self.env_file)
        email = optional_secret("OPENALEX_EMAIL", self.env_file)
        if key:
            params["api_key"] = key
        if email:
            params["mailto"] = email
        if cutoff and not identifier:
            params["filter"] = f"to_publication_date:{cutoff.isoformat()}"
        endpoint = "https://api.openalex.org/works"
        if identifier:
            endpoint += "/" + quote(identifier, safe=":/")
        response = await self.client.get(endpoint, params=params)
        response.raise_for_status()
        candidates = []
        items = [response.json()] if identifier else response.json().get("results", [])
        for item in items:
            external = item.get("ids") or {}
            ids = identifiers(
                doi=item.get("doi"),
                pmid=(external.get("pmid") or "").rsplit("/", 1)[-1],
                pmcid=(external.get("pmcid") or "").rsplit("/", 1)[-1],
                openalex=item.get("id"),
            )
            words = {
                pos: word
                for word, positions in (item.get("abstract_inverted_index") or {}).items()
                for pos in positions
            }
            content = item.get("content_urls") or {}
            urls = [content[k] for k in ("grobid_xml", "pdf") if content.get(k) and key]
            # Historical tasks use the primary publication's locations, not a later
            # repository/published version attached to the same aggregate work.
            locations = (
                [item.get("primary_location") or {}] if cutoff else item.get("locations") or []
            )
            for location in locations:
                if location.get("is_oa"):
                    urls.extend(
                        location[k] for k in ("pdf_url", "landing_page_url") if location.get(k)
                    )
            published, precision = parse_date(item.get("publication_date"))
            candidates.append(
                SourceCandidate(
                    title=item.get("display_name") or "Untitled",
                    identifiers=ids,
                    publication_date=published,
                    date_precision=precision,
                    url=item.get("doi") or item["id"],
                    fulltext_urls=list(dict.fromkeys(urls)),
                    abstract=" ".join(words[p] for p in sorted(words)),
                    discovered_by=["openalex_semantic" if semantic else "openalex"],
                    volume=(item.get("biblio") or {}).get("volume") or "",
                    issue=(item.get("biblio") or {}).get("issue") or "",
                    pages="–".join(
                        dict.fromkeys(
                            filter(
                                None,
                                [
                                    (item.get("biblio") or {}).get("first_page"),
                                    (item.get("biblio") or {}).get("last_page"),
                                ],
                            )
                        )
                    ),
                    authors=[
                        a.get("author", {}).get("display_name", "")
                        for a in item.get("authorships", [])
                    ],
                    venue=((item.get("primary_location") or {}).get("source") or {}).get(
                        "display_name", ""
                    ),
                    version=item.get("type", ""),
                )
            )
        return DiscoveryResult(candidates=candidates)

    async def _tavily(self, query, limit):
        key = optional_secret("TAVILY_API_KEY", self.env_file)
        if not key:
            return DiscoveryResult(
                limitations=["tavily: not_configured; known URLs still readable"],
                channels={"tavily": "not_configured"},
            )
        response = await self.client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {key}"},
            json={"query": query, "max_results": limit, "include_answer": False},
        )
        response.raise_for_status()
        candidates = []
        for item in response.json().get("results", []):
            published, precision = parse_date(item.get("published_date"))
            candidates.append(
                SourceCandidate(
                    title=item.get("title", "Web source"),
                    kind="web",
                    url=item["url"],
                    fulltext_urls=[item["url"]],
                    publication_date=published,
                    date_precision=precision,
                    discovered_by=["tavily"],
                )
            )
        return DiscoveryResult(candidates=candidates)

    def _scholar_headers(self):
        key = optional_secret("SEMANTIC_SCHOLAR_API_KEY", self.env_file)
        return {"x-api-key": key} if key else {}

    @staticmethod
    def _scholar_candidate(item, channel):
        external = item.get("externalIds") or {}
        doi, arxiv = external.get("DOI"), external.get("ArXiv")
        published, precision = parse_date(
            item.get("publicationDate") or str(item.get("year") or "")
        )
        pdf = (item.get("openAccessPdf") or {}).get("url")
        return SourceCandidate(
            title=item.get("title") or "Untitled",
            identifiers=identifiers(
                doi=doi, semantic_scholar=item.get("paperId"), pmid=external.get("PubMed")
            ),
            # S2 aggregates preprint/journal identities; don't merge their actual files.
            related_identifiers=identifiers(arxiv=arxiv),
            publication_date=published,
            date_precision=precision,
            url=item.get("url") or (f"https://doi.org/{doi}" if doi else ""),
            fulltext_urls=[pdf] if pdf else [],
            abstract=item.get("abstract") or "",
            authors=[a.get("name", "") for a in item.get("authors", [])],
            venue=item.get("venue") or "",
            discovered_by=[channel],
        )

    async def _scholar(self, query, limit):
        response = await self.client.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": query, "limit": min(limit, 100), "fields": self._scholar_fields()},
            headers=self._scholar_headers(),
        )
        response.raise_for_status()
        return DiscoveryResult(
            candidates=[
                self._scholar_candidate(i, "semantic_scholar")
                for i in response.json().get("data", [])
            ]
        )

    @staticmethod
    def _scholar_fields():
        return "title,abstract,externalIds,publicationDate,year,authors,venue,url,openAccessPdf"

    async def related(self, seeds: list[str], relation: str, cutoff=None, limit=6):
        """Only explicit seeds, one HTTP page per seed. Never enqueue returned papers."""
        channel = "semantic_scholar_" + relation
        if "semantic_scholar" not in self.config.enabled:
            return DiscoveryResult(channels={channel: "disabled"})
        if relation not in {"recommendations", "citations", "references"}:
            return DiscoveryResult(channels={channel: "unsupported_relation"})
        result = DiscoveryResult()
        for seed in list(dict.fromkeys(seeds))[: self.config.max_relation_seeds]:
            cache_key = (seed, channel, cutoff, limit)
            cached = self._cache.get(cache_key)
            if cached and monotonic() - cached[0] < self.config.cache_seconds:
                self.cache_hits += 1
                part = cached[1].model_copy(deep=True)
            else:
                try:
                    await self._pace("semantic_scholar")
                    base = "https://api.semanticscholar.org/"
                    path = (
                        f"recommendations/v1/papers/forpaper/{quote(seed, safe='')}"
                        if relation == "recommendations"
                        else f"graph/v1/paper/{quote(seed, safe='')}/{relation}"
                    )
                    response = await self.client.get(
                        base + path,
                        headers=self._scholar_headers(),
                        params={"limit": min(limit, 20), "fields": self._scholar_fields()},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    rows = (
                        payload.get("recommendedPapers", [])
                        if relation == "recommendations"
                        else [
                            r.get("citingPaper" if relation == "citations" else "citedPaper", {})
                            for r in payload.get("data", [])
                        ]
                    )
                    candidates = [self._scholar_candidate(i, channel) for i in rows if i]
                    eligible = [c for c in candidates if c.eligible(cutoff)]
                    part = DiscoveryResult(
                        candidates=eligible,
                        channels={channel: "ok" if eligible else "empty"},
                        limitations=[f"{channel}: excluded_after_cutoff"]
                        if len(eligible) != len(candidates)
                        else [],
                    )
                    self._cache[cache_key] = monotonic(), part.model_copy(deep=True)
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    status = self._failure(exc)
                    part = DiscoveryResult(
                        channels={channel: status}, limitations=[f"{channel}: {status}"]
                    )
            result.candidates.extend(part.candidates)
            result.limitations.extend(part.limitations)
            # Per-seed status preserves partial failures.
            result.channels[f"{channel}:{seed}"] = part.channels.get(channel, "unknown")
        return result

    async def _arxiv(self, query, limit):
        response = await self.client.get(
            "https://export.arxiv.org/api/query",
            params={
                "search_query": query,
                "start": 0,
                "max_results": min(limit, 20),
                "sortBy": "relevance",
            },
        )
        response.raise_for_status()
        root = etree.fromstring(response.content, parser=etree.XMLParser(resolve_entities=False))
        ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
        candidates = []
        for entry in root.findall("a:entry", ns):

            def value(tag):
                return (entry.findtext(tag, namespaces=ns) or "").strip()

            url = value("a:id")
            arxiv_id = url.split("/abs/")[-1]
            published, precision = parse_date(value("a:published"))
            updated, _ = parse_date(value("a:updated"))
            # Versioned URLs prevent historical v1 from silently reading today's vN.
            candidates.append(
                SourceCandidate(
                    title=" ".join(value("a:title").split()),
                    identifiers={"arxiv": arxiv_id},
                    related_identifiers=identifiers(
                        doi=value("x:doi"), arxiv_work=re.sub(r"v\d+$", "", arxiv_id)
                    ),
                    version="preprint:" + arxiv_id,
                    publication_date=published,
                    version_date=updated,
                    date_precision=precision,
                    authors=[
                        a.findtext("a:name", namespaces=ns) or ""
                        for a in entry.findall("a:author", ns)
                    ],
                    venue=value("x:journal_ref") or "arXiv preprint",
                    abstract=value("a:summary"),
                    url=url,
                    fulltext_urls=[f"https://arxiv.org/pdf/{arxiv_id}"],
                    discovered_by=["arxiv"],
                )
            )
        return DiscoveryResult(candidates=candidates)

    async def _crossref(self, query, limit):
        params = {"query.bibliographic": query, "rows": min(limit, 20)}
        email = optional_secret("CROSSREF_EMAIL", self.env_file)
        if email:
            params["mailto"] = email
        response = await self.client.get("https://api.crossref.org/works", params=params)
        response.raise_for_status()
        candidates = []
        for item in response.json().get("message", {}).get("items", []):
            parts = (item.get("published") or item.get("issued") or {}).get("date-parts", [[]])[0]
            published, precision = parse_date("-".join(str(p) for p in parts))
            relations = {}
            for relation in ("is-preprint-of", "has-preprint", "is-version-of", "has-version"):
                for linked in item.get("relation", {}).get(relation, []):
                    if linked.get("id-type") == "doi":
                        relations["doi"] = identifiers(doi=linked["id"])["doi"]
            candidates.append(
                SourceCandidate(
                    title=(item.get("title") or ["Untitled"])[0],
                    identifiers=identifiers(doi=item.get("DOI")),
                    publication_date=published,
                    date_precision=precision,
                    authors=[
                        " ".join(filter(None, (a.get("given"), a.get("family"))))
                        or a.get("name", "")
                        for a in item.get("author", [])
                    ],
                    venue=(item.get("container-title") or [""])[0],
                    volume=item.get("volume") or "",
                    issue=item.get("issue") or "",
                    pages=item.get("page") or "",
                    article_number=item.get("article-number") or "",
                    corporate_authors=[a["name"] for a in item.get("author", []) if a.get("name")],
                    version=item.get("type", ""),
                    related_identifiers=relations,
                    abstract=item.get("abstract") or "",
                    url=item.get("URL") or "",
                    fulltext_urls=[link["URL"] for link in item.get("link", []) if link.get("URL")],
                    discovered_by=["crossref"],
                )
            )
        return DiscoveryResult(candidates=candidates)
