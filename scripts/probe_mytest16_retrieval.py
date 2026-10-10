"""Bounded retrieval comparison on one frozen paper, never a complete review."""

import asyncio
import json
from pathlib import Path

from spacebio_review.evidence import retrieval
from spacebio_review.evidence.types import SourceDocument
from spacebio_review.models import DashScopeModel, load_config
from spacebio_review.models.embedding import DashScopeEmbedding, VectorIndex
from spacebio_review.models.scheduling import RequestScheduler
from spacebio_review.v4.budget import Budget
from spacebio_review.v4.config import ReportingConfig, V4Config
from spacebio_review.v4.records import Records, write_json


async def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "runs/local-probes/mytest16-retrieval"
    if output.exists():
        raise ValueError("Choose a fresh probe output directory; existing results are read-only")
    doc = SourceDocument.model_validate_json(
        (root / "runs/mytest16/sources/src-e1079308b8b9/source.json").read_text("utf-8")
    )
    cfg = load_config(root / "config/runtime.yaml")

    budget = Budget(
        Records(output),
        V4Config(max_model_calls=6, max_embedding_calls=60, max_seconds=600),
        ReportingConfig(),
    )
    scheduler = RequestScheduler()
    scheduler.request_budget = budget
    embedder = DashScopeEmbedding(cfg.embedding, scheduler, env_file=root / ".env")
    model = DashScopeModel(
        cfg.routed_model("rerank", "normal"), budget=budget, env_file=root / ".env"
    )
    cases = [
        (
            "What are the temperature range and CO2 concentration operating limits "
            "of the culture device?",
            [{"p72"}],
        ),
        (
            "How does the microscope motor and gear mechanism perform automatic focusing?",
            [{"p203", "p204"}],
        ),
        (
            "How does the gravity reference centrifuge provide the comparison condition "
            "for the microgravity culture unit?",
            [{"p116", "p117"}],
        ),
    ]
    rows = []
    chunks = retrieval.retrieval_chunks
    try:
        async with asyncio.timeout(budget.remaining_seconds()):
            for mode in ["paragraph", "chunk"]:
                index = VectorIndex(embedder)
                if mode == "paragraph":
                    retrieval.retrieval_chunks = lambda d: [
                        (p.section + " " + p.text, [i]) for i, p in enumerate(d.passages)
                    ]
                else:
                    retrieval.retrieval_chunks = chunks
                for question, gold in cases:
                    before = len(budget.data["requests"])
                    result = await retrieval.find_passages([doc], [question], model, index=index)
                    ids = {h["passage"]["passage_id"] for h in result["hits"]}
                    row = {
                        "mode": mode,
                        "query": question,
                        "requests": len(budget.data["requests"]) - before,
                        "gold_hit": all(bool(g & ids) for g in gold),
                        "gold": [sorted(g) for g in gold],
                        "result": result,
                    }
                    rows.append(row)
                    write_json(output / "comparison.json", rows)
                    print(json.dumps({k: v for k, v in row.items() if k != "result"}), flush=True)
    finally:
        budget.save()
        retrieval.retrieval_chunks = chunks
        await embedder.close()
        await model.close()
    assert all(r["gold_hit"] for r in rows if r["mode"] == "chunk")


if __name__ == "__main__":
    asyncio.run(main())
