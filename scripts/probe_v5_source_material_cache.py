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


async def run(output, evidence_config):
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
        first.store.restore()
        restored = await first.store.acquire(original.source_id)
        assert Path(restored.original_path).is_file() and len(first.requests) == 1
        assert "2 units" in " ".join(p.text for p in restored.passages)

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
        }
        write_json(output / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        for item in fixtures:
            await item.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-config", type=Path, default=REPO / "config/evidence.yaml")
    args = parser.parse_args()
    asyncio.run(run(args.output.resolve(), args.evidence_config.resolve()))


if __name__ == "__main__":
    main()
