"""Run twice in separate processes: cold, then --warm; embeddings only."""

import argparse
import asyncio
import json
from pathlib import Path

from spacebio_review.evidence.retrieval import find_passages
from spacebio_review.evidence.types import SourceDocument
from spacebio_review.models import load_config
from spacebio_review.models.embedding import DashScopeEmbedding, VectorIndex
from spacebio_review.models.scheduling import RequestScheduler
from spacebio_review.v4.budget import Budget
from spacebio_review.v4.config import ReportingConfig, V4Config
from spacebio_review.v4.records import Records, write_json


async def main(warm):
    root = Path(__file__).resolve().parents[1]
    output = root / "runs/local-probes/mytest16-cache"
    mode = "warm" if warm else "cold"
    if (output / mode).exists():
        raise ValueError("Choose a fresh probe output directory; existing results are read-only")
    cfg = load_config(root / "config/runtime.yaml")
    budget = Budget(
        Records(output / mode),
        V4Config(max_model_calls=1, max_embedding_calls=8, max_seconds=180),
        ReportingConfig(),
    )
    scheduler = RequestScheduler()
    scheduler.request_budget = budget
    embedder = DashScopeEmbedding(cfg.embedding, scheduler, env_file=root / ".env")
    index = VectorIndex(embedder, cache_dir=output / "vectors")
    doc = SourceDocument.model_validate_json(
        (root / "runs/mytest16/sources/src-e1079308b8b9/source.json").read_text("utf-8")
    )
    try:
        async with asyncio.timeout(budget.remaining_seconds()):
            result = await find_passages([doc], ["temperature control"], None, index=index)
        write_json(output / (mode + ".json"), result)
        calls = len(budget.data["requests"])
        metrics = {
            "mode": mode,
            "requests": calls,
            "hits": len(result["hits"]),
            "cache_hits": index.cache_hits,
        }
        write_json(output / (mode + "-metrics.json"), metrics)
        print(json.dumps(metrics))
        if warm:
            assert calls == 0
            assert result == json.loads((output / "cold.json").read_text("utf-8"))
    finally:
        budget.save()
        await embedder.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--warm", action="store_true")
    asyncio.run(main(parser.parse_args().warm))
