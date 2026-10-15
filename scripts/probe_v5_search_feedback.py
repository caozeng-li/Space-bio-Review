"""Offline provider-pool and per-query feedback probe; no model or network calls."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import httpx

from spacebio_review.evidence.access import SourceAccess
from spacebio_review.evidence.discovery import Discovery, DiscoveryConfig
from spacebio_review.evidence.knowledge import SearchQuery, SearchSources
from spacebio_review.evidence.store import SourceStore


async def main():
    requests = []
    target = {
        "id": "99999",
        "source": "MED",
        "title": "Indicator localization method",
        "abstractText": "Direct comparison of indicator localization in living cells.",
    }

    def respond(request):
        query = request.url.params["query"]
        width = int(request.url.params["pageSize"])
        requests.append((query, width))
        if query == "missing":
            rows = []
        elif query == "shared":
            rows = [target]
        else:
            rows = [
                {"id": str(10000 + i), "source": "MED", "title": f"Background survey {i}"}
                for i in range(width)
            ]
            rows[8] = target
        return httpx.Response(200, json={"resultList": {"result": rows}})

    with tempfile.TemporaryDirectory(prefix="spacebio-search-feedback-") as folder:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            discovery = Discovery(
                client,
                config=DiscoveryConfig(enabled=["europe_pmc"], intervals={"europe_pmc": 0}),
            )
            store = SourceStore(Path(folder), client, discovery=discovery)
            access = SourceAccess(store, discovery)
            result = await access.search(
                SearchSources(
                    queries=[
                        SearchQuery(query=q, provider="europe_pmc")
                        for q in ("indicator", "missing", "shared")
                    ],
                    limit=1,
                )
            )
            assert requests == [("indicator", 12), ("missing", 12), ("shared", 12)]
            assert result.ok and len(result.content["candidates"]) == 1
            assert result.content["candidates"][0]["title"] == target["title"]
            feedback = {row["query"]: row for row in result.content["query_feedback"]}
            assert feedback["indicator"]["returned_count"] == 12
            assert feedback["indicator"]["selected_count"] == 1
            assert feedback["missing"]["returned_count"] == 0
            assert feedback["missing"]["selected_count"] == 0
            assert feedback["shared"]["returned_count"] == 1
            assert feedback["shared"]["selected_count"] == 1
            assert result.content["candidate_count"] == 12
            assert len(result.content["candidates"][0]["search_origins"]) == 2
            await access.search(
                SearchSources(
                    queries=[SearchQuery(query="indicator", provider="europe_pmc")], limit=6
                )
            )
            assert requests[-1] == ("indicator", 18)

    print(json.dumps({
        "status": "passed",
        "provider_pool_sizes": [12, 18],
        "candidate_beyond_display_limit_selected": True,
        "empty_query_and_shared_candidate_feedback": True,
        "network_requests": 0,
        "model_requests": 0,
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
