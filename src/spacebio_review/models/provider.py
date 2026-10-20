from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

import httpx
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, AsyncOpenAI

from .capabilities import formal_capability
from .config import ModelConfig, credential
from .telemetry import request_settings


class ModelError(Exception):
    """Safe error text: never includes provider bodies, prompts, or credentials."""

    def __init__(
        self,
        reason,
        *,
        terminal=False,
        scope="model",
        retries=0,
        retry_wait_seconds=0.0,
        request=None,
        category="unknown",
        request_sent=None,
        http_status=None,
    ):
        super().__init__(reason)
        self.request = request
        self.terminal = terminal
        self.scope = scope
        self.retries = retries
        self.retry_wait_seconds = retry_wait_seconds
        self.category = category
        self.request_sent = request_sent
        self.http_status = http_status


def http_failure(status, code, *, profile):
    """Only explicit service codes establish a persistent unavailability scope."""
    normalized = re.sub(r"[_.-]", "", str(code or "")).lower()
    if status == 401:
        return "credential_unavailable", "credential"
    if normalized in {"insufficientquota", "quotaexhausted"} and profile != "dashscope":
        return "unknown_quota", None
    if profile == "dashscope" and status == 429:
        if normalized in {"throttlingallocationquota", "insufficientquota"}:
            return "rate_limit", None
        if normalized in {"throttlingratequota", "limitrequests"}:
            return "rate_limit_requests", None
        if normalized in {"throttlingburstrate", "limitburstrate"}:
            return "rate_limit_burst", None
    if normalized in {
        "arrearage",
        "invalidapikey",
        "unpaid",
        "insufficientbalance",
        "accountoverdue",
        "prepaidbilloverdue",
        "isvoutofservice",
    }:
        return "credential_unavailable", "credential"
    if normalized in {"modelnotfound", "modelnotavailable", "modelaccessdenied"}:
        return "model_unavailable", "model"
    if status == 429:
        return "rate_limit", None
    if status >= 500 or status in {408, 409}:
        return "temporary_server", None
    return "invalid_request" if status == 400 else "unknown", None


def _request_estimate(config, budget, messages=None, tools=None, schema=None, *, texts=None):
    if budget is not None:
        return budget.measure(config, messages, tools, response_schema=schema, texts=texts)
    from ..agents.context import TokenEstimator

    estimator = TokenEstimator(
        image_tokens=getattr(config, "image_tokens", 0),
        image_patch_size=getattr(config, "image_patch_size", None),
    )
    if texts is not None:
        return estimator.measure_texts(texts)
    return estimator.measure(
        messages, tools or [], response_schema=schema, output_reserve=config.max_tokens
    )


async def _scheduled_attempt(
    operation, config, scheduler, budget, estimate, *, kind, attempt, retry_wait_seconds=0.0
):
    if scheduler is None:
        return await operation()
    quota_group = config.quota_group
    weight = estimate["input_tokens"]
    if quota_group and scheduler.quotas[quota_group].token_basis == "input_and_output":
        weight += estimate["output_reserve"]
    deadline = (
        scheduler.clock() + budget.current_remaining_seconds(kind=kind)
        if budget is not None
        else None
    )
    return await scheduler.call(
        operation,
        kind=kind,
        name="literature_embedding" if kind == "embedding" else "",
        model_id=config.model,
        quota_group=quota_group,
        estimated_tokens=weight,
        deadline=deadline,
        attempt=attempt,
        retry_wait_seconds=retry_wait_seconds,
    )


def _mark_sent(budget, scheduler, ticket):
    if ticket is not None:
        budget.mark_sent(ticket)
    if scheduler is not None:
        scheduler.update_request(request_sent=True)


def _settle_attempt(budget, scheduler, ticket, usage, estimate, *, outcome, sent, kind="model"):
    if ticket is not None:
        row = budget.settle(ticket, usage, outcome=outcome, request_sent=sent)
    else:
        actual_input = getattr(usage, "prompt_tokens", None) if sent else None
        actual_output = (
            (0 if kind == "embedding" else getattr(usage, "completion_tokens", None))
            if sent
            else None
        )
        row = {
            "request_sent": sent,
            "outcome": outcome,
            "estimated_input_tokens": estimate["input_tokens"],
            "output_reserve": estimate["output_reserve"],
            "actual_input_tokens": actual_input,
            "actual_output_tokens": actual_output,
            "budget_input_tokens": (
                actual_input if actual_input is not None else estimate["input_tokens"]
            )
            if sent
            else 0,
            "budget_output_tokens": (
                actual_output if actual_output is not None else estimate["output_reserve"]
            )
            if sent
            else 0,
            "usage_status": (
                "not_sent"
                if not sent
                else "provider"
                if actual_input is not None and actual_output is not None
                else "partially_estimated"
                if actual_input is not None or actual_output is not None
                else "estimated_unavailable"
            ),
        }
    if scheduler is not None:
        scheduler.update_request(**row)


