"""One durable request ledger with explicit session ownership and completion reserves.

Provider adapters reserve once per actual attempt, including retries. Auxiliary
requests use the same ledger; scientific turn limits belong to the session runtime.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from spacebio_review.agents.context import TokenEstimator
from spacebio_review.models.provider import ModelError
from spacebio_review.persistence import write_json

Purpose = Literal["research", "completion", "auxiliary"]


def exhausted(reason):
    return ModelError(reason, category="budget", request_sent=False)


class BudgetLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_seconds: float = Field(default=14_400, gt=0)
    max_model_calls: int = Field(default=600, ge=0)
    max_embedding_calls: int = Field(default=200, ge=0)
    max_input_tokens: int | None = Field(default=None, ge=0)
    max_output_tokens: int | None = Field(default=None, ge=0)


class SessionAllowance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_input_tokens: int | None = Field(default=None, ge=0)
    max_output_tokens: int | None = Field(default=None, ge=0)
    completion_input_tokens: int = Field(default=0, ge=0)
    completion_output_tokens: int = Field(default=0, ge=0)
    completion_calls: int = Field(default=1, ge=0)
    completion_seconds: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def reserve_fits(self):
        for field in ("input", "output"):
            limit = getattr(self, f"max_{field}_tokens")
            if limit is not None and getattr(self, f"completion_{field}_tokens") > limit:
                raise ValueError(f"completion_{field}_reserve_exceeds_session_allowance")
        return self


class Budget:
    def __init__(self, root, limits: BudgetLimits | None = None, *, clock=monotonic):
        self.path = Path(root) / "v5" / "budget.json"
        self.clock, self.started = clock, clock()
        self._owner = ContextVar("v5_budget_owner", default=None)
        self._purpose = ContextVar("v5_budget_purpose", default="research")
        if self.path.exists():
            self.data = json.loads(self.path.read_text("utf-8"))
            if self.data.get("engine_version") != "v5":
                raise ValueError("unsupported_budget_version")
            self.limits = BudgetLimits.model_validate(self.data["limits"])
            if limits is not None and limits != self.limits:
                raise ValueError("budget_limits_changed_on_resume")
        else:
            self.limits = limits or BudgetLimits()
            self.data = {
                "engine_version": "v5",
                "limits": self.limits.model_dump(),
                "elapsed_seconds": 0,
                "sessions": {},
                "requests": [],
            }
        self.prior = self.data["elapsed_seconds"]

    def register(self, session_id: str, allowance: SessionAllowance | None = None):
        """Register once; reopening an existing identity never replenishes its allowance."""
        if session_id in self.data["sessions"]:
            return
        if not session_id:
            raise ValueError("empty_budget_session")
        allowance = allowance or SessionAllowance()
        self.data["sessions"][session_id] = {
            "allowance": allowance.model_dump(),
            "active": True,
            "reserve_start": len(self.data["requests"]),
            "extensions": [],
        }
        try:
            self._check_reserves()
        except ModelError:
            del self.data["sessions"][session_id]
            raise
        self.save()

    def activate(self, session_id: str):
        """Protect another completion on continuation; consumed tokens remain consumed."""
        session = self.data["sessions"][session_id]
        if session["active"]:
            return
        previous = session["reserve_start"]
        session.update(active=True, reserve_start=len(self.data["requests"]))
        try:
            self._check_reserves()
        except ModelError:
            session.update(active=False, reserve_start=previous)
            raise
        self.save()

    def finish(self, session_id: str):
        self.data["sessions"][session_id]["active"] = False
        self.save()

    def extend(self, session_id: str, *, input_tokens=0, output_tokens=0):
        """Explicitly add token capacity, bounded by the remaining global capacity."""
        session = self.data["sessions"][session_id]
        updated = dict(session["allowance"])
        for field, amount in (("input", input_tokens), ("output", output_tokens)):
            if not isinstance(amount, int) or amount < 0:
                raise ValueError("invalid_budget_extension")
            key = f"max_{field}_tokens"
            cap = getattr(self.limits, key)
            if cap is not None and amount > cap - self.usage()[f"{field}_tokens"]:
                raise exhausted(f"global_{field}_extension_unavailable")
            if updated[key] is not None:
                updated[key] += amount
            elif amount:
                raise ValueError("unlimited_session_allowance_cannot_be_extended")
        session["allowance"] = SessionAllowance.model_validate(updated).model_dump()
        session["extensions"].append(
            {
                "after_request": len(self.data["requests"]),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
        )
        self.save()

    @contextmanager
    def scope(self, session_id: str, *, purpose: Purpose = "research"):
        if session_id not in self.data["sessions"]:
            raise ValueError("unregistered_budget_session")
        if purpose not in {"research", "completion", "auxiliary"}:
            raise ValueError("unknown_budget_purpose")
        if not self.data["sessions"][session_id]["active"]:
            raise ValueError("inactive_budget_session")
        owner, intent = self._owner.set(session_id), self._purpose.set(purpose)
        try:
            yield
        finally:
            self._purpose.reset(intent)
            self._owner.reset(owner)

    def usage(self, session_id: str | None = None):
        rows = self.data["requests"]
        if session_id is not None:
            rows = [row for row in rows if row["session_id"] == session_id]
        return {
            "model_calls": sum(row["kind"] == "model" for row in rows),
            "embedding_calls": sum(row["kind"] == "embedding" for row in rows),
            "input_tokens": sum(row["input"] for row in rows),
            "output_tokens": sum(row["output"] for row in rows),
            "estimated_requests": sum(row["usage"] != "provider" for row in rows),
        }

    def _reserve_left(self, session_id, field):
        session = self.data["sessions"][session_id]
        if not session["active"]:
            return 0
        allowance = session["allowance"]
        if field == "seconds":
            return allowance["completion_seconds"]
        key = "completion_calls" if field == "calls" else f"completion_{field}_tokens"
        spent = sum(
            int(row["kind"] == "model") if field == "calls" else row[field]
            for row in self.data["requests"][session["reserve_start"] :]
            if row["session_id"] == session_id and row["purpose"] == "completion"
        )
        return max(0, allowance[key] - spent)

    def _protected(self, field, *, exclude=None):
        return sum(
            self._reserve_left(session_id, field)
            for session_id in self.data["sessions"]
            if session_id != exclude
        )

    def _check_reserves(self):
        used = self.usage()
        for field, cap, consumed in (
            ("calls", self.limits.max_model_calls, used["model_calls"]),
            ("input", self.limits.max_input_tokens, used["input_tokens"]),
            ("output", self.limits.max_output_tokens, used["output_tokens"]),
        ):
            if cap is not None and consumed + self._protected(field) > cap:
                raise exhausted(f"global_completion_{field}_reserve_unavailable")
        if self.remaining_seconds() < self._protected("seconds"):
            raise exhausted("global_completion_time_reserve_unavailable")
        for session_id, session in self.data["sessions"].items():
            consumed = self.usage(session_id)
            for field in ("input", "output"):
                cap = session["allowance"][f"max_{field}_tokens"]
                if (
                    cap is not None
                    and consumed[f"{field}_tokens"] + self._reserve_left(session_id, field) > cap
                ):
                    raise exhausted(f"session_completion_{field}_reserve_unavailable")

    def remaining_seconds(self, *, session_id=None, purpose: Purpose | None = None):
        remaining = self.limits.max_seconds - self.prior - (self.clock() - self.started)
        if session_id is not None:
            remaining -= self._protected(
                "seconds", exclude=session_id if purpose == "completion" else None
            )
        return max(0.0, remaining)

    @property
    def remaining_calls(self):
        return max(0, self.limits.max_model_calls - self.usage()["model_calls"])

    def reserve(self, config, messages=None, tools=None, response_schema=None, texts=None):
        """Synchronous check and append precedes provider awaits, including every retry."""
        owner, purpose = self._owner.get(), self._purpose.get()
        if owner is None:
            raise ValueError("model_request_without_budget_owner")
        # Embeddings remain auxiliary even when a closing turn requests a final lookup.
        kind = "embedding" if texts is not None else "model"
        if kind == "embedding":
            purpose = "auxiliary"
        if self.remaining_seconds(session_id=owner, purpose=purpose) <= 0:
            raise exhausted("elapsed_time_budget_exhausted")
        estimate = (
            TokenEstimator(None, getattr(config, "image_tokens", 0)).measure(
                messages, tools or [], response_schema=response_schema
            )["input_tokens"]
            if messages is not None
            else sum(len(text.encode("utf-8")) for text in texts or [])
        )
        output = getattr(config, "max_tokens", 0) if kind == "model" else 0
        used, own = self.usage(), self.usage(owner)
        exclude = owner if purpose == "completion" else None
        protected_calls = self._protected("calls", exclude=exclude) if kind == "model" else 0
        call_cap = getattr(self.limits, f"max_{kind}_calls")
        if used[f"{kind}_calls"] + 1 + protected_calls > call_cap:
            raise exhausted(f"{kind}_call_budget_exhausted")
        session = self.data["sessions"][owner]
        for field, amount in (("input", estimate), ("output", output)):
            key = f"max_{field}_tokens"
            cap = getattr(self.limits, key)
            if cap is not None and (
                used[f"{field}_tokens"] + amount + self._protected(field, exclude=exclude) > cap
            ):
                raise exhausted(f"global_{field}_token_budget_exhausted")
            own_reserve = self._reserve_left(owner, field) if purpose != "completion" else 0
            cap = session["allowance"][key]
            if cap is not None and own[f"{field}_tokens"] + amount + own_reserve > cap:
                raise exhausted(f"session_{field}_token_budget_exhausted")
        row = {
            "session_id": owner,
            "purpose": purpose,
            "kind": kind,
            "model": config.model,
            "input": estimate,
            "output": output,
            "estimated_input": estimate,
            "estimated_output": output,
            "usage": "estimated_pending_or_unavailable",
            "started_seconds": self.prior + self.clock() - self.started,
        }
        self.data["requests"].append(row)
        self.save()
        return len(self.data["requests"]) - 1

    def settle(self, ticket, usage):
        row = self.data["requests"][ticket]
        row["finished_seconds"] = self.prior + self.clock() - self.started
        actual_input = getattr(usage, "prompt_tokens", None)
        actual_output = (
            0 if row["kind"] == "embedding" else getattr(usage, "completion_tokens", None)
        )
        if actual_input is not None:
            row["input"] = actual_input
        if actual_output is not None:
            row["output"] = actual_output
        row["usage"] = (
            "provider"
            if actual_input is not None and actual_output is not None
            else "partially_estimated"
            if actual_input is not None or actual_output is not None
            else "estimated_unavailable"
        )
        row["estimated_fields"] = [
            field
            for field, value in (("input", actual_input), ("output", actual_output))
            if value is None
        ]
        self.save()

    def save(self):
        self.data["elapsed_seconds"] = self.prior + self.clock() - self.started
        write_json(self.path, self.data)
