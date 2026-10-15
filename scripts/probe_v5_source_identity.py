"""Offline source-entry and question-directed citation-following probe; no model calls."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import httpx

from spacebio_review.evidence.access import SourceAccess
from spacebio_review.evidence.discovery import Discovery, DiscoveryConfig
from spacebio_review.evidence.knowledge import AcquireSource, ReadSource, RelatedPapers
from spacebio_review.evidence.store import SourceStore
from spacebio_review.evidence.types import SourceCandidate

DOI = "10.14814/phy2.13532"
PMCID = "PMC1234567"
PMID = "12345678"
XML_URL = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{PMCID}/fullTextXML"
XML = b"""<article><body>
<sec><title>Methods</title><p>Original methods and experimental conditions.</p></sec>
<sec><title>Results</title><p>Original comparison and measurement results.</p></sec>
</body></article>"""


class Fixture:
    def __init__(self):
        self.requests = []

    def respond(self, request):
        self.requests.append(request)
        url = str(request.url)
        if url == XML_URL:
            return httpx.Response(200, headers={"content-type": "application/xml"}, content=XML)
        if request.url.path == "/europepmc/webservices/rest/search":
            return httpx.Response(
                200,
                json={
                    "resultList": {
                        "result": [
                            {
                                "title": "Fixture paper",
                                "doi": DOI,
                                "pmcid": PMCID,
                                "id": PMID,
                                "source": "MED",
                                "abstractText": "Fixture abstract.",
                            }
                        ]
                    }
                },
            )
        if request.url.path.endswith("/references"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"citedPaper": {"paperId": "method", "title": "Calcium reporter method"}},
                        {"citedPaper": {"paperId": "overview", "title": "Organoid overview"}},
                    ]
                },
            )
        # The selected publisher/landing page is unavailable; a same-paper repository exists.
        return httpx.Response(403)


async def main():
    fixture = Fixture()
    with tempfile.TemporaryDirectory(prefix="spacebio-source-entry-") as folder:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.respond)) as client:
            discovery = Discovery(
                client,
                config=DiscoveryConfig(
                    enabled=["europe_pmc", "semantic_scholar"],
                    intervals={"europe_pmc": 0, "semantic_scholar": 0},
                ),
            )
            urls = [
                f"https://doi.org/{DOI}",
                f"https://dx.doi.org/{DOI.upper()}",
                f"https://pmc.ncbi.nlm.nih.gov/articles/{PMCID}/",
                f"https://www.ncbi.nlm.nih.gov/pmc/articles/{PMCID}/",
                f"https://europepmc.org/articles/{PMCID}",
                f"https://europepmc.org/article/PMC/{PMCID}",
                f"https://pubmed.ncbi.nlm.nih.gov/{PMID}/",
                f"https://www.ncbi.nlm.nih.gov/pubmed/{PMID}",
                f"https://europepmc.org/article/MED/{PMID}",
            ]
            for index, url in enumerate(urls):
                store = SourceStore(Path(folder) / str(index), client, discovery=discovery)
                access = SourceAccess(store, discovery)
                result = await access.acquire_source(AcquireSource(url=url))
                assert result.ok and result.content["availability"] == "full_text", url
                assert result.content["total_passages"] == 2, url
                sid = result.source_ids[0]
                assert store.candidate(sid).kind == "literature"
                read = await access._read(ReadSource(url=url))
                assert read["source_id"] == sid and len(read["passages"]) == 2
                assert len(store.catalogue()) == 1

            store = SourceStore(Path(folder) / "identity-reuse", client, discovery=discovery)
            sid = store.register(
                SourceCandidate(title="Discovered paper", identifiers={"doi": DOI})
            )
            access = SourceAccess(store, discovery)
            result = await access.acquire_source(AcquireSource(url=f"https://doi.org/{DOI}"))
            assert result.source_ids == [sid] and len(store.catalogue()) == 1

            before = len(fixture.requests)
            for url in (
                "https://publisher.invalid/article/paper",
                f"https://doi.org.evil.invalid/{DOI}",
                "https://pubmed.ncbi.nlm.nih.gov/?term=calcium",
            ):
                result = await access.acquire_source(AcquireSource(url=url))
                assert result.content["availability"] == "unavailable"
                assert store.candidate(result.source_ids[0]).kind == "web"
            assert len(fixture.requests) == before + 3

            access.objective = "Organoid"
            targeted = await access.related(
                RelatedPapers(
                    source_ids=[sid], relation="references", information_need="Calcium", limit=1
                )
            )
            default = await access.related(
                RelatedPapers(source_ids=[sid], relation="references", limit=1)
            )
            assert targeted.content["candidates"][0]["title"] == "Calcium reporter method"
            assert default.content["candidates"][0]["title"] == "Organoid overview"
            assert any(r.url.path == "/europepmc/webservices/rest/search" for r in fixture.requests)

    print(json.dumps({
        "status": "passed",
        "recognized_url_cases": len(urls),
        "same_paper_fallback": True,
        "existing_identity_reused": True,
        "ordinary_web_entries_unchanged": True,
        "citation_following_uses_current_question": True,
        "network_requests": 0,
        "model_requests": 0,
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
