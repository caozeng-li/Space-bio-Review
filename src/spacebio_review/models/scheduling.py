"""Request-scoped limits and small, run-local timing records (no conversations)."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

from .config import ConcurrencyConfig

_task = ContextVar("model_task", default={})
_alias = ContextVar("model_alias", default="")


class _TokenPacer:
    """A FIFO head waits for its turn, then advances from actual admission time."""

    def __init__(self):
        self.turn = asyncio.Lock()
        self.next_start = 0.0


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
    def __init__(
        self, config: ConcurrencyConfig | None = None, *, clock=monotonic, sleep=asyncio.sleep
    ):
        config = config or ConcurrencyConfig()
        self.clock = clock
        self.sleep = sleep
        self.started = clock()
        self.models = asyncio.Semaphore(config.model_requests)
        self.tools = asyncio.Semaphore(config.tools)
        self.per_model = {key: asyncio.Semaphore(n) for key, n in config.per_model.items()}
        self.quotas = config.quotas
        self._pacers = {key: _TokenPacer() for key in self.quotas}
        self._request = ContextVar("scheduled_request", default=None)
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

    @contextmanager
    def alias(self, name):
        token = _alias.set(name)
        try:
            yield
        finally:
            _alias.reset(token)

    def update_request(self, **fields):
        """Provider settlement annotates the current attempt, without another ledger."""
        if (row := self._request.get()) is not None:
            row.update(fields)

    async def call(
        self, operation, *, kind="model", name="", model_id="", quota_group=None,
        estimated_tokens=None, deadline=None, attempt=0, retry_wait_seconds=0.0,
    ):
        """Admit one provider attempt; rate waits never hold concurrency permits."""
        from .provider import ModelError

        model_request = kind not in {"tool", "delegation"}
        pacer = self._pacers[quota_group] if quota_group is not None else None
        if pacer and (estimated_tokens is None or estimated_tokens < 0):
            raise ValueError("token_quota_requires_request_estimate")
        queued = self.clock()
        row = _task.get() | {
            "kind": kind, "name": name or _alias.get(), "model": model_id or None,
            "quota_group": quota_group, "estimated_tokens": estimated_tokens,
            "attempt": attempt, "retries": int(attempt > 0),
            "retry_wait_seconds": retry_wait_seconds,
            "queue_seconds": 0.0, "rate_wait_seconds": 0.0,
            "concurrency_wait_seconds": 0.0, "response_seconds": 0.0,
            "actual_input_tokens": None, "actual_output_tokens": None,
            "budget_input_tokens": 0, "budget_output_tokens": 0,
            "usage_status": "not_sent" if model_request else "not_applicable",
            "request_sent": False,
        }
        if pool := self.research_pool.get():
            row["research_root"] = pool.root
            row["research_turns_remaining"] = pool.remaining
        narrow = self.per_model.get(model_id) if model_request else None
        gate = None if kind == "delegation" else self.tools if kind == "tool" else self.models
        held_turn = held_narrow = held_gate = False
        start = None
        token = self._request.set(row)

        def time_left():
            remaining = None if deadline is None else deadline - self.clock()
            if remaining is not None and remaining <= 0:
                raise ModelError(
                    "elapsed_time_budget_exhausted", category="budget", request_sent=False,
                )
            return remaining

        try:
            try:
                async with asyncio.timeout(time_left()):
                    if pacer:
                        wait_start = self.clock()
                        try:
                            if deadline is not None and pacer.next_start >= deadline:
                                raise ModelError(
                                    "token_quota_wait_exceeds_time_budget",
                                    category="budget", request_sent=False,
                                )
                            await pacer.turn.acquire()
                            held_turn = True
                            while (delay := pacer.next_start - self.clock()) > 0:
                                remaining = time_left()
                                if remaining is not None and delay >= remaining:
                                    raise ModelError(
                                        "token_quota_wait_exceeds_time_budget",
                                        category="budget", request_sent=False,
                                    )
                                await self.sleep(delay)
                        finally:
                            row["rate_wait_seconds"] = self.clock() - wait_start
                    wait_start = self.clock()
                    try:
                        # Narrow first: one busy model cannot occupy every global slot.
                        if narrow:
                            await narrow.acquire()
                            held_narrow = True
                        if gate:
                            await gate.acquire()
                            held_gate = True
                    finally:
                        row["concurrency_wait_seconds"] = self.clock() - wait_start
                    time_left()
                    start = self.clock()
                    row["admitted_seconds"] = start - self.started
                    if pacer:
                        quota = self.quotas[quota_group]
                        interval = 60 * estimated_tokens / quota.tokens_per_minute
                        pacer.next_start = start + interval
                        pacer.turn.release()
                        held_turn = False
            except TimeoutError as exc:
                raise ModelError(
                    "elapsed_time_budget_exhausted", category="budget", request_sent=False,
                ) from exc
            if self.request_guard and model_request:
                self.request_guard(kind, _task.get())
            result = await operation()
            row["status"] = "completed"
            return result
        except BaseException as exc:
            row["status"] = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            if str(exc).startswith("model_service_stopped"):
                row["status"] = "skipped"
            if getattr(exc, "request_sent", None) is not None:
                row["request_sent"] = exc.request_sent
            if getattr(exc, "category", None) == "budget" and not row["request_sent"]:
                row["status"] = "budget_blocked"
            row["error_category"] = getattr(exc, "category", "unknown")
            row["error_scope"] = getattr(exc, "scope", "request")
            row["service_unavailable"] = getattr(exc, "terminal", False)
            row["http_status"] = getattr(exc, "http_status", None)
            raise
        finally:
            now = self.clock()
            row["queue_seconds"] = (start if start is not None else now) - queued
            row["response_seconds"] = now - start if start is not None else 0.0
            if row["request_sent"] and row["usage_status"] == "not_sent":
                row["usage_status"] = "estimated_unavailable"
            self.records.append(row)
            self._request.reset(token)
            if held_gate:
                gate.release()
            if held_narrow:
                narrow.release()
            if held_turn:
                pacer.turn.release()

    def metrics(self):
        groups = {}
        for row in self.records:
            key = (
                row.get("phase", "unscoped"), row.get("role", "unscoped"),
                row["kind"], row["model"],
            )
            group = groups.setdefault(key, {
                "phase": key[0], "role": key[1], "kind": key[2], "model": key[3],
                "requests": 0, "skipped": 0, "budget_blocked": 0, "failed": 0,
                "queue_seconds": 0.0, "rate_wait_seconds": 0.0,
                "concurrency_wait_seconds": 0.0, "response_seconds": 0.0,
                "retries": 0, "retry_wait_seconds": 0.0,
                "known_input_tokens": 0, "known_output_tokens": 0,
                "unknown_input_requests": 0, "unknown_output_requests": 0,
                "budget_input_tokens": 0, "budget_output_tokens": 0,
                "unknown_usage_budget_input_tokens": 0,
                "unknown_usage_budget_output_tokens": 0, "usage_unavailable": 0,
            })
            model_request = row["kind"] not in {"tool", "delegation"}
            requested = row["request_sent"] if model_request else "admitted_seconds" in row
            group["requests"] += bool(requested)
            group["skipped"] += row["status"] == "skipped"
            group["budget_blocked"] += row["status"] == "budget_blocked"
            group["failed"] += row["status"] in {"failed", "cancelled"}
            group["retries"] += row["retries"] if requested else 0
            for field in (
                "queue_seconds", "rate_wait_seconds", "concurrency_wait_seconds",
                "response_seconds", "retry_wait_seconds", "budget_input_tokens",
                "budget_output_tokens",
            ):
                group[field] += row.get(field) or 0
            if model_request and requested:
                unknown = False
                for field in ("input", "output"):
                    actual = row[f"actual_{field}_tokens"]
                    if actual is None:
                        unknown = True
                        group[f"unknown_{field}_requests"] += 1
                        group[f"unknown_usage_budget_{field}_tokens"] += (
                            row[f"budget_{field}_tokens"]
                        )
                    else:
                        group[f"known_{field}_tokens"] += actual
                group["usage_unavailable"] += unknown
        return {
            "wall_seconds": self.clock() - self.started,
            "stages": self.phases,
            "groups": list(groups.values()),
            "model_response_seconds_sum": sum(
                r["response_seconds"] for r in self.records
                if r["kind"] in {"model", "embedding"} and r["request_sent"]
            ),
            "tool_seconds_sum": sum(
                r["response_seconds"] for r in self.records if r["kind"] == "tool"
            ),
            "queue_seconds_sum": sum(r["queue_seconds"] for r in self.records),
            "rate_wait_seconds_sum": sum(r["rate_wait_seconds"] for r in self.records),
            "concurrency_wait_seconds_sum": sum(
                r["concurrency_wait_seconds"] for r in self.records
            ),
            "retries": sum(group["retries"] for group in groups.values()),
            "retry_wait_seconds_sum": sum(r["retry_wait_seconds"] for r in self.records),
            "interpretation": "Each model/embedding row is one provider attempt. Known usage "
            "is a subtotal; unknown fields retain separate budget estimates. Rate and concurrency "
            "waits precede the operation; response duration includes network and provider work. "
            "Parallel cumulative durations are not wall time or pure inference. "
            "Delegation overlaps child operations; do not add them together.",
        }


class ScheduledModel:
    def __init__(self, model, scheduler: RequestScheduler, alias: str):
        self.model, self.scheduler, self.alias = model, scheduler, alias
        self.config = model.config

    @property
    def available(self):
        return getattr(self.model, "available", True)

    async def complete(self, messages, tools=None, *, response_schema=None, tool_choice=None):
        pool = self.scheduler.research_pool.get()
        if pool:
            pool.claim(self.scheduler.research_owner.get(), self.scheduler.research_formal.get())

        with self.scheduler.alias(self.alias):
            request = {}
            if response_schema is not None:
                request["response_schema"] = response_schema
            if tool_choice is not None:
                request["tool_choice"] = tool_choice
            return await self.model.complete(messages, tools, **request)
