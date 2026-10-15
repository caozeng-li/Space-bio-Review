"""Candidate selection keeps complementary search purposes available to one model call."""

import json
from types import SimpleNamespace

from spacebio_review.evidence.retrieval import find_passages, hybrid_rank
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument


class SelectCandidates:
    def __init__(self, selected):
        self.selected = selected
        self.requests = []

    async def complete(self, messages):
        self.requests.append(messages)
        candidates = json.loads(messages[-1]["content"])["candidates"]
        assert set(self.selected) <= {c["id"] for c in candidates}
        return SimpleNamespace(message={"content": json.dumps({"ids": self.selected})})


def contexts(texts, queries, rare_id):
    return [
        {
            "search_origins": [
                {
                    "query": queries[1 if i == rare_id else 0],
                    "provider": "europe_pmc",
                    "information_need": "measurement specificity"
                    if i == rare_id
                    else "microgravity experiments",
                }
            ]
        }
        for i, _ in enumerate(texts)
    ]


async def test_lexical_recall_keeps_the_second_purpose_when_first_has_many_matches():
    common = "protein microgravity condensate coarsening orbit experiment droplet kinetics"
    texts = [f"{common} trial{i}" for i in range(14)]
    rare_id = len(texts)
    texts += ["TMRM membrane potential"]
    texts += ["unrelated geology soil sediment weather rocks strata climate"] * 40
    queries = [common, "TMRM"]
    candidate_context = contexts(texts, queries, rare_id)
    model = SelectCandidates([rare_id, 0])

    selected, channels, notes, origins = await hybrid_rank(
        texts, queries, model, limit=2, candidate_context=candidate_context
    )

    assert selected == [rare_id, 0]
    assert channels["rerank"] == "used"
    assert notes == []
    assert origins[rare_id] == ["bm25"]
    assert len(model.requests) == 1
    payload = json.loads(model.requests[0][-1]["content"])
    rare = next(c for c in payload["candidates"] if c["id"] == rare_id)
    assert rare["context"] == candidate_context[rare_id]


async def test_dense_recall_also_unions_each_purpose_before_one_selection():
    texts = [f"Paper number {i}" for i in range(15)]
    queries = ["microgravity experiments", "measurement specificity"]
    rare_id = 14
    model = SelectCandidates([rare_id, 0])

    class DenseIndex:
        def __init__(self):
            self.requests = []

        async def rank(self, supplied_texts, supplied_queries):
            assert supplied_texts == texts
            self.requests.append(supplied_queries)
            if supplied_queries == queries[:1]:
                return list(range(len(texts)))
            assert supplied_queries == queries[1:]
            return [rare_id, *range(rare_id)]

    index = DenseIndex()
    selected, channels, notes, origins = await hybrid_rank(
        texts,
        queries,
        model,
        index=index,
        limit=2,
        candidate_context=contexts(texts, queries, rare_id),
    )

    assert selected == [rare_id, 0]
    assert index.requests == [queries[:1], queries[1:]]
    assert channels == {"bm25": "used", "dense": "used", "rerank": "used"}
    assert notes == []
    assert origins[rare_id] == ["dense"]
    assert len(model.requests) == 1
    assert len(json.loads(model.requests[0][-1]["content"])["candidates"]) == 13


async def test_find_passages_retains_its_existing_multi_query_interface():
    documents = [
        SourceDocument(
            source_id=sid,
            candidate=SourceCandidate(title=sid),
            availability="full_text",
            passages=[Passage(passage_id="p1", text=text, locator="Methods")],
        )
        for sid, text in [("a", "Microgravity experiment."), ("b", "TMRM measurement.")]
    ]
    queries = ["microgravity", "TMRM"]
    model = SelectCandidates([1])

    class DenseIndex:
        requests = []

        async def rank(self, texts, supplied_queries):
            self.requests.append(supplied_queries)
            return [1, 0]

    index = DenseIndex()
    result = await find_passages(documents, queries, model, index=index, limit=1)

    assert index.requests == [queries]
    assert len(model.requests) == 1
    candidates = json.loads(model.requests[0][-1]["content"])["candidates"]
    assert all("context" not in c for c in candidates)
    assert result["hits"][0]["source_id"] == "b"
    assert result["hits"][0]["passage"]["text"] == "TMRM measurement."