_RETRYABLE_CATEGORIES = {
    "timeout",
    "connection",
    "rate_limit",
    "rate_limit_requests",
    "rate_limit_burst",
    "temporary_server",
    "unknown_quota",
}


@dataclass
class ChatReply:
    message: dict[str, Any]
    finish_reason: str = "stop"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    retries: int = 0
    retry_wait_seconds: float = 0.0
    effective_request: dict = field(default_factory=dict)
    reasoning_tokens: int | None = None
    provider_response: dict = field(default_factory=dict)


class ChatModel(Protocol):
    config: ModelConfig

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        response_schema: dict | None = None,
        tool_choice: str | dict | None = None,
        max_tokens: int | None = None,
    ) -> ChatReply: ...


async def _stream_completion(stream, receive_usage):
    """Join SDK deltas without parsing or rewriting the model's tool arguments."""
    message, calls, metadata = {"role": "assistant"}, {}, {}
    usage, finish, seen_choice = None, "stream_incomplete", False
    async with stream:
        async for chunk in stream:
            metadata.update({
                key: value for key in ("id", "model", "created", "system_fingerprint",
                                       "service_tier")
                if (value := getattr(chunk, key, None)) is not None
            })
            if chunk.usage is not None:
                usage = chunk.usage
                receive_usage(usage)
            for choice in chunk.choices:
                if choice.index != 0:
                    continue
                seen_choice = True
                if choice.finish_reason is not None:
                    finish = choice.finish_reason
                delta = choice.delta.model_dump(exclude_none=True)
                # Preserve reasoning_content only in the transient protocol message,
                # just as for non-streaming tool conversations; never in public raw output.
                for key in ("content", "refusal", "reasoning_content"):
                    if key in delta:
                        message[key] = message.get(key, "") + delta[key]
                for fragment in delta.get("tool_calls", []):
                    call = calls.setdefault(fragment["index"], {})
                    if fragment.get("id"):
                        call["id"] = fragment["id"]
                    if fragment.get("type"):
                        call["type"] = fragment["type"]
                    for key, value in fragment.get("function", {}).items():
                        function = call.setdefault("function", {})
                        function[key] = function.get(key, "") + value
    if not seen_choice:
        raise ModelError("empty_model_response")
    if calls:
        message["tool_calls"] = [calls[index] for index in sorted(calls)]
    public_message = {key: value for key, value in message.items() if key != "reasoning_content"}
    raw = {
        **metadata, "object": "chat.completion",
        "choices": [{"index": 0, "message": public_message, "finish_reason": finish}],
        "usage": usage.model_dump(exclude_none=True) if usage is not None else None,
    }
    return message, finish, raw


