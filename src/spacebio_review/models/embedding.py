"""DashScope embeddings with a content-addressed memory and optional disk cache."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from openai import AsyncOpenAI, OpenAIError

from .config import EmbeddingConfig, credential
from .provider import ModelError
from .scheduling import RequestScheduler
from .session import RunModel


@dataclass
class EmbeddingReply:
    vectors: list[list[float]]
    prompt_tokens: int | None = None
    completion_tokens: int = 0
    retries: int = 0
    retry_wait_seconds: float = 0.0


class DashScopeEmbedding:
    def __init__(
        self, config: EmbeddingConfig, scheduler: RequestScheduler, *, env_file=".env", client=None
    ):
        self.config, self.scheduler = config, scheduler
        self.client = client or AsyncOpenAI(
            api_key=credential(config.api_key_env, env_file),
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            max_retries=0,
        )
        key = getattr(self.client, "api_key", None)
        self.credential_id = (
            hashlib.sha256(key.encode()).hexdigest() if isinstance(key, str) else None
        )
        self.availability = RunModel(self, scheduler.stopped)

    async def close(self):
        await self.client.close()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for start in range(0, len(texts), self.config.batch_size):
            batch = texts[start : start + self.config.batch_size]

            async def request():
                if not self.availability.available:
                    raise ModelError("model_service_stopped:embedding", terminal=True)
                retry_wait = 0.0
                for attempt in range(self.config.retries + 1):
                    budget = getattr(self.scheduler, "request_budget", None)
                    ticket = budget.reserve(self.config, texts=batch) if budget else None
                    try:
                        response = await self.client.embeddings.create(
                            model=self.config.model,
                            input=batch,
                            dimensions=self.config.dimensions,
                            encoding_format="float",
                        )
                        if budget:
                            budget.settle(ticket, response.usage)
                        rows = sorted(response.data, key=lambda row: row.index)
                        vectors = [r.embedding for r in rows]
                        if [r.index for r in rows] != list(range(len(batch))):
                            raise ValueError("embedding_response_indices")
                        validate_vectors(vectors, len(batch), self.config.dimensions)
                        return EmbeddingReply(
                            vectors,
                            getattr(response.usage, "prompt_tokens", None),
                            retries=attempt,
                            retry_wait_seconds=retry_wait,
                        )
                    except OpenAIError as exc:
                        status = getattr(exc, "status_code", None)
                        body = getattr(exc, "body", None) or {}
                        detail = body.get("error", body) if isinstance(body, dict) else {}
                        code = (
                            str(detail.get("code", "")).lower() if isinstance(detail, dict) else ""
                        )
                        terminal = status in {401, 403} or any(
                            s in code
                            for s in (
                                "arrearage",
                                "insufficient",
                                "quotaexhaust",
                                "quota_exhaust",
                                "balance",
                            )
                        )
                        if terminal:
                            self.scheduler.stopped[self.availability.credential_key] = (
                                "embedding_credentials_unavailable"
                            )
                            raise ModelError(
                                "embedding_service_unavailable",
                                terminal=True,
                                scope="credential",
                                retries=attempt,
                                retry_wait_seconds=retry_wait,
                            ) from None
                        retryable = status is None or status in {408, 409, 429} or status >= 500
                        if not retryable or attempt == self.config.retries:
                            raise ModelError(
                                "embedding_service_unavailable",
                                retries=attempt,
                                retry_wait_seconds=retry_wait,
                            ) from exc
                        await asyncio.sleep(2**attempt)
                        retry_wait += 2**attempt
                    except (ValueError, TypeError) as exc:
                        raise ModelError("embedding_invalid_vectors") from exc

            reply = await self.scheduler.call(
                request, kind="embedding", name="literature_embedding", model_id=self.config.model
            )
            result.extend(reply.vectors)
        return result


def validate_vectors(vectors, count, dimensions):
    if len(vectors) != count or any(
        len(v) != dimensions or not all(math.isfinite(x) for x in v) or not any(x != 0 for x in v)
        for v in vectors
    ):
        raise ValueError("incompatible_embedding_vectors")


class VectorIndex:
    """No persistent database. Identical passages share vectors, never interpretations.

    Both query and document use the same plain-text encoding. Long text is split,
    embedded in full, and pooled; it is never silently truncated to a prefix.
    """

    def __init__(self, embedder, *, cache_dir=None):
        self.embedder = embedder
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._vectors: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()
        self.cache_hits = 0

    def _load_vector(self, key):
        if self.cache_dir is None:
            return None
        try:
            vector = json.loads((self.cache_dir / (key + ".json")).read_text("utf-8"))
            validate_vectors([vector], 1, self.embedder.config.dimensions)
            return vector
        except (OSError, ValueError, TypeError):
            return None

    def _save_vector(self, key, vector):
        if self.cache_dir is None:
            return
        path = self.cache_dir / (key + ".json")
        temp = path.with_suffix("." + uuid4().hex + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps(vector, allow_nan=False), "utf-8")
            temp.replace(path)
        except OSError:
            logging.getLogger(__name__).warning("Embedding cache unavailable; using memory cache")
            self.cache_dir = None
        finally:
            with suppress(OSError):
                temp.unlink(missing_ok=True)

    async def vectors(self, texts: list[str]):
        config = self.embedder.config
        space = json.dumps(
            {
                "model": config.model,
                "endpoint": config.base_url,
                "dimensions": config.dimensions,
                "max_chars": config.max_chars,
                "encoding": "float/plain-whitespace-v1/mean-chunks",
            },
            sort_keys=True,
        )
        normalized = [" ".join(t.split()) or "[empty]" for t in texts]
        chunks = [
            [t[i : i + config.max_chars] for i in range(0, len(t), config.max_chars)]
            for t in normalized
        ]

        def key(text):
            return hashlib.sha256((space + text).encode()).hexdigest()

        async with self._lock:
            for t in {t for group in chunks for t in group}:
                k = key(t)
                if k not in self._vectors and (vector := self._load_vector(k)) is not None:
                    self._vectors[k] = vector
            self.cache_hits += sum(key(t) in self._vectors for group in chunks for t in group)
            missing = {key(t): t for group in chunks for t in group if key(t) not in self._vectors}
            items = list(missing.items())
            for start in range(0, len(items), config.batch_size):
                batch = items[start : start + config.batch_size]
                vectors = await self.embedder.embed([t for _, t in batch])
                validate_vectors(vectors, len(batch), config.dimensions)
                for (k, _), vector in zip(batch, vectors, strict=True):
                    self._vectors[k] = vector
                    self._save_vector(k, vector)
            return [
                [
                    sum(self._vectors[key(t)][d] for t in group) / len(group)
                    for d in range(config.dimensions)
                ]
                for group in chunks
            ]

    async def rank(self, texts, queries):
        vectors = await self.vectors(list(texts) + list(queries))
        docs, questions = vectors[: len(texts)], vectors[len(texts) :]

        def cosine(a, b):
            scale = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
            return sum(x * y for x, y in zip(a, b)) / scale if scale else 0.0

        scores = [max(cosine(doc, q) for q in questions) for doc in docs]
        return sorted(range(len(docs)), key=lambda i: scores[i], reverse=True)
