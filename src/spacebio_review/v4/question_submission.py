"""Durable question submission; recovery never buys another sample."""

import json
from pathlib import Path

from spacebio_review.agents.serialization import parse_output
from spacebio_review.agents.types import TaskResult

PARSER_VERSION = "question-parser-1"


class QuestionSubmission:
    def __init__(self, records, identity, key, contract):
        self.records, self.identity, self.key = records, identity, key
        self.contract = contract
        self.saved = records.receipt(identity)

    def observe(self, entry):
        value = self.records.receipt(self.identity) or {}
        value.update(
            submission=entry,
            mapping=self.contract.task_context.mapping() if self.contract.task_context else {},
            not_sent=entry.get("not_sent", False),
            schema=self.contract.schema(),
        )
        self.records.receipt(self.identity, value)

    def recover(self):
        saved = self.saved
        if saved is None or saved.get("not_sent"):
            return None
        result = saved.get("result")
        if result and (result["status"] == "completed" or saved.get("parser") == PARSER_VERSION):
            return TaskResult.model_validate(result)
        entry = saved.get("submission") or {}
        path = entry.get("path")
        failure = "question_request_outcome_unknown"
        product = None
        if path:
            raw = json.loads(Path(path).read_text("utf-8"))
            failure = raw.get("state", "response_unavailable")
            if raw.get("finish_reason") == "stop" and raw.get("raw_content") is not None:
                try:
                    context = self.contract.task_context
                    if context:
                        context.evidence.clear()
                        context.results.clear()
                        context.aliases.clear()
                        context.reverse.clear()
                        context.active.clear()
                        context.frozen = False
                        for alias, row in saved.get("mapping", {}).items():
                            context.add({row["identity"]: row["value"]}, alias[0])
                        self.contract.refresh(self.contract)
                    if self.contract.schema() != saved["schema"]:
                        raise ValueError("question_replay_contract_changed")
                    product = self.contract.product(
                        self.contract.parse(parse_output(raw["raw_content"], "json"))
                    ).model_dump(mode="json")
                    failure = "replayed_saved_response"
                except (ValueError, TypeError, KeyError) as exc:
                    failure = f"question_local_parse_failed: {exc}"
        result = TaskResult(
            task_id=self.key,
            agent_id=self.key,
            status="completed" if product is not None else "failed",
            product=product,
            reason=failure,
        )
        saved.update(
            result=result, parser=PARSER_VERSION, local_replays=saved.get("local_replays", 0) + 1
        )
        self.records.receipt(self.identity, saved)
        return result

    def begin(self):
        self.records.receipt(
            self.identity, {"phase": "generation_started", "parser": PARSER_VERSION}
        )
        self.contract.submission_observer = self.observe

    def not_sent(self):
        self.records.receipt(self.identity, {"not_sent": True, "parser": PARSER_VERSION})
