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
            (
                self.stopped[k]
                for k in (self.key, self.credential_key)
                if k in self.stopped
            ),
            None,
        )

    @property
    def available(self):
        return self.stopped_reason is None

    async def complete(self, messages, tools=None, *, response_schema=None):
        if not self.available:
            raise ModelError("model_service_stopped:" + self.stopped_reason, terminal=True)
        try:
            if response_schema is None:
                return await self.model.complete(messages, tools)
            return await self.model.complete(messages, tools, response_schema=response_schema)
        except ModelError as exc:
            if exc.terminal and exc.scope in {"credential", "model"}:
                key = self.credential_key if exc.scope == "credential" else self.key
                self.stopped[key] = str(exc)
            raise


def available(runtime, name):
    return name in runtime.models and getattr(runtime.models[name], "available", True)
