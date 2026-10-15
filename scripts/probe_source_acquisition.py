"""One bounded acquisition probe; no research model, review runner, or coordinator."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx

from spacebio_review.evidence.discovery import Discovery
from spacebio_review.evidence.store import SourceStore
from spacebio_review.evidence.types import SourceCandidate

PAPERS = [
    SourceCandidate(
        title="Super-resolution microscopy reveals that mammalian mitochondrial nucleoids "
        "have a uniform size and frequently contain a single copy of mtDNA",
        identifiers={"doi": "10.1073/pnas.1109263108"},
    ),
    SourceCandidate(
        title="Superresolution Fluorescence Imaging of Mitochondrial Nucleoids Reveals "
        "Their Spatial Range, Limits, and Membrane Interaction",
        identifiers={"doi": "10.1128/mcb.05694-11"},
        url="https://www.ncbi.nlm.nih.gov/pmc/articles/3233019",
    ),
]


class RequestLimit(BaseException):
    pass


async def probe(output):
    output.mkdir(parents=True, exist_ok=False)
    (output / "http").mkdir()
    # PDF parsing can use installed local assets, but must not download model weights.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    requests, results = [], []
    limits = {"http_requests": 30, "papers": 2, "supplement_requests": 2, "model_calls": 0}
    (output / "input.json").write_text(
        json.dumps(
            {
                "started_at": datetime.now(UTC).isoformat(),
                "limits": limits,
                "browser_fallback": False,
                "papers": [paper.model_dump(mode="json") for paper in PAPERS],
                "selection": "First readable-format supplement and first figure per fixed paper",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    async def request_hook(request):
        if len(requests) >= limits["http_requests"]:
            raise RequestLimit()
        request.extensions["probe_index"] = len(requests)
        requests.append(
            {
                "url": str(request.url.copy_remove_param("api_key").copy_remove_param("mailto")),
                "method": request.method,
            }
        )

    async def response_hook(response):
        index = response.request.extensions["probe_index"]
        requests[index]["status"] = response.status_code
        body = await response.aread()
        requests[index]["bytes"] = len(body)
        (output / "http" / f"response-{index:02d}.body").write_bytes(body)

    stop_reason = "fixed_materials_acquired_or_attempted"
    supplements = 0
    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "SpaceBioReview/0.1 (bounded source acquisition probe)"},
        event_hooks={"request": [request_hook], "response": [response_hook]},
    ) as client:
        discovery = Discovery(client)
        store = SourceStore(output / "sources", client, discovery=discovery, browser_fallback=False)
        try:
            for paper in PAPERS:
                sid = store.register(paper)
                item = {"source_id": sid, "doi": paper.identifiers["doi"]}
                results.append(item)
                doc = await store.acquire(sid)
                supported = {".pdf", ".docx", ".txt", ".csv", ".png", ".jpg", ".html"}
                selected = next(
                    (
                        m
                        for m in doc.materials
                        if m.role == "supplement"
                        and Path(m.package_member or httpx.URL(m.url).path).suffix.lower()
                        in supported
                    ),
                    None,
                )
                if selected and supplements < limits["supplement_requests"]:
                    supplements += 1
                    item["selected_supplement"] = selected.material_id
                    doc = await store.acquire(sid, material_id=selected.material_id)
                if doc.visuals:
                    item["selected_visual"] = doc.visuals[0].visual_id
                    try:
                        pixels, mime = await store.visual_bytes(sid, doc.visuals[0])
                        item["visual_result"] = {"bytes": len(pixels), "mime": mime}
                    except Exception as exc:
                        item["visual_result"] = {"error": str(exc)}
                doc = store.retained(sid)
                item.update(
                    {
                        "availability": doc.availability,
                        "passages": len(doc.passages),
                        "visuals": len(doc.visuals),
                        "sections": list(dict.fromkeys(p.section for p in doc.passages)),
                        "materials": [m.model_dump(mode="json") for m in doc.materials],
                        "limitations": doc.limitations,
                    }
                )
                print(
                    json.dumps(
                        {k: item[k] for k in ("doi", "availability", "passages", "visuals")}
                    ),
                    flush=True,
                )
        except RequestLimit:
            stop_reason = "http_request_limit"
        finally:
            summary = {
                "limits": limits,
                "http_requests": len(requests),
                "supplement_requests": supplements,
                "model_calls": 0,
                "stop_reason": stop_reason,
                "papers": results,
                "requests": requests,
            }
            (output / "result.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    print(
        json.dumps(
            {"output": str(output), "http_requests": len(requests), "stop_reason": stop_reason}
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(probe(parser.parse_args().output.resolve()))
