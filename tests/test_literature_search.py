"""Search adapters preserve provider query semantics and discovery outcomes."""

import httpx
import pytest

from spacebio_review.evidence.discovery import Discovery, DiscoveryConfig


@pytest.fixture(autouse=True)
def no_external_credentials(monkeypatch):
    monkeypatch.setattr("spacebio_review.evidence.discovery.optional_secret", lambda *args: None)


@pytest.mark.parametrize(
    "query",
    [
        "microgravity phase separation droplet coarsening",
        'all:microgravity AND (ti:"phase separation" OR all:LLPS)',
    ],
)
async def test_arxiv_preserves_terms_and_explicit_phrase_expression(query):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.host == "export.arxiv.org"
        assert request.url.path == "/api/query"
        assert dict(request.url.params) == {
            "search_query": query,
            "start": "0",
            "max_results": "3",
            "sortBy": "relevance",
        }
        return httpx.Response(
            200,
            headers={"Content-Type": "application/atom+xml"},
            text="""<feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <id>https://arxiv.org/abs/2601.00001v1</id>
                <title>Phase separation under reduced gravity</title>
                <summary>We measured droplet coarsening under reduced gravity.</summary>
                <published>2026-01-01T00:00:00Z</published>
                <updated>2026-01-01T00:00:00Z</updated>
                <author><name>Example Author</name></author>
              </entry>
            </feed>""",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await Discovery(client, env_file=None).search(query, "arxiv", None, limit=3)

    assert len(requests) == 1
    assert result.channels == {"arxiv": "ok"}
    assert result.candidates[0].identifiers["arxiv"] == "2601.00001v1"
    assert result.candidates[0].fulltext_urls == ["https://arxiv.org/pdf/2601.00001v1"]


async def test_semantic_scholar_uses_plain_text_relevance_api(monkeypatch):
    monkeypatch.setattr(
        "spacebio_review.evidence.discovery.optional_secret",
        lambda name, env_file: "test-api-key" if name == "SEMANTIC_SCHOLAR_API_KEY" else None,
    )
    requests = []
    query = "TMRM mitochondrial membrane potential measurement"

    def respond(request):
        requests.append(request)
        assert request.url.host == "api.semanticscholar.org"
        assert request.url.path == "/graph/v1/paper/search"
        assert request.url.params["query"] == query
        assert request.url.params["limit"] == "4"
        assert set(request.url.params) == {"query", "limit", "fields"}
        assert {"title", "abstract", "externalIds", "openAccessPdf"} <= set(
            request.url.params["fields"].split(",")
        )
        assert request.headers["x-api-key"] == "test-api-key"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "paperId": "example-paper",
                        "title": "Membrane potential measurements with TMRM",
                        "abstract": "Fluorescence depends on membrane potential and dye loading.",
                        "externalIds": {"DOI": "10.1234/TMRM"},
                        "publicationDate": "2020-01-01",
                        "openAccessPdf": {"url": "https://example.org/tmrm.pdf"},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await Discovery(client, env_file=None).search(
            query, "semantic_scholar", None, limit=4
        )

    assert len(requests) == 1
    assert result.channels == {"semantic_scholar": "ok"}
    assert result.candidates[0].identifiers["doi"] == "10.1234/tmrm"
    assert result.candidates[0].fulltext_urls == ["https://example.org/tmrm.pdf"]


@pytest.mark.parametrize(
    ("outcome", "expected_status", "expected_limits"),
    [
        ("empty", "empty", []),
        ("http_failure", "request_failed", ["semantic_scholar: request_failed"]),
        ("connection_failure", "request_failed", ["semantic_scholar: request_failed"]),
    ],
)
async def test_failed_request_is_distinct_from_successful_empty_search(
    outcome, expected_status, expected_limits
):
    requests = []

    def respond(request):
        requests.append(request)
        if outcome == "connection_failure":
            raise httpx.ConnectError("Connection unavailable", request=request)
        if outcome == "http_failure":
            return httpx.Response(503, text="Service unavailable")
        return httpx.Response(200, json={"total": 0, "data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        discovery = Discovery(client, env_file=None, config=DiscoveryConfig(intervals={}))
        result = await discovery.search(
            "microgravity protein condensates", "semantic_scholar", None
        )

    assert len(requests) == 1
    assert result.candidates == []
    assert result.channels == {"semantic_scholar": expected_status}
    assert result.limitations == expected_limits
