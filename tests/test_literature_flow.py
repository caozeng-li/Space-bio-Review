"""Local search provenance and citation traversal, with no external requests."""

from types import SimpleNamespace

import httpx
import pytest

from spacebio_review.evidence.discovery import DiscoveryResult
from spacebio_review.evidence.knowledge import KnowledgeQuestion, RelatedPapers, SearchSources
from spacebio_review.evidence.literature import KnowledgeSession
from spacebio_review.evidence.store import SourceStore
from spacebio_review.evidence.types import SourceCandidate


def session_for(store, discovery, source_ids=()):
    researcher = SimpleNamespace(
        store=store,
        discovery=discovery,
        runtime=SimpleNamespace(models={"task/rerank/normal": None}),
        index=None,
        rerank=False,
    )
    return KnowledgeSession(
        researcher,
        KnowledgeQuestion(
            question="What does this measurement tell us?", existing_source_ids=source_ids
        ),
    )


def paper(title, doi):
    return SourceCandidate(
        title=title, abstract="A method and its conditions.", identifiers={"doi": doi}
    )


@pytest.mark.asyncio
async def test_selected_candidates_keep_all_search_origins_after_source_deduplication(
    tmp_path, monkeypatch
):
    shared = paper("Shared measurement paper", "10.1000/shared")
    method = paper("Complementary method paper", "10.1000/method")
    queries = SearchSources(
        queries=[
            {
                "provider": "europe_pmc",
                "query": "TMRM membrane potential",
                "information_need": "What TMRM measures",
            },
            {
                "provider": "tavily",
                "query": "mitochondrial nucleoid imaging protocol",
                "information_need": "How mtDNA nucleoids are measured",
            },
        ]
    )
    requests, ranked = [], {}

    async def search(query, provider, cutoff, limit):
        requests.append((query, provider))
        candidates = [shared] if provider == "europe_pmc" else [shared, method]
        return DiscoveryResult(candidates=candidates, channels={provider: "ok"})

    async def rank(texts, search_queries, model, **kwargs):
        ranked.update(texts=texts, queries=search_queries, context=kwargs["candidate_context"])
        return [1, 0], {}, [], {0: ["fixture"], 1: ["fixture"]}

    monkeypatch.setattr("spacebio_review.evidence.literature.hybrid_rank", rank)
    async with httpx.AsyncClient() as client:
        store = SourceStore(tmp_path / "sources", client)
        result = await session_for(store, SimpleNamespace(search=search)).search(queries)
        shared_id, method_id = store.register(shared), store.register(method)

    assert requests == [(q.query, q.provider) for q in queries.queries]
    assert len(ranked["texts"]) == result.content["candidate_count"] == 2
    origins = [q.model_dump() for q in queries.queries]
    assert ranked["context"] == [{"search_origins": origins}, {"search_origins": origins[1:]}]
    selected = {row["source_id"]: row for row in result.content["candidates"]}
    assert selected[shared_id]["search_origins"] == origins
    assert selected[method_id]["search_origins"] == origins[1:]
    assert result.source_ids == [method_id, shared_id]


@pytest.mark.asyncio
async def test_related_paper_can_become_a_new_seed_for_method_reference(tmp_path, monkeypatch):
    original = paper("Original seed", "10.1000/seed")
    child = paper("Related experiment", "10.1000/experiment")
    method = paper("Referenced original method", "10.1000/method")
    requests = []

    async def related(seeds, relation, cutoff, limit):
        requests.append((seeds, relation))
        found = child if len(requests) == 1 else method
        return DiscoveryResult(candidates=[found], channels={"semantic_scholar": "ok"})

    async def rank(texts, queries, model, **kwargs):
        return list(range(len(texts))), {}, [], {i: ["fixture"] for i in range(len(texts))}

    monkeypatch.setattr("spacebio_review.evidence.literature.hybrid_rank", rank)
    async with httpx.AsyncClient() as client:
        store = SourceStore(tmp_path / "sources", client)
        seed_id = store.register(original)
        session = session_for(store, SimpleNamespace(related=related), [seed_id])
        first = await session.related(RelatedPapers(source_ids=[seed_id], relation="citations"))
        assert first.ok
        child_id = first.source_ids[0]
        second = await session.related(RelatedPapers(source_ids=[child_id], relation="references"))
        assert second.ok
        assert second.source_ids == [store.register(method)]
        assert {seed_id, child_id, *second.source_ids} <= session.visible

    assert requests == [
        (["DOI:10.1000/seed"], "citations"),
        (["DOI:10.1000/experiment"], "references"),
    ]
