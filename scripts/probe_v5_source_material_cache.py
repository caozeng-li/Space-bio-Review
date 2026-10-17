"""Raw-file cache acceptance through real acquisition/parsing, with offline HTTP fixtures."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import date
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import httpx
from PIL import Image

from spacebio_review.evidence.material_cache import MaterialCache
from spacebio_review.evidence.research import EvidenceResources, load_evidence_config
from spacebio_review.evidence.store import SourceStore
from spacebio_review.evidence.types import SourceCandidate, Visual
from spacebio_review.persistence import write_json

REPO = Path(__file__).resolve().parents[1]
CELL = REPO / "runs/mytest34_sample2/sources/src-4051da8bd898"
URL = "https://fixture.example/article.xml"


def article(result):
    return (
        "<article><body><sec><title>Methods</title><p>The paired samples were measured "
        "at the same time after return.</p></sec><sec><title>Results</title><p>"
        + result
        + "</p></sec></body></article>"
    ).encode()


class Fixture:
    def __init__(self, root, cache, content=None, *, status=200, content_type="application/xml"):
        self.requests = []

        def respond(request):
            self.requests.append(str(request.url))
            if content is None:
                raise httpx.ConnectError("offline fixture", request=request)
            return httpx.Response(status, content=content, headers={"content-type": content_type})

        self.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        self.store = SourceStore(
            root / "sources", self.client, material_cache=cache, browser_fallback=False
        )

    async def acquire(self, candidate, **kwargs):
        sid = self.store.register(candidate)
        return await self.store.acquire(sid, **kwargs)

    async def close(self):
        await self.client.aclose()


async def run(output, history, evidence_config):
    output.mkdir(parents=True, exist_ok=False)
    cache = MaterialCache(output / "cache")
    fixtures = []

    def fixture(name, content=None, **kwargs):
        item = Fixture(output / name, cache, content, **kwargs)
        fixtures.append(item)
        return item

    candidate = SourceCandidate(
        title="Fixed paired-sample study",
        identifiers={"doi": "10.1234/cache-example"},
        url=URL,
        version="article",
        publication_date=date(2020, 1, 1),
    )
    try:
        first = fixture("first", article("The original measured difference was 2 units."))
        original = await first.acquire(candidate)
        assert original.availability == "full_text" and len(first.requests) == 1
        snap = original.materials[0].snapshot_ref
        assert snap and original.materials[0].acquired_at

        second = fixture("second")
        same = candidate.model_copy(update={"title": "Different display title", "url": ""})
        repeated = await second.acquire(same)
        assert second.requests == [] and repeated.source_id != original.source_id
        assert repeated.materials[0].snapshot_ref == snap
        assert Path(repeated.original_path).parent != Path(original.original_path).parent
        assert "2 units" in " ".join(p.text for p in repeated.passages)
        assert repeated.passages is not original.passages

        assert not cache.find(candidate.model_copy(update={"version": "preprint-v2"}))
        unknown_version = candidate.model_copy(
            update={"identifiers": {"arxiv": "2301.12345"}, "version": "", "url": ""}
        )
        cache.retain(
            unknown_version,
            Path(original.original_path),
            role="main",
            url="https://fixture.example/preprint.xml",
        )
        assert not cache.find(unknown_version.model_copy(update={"version": "v2"}))
        assert not cache.find(candidate, cutoff=date(2019, 12, 31))
        assert not cache.find(
            candidate.model_copy(update={"identifiers": {"doi": "10.1234/other"}})
        )

        refreshed = fixture("refreshed", article("The revised measured difference was 3 units."))
        revised = await refreshed.acquire(candidate, refresh=True)
        assert len(refreshed.requests) == 1
        assert revised.materials[0].snapshot_ref != snap
        assert revised.materials[0].content_sha256 != original.materials[0].content_sha256
        first.store.restore()
        pinned = await first.store.acquire(original.source_id, refresh=True)
        assert len(first.requests) == 1
        assert "2 units" in " ".join(p.text for p in pinned.passages)

        unavailable = fixture("refresh-failed", b"", status=503)
        fallback = await unavailable.acquire(candidate, refresh=True)
        assert len(unavailable.requests) == 1
        assert "3 units" in " ".join(p.text for p in fallback.passages)
        assert "retained_snapshot_after_acquisition_failure" in fallback.limitations
        assert any("HTTP_503" in limitation for limitation in fallback.limitations)

        # Dynamic pages fetch first, then can use an explicitly recorded earlier snapshot.
        web = SourceCandidate(
            title="Fixed web page", kind="web", url="https://fixture.example/page.txt"
        )
        page1 = fixture("web-first", b"First dated page.", content_type="text/plain")
        await page1.acquire(web)
        page2 = fixture("web-second", b"Updated page.", content_type="text/plain")
        current = await page2.acquire(web)
        assert len(page2.requests) == 1 and current.passages[0].text == "Updated page."
        page3 = fixture("web-unavailable")
        retained = await page3.acquire(web)
        assert len(page3.requests) == 1 and retained.passages[0].text == "Updated page."

        # Pixels are raw assets too; their scientific interpretation is never cached here.
        pixels = BytesIO()
        Image.new("RGB", (2, 2), color=(210, 80, 30)).save(pixels, format="PNG")
        image_url = "https://fixture.example/figure.png"
        image_first = fixture("visual-first", pixels.getvalue(), content_type="image/png")
        image_doc = await image_first.acquire(candidate)
        visual = Visual(visual_id="fixed-figure", locator="Figure 1", url=image_url)
        image_doc.visuals.append(visual)
        image_first.store._save(image_doc)
        image_bytes, _ = await image_first.store.visual_bytes(image_doc.source_id, visual)
        assert len(image_first.requests) == 1
        image_second = fixture("visual-second")
        image_doc2 = await image_second.acquire(candidate)
        image_doc2.visuals.append(visual.model_copy())
        image_second.store._save(image_doc2)
        again, _ = await image_second.store.visual_bytes(image_doc2.source_id, visual)
        assert again == image_bytes and image_second.requests == []

        # Cache the actual rendered document, never the empty HTML shell it replaced.
        rendered_html = (
            "<html><head><title>Experimental methods and findings</title></head><body><article>"
            "<h1>Experimental methods and findings</h1><h2>Methods</h2>"
            "<p>The paired biological samples were cultured in a controlled incubator and "
            "measured at the same time after return. Identical equipment and acquisition "
            "settings were used for the measurements in both experimental groups.</p>"
            "<h2>Results</h2><p>The measured fluorescence changed over time in both groups. "
            "The authors report the measurement distribution and experimental conditions "
            "along with the observed difference between the biological samples.</p>"
            "</article></body></html>"
        ).encode()
        shell = b'<html><head><title>App</title></head><body><div id="root"></div></body></html>'
        dynamic = fixture("rendered-first", shell, content_type="text/html")
        dynamic.store.browser_fallback = True
        dynamic_candidate = SourceCandidate(
            title="Rendered scientific page", url="https://fixture.example/rendered"
        )

        async def rendered(url):
            return rendered_html, url

        with patch("spacebio_review.evidence.store.render_public_page", rendered):
            rendered_doc = await dynamic.acquire(dynamic_candidate)
        assert rendered_doc.availability == "partial"
        assert Path(rendered_doc.materials[0].original_path).name == "rendered.html"
        assert rendered_doc.materials[0].content_sha256 == sha256(rendered_html).hexdigest()
        dynamic2 = fixture("rendered-reused")
        rendered_again = await dynamic2.acquire(dynamic_candidate)
        assert dynamic2.requests == [] and rendered_again.passages
        assert Path(rendered_again.original_path).read_bytes() == rendered_html

        # Import the actual old Cell file, not its reader note or source ID.
        source_before = (history / "source.json").read_bytes()
        cell_record = json.loads(source_before)
        old_file = Path(cell_record["original_path"])
        original_hash = sha256(old_file.read_bytes()).hexdigest()
        imported = cache.import_source(history)
        assert imported and all(row.role != "visual" for row in imported)
        assert all(row.acquired_at is None for row in imported)
        cell = fixture("cell-reused")
        cell_candidate = SourceCandidate(
            title="Cell paper via DOI",
            identifiers={"doi": "10.1016/j.cell.2020.11.002"},
            url="https://doi.org/10.1016/j.cell.2020.11.002",
            version="article",
        )
        cell_doc = await cell.acquire(cell_candidate)
        assert cell.requests == [] and cell_doc.availability == "partial"
        assert len(cell_doc.passages) > 1 and cell_doc.source_id != cell_record["source_id"]
        assert any(p.kind != "abstract" for p in cell_doc.passages)
        assert sha256(Path(cell_doc.original_path).read_bytes()).hexdigest() == original_hash
        await cell.store.acquire(cell_doc.source_id)
        assert cell.requests == []
        assert source_before == (history / "source.json").read_bytes()
        assert original_hash == sha256(old_file.read_bytes()).hexdigest()
        failed_history = REPO / "runs/mytest35_sample2/sources/src-2a0641f07dfb"
        assert cache.import_source(failed_history) == []

        # One loader/factory owns path resolution for production and direct topic entry.
        config = load_evidence_config(evidence_config)
        root = config.material_cache.root
        previous_cwd = Path.cwd()
        try:
            os.chdir(output)
            loaded_elsewhere = load_evidence_config(evidence_config)
        finally:
            os.chdir(previous_cwd)
        assert root == loaded_elsewhere.material_cache.root
        resources = EvidenceResources.create(None, output / "factory", config)
        try:
            assert resources.store.material_cache.root == root
        finally:
            await resources.close()
        legacy = output / "legacy-evidence.yaml"
        legacy.write_text("http_timeout_seconds: 60\n", encoding="utf-8")
        assert load_evidence_config(legacy).material_cache is None

        # Detaching this isolated cache cannot break the run-local originals.
        moved_cache = output / "cache-detached"
        cache.root.rename(moved_cache)
        cell.store.restore()
        restored_cell = await cell.store.acquire(cell_doc.source_id)
        assert Path(restored_cell.original_path).is_file() and cell.requests == []

        summary = {
            "status": "passed",
            "model_requests": 0,
            "live_http_requests": 0,
            "fixture_http_requests": sum(len(item.requests) for item in fixtures),
            "fresh_acquisition_and_snapshot_reuse": True,
            "explicit_identity_version_and_cutoff": True,
            "refresh_preserves_previous_run": True,
            "refresh_failure_uses_recorded_snapshot": True,
            "dynamic_web_fetches_before_fallback": True,
            "downloaded_image_pixels_reused": True,
            "actual_rendered_file_reused": True,
            "configuration_path_and_factory_consistent": True,
            "run_survives_cache_detachment": True,
            "cell": {
                "imported_source": str(history),
                "snapshots": [row.snapshot_id for row in imported],
                "availability": cell_doc.availability,
                "passages": len(cell_doc.passages),
                "visual_entries": len(cell_doc.visuals),
                "downloaded_visuals": sum(bool(v.path) for v in cell_doc.visuals),
                "source_id": cell_doc.source_id,
                "retained_original": cell_doc.original_path,
                "original_sha256": original_hash,
                "sample_sections": list(dict.fromkeys(p.section for p in cell_doc.passages))[:8],
            },
        }
        write_json(output / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        for item in fixtures:
            await item.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source", type=Path, default=CELL)
    parser.add_argument("--evidence-config", type=Path, default=REPO / "config/evidence.yaml")
    parser.add_argument(
        "--import-only", action="store_true", help="Import only the specified source"
    )
    args = parser.parse_args()
    config_path = args.evidence_config.resolve()
    if args.import_only:
        config = load_evidence_config(config_path)
        if config.material_cache is None:
            parser.error("the evidence configuration must provide material_cache")
        snapshots = MaterialCache(config.material_cache.root).import_source(args.source)
        print(
            json.dumps(
                {
                    "cache": str(config.material_cache.root),
                    "source": str(args.source.resolve()),
                    "snapshots": [item.snapshot_id for item in snapshots],
                    "model_requests": 0,
                    "http_requests": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        if not args.output:
            parser.error("--output is required for the offline probe")
        asyncio.run(run(args.output.resolve(), args.source.resolve(), config_path))


if __name__ == "__main__":
    main()
