"""One structured submission, durable before parsing, with no repair loop."""

import json
from pathlib import Path
from time import monotonic

import yaml
from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.agents.context import TokenEstimator, request_capacity
from spacebio_review.agents.diagnostics import digest
from spacebio_review.models.config import ModelConfig
from spacebio_review.models.provider import ModelError, model_class
from spacebio_review.models.scheduling import RequestScheduler, ScheduledModel

from .records import write_json


class ManuscriptConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: ModelConfig
    context_tokens: int = Field(default=96000, gt=0)
    assessment_tokens: int = Field(default=24576, gt=0)
    scientific_tokens: int = Field(default=24576, gt=0)
    decision_tokens: int = Field(default=16384, gt=0)
    author_tokens: int = Field(default=8192, gt=0)
    summary_tokens: int = Field(default=8192, gt=0)

    @classmethod
    def load(cls, path="config/assessment.yaml"):
        return cls.model_validate(yaml.safe_load(Path(path).read_text("utf-8")))


class ManuscriptCall:
    def __init__(
        self, records, config, *, budget=None, scheduler=None, env_file=None, model_factory=None
    ):
        self.records, self.config = records, config
        self.budget, self.env_file = budget, env_file
        self.scheduler = scheduler or RequestScheduler()
        self.model_factory = model_factory

    def request(self, prompt, materials, contract, max_tokens):
        model = self.config.model.model_copy(update={"max_tokens": max_tokens})
        schema = contract.schema()
        if model.structured_output == "json_object":
            prompt += "\n按以下 JSON Schema 提交 JSON 对象：\n" + json.dumps(
                schema, ensure_ascii=False, separators=(",", ":")
            )
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(materials, ensure_ascii=False, separators=(",", ":")),
            },
        ]
        return {
            "messages": messages,
            "schema": schema,
            "model": model.model_dump(mode="json"),
        }

    def preview(self, request):
        model = ModelConfig.model_validate(request["model"])
        estimate = TokenEstimator(None, model.image_tokens).measure(
            request["messages"],
            response_schema=request["schema"],
            output_reserve=model.max_tokens,
        )
        return request_capacity(estimate, model.context_window, self.config.context_tokens)

    async def submit(self, key, prompt, materials, contract, *, max_tokens):
        request = self.request(prompt, materials, contract, max_tokens)
        identity = {"task": key, "input": digest(request)}
        saved = self.records.receipt(identity)
        if saved and saved.get("status") == "completed":
            return saved["product"]
        if saved and saved.get("reply") is not None:
            return self.accept(identity, saved, contract)
        if saved and saved.get("status") != "not_sent":
            raise ValueError("manuscript_submission_already_attempted:" + key)
        capacity = self.preview(request)
        if not capacity["fits"]:
            raise ValueError("manuscript_context_exceeds_capacity:" + key)
        path = self.records.root / "manuscript" / (identity["input"] + ".json")
        write_json(
            path, {"request": request, "capacity": capacity, "contract": contract.metadata()}
        )
        model_config = ModelConfig.model_validate(request["model"])
        model = (
            self.model_factory(model_config)
            if self.model_factory
            else model_class(model_config)(
                model_config,
                env_file=self.env_file,
                budget=self.budget,
            )
        )
        self.records.receipt(identity, {"status": "started", "request_path": str(path)})
        started = monotonic()
        try:
            with self.scheduler.task(
                key, "assessment" if key.startswith("assessment/") else "author", "publication"
            ):
                reply = await ScheduledModel(model, self.scheduler, "manuscript").complete(
                    request["messages"],
                    response_schema=request["schema"],
                )
            saved = {
                "status": "received",
                "request_path": str(path),
                "reply": reply.message,
                "finish_reason": reply.finish_reason,
                "provider_response": reply.provider_response,
                "usage": {"input": reply.prompt_tokens, "output": reply.completion_tokens},
                "elapsed_seconds": monotonic() - started,
            }
            self.records.receipt(identity, saved)
        except ModelError as exc:
            self.records.receipt(
                identity,
                {
                    "status": "not_sent" if exc.request_sent is False else "failed",
                    "reason": str(exc),
                    "request_path": str(path),
                    "elapsed_seconds": monotonic() - started,
                },
            )
            raise
        finally:
            await model.close()
        return self.accept(identity, saved, contract)

    def accept(self, identity, saved, contract):
        if saved["finish_reason"] != "stop":
            raise ValueError("manuscript_submission_incomplete:" + saved["finish_reason"])
        # Local replay of the original response is allowed; another paid sample is not.
        product = contract.product(
            contract.parse(json.loads(saved["reply"]["content"]))
        ).model_dump(mode="json")
        self.records.receipt(identity, {**saved, "status": "completed", "product": product})
        return product
