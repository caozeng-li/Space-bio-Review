"""Selected originals, their explicit files, and stable citation positions."""

from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest

from spacebio_review.evidence.discovery import Discovery, DiscoveryConfig
from spacebio_review.evidence.documents import parse_document
from spacebio_review.evidence.store import SourceStore
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch):
    monkeypatch.setattr("spacebio_review.evidence.discovery.optional_secret", lambda *args: None)
    monkeypatch.setattr("spacebio_review.evidence.store.optional_secret", lambda *args: None)


def article(*, supplements="", paragraph="Measured mitochondrial nucleoids.", year=2011):
    return f"""<article xmlns:xlink="http://www.w3.org/1999/xlink">
      <front><article-meta><pub-date><year>{year}</year></pub-date></article-meta></front>
      <body><sec><title>Methods</title><p>{paragraph}</p></sec>
      <fig id="F1"><caption><p>Conditions for Figure 1.</p></caption>
      <graphic xlink:href="https://example.org/figure.png"/></fig></body>
      <back>{supplements}</back></article>"""


def xml_response(text):
    return httpx.Response(200, text=text, headers={"content-type": "application/xml"})


async def test_selected_doi_locates_pmc_and_keeps_source_identity(tmp_path):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("/search"):
            assert request.url.params["query"] == 'DOI:"10.1234/nucleoids"'
            return httpx.Response(
                200,
                json={
                    "resultList": {
                        "result": [
                            {
                                "id": "123",
                                "source": "MED",
                                "doi": "10.1234/nucleoids",
                                "pmcid": "PMC123",
                                "title": "Nucleoids",
                                "firstPublicationDate": "2011-01-01",
                            }
                        ]
                    }
                },
            )
        assert request.url.path.endswith("/PMC123/fullTextXML")
        return xml_response(article())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        discovery = Discovery(client, config=DiscoveryConfig(intervals={}))
        store = SourceStore(tmp_path, client, discovery=discovery, browser_fallback=False)
        sid = store.register(
            SourceCandidate(title="Nucleoids", identifiers={"doi": "10.1234/nucleoids"})
        )
        doc = await store.acquire(sid)
        assert doc.source_id == sid and doc.availability == "full_text"
        assert store.candidate(sid).identifiers["pmcid"] == "PMC123"
        assert len(store.catalogue()) == 1
        assert len(requests) == 2
        again = await store.acquire(sid)
        assert again.passages == doc.passages and len(requests) == 2


async def test_exact_openalex_lookup_uses_identity_and_retains_primary_historical_location(
    tmp_path,
):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.path == "/works/W123"
        assert "search" not in request.url.params
        return httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W123",
                "doi": "https://doi.org/10.1234/paper",
                "display_name": "Paper",
                "publication_date": "2010-01-01",
                "primary_location": {"is_oa": True, "pdf_url": "https://example.org/published.pdf"},
                "locations": [{"is_oa": True, "pdf_url": "https://example.org/later-version.pdf"}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        discovery = Discovery(client, config=DiscoveryConfig(intervals={}))
        candidate = SourceCandidate(
            title="Paper", identifiers={"openalex": "https://openalex.org/W123"}
        )
        result = await discovery.locate(candidate, "openalex", date(2012, 1, 1))
        assert result.candidates[0].fulltext_urls == ["https://example.org/published.pdf"]
        await discovery.locate(candidate, "openalex", date(2012, 1, 1))
        assert len(requests) == 1


async def test_landing_metadata_follows_redirect_base_and_exposes_unfetched_supplement(
    tmp_path, monkeypatch
):
    requests = []

    def respond(request):
        requests.append(request.url.path)
        if request.url.path == "/entry":
            return httpx.Response(302, headers={"location": "/paper/index.html"})
        if request.url.path == "/paper/index.html":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="""
              <html><head><meta name="citation_pdf_url" content="../paper.pdf"></head>
              <body><a href="supp/methods.txt">Supplementary Methods</a></body></html>""",
            )
        assert request.url.path == "/paper.pdf"
        return httpx.Response(
            200, content=b"%PDF-fixture", headers={"content-type": "application/pdf"}
        )

    def parse(sid, candidate, path, output, *args):
        if path.suffix != ".pdf":
            return parse_document(sid, candidate, path, output, *args)
        return SourceDocument(
            source_id=sid,
            candidate=candidate,
            availability="full_text",
            original_path=str(path),
            passages=[Passage(passage_id="p1", locator="page 1", text="Actual PDF methods.")],
        )

    monkeypatch.setattr("spacebio_review.evidence.store.parse_document", parse)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True
    ) as client:
        store = SourceStore(tmp_path, client, browser_fallback=False)
        sid = store.register(SourceCandidate(title="Paper", url="https://example.org/entry"))
        doc = await store.acquire(sid)
        assert doc.availability == "full_text"
        assert requests == ["/entry", "/paper/index.html", "/paper.pdf"]
        supplements = [m for m in doc.materials if m.role == "supplement"]
        assert len(supplements) == 1
        assert supplements[0].url == "https://example.org/paper/supp/methods.txt"
        assert supplements[0].original_path is None


