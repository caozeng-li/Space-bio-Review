"""Oversized originals never reach expensive parsing or scientific readers."""

import gzip
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest
from reportlab.pdfgen.canvas import Canvas

from spacebio_review.evidence import documents
from spacebio_review.evidence.access import SourceAccess
from spacebio_review.evidence.documents import add_material, parse_document
from spacebio_review.evidence.knowledge import AcquireSource, RetrieveKnowledge
from spacebio_review.evidence.material_cache import MaterialCache
from spacebio_review.evidence.material_limits import MaterialLimits
from spacebio_review.evidence.research import EvidenceResources, load_evidence_config
from spacebio_review.evidence.store import SourceStore
from spacebio_review.evidence.types import SourceCandidate
from spacebio_review.v5.source_reading import SourceQuestion, SourceReading


def pdf_file(path, pages):
    canvas = Canvas(str(path))
    for _ in range(pages):
        canvas.drawString(20, 20, "Scientific material")
        canvas.showPage()
    canvas.save()
    return path


def test_pdf_page_boundary_precedes_docling(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(documents, "_docling", lambda *args: calls.append(args))
    for pages in (60, 61):
        path = pdf_file(tmp_path / f"{pages}.pdf", pages)
        doc = parse_document("source", SourceCandidate(title="paper"), path, tmp_path / "parsed")
        if pages == 61:
            assert doc.size_exclusion.model_dump() == {
                "metric": "pdf_pages",
                "actual": 61,
                "limit": 60,
            }
            assert not doc.passages and not doc.visuals
        else:
            assert doc.size_exclusion is None
    assert len(calls) == 1


@pytest.mark.parametrize(
    "enabled,kind,limit,excluded",
    [
        (True, "literature", 5, True),
        (True, "literature", 6, False),
        (False, "literature", 5, False),
        (True, "application", 5, False),
        (True, "literature", None, False),
    ],
)
def test_text_limit_is_characters_and_never_truncates(tmp_path, enabled, kind, limit, excluded):
    path = tmp_path / "paper.txt"
    text = "科学材料六字"
    path.write_text(text, encoding="utf-8")
    doc = parse_document(
        "source",
        SourceCandidate(title="paper", kind=kind),
        path,
        tmp_path / "parsed",
        material_limits=MaterialLimits(
            enabled=enabled,
            max_text_chars=limit,
        ),
    )
    assert bool(doc.size_exclusion) is excluded
    assert [p.text for p in doc.passages] == ([] if excluded else [text])


@pytest.mark.parametrize(
    "suffix,content",
    [
        (".html", "<html><script>" + "x" * 500 + "</script><body>123456</body></html>"),
        (".xml", "<article><body><p>123456</p></body></article>"),
        (
            ".docx",
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            "<w:body><w:p><w:r><w:t>123456</w:t></w:r></w:p></w:body></w:document>",
        ),
    ],
)
def test_rich_text_counts_content_before_full_parser(tmp_path, suffix, content, monkeypatch):
    path = tmp_path / ("paper" + suffix)
    if suffix == ".docx":
        with ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", content)
    else:
        path.write_text(content, encoding="utf-8")
    calls = []
    for parser in ("_html", "_xml", "_docling"):
        monkeypatch.setattr(documents, parser, lambda *args: calls.append(args))
    for limit in (6, 5):
        doc = parse_document(
            "source",
            SourceCandidate(title="paper"),
            path,
            tmp_path / "parsed",
            material_limits=MaterialLimits(max_text_chars=limit),
        )
        assert bool(doc.size_exclusion) is (limit == 5)
        if limit == 5:
            assert doc.size_exclusion.actual == 6
    assert len(calls) == 1


async def test_rejection_stops_fallback_retrieval_and_reader_creation(tmp_path, monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, text="123456", headers={"content-type": "text/plain"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        store = SourceStore(
            tmp_path / "sources", client, material_limits=MaterialLimits(max_text_chars=5)
        )
        sid = store.register(
            SourceCandidate(
                title="paper",
                url="https://example.org/paper.txt",
                abstract="This abstract must not become a fallback.",
            )
        )
        access = SourceAccess(store, None, visible=[sid])
        result = await access.acquire_source(AcquireSource(source_id=sid, reading_purpose="研究"))
        assert not result.ok and "text_chars:6>5" in result.error
        assert store.excluded(sid)
        assert not store.retained(sid).passages
        assert not (await access.retrieve(RetrieveKnowledge(source_id=sid, queries=["研究"]))).ok
        assert "material_size_limit_exceeded" in (await access.material(sid))["error"]

        reader = SourceReading.__new__(SourceReading)
        reader.topics = SimpleNamespace(access=lambda topic: access)
        # No runtime or creation machinery: rejection must precede all author work.
        result = await reader.research("topic", SourceQuestion(source_id=sid, question="研究"))
        assert not result.ok and "material_size_limit_exceeded" in result.error
        assert len(requests) == 1


async def test_shared_raw_cache_obeys_current_limits_without_download(tmp_path):
    cache = MaterialCache(tmp_path / "cache")
    candidate = SourceCandidate(title="cached paper", url="https://example.org/paper.txt")
    path = tmp_path / "cached.txt"
    path.write_text("123456", encoding="utf-8")
    cache.retain(candidate, path, role="main", url=candidate.url, content_type="text/plain")

    def no_network(request):
        pytest.fail("Rejected cached originals must not be downloaded again")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as client:
        store = SourceStore(
            tmp_path / "sources",
            client,
            material_cache=cache,
            material_limits=MaterialLimits(max_text_chars=5),
        )
        sid = store.register(candidate)
        doc = await store.acquire(sid)
        assert doc.size_exclusion.actual == 6
        assert not doc.passages


async def test_oversized_zip_supplement_keeps_accepted_main(tmp_path):
    package = BytesIO()
    with ZipFile(package, "w", ZIP_DEFLATED) as archive:
        archive.writestr("supplement.txt", "x" * 2000)
    assert len(package.getvalue()) < 1000

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=package.getvalue())
        )
    ) as client:
        store = SourceStore(
            tmp_path / "sources",
            client,
            material_limits=MaterialLimits(max_file_mb=1000 / (1024 * 1024)),
        )
        path = tmp_path / "main.txt"
        path.write_text("Accepted scientific result.", encoding="utf-8")
        sid = await store.add_local(path, kind="literature")
        doc = store.retained(sid)
        material = add_material(
            doc,
            url="https://example.org/supplement.zip",
            role="supplement",
            package_member="supplement.txt",
        )
        store._save(doc)
        access = SourceAccess(store, None, visible=[sid])
        rejected = await access.acquire_source(
            AcquireSource(source_id=sid, material_id=material.material_id)
        )
        assert not rejected.ok and "file_bytes:2000>1000" in rejected.error
        assert (await access.acquire_source(AcquireSource(source_id=sid))).ok
        assert [p.text for p in store.retained(sid).passages] == ["Accepted scientific result."]


