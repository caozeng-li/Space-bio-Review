from __future__ import annotations

from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.agents import AgentRuntime

from .discovery import Discovery, DiscoveryConfig
from .material_cache import MaterialCache, MaterialCacheConfig
from .normalization import LegacyDocumentConfig
from .store import SourceStore


class EvidenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    http_timeout_seconds: float = Field(default=60, gt=0)
    browser_fallback: bool = True
    model_rerank: bool = True
    legacy_document: LegacyDocumentConfig = Field(default_factory=LegacyDocumentConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    material_cache: MaterialCacheConfig | None = None


def load_evidence_config(path: str | Path) -> EvidenceConfig:
    path = Path(path).resolve()
    config = EvidenceConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if config.material_cache:
        config.material_cache.root = (path.parent / config.material_cache.root).resolve()
    return config


class EvidenceResources:
    def __init__(
        self,
        runtime: AgentRuntime,
        store: SourceStore,
        discovery: Discovery,
        config: EvidenceConfig | None = None,
    ):
        self.runtime, self.store, self.discovery = runtime, store, discovery
        self.config = config or EvidenceConfig()

    @classmethod
    def create(cls, runtime, run_dir, config=None, env_file=".env"):
        config = config or EvidenceConfig()
        client = httpx.AsyncClient(
            timeout=config.http_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "SpaceBioReview/0.1 (research reader)"},
        )
        discovery = Discovery(client, env_file, config.discovery)
        store = SourceStore(
            Path(run_dir) / "sources",
            client,
            env_file=env_file,
            browser_fallback=config.browser_fallback,
            legacy_document=config.legacy_document,
            discovery=discovery,
            material_cache=(
                MaterialCache(config.material_cache.root) if config.material_cache else None
            ),
            prefer_cached=config.material_cache.prefer_existing if config.material_cache else True,
        )
        store.restore()
        return cls(
            runtime,
            store,
            discovery,
            config,
        )

    async def close(self):
        await self.store.client.aclose()