async def test_append_supplement_keeps_long_units_and_restored_originals(tmp_path):
    requests = []
    long_text = "Culture at 37 degrees. " * 180
    supplements = """<supplementary-material id="S1" xlink:href="https://example.org/methods.xml">
      <label>Supplementary Methods</label></supplementary-material>
      <supplementary-material id="S2" xlink:href="https://example.org/table.csv">
      <label>Table S2</label></supplementary-material>"""

    def respond(request):
        requests.append(request.url.path)
        if request.url.path == "/main.xml":
            return xml_response(article(supplements=supplements, paragraph=long_text))
        if request.url.path == "/methods.xml":
            return xml_response(article(paragraph="Other conditions. " * 180))
        assert request.url.path == "/table.csv"
        return httpx.Response(
            200, text="Group,Size\nControl,100", headers={"content-type": "text/csv"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        store = SourceStore(tmp_path, client, browser_fallback=False)
        sid = store.register(
            SourceCandidate(title="Paper", fulltext_urls=["https://example.org/main.xml"])
        )
        first = await store.acquire(sid)
        original = Path(first.original_path).read_bytes()
        selected = next(
            m for m in first.materials if m.role == "supplement" and "Methods" in m.title
        )
        second = await store.acquire(sid, material_id=selected.material_id)
        assert second.passages[: len(first.passages)] == first.passages
        groups = {p.unit_id for p in second.passages if p.unit_id}
        assert len(groups) == 2
        assert len({p.passage_id for p in second.passages}) == len(second.passages)
        assert Path(first.original_path).read_bytes() == original
        restored = SourceStore(tmp_path, client, browser_fallback=False)
        restored.restore()
        table = next(m for m in second.materials if m.title == "Table S2")
        third = await restored.acquire(sid, material_id=table.material_id)
        assert third.passages[: len(second.passages)] == second.passages
        assert Path(first.original_path).read_bytes() == original
        assert any("Table S2" in p.locator and "Control,100" in p.text for p in third.passages)
        await restored.acquire(sid, material_id=table.material_id)
        assert requests == ["/main.xml", "/methods.xml", "/table.csv"]


async def test_pmc_package_only_extracts_selected_members_and_is_reused(tmp_path):
    package = BytesIO()
    with ZipFile(package, "w") as archive:
        archive.writestr("files/methods.txt", "Supplementary method conditions.")
        archive.writestr("files/table.csv", "Group,Value\nTreated,8")
        archive.writestr("unrelated.txt", "Do not open this automatically.")
    requests = []
    supplements = """<supplementary-material id="S1"><label>Methods S1</label>
      <media xlink:href="methods.txt"/></supplementary-material>
      <supplementary-material id="S2"><label>Table S2</label>
      <media xlink:href="table.csv"/></supplementary-material>"""

    def respond(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/fullTextXML"):
            return xml_response(article(supplements=supplements))
        assert request.url.path.endswith("/supplementaryFiles")
        return httpx.Response(
            200, content=package.getvalue(), headers={"content-type": "application/zip"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        store = SourceStore(tmp_path, client, browser_fallback=False)
        sid = store.register(SourceCandidate(title="Paper", identifiers={"pmcid": "PMC123"}))
        first = await store.acquire(sid)
        materials = [m for m in first.materials if m.role == "supplement"]
        assert len(requests) == 1
        assert {m.package_member for m in materials} == {"methods.txt", "table.csv"}
        second = await store.acquire(sid, material_id=materials[0].material_id)
        assert not any("Treated" in p.text for p in second.passages)
        third = await store.acquire(sid, material_id=materials[1].material_id)
        assert any("Treated,8" in p.text for p in third.passages)
        assert len(requests) == 2
        assert not list(tmp_path.rglob("unrelated.txt"))


async def test_supplement_does_not_upgrade_missing_main_and_images_are_available(tmp_path):
    image = BytesIO()
    from PIL import Image

    Image.new("RGB", (2, 2)).save(image, format="PNG")

    def respond(request):
        if request.url.path == "/paper":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="""
              <html><a href="/supp.txt">Supplementary methods</a>
              <a href="/image.png">Supplementary Figure 1</a></html>""",
            )
        if request.url.path == "/supp.txt":
            return httpx.Response(200, text="Method only.", headers={"content-type": "text/plain"})
        assert request.url.path == "/image.png"
        return httpx.Response(200, content=image.getvalue(), headers={"content-type": "image/png"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        store = SourceStore(tmp_path, client, browser_fallback=False)
        sid = store.register(
            SourceCandidate(
                title="Paper", url="https://example.org/paper", abstract="Abstract only."
            )
        )
        first = await store.acquire(sid)
        assert first.availability != "full_text"
        for material in [m for m in first.materials if m.role == "supplement"]:
            doc = await store.acquire(sid, material_id=material.material_id)
            assert doc.availability != "full_text"
        assert doc.visuals
        pixels, mime = await store.visual_bytes(sid, doc.visuals[0])
        assert pixels == image.getvalue() and mime == "image/png"


async def test_original_after_cutoff_is_not_added_to_readable_materials(tmp_path):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: xml_response(article(year=2025)))
    ) as client:
        store = SourceStore(tmp_path, client, browser_fallback=False)
        sid = store.register(
            SourceCandidate(
                title="Old metadata",
                publication_date=date(2011, 1, 1),
                fulltext_urls=["https://example.org/paper.xml"],
            )
        )
        doc = await store.acquire(sid, cutoff=date(2020, 1, 1))
        assert not doc.passages
        assert any("original_after_cutoff" in message for message in doc.limitations)


async def test_article_links_keep_supplements_and_other_papers_out_of_default_acquisition(tmp_path):
    requested = []
    page = """<html><body><article><p>Available abstract.</p>
      <a href="https://publisher.org/DCSupplemental/SI.pdf?targetid=SF1">Fig. S1</a>
      <a href="https://publisher.org/DCSupplemental/SI.pdf?targetid=STXT">
        SI Materials and Methods</a>
      <a href="/articles/instance/123/bin/paperSI.pdf">paperSI.pdf</a>
      <section id="data-supp"><h3>Supporting Information</h3>
        <a href="/table.csv">Table 1</a></section>
      <section class="ref-list"><h2>References</h2>
        <a href="https://elsewhere.org/unrelated.pdf">PDF</a></section>
      <a href="/policy.pdf">Privacy policy</a>
      </article></body></html>"""

    def respond(request):
        requested.append(str(request.url))
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        store = SourceStore(tmp_path, client, browser_fallback=False)
        sid = store.register(SourceCandidate(title="Paper", url="https://example.org/article"))
        doc = await store.acquire(sid)
        assert requested == ["https://example.org/article"]
        assert len([m for m in doc.materials if m.role == "main"]) == 1
        supplements = [m for m in doc.materials if m.role == "supplement"]
        assert len(supplements) == 3
        assert all("targetid" not in m.url for m in supplements)
        assert not any("unrelated" in m.url or "policy" in m.url for m in doc.materials)


async def test_docx_content_type_reaches_native_document_parser(tmp_path, monkeypatch):
    seen = []

    def parse(sid, candidate, path, output, *args):
        seen.append((path.suffix, path.read_bytes()))
        assert path.suffix == ".docx"
        return SourceDocument(
            source_id=sid,
            candidate=candidate,
            availability="full_text",
            passages=[Passage(passage_id="p1", locator="Methods", text="Methods.")],
        )

    monkeypatch.setattr("spacebio_review.evidence.store.parse_document", parse)
    response = httpx.Response(
        200,
        content=b"PK-native-docx",
        headers={
            "content-type": (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
        },
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
        store = SourceStore(tmp_path, client, browser_fallback=False)
        sid = store.register(SourceCandidate(title="Methods", url="https://example.org/file"))
        doc = await store.acquire(sid)
        assert doc.availability == "full_text" and seen == [(".docx", b"PK-native-docx")]


async def test_later_supplement_version_survives_restore_and_earlier_cutoff(tmp_path):
    supplements = """<supplementary-material xlink:href="https://example.org/supp.xml">
      <label>Supporting Information</label></supplementary-material>"""

    def respond(request):
        return xml_response(
            article(year=2025)
            if request.url.path == "/supp.xml"
            else article(supplements=supplements)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        store = SourceStore(tmp_path, client, browser_fallback=False)
        sid = store.register(SourceCandidate(title="Paper", url="https://example.org/main.xml"))
        main = await store.acquire(sid)
        supp = next(m for m in main.materials if m.role == "supplement")
        doc = await store.acquire(sid, material_id=supp.material_id)
        assert doc.candidate.version_date.year == 2025
        assert any("Supporting Information (supp.xml)" in p.locator for p in doc.passages)
        restored = SourceStore(tmp_path, client, browser_fallback=False)
        restored.restore()
        with pytest.raises(ValueError, match="original_after_cutoff"):
            await restored.acquire(sid, cutoff=date(2020, 1, 1))


async def test_identity_enrichment_keeps_original_date_and_precision(tmp_path):
    from spacebio_review.evidence.discovery import DiscoveryResult

    class Metadata:
        async def locate(self, candidate, provider, cutoff):
            return DiscoveryResult(
                candidates=[
                    candidate.model_copy(
                        update={"publication_date": date(2010, 12, 31), "date_precision": "year"}
                    )
                ]
            )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404))
    ) as client:
        store = SourceStore(tmp_path, client, discovery=Metadata(), browser_fallback=False)
        candidate = SourceCandidate(
            title="Paper",
            identifiers={"doi": "10.1234/date"},
            publication_date=date(2010, 12, 31),
            date_precision="year",
        )
        sid = store.register(candidate)
        original = candidate.model_copy(
            update={"publication_date": date(2020, 4, 5), "date_precision": "day"}
        )
        store._save(SourceDocument(source_id=sid, candidate=original, availability="partial"))
        doc = await store.acquire(sid, cutoff=date(2021, 1, 1))
        assert doc.candidate.publication_date == date(2020, 4, 5)
        assert doc.candidate.date_precision == "day"


async def test_located_abstract_remains_available_when_fulltext_cannot_be_acquired(tmp_path):
    def respond(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={
                    "resultList": {
                        "result": [
                            {
                                "id": "123",
                                "source": "MED",
                                "doi": "10.1234/abstract",
                                "title": "Paper",
                                "abstractText": "Original metadata abstract.",
                            }
                        ]
                    }
                },
            )
        return httpx.Response(403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        discovery = Discovery(client, config=DiscoveryConfig(enabled=["europe_pmc"], intervals={}))
        store = SourceStore(tmp_path, client, discovery=discovery, browser_fallback=False)
        sid = store.register(
            SourceCandidate(title="Paper", identifiers={"doi": "10.1234/abstract"})
        )
        doc = await store.acquire(sid)
        assert doc.availability == "abstract"
        assert doc.passages[0].text == "Original metadata abstract."
