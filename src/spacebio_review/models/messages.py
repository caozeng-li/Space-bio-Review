"""Text-only, non-retrying Messages adapter for native structured submissions."""

from hashlib import sha256
from types import SimpleNamespace

import httpx

from .capabilities import formal_capability
from .config import credential
from .provider import ChatReply, ModelError, http_failure


def text_blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list) or any(p.get("type") != "text" for p in content):
        raise ModelError("unsupported_messages_content")
    return content


class MessagesModel:
    def __init__(self, config, *, env_file=None, client=None, budget=None):
        self.config = config.model_copy(deep=True)
        self.budget = budget
        if config.request_protocol != "messages" or config.retries:
            raise ValueError("messages_requires_explicit_nonretrying_route")
        reserved = {
            "model",
            "messages",
            "system",
            "tools",
            "stream",
            "max_tokens",
            "response_format",
        }
        if reserved.intersection(config.parameters) or reserved.intersection(config.extra_body):
            raise ValueError("Model parameters override a runtime-owned field")
        if set(config.extra_body.get("output_config", {})) - {"effort"}:
            raise ValueError("output_format_is_contract_owned")
        key = (
            client.headers.get("x-api-key", "")
            if client
            else credential(config.api_key_env, env_file)
        )
        self._client = client or httpx.AsyncClient(
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout=config.timeout_seconds,
        )
        self.credential_id = sha256(key.encode()).hexdigest() if key else None

    async def close(self):
        await self._client.aclose()

    async def complete(self, messages, tools=None, *, response_schema=None):
        if tools or response_schema is None or self.config.structured_output != "json_schema":
            raise ModelError("messages_requires_formal_schema")
        try:
            capability = formal_capability(self.config, schema=response_schema)
        except ValueError as exc:
            raise ModelError(str(exc)) from None
        system, conversation = [], []
        for message in messages:
            blocks = text_blocks(message.get("content", ""))
            if message["role"] == "system":
                system.extend(blocks)
            elif message["role"] in {"user", "assistant"}:
                conversation.append({"role": message["role"], "content": blocks})
            else:
                raise ModelError("unsupported_messages_role")
        payload = {
            **self.config.parameters,
            **self.config.extra_body,
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "stream": False,
            "messages": conversation,
            "output_config": {
                **self.config.extra_body.get("output_config", {}),
                "format": {"type": "json_schema", "schema": response_schema},
            },
        }
        if system:
            payload["system"] = system
        settings = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "request_protocol": "messages",
            "base_url": self.config.base_url,
            "response_format": "json_schema",
            "tools_enabled": False,
            "thinking": payload.get("thinking"),
            "effort": payload["output_config"].get("effort"),
            "generation_capability": capability,
        }
        try:
            ticket = (
                self.budget.reserve(self.config, messages, None, response_schema)
                if self.budget
                else None
            )
        except ModelError as exc:
            exc.request_sent = False
            raise
        try:
            response = await self._client.post(
                self.config.base_url.rstrip("/") + "/v1/messages", json=payload
            )
        except httpx.RequestError as exc:
            timeout = isinstance(exc, httpx.TimeoutException)
            raise ModelError(
                "model_request_timeout" if timeout else "model_connection_failed",
                category="timeout" if timeout else "connection",
                scope="request",
                request=settings,
                request_sent=True,
            ) from None
        if response.is_error:
            category, scope = http_failure(response.status_code, None)
            raise ModelError(
                f"model_http_{response.status_code}",
                category=category,
                terminal=bool(scope),
                scope=scope or "request",
                request=settings,
                request_sent=True,
            )
        try:
            raw = response.json()
            blocks = raw["content"]
        except (ValueError, KeyError, TypeError):
            raise ModelError("invalid_messages_response", request=settings) from None
        usage = raw.get("usage") or {}
        input_tokens = usage.get("input_tokens")
        if input_tokens is not None:
            input_tokens += (usage.get("cache_read_input_tokens") or 0) + (
                usage.get("cache_creation_input_tokens") or 0
            )
        output_tokens = usage.get("output_tokens")
        if self.budget:
            self.budget.settle(
                ticket, SimpleNamespace(prompt_tokens=input_tokens, completion_tokens=output_tokens)
            )
        finish = {"end_turn": "stop", "max_tokens": "length", "refusal": "content_filter"}.get(
            raw.get("stop_reason"), raw.get("stop_reason", "unknown")
        )
        message = {
            "role": "assistant",
            "content": "".join(b.get("text", "") for b in blocks if b["type"] == "text"),
        }
        thoughts = "".join(b.get("thinking", "") for b in blocks if b["type"] == "thinking")
        if thoughts:
            message["reasoning_content"] = thoughts
        if any(b["type"] == "refusal" for b in blocks):
            finish = "content_filter"
        return ChatReply(
            message,
            finish,
            input_tokens,
            output_tokens,
            effective_request=settings,
            provider_response=raw,
        )
