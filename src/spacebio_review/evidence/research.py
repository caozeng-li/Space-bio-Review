from __future__ import annotations

from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.agents import AgentRuntime

from .discovery import Discovery, DiscoveryConfig
from .knowledge import LiteratureConfig
from .normalization import LegacyDocumentConfig
from .store import SourceStore


class EvidenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    http_timeout_seconds: float = Field(default=60, gt=0)
    browser_fallback: bool = True
    model_rerank: bool = True
    legacy_document: LegacyDocumentConfig = Field(default_factory=LegacyDocumentConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    literature: LiteratureConfig = Field(default_factory=LiteratureConfig)


def load_evidence_config(path: str | Path) -> EvidenceConfig:
    return EvidenceConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


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
        return cls(
            runtime,
            SourceStore(
                Path(run_dir) / "sources",
                client,
                env_file=env_file,
                browser_fallback=config.browser_fallback,
                legacy_document=config.legacy_document,
            ),
            Discovery(client, env_file, config.discovery),
            config,
        )

    async def close(self):
        await self.store.client.aclose()