@pytest.mark.parametrize("compressed", [False, True])
async def test_download_limit_and_decoding(tmp_path, compressed):
    content = b"x" * 2000
    response = gzip.compress(content) if compressed else content
    headers = {"content-type": "text/plain", **({"content-encoding": "gzip"} if compressed else {})}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=response, headers=headers)
        )
    ) as client:
        store = SourceStore(
            tmp_path / "sources",
            client,
            material_limits=MaterialLimits(max_file_mb=1000 / (1024 * 1024)),
        )
        sid = store.register(SourceCandidate(title="paper", url="https://example.org/paper.txt"))
        doc = await store.acquire(sid)
        assert doc.size_exclusion.metric == "file_bytes"
        assert not doc.passages
        store.material_limits.max_file_mb = 1
        downloaded = await store._get("https://example.org/paper.txt", max_bytes=3000)
        assert downloaded.content == content


async def test_yaml_switch_and_thresholds_reach_source_store(tmp_path):
    path = tmp_path / "evidence.yaml"
    path.write_text(
        "material_limits:\n  enabled: false\n  max_pdf_pages: 7\n"
        "  max_text_chars: 30\n  max_file_mb: null\n",
        encoding="utf-8",
    )
    config = load_evidence_config(path)
    resources = EvidenceResources.create(None, tmp_path / "run", config)
    try:
        assert resources.store.material_limits.model_dump() == {
            "enabled": False,
            "max_pdf_pages": 7,
            "max_text_chars": 30,
            "max_file_mb": None,
        }
    finally:
        await resources.close()
