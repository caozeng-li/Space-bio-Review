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

from openai import APITimeoutError, AsyncOpenAI, OpenAIError

from .config import EmbeddingConfig, credential
from .provider import (
    _RETRYABLE_CATEGORIES,
    ModelError,
    _mark_sent,
    _request_estimate,
    _scheduled_attempt,
    _settle_attempt,
    http_failure,
)
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
        self,
        config: EmbeddingConfig,
        scheduler: RequestScheduler,
        *,
        env_file=".env",
        client=None,
        budget=None,
    ):
        self.config, self.scheduler = config, scheduler
        self.budget = budget
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

            retry_wait = retry_delay = 0.0
            for attempt in range(self.config.retries + 1):
                estimate = _request_estimate(self.config, self.budget, texts=batch)

                async def request():
                    ticket, usage, sent, outcome = None, None, False, "failed"
                    try:
                        if not self.availability.available:
                            raise ModelError(
                                "model_service_stopped:embedding", terminal=True, request_sent=False
                            )
                        if self.budget is not None:
                            ticket = self.budget.reserve(self.config, texts=batch)
                        _mark_sent(self.budget, self.scheduler, ticket)
                        sent = True
                        response = await self.client.embeddings.create(
                            model=self.config.model,
                            input=batch,
                            dimensions=self.config.dimensions,
                            encoding_format="float",
                        )
                        usage = response.usage
                        rows = sorted(response.data, key=lambda row: row.index)
                        vectors = [r.embedding for r in rows]
                        if [r.index for r in rows] != list(range(len(batch))):
                            raise ValueError("embedding_response_indices")
                        validate_vectors(vectors, len(batch), self.config.dimensions)
                        outcome = "completed"
                        return EmbeddingReply(
                            vectors,
                            getattr(usage, "prompt_tokens", None),
                            retries=attempt,
                            retry_wait_seconds=retry_wait,
                        )
                    except OpenAIError as exc:
                        status = getattr(exc, "status_code", None)
                        body = getattr(exc, "body", None) or {}
                        detail = body.get("error", body) if isinstance(body, dict) else {}
                        code = detail.get("code") if isinstance(detail, dict) else None
                        category, scope = (
                            http_failure(status, code, profile=self.config.error_profile)
                            if status is not None
                            else (
                                "timeout" if isinstance(exc, APITimeoutError) else "connection",
                                None,
                            )
                        )
                        raise ModelError(
                            f"embedding_http_{status}" if status else "embedding_connection_failed",
                            terminal=bool(scope),
                            scope=scope or "request",
                            category=category,
                            retries=attempt,
                            retry_wait_seconds=retry_wait,
                            request_sent=sent,
                            http_status=status,
                        ) from None
                    except (ValueError, TypeError) as exc:
                        raise ModelError("embedding_invalid_vectors", request_sent=sent) from exc
                    except asyncio.CancelledError:
                        outcome = "cancelled"
                        raise
                    except ModelError as exc:
                        exc.request_sent = sent
                        raise
                    finally:
                        _settle_attempt(
                            self.budget,
                            self.scheduler,
                            ticket,
                            usage,
                            estimate,
                            outcome=outcome,
                            sent=sent,
                            kind="embedding",
                        )

                try:
                    reply = await _scheduled_attempt(
                        request,
                        self.config,
                        self.scheduler,
                        self.budget,
                        estimate,
                        kind="embedding",
                        attempt=attempt,
                        retry_wait_seconds=retry_delay,
                    )
                    result.extend(reply.vectors)
                    break
                except ModelError as exc:
                    self.availability.record_failure(exc)
                    retryable_status = (
                        exc.http_status is None
                        or exc.http_status in {408, 409, 429}
                        or exc.http_status >= 500
                    )
                    if (
                        exc.terminal
                        or not retryable_status
                        or exc.category not in _RETRYABLE_CATEGORIES
                        or attempt == self.config.retries
                    ):
                        raise
                    retry_delay = 2**attempt
                    await asyncio.sleep(retry_delay)
                    retry_wait += retry_delay
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
        return (await self.rank_groups(texts, [queries]))[0]

    async def rank_groups(self, texts, query_groups):
        """Encode one candidate pool once, preserving each group's independent ranking."""
        queries = list(dict.fromkeys(q for group in query_groups for q in group))
        vectors = await self.vectors(list(texts) + queries)
        docs = vectors[: len(texts)]
        questions = dict(zip(queries, vectors[len(texts) :], strict=True))

        def cosine(a, b):
            scale = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
            return sum(x * y for x, y in zip(a, b)) / scale if scale else 0.0

        orders = []
        for group in query_groups:
            scores = [max(cosine(doc, questions[q]) for q in group) for doc in docs]
            orders.append(sorted(range(len(docs)), key=lambda i: scores[i], reverse=True))
        return orders
