"""One request ledger per run with explicit session ownership and completion reserves.

Provider adapters reserve once per actual attempt, including retries. Auxiliary
requests use the same ledger; scientific turn limits belong to the session runtime.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
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


class CompletionReserve(BaseModel):
    """Estimated requests still needed at the current delivery stage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    calls: int = Field(default=0, ge=0)
    seconds: float = Field(default=0, ge=0)
    steps: list[str] = Field(default_factory=list)


class Budget:
    def __init__(
        self, root, limits: BudgetLimits | None = None, *, clock=monotonic, token_counters=None
    ):
        self.path = Path(root) / "v5" / "budget.json"
        self.clock, self.started = clock, clock()
        self._owner = ContextVar("v5_budget_owner", default=None)
        self._purpose = ContextVar("v5_budget_purpose", default="research")
        self._estimators = {}
        self._token_counters = token_counters or {}
        self.limits = limits or BudgetLimits()
        self.data = {
            "limits": self.limits.model_dump(),
            "elapsed_seconds": 0,
            "sessions": {},
            "requests": [],
            "calibration": {},
        }

    def register(self, session_id: str, allowance: SessionAllowance | None = None):
        """Register each owner once during this run."""
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
        """Begin the next delivery cycle of an inactive owner in this run."""
        session = self.data["sessions"][session_id]
        if session["active"]:
            return
        previous = dict(session)
        session.update(active=True, reserve_start=len(self.data["requests"]))
        session.pop("completion_plan", None)
        try:
            self._check_reserves()
        except ModelError:
            session.clear()
            session.update(previous)
            raise
        self.save()

    def finish(self, session_id: str):
        """Release protection after this owner's current delivery cycle."""
        self.data["sessions"][session_id]["active"] = False
        self.save()

    def update_completion(self, session_id: str, reserve: CompletionReserve) -> str | None:
        """Replace the estimate of remaining work, never the allowance or consumed usage.

        A nonempty plan cannot lower the fixed reserve for the current cycle.
        An empty plan releases protection because no delivery work remains.
        An unavailable plan is retained to stop further research; the caller can
        still attempt delivery with what remains rather than losing its draft.
        """
        session = self.data["sessions"][session_id]
        session["completion_plan"] = {
            **reserve.model_dump(),
            "after_request": len(self.data["requests"]),
        }
        reason = None
        try:
            self._check_reserves()
        except ModelError as exc:
            reason = str(exc)
        self.save()
        return reason

    def research_capacity(self, session_id: str, costs) -> str | None:
        """Price a nested reading's next requests while preserving its parent's delivery.

        A reading owns no separate allowance or completion reserve. Its requests
        remain ordinary research charged to the already registered parent.
        """
        used, own = self.usage(), self.usage(session_id)
        session = self.data["sessions"][session_id]
        for field, amount, cap, consumed in (
            ("calls", len(costs), self.limits.max_model_calls, self._charged_calls("model")),
            (
                "input",
                sum(row["input_tokens"] for row in costs),
                self.limits.max_input_tokens,
                used["input_tokens"],
            ),
            (
                "output",
                sum(row["output_reserve"] for row in costs),
                self.limits.max_output_tokens,
                used["output_tokens"],
            ),
        ):
            if cap is not None and consumed + amount + self._protected(field) > cap:
                return f"reading_{field}_capacity_reached"
            if field != "calls":
                own_cap = session["allowance"][f"max_{field}_tokens"]
                if (
                    own_cap is not None
                    and own[f"{field}_tokens"] + amount + self._reserve_left(session_id, field)
                    > own_cap
                ):
                    return f"reading_session_{field}_capacity_reached"
        return None

    def measure(
        self, config, messages=None, tools=(), *, response_schema=None, output_reserve=None,
        texts=None,
    ):
        """Use one route calibration for context capacity and provider reservation."""
        vision = any(
            isinstance(message.get("content"), list)
            and any(part.get("type") == "image_url" for part in message["content"])
            for message in messages or []
        )
        # Actual generation configuration separates parser, rerank and science
        # routes. Adding/removing tool schemas within a science session does not.
        fields = (
            "base_url",
            "model",
            "tools",
            "structured_output",
            "vision",
            "max_tokens",
            "image_tokens",
            "image_patch_size",
            "parameters",
            "extra_body",
        )
        route = {field: getattr(config, field, None) for field in fields}
        route["input_form"] = "embedding" if texts is not None else "vision" if vision else "text"
        route_key = sha256(
            json.dumps(route, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:24]
        if route_key not in self._estimators:
            estimator = TokenEstimator(
                self._token_counters.get(config.model),
                getattr(config, "image_tokens", 0),
                image_patch_size=getattr(config, "image_patch_size", None),
                allow_downward=True,
            )
            calibration = self.data["calibration"].setdefault(
                route_key,
                {
                    "model": config.model,
                    "input_form": route["input_form"],
                    "scale": 1.0,
                    "observations": 0,
                },
            )
            estimator.scale = 1.0 if estimator.counter else calibration["scale"]
            self._estimators[route_key] = estimator
        estimator = self._estimators[route_key]
        if texts is not None:
            measured = estimator.measure_texts(texts)
        else:
            reserve = getattr(config, "max_tokens", 0) if output_reserve is None else output_reserve
            measured = estimator.measure(
                messages, tools or [], output_reserve=reserve, response_schema=response_schema
            )
        return measured | {"route_key": route_key}

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
        if purpose == "completion" and not self.data["sessions"][session_id]["active"]:
            raise ValueError("inactive_budget_session")
        owner, intent = self._owner.set(session_id), self._purpose.set(purpose)
        try:
            yield
        finally:
            self._purpose.reset(intent)
            self._owner.reset(owner)

    def usage(self, session_id: str | None = None):
        """Budget occupancy and known provider subtotals have distinct meanings."""
        rows = self.data["requests"]
        if session_id is not None:
            rows = [row for row in rows if row["session_id"] == session_id]
        sent = [row for row in rows if row["request_sent"]]
        unknown = [row for row in sent if row["usage_status"] != "provider"]
        return {
            "model_calls": sum(row["kind"] == "model" for row in sent),
            "embedding_calls": sum(row["kind"] == "embedding" for row in sent),
            "input_tokens": sum(row["budget_input_tokens"] for row in rows),
            "output_tokens": sum(row["budget_output_tokens"] for row in rows),
            "token_basis": "budget_occupancy",
            "known_input_tokens": sum(row["actual_input_tokens"] or 0 for row in sent),
            "known_output_tokens": sum(row["actual_output_tokens"] or 0 for row in sent),
            "estimated_requests": len(unknown),
            "unknown_input_requests": sum(row["actual_input_tokens"] is None for row in sent),
            "unknown_output_requests": sum(row["actual_output_tokens"] is None for row in sent),
            "estimated_input_tokens": sum(
                row["budget_input_tokens"] for row in unknown if row["actual_input_tokens"] is None
            ),
            "estimated_output_tokens": sum(
                row["budget_output_tokens"]
                for row in unknown if row["actual_output_tokens"] is None
            ),
            "pending_requests": sum(row["outcome"] == "pending" for row in rows),
            "not_sent_requests": sum(row["usage_status"] == "not_sent" for row in rows),
        }

    def _charged_calls(self, kind):
        # Pending reservations protect capacity, but are not reported as sent calls.
        return sum(
            row["kind"] == kind and (row["request_sent"] or row["outcome"] == "pending")
            for row in self.data["requests"]
        )

    def _reserve_left(self, session_id, field):
        session = self.data["sessions"][session_id]
        if not session["active"]:
            return 0
        plan = session.get("completion_plan")
        if plan is not None and not any(
            plan[key] for key in ("input_tokens", "output_tokens", "calls", "seconds")
        ):
            return 0

        def remaining(amount, start):
            rows = [
                row
                for row in self.data["requests"][start:]
                if row["session_id"] == session_id and row["purpose"] == "completion"
            ]
            if field == "seconds":
                # Count wall time once if requests of this owner overlap.
                intervals = sorted(
                    (row["started_seconds"], row["finished_seconds"])
                    for row in rows
                    if "finished_seconds" in row
                )
                spent, end = 0.0, 0.0
                for begin, finish in intervals:
                    spent += max(0.0, finish - max(begin, end))
                    end = max(end, finish)
            else:
                spent = sum(
                    int(row["kind"] == "model" and (
                        row["request_sent"] or row["outcome"] == "pending"
                    )) if field == "calls" else row[f"budget_{field}_tokens"]
                    for row in rows
                )
            return max(0, amount - spent)

        key = field if field in {"calls", "seconds"} else f"{field}_tokens"
        fixed = remaining(session["allowance"][f"completion_{key}"], session["reserve_start"])
        dynamic = remaining(plan[key], plan["after_request"]) if plan is not None else 0
        return max(fixed, dynamic)

    def _protected(self, field, *, exclude=None):
        return sum(
            self._reserve_left(session_id, field)
            for session_id in self.data["sessions"]
            if session_id != exclude
        )

    def _check_reserves(self):
        used = self.usage()
        for field, cap, consumed in (
            ("calls", self.limits.max_model_calls, self._charged_calls("model")),
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
        remaining = self.limits.max_seconds - (self.clock() - self.started)
        if session_id is not None:
            remaining -= self._protected(
                "seconds", exclude=session_id if purpose == "completion" else None
            )
        return max(0.0, remaining)

    @property
    def remaining_calls(self):
        return max(0, self.limits.max_model_calls - self._charged_calls("model"))

    def current_remaining_seconds(self, *, kind="model"):
        """The active provider owner includes protected completion time in its deadline."""
        return self.remaining_seconds(
            session_id=self._owner.get(),
            purpose="auxiliary" if kind == "embedding" else self._purpose.get(),
        )

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
        measured = self.measure(
            config, messages, tools, response_schema=response_schema, texts=texts,
        )
        estimate = measured["input_tokens"]
        output = getattr(config, "max_tokens", 0) if kind == "model" else 0
        used, own = self.usage(), self.usage(owner)
        exclude = owner if purpose == "completion" else None
        protected_calls = self._protected("calls", exclude=exclude) if kind == "model" else 0
        call_cap = getattr(self.limits, f"max_{kind}_calls")
        if self._charged_calls(kind) + 1 + protected_calls > call_cap:
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
            "budget_input_tokens": estimate,
            "budget_output_tokens": output,
            "estimated_input_tokens": estimate,
            "output_reserve": output,
            "actual_input_tokens": None,
            "actual_output_tokens": None,
            "usage_status": "pending",
            "outcome": "pending",
            "request_sent": False,
            "started_seconds": self.clock() - self.started,
        }
        row["estimate"] = measured
        self.data["requests"].append(row)
        self.save()
        return len(self.data["requests"]) - 1

    def mark_sent(self, ticket):
        self.data["requests"][ticket]["request_sent"] = True
        self.save()

    def settle(self, ticket, usage=None, *, outcome="completed", request_sent=True):
        row = self.data["requests"][ticket]
        row.update(outcome=outcome, request_sent=request_sent)
        row["finished_seconds"] = self.clock() - self.started
        actual_input = getattr(usage, "prompt_tokens", None) if request_sent else None
        actual_output = (
            0 if row["kind"] == "embedding" else getattr(usage, "completion_tokens", None)
        ) if request_sent else None
        row.update(actual_input_tokens=actual_input, actual_output_tokens=actual_output)
        if not request_sent:
            row.update(budget_input_tokens=0, budget_output_tokens=0, usage_status="not_sent")
            self.save()
            return row
        if actual_input is not None:
            row["budget_input_tokens"] = actual_input
            measured = row.get("estimate")
            if measured is not None:
                route_key = measured["route_key"]
                estimator = self._estimators[route_key]
                calibration = self.data["calibration"][route_key]
                # Each ticket keeps its own base, even if another concurrent
                # request has already updated the shared scale.
                estimator.observe(measured, actual_input)
                calibration["scale"] = estimator.scale
                calibration["observations"] += 1
        if actual_output is not None:
            row["budget_output_tokens"] = actual_output
        row["usage_status"] = (
            "provider"
            if actual_input is not None and actual_output is not None
            else "partially_estimated"
            if actual_input is not None or actual_output is not None
            else "estimated_unavailable"
        )
        self.save()
        return row

    def save(self):
        self.data["elapsed_seconds"] = self.clock() - self.started
        write_json(self.path, self.data)
