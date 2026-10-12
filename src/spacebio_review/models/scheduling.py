"""Request-scoped limits and small, run-local timing records (no conversations)."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

from .config import ConcurrencyConfig

_task = ContextVar("model_task", default={})


class ResearchCalls:
    """Logical research opportunities, not a second provider/token billing ledger."""

    def __init__(self, limit, root):
        self.limit, self.remaining = limit, limit
        self.root = root
        self.reserves = {}

    def exploration_reason(self):
        return (
            "shared_research_completion_reserve"
            if self.remaining <= sum(self.reserves.values())
            else None
        )

    def claim(self, owner, formal=False):
        from .provider import ModelError

        if formal and self.reserves.get(owner, 0):
            self.reserves[owner] -= 1
        elif reason := self.exploration_reason():
            raise ModelError(reason, category="exploration")
        self.remaining -= 1


class RequestScheduler:
    def __init__(self, config: ConcurrencyConfig | None = None, *, clock=monotonic):
        config = config or ConcurrencyConfig()
        self.clock = clock
        self.started = clock()
        self.models = asyncio.Semaphore(config.model_requests)
        self.tools = asyncio.Semaphore(config.tools)
        self.per_model = {key: asyncio.Semaphore(n) for key, n in config.per_model.items()}
        if any(n <= 0 for n in config.per_model.values()):
            raise ValueError("Per-model concurrency must be positive")
        self.records: list[dict] = []
        self.stages: list[dict] = []
        self.phases: list[dict] = []
        self.stopped: dict = {}
        self.request_guard = None
        self.research_pool = ContextVar("research_call_pool", default=None)
        self.research_owner = ContextVar("research_call_owner", default=None)
        self.research_formal = ContextVar("research_call_formal", default=False)

    @contextmanager
    def research_calls(self, key, limit, reserve):
        from .provider import ModelError

        inherited = self.research_pool.get()
        pool = inherited or ResearchCalls(limit, key)
        if key in pool.reserves and self.research_owner.get() == key:
            yield pool
            return
        reserve = min(reserve, limit)
        if pool.remaining < sum(pool.reserves.values()) + reserve + bool(inherited):
            raise ModelError("shared_research_completion_reserve", category="exploration")
        pool.reserves[key] = reserve
        token = self.research_pool.set(pool)
        owner = self.research_owner.set(key)
        try:
            yield pool
        finally:
            pool.reserves.pop(key, None)
            self.research_owner.reset(owner)
            self.research_pool.reset(token)

    @contextmanager
    def formal_call(self):
        token = self.research_formal.set(True)
        try:
            yield
        finally:
            self.research_formal.reset(token)

    @contextmanager
    def stage(self, name):
        start = self.clock()
        try:
            yield
        finally:
            self.phases.append({"phase": name, "wall_seconds": self.clock() - start})

    @contextmanager
    def task(self, task_id: str, role: str, phase: str):
        token = _task.set({"task_id": task_id, "role": role, "phase": phase})
        start = self.clock()
        try:
            yield
        finally:
            self.stages.append(_task.get() | {"wall_seconds": self.clock() - start})
            _task.reset(token)

    async def call(self, operation, *, kind="model", name="", model_id=""):
        """Also usable for embeddings/reranking; permits cover only this operation."""
        queued = self.clock()
        # Acquire the narrow limit first: requests for one busy model must not fill
        # every global slot while merely waiting for that same model's limit.
        narrow = self.per_model.get(model_id) if kind not in {"tool", "delegation"} else None
        if narrow:
            await narrow.acquire()
        gate = None if kind == "delegation" else self.tools if kind == "tool" else self.models
        acquired = False
        try:
            if gate:
                await gate.acquire()
                acquired = True
            start = self.clock()
            row = _task.get() | {
                "kind": kind,
                "name": name,
                "model": model_id or None,
                "queue_seconds": start - queued,
                "prompt_tokens": None,
                "completion_tokens": None,
                "retries": 0,
                "retry_wait_seconds": 0.0,
            }
            if pool := self.research_pool.get():
                row["research_root"] = pool.root
                row["research_turns_remaining"] = pool.remaining
            try:
                if self.request_guard and kind not in {"tool", "delegation"}:
                    self.request_guard(kind, _task.get())
                result = await operation()
                for key in ("prompt_tokens", "completion_tokens", "retries", "retry_wait_seconds"):
                    row[key] = getattr(result, key, row[key])
                row["status"] = "completed"
                return result
            except BaseException as exc:
                row["status"] = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
                if str(exc).startswith("model_service_stopped"):
                    row["status"] = "skipped"
                row["retries"] = getattr(exc, "retries", 0)
                row["retry_wait_seconds"] = getattr(exc, "retry_wait_seconds", 0.0)
                row["error_category"] = getattr(exc, "category", "unknown")
                row["error_scope"] = getattr(exc, "scope", "request")
                row["service_unavailable"] = getattr(exc, "terminal", False)
                raise
            finally:
                row["response_seconds"] = self.clock() - start
                self.records.append(row)
        finally:
            if acquired:
                gate.release()
            if narrow:
                narrow.release()

    def metrics(self):
        groups = {}
        for row in self.records:
            key = (
                row.get("phase", "unscoped"),
                row.get("role", "unscoped"),
                row["kind"],
                row["model"],
            )
            group = groups.setdefault(
                key,
                {
                    "phase": key[0],
                    "role": key[1],
                    "kind": key[2],
                    "model": key[3],
                    "requests": 0,
                    "skipped": 0,
                    "failed": 0,
                    "queue_seconds": 0.0,
                    "response_seconds": 0.0,
                    "retries": 0,
                    "retry_wait_seconds": 0.0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "usage_unavailable": 0,
                },
            )
            group["requests"] += row["status"] != "skipped"
            group["skipped"] += row["status"] == "skipped"
            group["failed"] += row["status"] in {"failed", "cancelled"}
            group["usage_unavailable"] += (
                row["status"] != "skipped" and row["prompt_tokens"] is None
            )
            for field in (
                "queue_seconds",
                "response_seconds",
                "retries",
                "retry_wait_seconds",
                "prompt_tokens",
                "completion_tokens",
            ):
                group[field] += row.get(field) or 0
        return {
            "wall_seconds": self.clock() - self.started,
            "stages": self.phases,
            "groups": list(groups.values()),
            "model_response_seconds_sum": sum(
                r["response_seconds"] for r in self.records if r["kind"] in {"model", "embedding"}
            ),
            "tool_seconds_sum": sum(
                r["response_seconds"] for r in self.records if r["kind"] == "tool"
            ),
            "queue_seconds_sum": sum(r["queue_seconds"] for r in self.records),
            "retries": sum(r["retries"] for r in self.records),
            "retry_wait_seconds_sum": sum(r["retry_wait_seconds"] for r in self.records),
            "interpretation": "Response duration includes network, provider work and retries; "
            "parallel cumulative durations are not wall time or pure inference. "
            "Delegation overlaps child operations; do not add them together.",
        }


class ScheduledModel:
    def __init__(self, model, scheduler: RequestScheduler, alias: str):
        self.model, self.scheduler, self.alias = model, scheduler, alias
        self.config = model.config

    @property
    def available(self):
        return getattr(self.model, "available", True)

    async def complete(self, messages, tools=None, *, response_schema=None):
        pool = self.scheduler.research_pool.get()
        if pool:
            pool.claim(self.scheduler.research_owner.get(), self.scheduler.research_formal.get())

        async def request():
            if response_schema is None:
                return await self.model.complete(messages, tools)
            return await self.model.complete(messages, tools, response_schema=response_schema)

        return await self.scheduler.call(request, name=self.alias, model_id=self.config.model)
