from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from spacebio_review.agents.types import Material
from spacebio_review.evidence.store import SourceStore
from spacebio_review.evidence.types import SourceCandidate, SourceDocument

from .application import ApplicationBundle, ApplicationSource


async def load_application(store: SourceStore, main: str | Path, attachments: list[str | Path]):
    sources = []
    for index, path in enumerate([main, *attachments]):
        empty_text, load_error = False, None
        try:
            source_id = await store.add_local(path, kind="application")
            doc = store.retained(source_id)
            if Path(path).suffix.lower() in {".txt", ".md"}:
                empty_text = not Path(path).read_text("utf-8-sig").strip()
        except Exception as exc:
            load_error = type(exc).__name__
            candidate = SourceCandidate(title=Path(path).name, kind="application")
            source_id = store.register(candidate)
            doc = SourceDocument(
                source_id=source_id,
                candidate=candidate,
                limitations=["input_read_failed:" + load_error],
            )
            store._save(doc)
        sources.append(
            ApplicationSource(
                source_id=source_id,
                empty_text=empty_text,
                load_error=load_error,
                title=doc.candidate.title,
                role="main" if index == 0 else "attachment",
                availability=doc.availability,
                sections=list(dict.fromkeys(p.section for p in doc.passages)),
                limitations=doc.limitations,
                reading_gaps=doc.reading_gaps,
            )
        )
    return ApplicationBundle(sources=sources)


def product_material(source_id: str, product, *, owner: str | None = None) -> Material:
    payload = product.model_dump(mode="json") if isinstance(product, BaseModel) else product
    return Material(
        source_id=source_id,
        data=payload,
        essential=True,
        kind="interpretation",
        scope="private" if owner else "public",
        owner=owner,
    )
