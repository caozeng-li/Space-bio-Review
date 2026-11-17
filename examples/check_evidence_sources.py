"""Small live service/reader check without an LLM call."""

import asyncio
from pathlib import Path

import httpx

from spacebio_review.evidence.discovery import Discovery
from spacebio_review.evidence.store import SourceStore


async def main():
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        discovery = Discovery(client)
        store = SourceStore(Path("runs/source-check/sources"), client)
        for provider in ["europe_pmc", "openalex"]:
            found = await discovery.search("random positioning machine shear", provider, None, 3)
            print(provider, "candidates=", len(found.candidates), "limits=", found.limitations)
            for candidate in found.candidates:
                print(candidate.title, candidate.identifiers)
            if found.candidates:
                sid = store.register(found.candidates[0])
                document = await store.acquire(sid)
                print(
                    "acquired",
                    document.availability,
                    len(document.passages),
                    "visuals",
                    len(document.visuals),
                    "limits",
                    document.limitations,
                )


if __name__ == "__main__":
    asyncio.run(main())
