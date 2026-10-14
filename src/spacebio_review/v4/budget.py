"""Single request ledger, including each provider retry and delegated request."""

import asyncio
import json
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

from spacebio_review.agents.context import TokenEstimator
from spacebio_review.models.provider import ModelError
from spacebio_review.models.scheduling import _task

from .records import write_json


class Budget:
    def __init__(self, records, config, reporting):
        self.config, self.reporting = config, reporting
        self.path = records.root / "budget.json"
        self.data = (
            json.loads(self.path.read_text("utf-8"))
            if self.path.exists()
            else {
                "requests": [],
                "elapsed_seconds": 0,
            }
        )
        self.started = monotonic()
        self.prior = self.data["elapsed_seconds"]
        if any("kind" not in r for r in self.data["requests"]):
            raise ValueError("legacy_budget_read_only_use_new_output")
        self.active = {}
        self.completion_tokens = {"input": 0, "output": 0}
        self._owner = ContextVar("research_budget_owner", default=None)
        self._formal = ContextVar("research_budget_formal", default=False)
        self._exploring = ContextVar("research_budget_exploring", default=False)
        self._closed_reason = None

    def remaining_seconds(self):
        return max(0, self.config.max_seconds - self.prior - (monotonic() - self.started))

    @property
    def remaining_calls(self):
        return max(
            0,
            self.config.max_model_calls - sum(r["kind"] == "model" for r in self.data["requests"]),
        )

    def _protected(self, field, *, exclude=None):
        base = (
            self.config.completion_reserve_calls
            if field == "calls"
            else self.completion_tokens[field]
        )
        return base + sum(v[field] for k, v in self.active.items() if k != exclude)

    def exploration_reason(self, *, calls=1, input_tokens=0, output_tokens=0):
        reason = self._closed_reason or self.stop_reason()
        if not reason and self.remaining_seconds() <= self.config.completion_reserve_seconds:
            reason = "completion_time_reserve"
        if not reason and self.remaining_calls < self._protected("calls") + calls:
            reason = "completion_call_reserve"
        for field, amount, cap in [
            ("input", input_tokens, self.config.max_input_tokens),
            ("output", output_tokens, self.config.max_output_tokens),
        ]:
            if (
                not reason
                and cap is not None
                and (
                    sum(r[field] for r in self.data["requests"]) + amount + self._protected(field)
                    > cap
                )
            ):
                reason = f"completion_{field}_reserve"
        if reason:
            self._closed_reason = reason
        return reason

    @contextmanager
    def exploring(self):
        token = self._exploring.set(True)
        try:
            yield
        finally:
            self._exploring.reset(token)

    @contextmanager
    def research_task(self, key, *, calls, input_tokens=0, output_tokens=0):
        if reason := self.exploration_reason(
            calls=calls + 1, input_tokens=input_tokens, output_tokens=output_tokens
        ):
            raise ModelError(reason, category="exploration")
        self.active[key] = {"calls": calls, "input": input_tokens, "output": output_tokens}
        owner, formal = self._owner.set(key), self._formal.set(False)
        try:
            with self.exploring():
                yield
        finally:
            self.active.pop(key, None)
            self._owner.reset(owner)
            self._formal.reset(formal)

    @contextmanager
    def formal_request(self):
        token = self._formal.set(True)
        try:
            yield
        finally:
            self._formal.reset(token)

    def refresh_completion(self, *, input_tokens, output_tokens):
        """Protect the current owner's remaining formal requests at their current size."""
        pending = self.active.get(self._owner.get())
        if pending is not None:
            pending["input"] = input_tokens * pending["calls"]
            pending["output"] = output_tokens * pending["calls"]

    async def run_exploration(self, operation):
        if not self._exploring.get() or self._formal.get():
            return await operation()
        if reason := self.exploration_reason():
            raise ModelError(reason, category="exploration")
        deadline = asyncio.timeout(
            max(0, self.remaining_seconds() - self.config.completion_reserve_seconds)
        )
        try:
            async with deadline:
                return await operation()
        except TimeoutError:
            if not deadline.expired():
                raise
            self._closed_reason = "completion_time_reserve"
            raise ModelError(self._closed_reason, category="exploration") from None

    def stop_reason(self):
        if self.remaining_seconds() <= 0:
            return "elapsed_time_budget_exhausted"
        if self.remaining_calls <= 0:
            return "model_call_budget_exhausted"
        for field, cap in [
            ("input", self.config.max_input_tokens),
            ("output", self.config.max_output_tokens),
        ]:
            if cap is not None and sum(r[field] for r in self.data["requests"]) >= cap:
                return f"{field}_token_budget_exhausted"
        return None

    def reserve(self, config, messages=None, tools=None, response_schema=None, texts=None):
        task = _task.get()
        writing = task.get("task_id", "").startswith("writer/")
        rows = self.data["requests"]
        if reason := self.stop_reason():
            raise ModelError(reason, category="budget")
        if writing and sum(r["writing"] for r in rows) >= self.reporting.max_requests:
            raise ModelError("report_request_budget_exhausted", category="budget")
        estimate = (
            TokenEstimator(None, getattr(config, "image_tokens", 0)).measure(
                messages, tools or [], response_schema=response_schema
            )["input_tokens"]
            if messages is not None
            else sum(len(t.encode("utf-8")) for t in texts or [])
        )
        output = getattr(config, "max_tokens", 0)
        kind = "embedding" if texts is not None else "model"
        if (
            kind == "embedding"
            and sum(r["kind"] == kind for r in rows) >= self.config.max_embedding_calls
        ):
            raise ModelError("embedding_call_budget_exhausted", category="budget")
        owner = self._owner.get()
        formal = self._formal.get() and owner in self.active
        if self._exploring.get() and not formal:
            if reason := self.exploration_reason(input_tokens=estimate, output_tokens=output):
                raise ModelError(reason, category="exploration")
        if formal:
            if kind != "model" or self.active[owner]["calls"] <= 0:
                raise ModelError("task_completion_budget_exhausted", category="budget")
            if self.remaining_calls <= self._protected("calls", exclude=owner):
                raise ModelError("completion_call_reserve", category="budget")
        for field, reserved, cap in [
            ("input", estimate, self.config.max_input_tokens),
            ("output", output, self.config.max_output_tokens),
        ]:
            if cap is not None and sum(r[field] for r in rows) + reserved > cap:
                raise ModelError(f"{field}_token_budget_exhausted", category="budget")
            if (
                formal
                and cap is not None
                and (
                    sum(r[field] for r in rows) + reserved + self._protected(field, exclude=owner)
                    > cap
                )
            ):
                raise ModelError(f"completion_{field}_reserve", category="budget")
        if formal:
            for field, used in [("calls", 1), ("input", estimate), ("output", output)]:
                self.active[owner][field] = max(0, self.active[owner][field] - used)
        row = {
            "kind": kind,
            "task": task,
            "model": config.model,
            "writing": writing,
            "input": estimate,
            "output": output,
            "usage": "reserved_or_unavailable",
        }
        rows.append(row)
        self.save()  # synchronous reserve precedes any await, so concurrent tasks cannot overspend
        return len(rows) - 1

    def settle(self, ticket, usage):
        row = self.data["requests"][ticket]
        if usage:
            row.update(
                input=usage.prompt_tokens if usage.prompt_tokens is not None else row["input"],
                output=getattr(usage, "completion_tokens", None)
                if getattr(usage, "completion_tokens", None) is not None
                else row["output"],
                usage="provider",
            )
        self.save()

    def save(self):
        self.data["elapsed_seconds"] = self.prior + monotonic() - self.started
        write_json(self.path, self.data)