class DashScopeModel:
    def __init__(
        self,
        config: ModelConfig,
        *,
        env_file: str | Path | None = None,
        client: AsyncOpenAI | None = None,
        budget=None,
        scheduler=None,
    ):
        self.budget = budget
        self.scheduler = scheduler
        self.config = config.model_copy(deep=True)
        reserved = {
            "model",
            "messages",
            "tools",
            "tool_choice",
            "api_key",
            "max_tokens",
            "response_format",
        }
        if reserved.intersection(config.parameters) or (
            reserved | {"stream", "stream_options"}
        ).intersection(config.extra_body):
            raise ValueError("Model parameters override a runtime-owned field")
        self._client = client or AsyncOpenAI(
            api_key=credential(config.api_key_env, env_file),
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            max_retries=0,
        )
        # Only explicit credential failures use this run-local stop identity;
        # token quotas use the configured non-secret quota_group instead.
        key = getattr(self._client, "api_key", None)
        self.credential_id = sha256(key.encode()).hexdigest() if isinstance(key, str) else None

    async def close(self) -> None:
        await self._client.close()

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        response_schema: dict | None = None,
        tool_choice: str | dict | None = None,
        max_tokens: int | None = None,
    ) -> ChatReply:
        config = self.config
        if max_tokens is not None:
            if not 0 < max_tokens <= config.max_tokens:
                raise ValueError("invalid_request_output_limit")
            config = config.model_copy(update={"max_tokens": max_tokens})
        has_image = any(
            isinstance(m.get("content"), list)
            and any(p.get("type") == "image_url" for p in m["content"])
            for m in messages
        )
        if has_image and not self.config.vision:
            raise ModelError("unsupported_image_input", request_sent=False)
        if tools and not self.config.tools:
            raise ModelError("unsupported_tool_calling", request_sent=False)
        if response_schema is not None and (tools or self.config.structured_output == "tool_call"):
            raise ModelError("unsupported_structured_request", request_sent=False)
        kwargs = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "max_tokens": config.max_tokens,
            **self.config.parameters,
        }
        if kwargs["stream"]:
            kwargs.setdefault("stream_options", {"include_usage": True})
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None and (tools or tool_choice == "none"):
            kwargs["tool_choice"] = tool_choice
        if response_schema is not None:
            try:
                capability = formal_capability(
                    self.config, has_image=has_image, schema=response_schema
                )
            except ValueError as exc:
                raise ModelError(str(exc), request_sent=False) from None
            response_format = (
                {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "formal_product",
                        "strict": True,
                        "schema": response_schema,
                    },
                }
                if self.config.structured_output == "json_schema"
                else {"type": "json_object"}
            )
            if self.config.structured_output != "yaml":
                kwargs["response_format"] = response_format
        if self.config.extra_body:
            kwargs["extra_body"] = self.config.extra_body
        retry_wait = 0.0
        retry_delay = 0.0
        for attempt in range(self.config.retries + 1):
            estimate = _request_estimate(config, self.budget, messages, tools, response_schema)

            async def request():
                ticket, usage, sent, outcome = None, None, False, "failed"
                try:
                    if self.budget is not None:
                        ticket = self.budget.reserve(config, messages, tools, response_schema)
                    _mark_sent(self.budget, self.scheduler, ticket)
                    sent = True
                    response = await self._client.chat.completions.create(**kwargs)
                    raw = {}
                    if kwargs["stream"]:
                        def receive_usage(value):
                            nonlocal usage
                            usage = value

                        message, finish, raw = await _stream_completion(response, receive_usage)
                    else:
                        usage = response.usage
                        if not response.choices:
                            raise ModelError(
                                "empty_model_response", request=request_settings(kwargs)
                            )
                        choice = response.choices[0]
                        # model_dump preserves provider extras such as reasoning_content.
                        message = choice.message.model_dump(exclude_none=True)
                        finish = choice.finish_reason
                    outcome = "completed"
                    return ChatReply(
                        message,
                        finish,
                        getattr(usage, "prompt_tokens", None),
                        getattr(usage, "completion_tokens", None),
                        attempt,
                        retry_wait,
                        request_settings(kwargs)
                        | (
                            {"generation_capability": capability}
                            if response_schema is not None
                            else {}
                        ),
                        getattr(
                            getattr(usage, "completion_tokens_details", None),
                            "reasoning_tokens",
                            None,
                        ),
                        provider_response=raw,
                    )
                except (APITimeoutError, APIConnectionError, httpx.RequestError) as exc:
                    timeout = isinstance(exc, (APITimeoutError, httpx.TimeoutException))
                    logging.getLogger(__name__).warning(
                        "model=%s attempt=%s connection_error=%s cause=%s",
                        self.config.model,
                        attempt + 1,
                        type(exc).__name__,
                        type(exc.__cause__).__name__ if exc.__cause__ else "unknown",
                    )
                    raise ModelError(
                        "model_request_timeout" if timeout else "model_connection_failed",
                        category="timeout" if timeout else "connection",
                        scope="request",
                        retries=attempt,
                        retry_wait_seconds=retry_wait,
                        request=request_settings(kwargs),
                        request_sent=sent,
                    ) from None
                except APIStatusError as exc:
                    error = f"model_http_{exc.status_code}"
                    body = exc.body if isinstance(exc.body, dict) else {}
                    detail = body.get("error", body)
                    code = detail.get("code") if isinstance(detail, dict) else None
                    if (
                        isinstance(code, str)
                        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", code)
                        and self._client.api_key not in code
                    ):
                        error += f":{code}"
                    category, scope = http_failure(
                        exc.status_code, code, profile=self.config.error_profile
                    )
                    raise ModelError(
                        error,
                        terminal=bool(scope),
                        scope=scope or "request",
                        category=category,
                        retries=attempt,
                        retry_wait_seconds=retry_wait,
                        request=request_settings(kwargs),
                        request_sent=sent,
                        http_status=exc.status_code,
                    ) from None
                except APIError:
                    # An SSE error event has no HTTP error status. Keep provider bodies
                    # out of diagnostics, as in the ordinary HTTP error path.
                    raise ModelError(
                        "model_stream_failed", scope="request", request=request_settings(kwargs),
                        request_sent=sent, retries=attempt, retry_wait_seconds=retry_wait,
                    ) from None
                except asyncio.CancelledError:
                    outcome = "cancelled"
                    raise
                except ModelError as exc:
                    exc.request_sent = sent
                    if exc.request is None:
                        exc.request = request_settings(kwargs)
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
                    )

            try:
                return await _scheduled_attempt(
                    request,
                    config,
                    self.scheduler,
                    self.budget,
                    estimate,
                    kind="model",
                    attempt=attempt,
                    retry_wait_seconds=retry_delay,
                )
            except ModelError as exc:
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
                delay = min(5 * 2**attempt, 30)
                await asyncio.sleep(delay)
                retry_wait += delay
                retry_delay = delay
