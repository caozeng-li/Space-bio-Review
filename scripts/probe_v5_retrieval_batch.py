"""Offline comparison of multi-purpose recall, batching, and persistent vector reuse."""

from __future__ import annotations

import asyncio
import json
import math
import tempfile
from pathlib import Path
from types import SimpleNamespace

from spacebio_review.evidence.retrieval import hybrid_rank
from spacebio_review.models.embedding import VectorIndex
from spacebio_review.models.provider import ModelError

TEXTS = [
    "Microgravity changes cancer cell aggregation",
    "Normal hepatocyte functions in three dimensional culture",
    "Space radiation damage in liver cells",
    "Ground centrifuge control for orbital cell experiments",
    "Cancer spheroid morphology and drug responses",
    "Primary liver cell metabolism and viability",
    "Radiation exposure and DNA repair",
    "Onboard centrifuge matches culture hardware",
]
QUERIES = [
    "Tumor model evidence under microgravity",
    "Functional validity of normal liver models",
    "Radiation confounding of biological measurements",
    "Controls that distinguish gravity from flight conditions",
]
VECTORS = dict(
    zip(
        TEXTS + QUERIES,
        [
            [1.0, 0.0, 0.0, 0.1],
            [0.0, 1.0, 0.0, 0.1],
            [0.0, 0.0, 1.0, 0.1],
            [0.0, 0.0, 0.1, 1.0],
            [0.9, 0.2, 0.0, 0.0],
            [0.1, 0.9, 0.0, 0.0],
            [0.0, 0.0, 0.9, 0.2],
            [0.0, 0.0, 0.0, 1.0],
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        strict=True,
    )
)


class EmbeddingFixture:
    """Count actual batches, without contacting an embedding provider."""

    def __init__(self, *, max_requests=None, max_chars=6000):
        self.config = SimpleNamespace(
            model="offline-vector-fixture",
            base_url="https://local.invalid",
            dimensions=4,
            batch_size=10,
            max_chars=max_chars,
        )
        self.requests = []
        self.max_requests = max_requests

    async def embed(self, texts):
        if self.max_requests is not None and len(self.requests) >= self.max_requests:
            raise ModelError("embedding_request_allowance_exhausted")
        assert 0 < len(texts) <= self.config.batch_size
        self.requests.append(list(texts))
        return [self.vector(t) for t in texts]

    @staticmethod
    def vector(text):
        if text in VECTORS:
            return VECTORS[text]
        return [float(sum(ord(c) for c in text[i::4]) + 1) for i in range(4)]


class PreviousQueryLoop(VectorIndex):
    """Reproduce the prior per-query request boundary for comparison."""

    async def rank_groups(self, texts, query_groups):
        orders = []
        for group in query_groups:
            orders.extend(await super().rank_groups(texts, [group]))
        return orders


def expected_orders(groups):
    def cosine(a, b):
        return sum(x * y for x, y in zip(a, b, strict=True)) / math.sqrt(
            sum(x * x for x in a) * sum(y * y for y in b)
        )

    return [
        sorted(
            range(len(TEXTS)),
            key=lambda i: max(cosine(VECTORS[TEXTS[i]], VECTORS[q]) for q in group),
            reverse=True,
        )
        for group in groups
    ]


async def main():
    groups = [[q] for q in QUERIES]
    previous = EmbeddingFixture()
    batched = EmbeddingFixture()
    old_order = await PreviousQueryLoop(previous).rank_groups(TEXTS, groups)
    with tempfile.TemporaryDirectory(prefix="spacebio-retrieval-batch-") as directory:
        index = VectorIndex(batched, cache_dir=Path(directory) / "vectors")
        new_order = await index.rank_groups(TEXTS, groups)
        assert old_order == new_order == expected_orders(groups)
        assert len(previous.requests) == 4 and len(batched.requests) == 2
        assert sorted(t for batch in batched.requests for t in batch) == sorted(TEXTS + QUERIES)
        combined = [[QUERIES[0], QUERIES[1]], [QUERIES[2]], [QUERIES[0]]]
        assert await index.rank_groups(TEXTS, combined) == expected_orders(combined)
        assert await index.rank(TEXTS, QUERIES) == expected_orders([QUERIES])[0]
        assert len(batched.requests) == 2
        reopened_embedder = EmbeddingFixture(max_requests=0)
        reopened = VectorIndex(reopened_embedder, cache_dir=Path(directory) / "vectors")
        assert await reopened.rank_groups(TEXTS, groups) == new_order
        assert len(reopened_embedder.requests) == 0

    limited_results = {}
    for name, implementation in [("previous", PreviousQueryLoop), ("batched", VectorIndex)]:
        embedder = EmbeddingFixture(max_requests=2)
        order, channels, notes, origins = await hybrid_rank(
            TEXTS,
            QUERIES,
            index=implementation(embedder),
            candidate_context=[{} for _ in TEXTS],
            limit=8,
        )
        limited_results[name] = {
            "requests": len(embedder.requests),
            "dense": channels["dense"],
            "selected": len(order),
        }
        if name == "batched":
            assert channels["dense"] == "used" and not notes
            assert all("dense" in origins[i] for i in range(len(TEXTS)))
        else:
            assert channels["dense"] == "unavailable"

    chunk_embedder = EmbeddingFixture(max_chars=4)
    chunk_index = VectorIndex(chunk_embedder)
    vectors = await chunk_index.vectors(["abcdefghabcd", "abcdefghabcd", "abcd"])
    first, second = chunk_embedder.vector("abcd"), chunk_embedder.vector("efgh")
    assert vectors[0] == vectors[1] == [(2 * a + b) / 3 for a, b in zip(first, second)]
    assert vectors[2] == first
    assert chunk_embedder.requests == [["abcd", "efgh"]]
    print(
        json.dumps(
            {
                "status": "passed",
                "cold_requests": {"previous": 4, "batched": 2},
                "warm_memory_requests": 0,
                "warm_disk_requests": 0,
                "same_independent_query_rankings": True,
                "two_request_allowance": limited_results,
                "whole_text_chunk_pooling_unchanged": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
