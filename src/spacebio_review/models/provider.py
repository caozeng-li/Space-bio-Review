from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

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
    ):
        super().__init__(reason)
        self.request = request
        self.terminal = terminal
        self.scope = scope
        self.retries = retries
        self.retry_wait_seconds = retry_wait_seconds
        self.category = category


def http_failure(status, code):
    """Only explicit service codes establish a persistent unavailability scope."""
    normalized = re.sub(r"[_.-]", "", str(code or "")).lower()
    if (
        normalized
        in {
            "arrearage",
            "insufficientquota",
            "quotaexhausted",
            "invalidapikey",
            "unpaid",
            "insufficientbalance",
            "accountoverdue",
        }
        or status == 401
    ):
        return "credential_unavailable", "credential"
    if normalized in {"modelnotfound", "modelnotavailable", "modelaccessdenied"}:
        return "model_unavailable", "model"
    if status == 429:
        return "rate_limit", None
    if status >= 500 or status in {408, 409}:
        return "temporary_server", None
    return "invalid_request" if status == 400 else "unknown", None


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


class ChatModel(Protocol):
    config: ModelConfig

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        response_schema: dict | None = None,
    ) -> ChatReply: ...


class DashScopeModel:
    def __init__(
        self,
        config: ModelConfig,
        *,
        env_file: str | Path | None = None,
        client: AsyncOpenAI | None = None,
        budget=None,
    ):
        self.budget = budget
        self.config = config.model_copy(deep=True)
        reserved = {
            "model",
            "messages",
            "tools",
            "stream",
            "api_key",
            "max_tokens",
            "response_format",
        }
        if reserved.intersection(config.parameters) or reserved.intersection(config.extra_body):
            raise ValueError("Model parameters override a runtime-owned field")
        self._client = client or AsyncOpenAI(
            api_key=credential(config.api_key_env, env_file),
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            max_retries=0,
        )
        # A non-secret identity lets aliases sharing the actual credential share
        # quota stops, even when their environment variable names differ.
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
    ) -> ChatReply:
        has_image = any(
            isinstance(m.get("content"), list)
            and any(p.get("type") == "image_url" for p in m["content"])
            for m in messages
        )
        if has_image and not self.config.vision:
            raise ModelError("unsupported_image_input")
        if tools and not self.config.tools:
            raise ModelError("unsupported_tool_calling")
        if response_schema is not None and (tools or self.config.structured_output == "tool_call"):
            raise ModelError("unsupported_structured_request")
        kwargs = dict(
            model=self.config.model,
            messages=messages,
            stream=False,
            max_tokens=self.config.max_tokens,
            **self.config.parameters,
        )
        if tools:
            kwargs["tools"] = tools
        if response_schema is not None:
            try:
                capability = formal_capability(
                    self.config, has_image=has_image, schema=response_schema
                )
            except ValueError as exc:
                raise ModelError(str(exc)) from None
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
        for attempt in range(self.config.retries + 1):
            ticket = (
                self.budget.reserve(self.config, messages, tools, response_schema)
                if self.budget
                else None
            )
            try:
                response = await self._client.chat.completions.create(**kwargs)
                if self.budget:
                    self.budget.settle(ticket, response.usage)
                if not response.choices:
                    raise ModelError("empty_model_response", request=request_settings(kwargs))
                choice = response.choices[0]
                # model_dump preserves provider extras such as reasoning_content.
                message = choice.message.model_dump(exclude_none=True)
                return ChatReply(
                    message,
                    choice.finish_reason,
                    response.usage.prompt_tokens if response.usage else None,
                    response.usage.completion_tokens if response.usage else None,
                    attempt,
                    retry_wait,
                    request_settings(kwargs)
                    | (
                        {"generation_capability": capability} if response_schema is not None else {}
                    ),
                    getattr(
                        getattr(response.usage, "completion_tokens_details", None),
                        "reasoning_tokens",
                        None,
                    ),
                )
            except (APITimeoutError, APIConnectionError) as exc:
                category = "timeout" if isinstance(exc, APITimeoutError) else "connection"
                error = (
                    "model_request_timeout" if category == "timeout" else "model_connection_failed"
                )
                logging.getLogger(__name__).warning(
                    "model=%s attempt=%s connection_error=%s cause=%s",
                    self.config.model,
                    attempt + 1,
                    type(exc).__name__,
                    type(exc.__cause__).__name__ if exc.__cause__ else "unknown",
                )
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
                category, scope = http_failure(exc.status_code, code)
                if scope:
                    raise ModelError(
                        error,
                        terminal=True,
                        scope=scope,
                        category=category,
                        retries=attempt,
                        retry_wait_seconds=retry_wait,
                        request=request_settings(kwargs),
                    ) from None
                if exc.status_code not in {408, 409, 429} and exc.status_code < 500:
                    raise ModelError(
                        error,
                        category=category,
                        scope="request",
                        retries=attempt,
                        retry_wait_seconds=retry_wait,
                        request=request_settings(kwargs),
                    ) from None
            if attempt < self.config.retries:
                # Short network outages can affect every concurrent reviewer at once.
                # Retry finitely, but do not spend every attempt in the same few seconds.
                delay = min(5 * 2**attempt, 30)
                await asyncio.sleep(delay)
                retry_wait += delay
        raise ModelError(
            error,
            category=category,
            scope="request",
            retries=self.config.retries,
            retry_wait_seconds=retry_wait,
            request=request_settings(kwargs),
        )
