"""Run-local availability shared by aliases of the same model endpoint."""

from .provider import ModelError


class RunModel:
    def __init__(self, model, stopped):
        self.model, self.stopped = model, stopped
        self.config = model.config
        identity = getattr(model, "credential_id", None) or self.config.api_key_env
        self.key = (self.config.base_url.rstrip("/"), self.config.model, identity)
        self.credential_key = (self.config.base_url.rstrip("/"), identity)

    @property
    def stopped_reason(self):
        return next(
            (self.stopped[k] for k in (self.key, self.credential_key) if k in self.stopped),
            None,
        )

    @property
    def available(self):
        return self.stopped_reason is None

    def record_failure(self, exc):
        if exc.terminal and exc.scope in {"credential", "model"}:
            key = self.credential_key if exc.scope == "credential" else self.key
            self.stopped[key] = str(exc)

    async def complete(
        self, messages, tools=None, *, response_schema=None, tool_choice=None, max_tokens=None,
    ):
        if not self.available:
            raise ModelError(
                "model_service_stopped:" + self.stopped_reason,
                terminal=True,
                request_sent=False,
            )
        try:
            request = {}
            if response_schema is not None:
                request["response_schema"] = response_schema
            if tool_choice is not None:
                request["tool_choice"] = tool_choice
            if max_tokens is not None:
                request["max_tokens"] = max_tokens
            return await self.model.complete(messages, tools, **request)
        except ModelError as exc:
            self.record_failure(exc)
            raise


def available(runtime, name):
    return name in runtime.models and getattr(runtime.models[name], "available", True)
