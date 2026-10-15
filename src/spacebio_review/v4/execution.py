"""Bounded generation with a durable final receipt, not a second task state machine."""

import logging
import traceback

from spacebio_review.agents import AgentTask, ContextView, RoleProfile
from spacebio_review.agents.diagnostics import digest
from spacebio_review.agents.types import TaskResult
from spacebio_review.context import product_material
from spacebio_review.models.provider import ModelError
from spacebio_review.review.evaluation import get_evaluation_system, system_from_context
from spacebio_review.tools import ToolRegistry

from .contracts import GENERATION_VERSION
from .records import VERSION, plain, write_json


class Execution:
    def __init__(self, runtime, records, config, evaluation, reporting=None):
        from .config import ReportingConfig

        self.runtime, self.records, self.config = runtime, records, config
        self.evaluation, self.reporting = evaluation, reporting or ReportingConfig()
        self.system = (
            system_from_context(evaluation)
            if "system" in evaluation
            else get_evaluation_system(config.evaluation_system)
        )
        self.tasks, self.receipts = [], {}
        runtime_identity = plain(runtime.config) if runtime.config else {}
        # New opt-in routing fields must not invalidate receipts for unchanged routes.
        for model in runtime_identity.get("models", {}).values():
            if model.get("request_protocol") == "chat_completions":
                model.pop("request_protocol")
        for route in runtime_identity.get("task_routes", {}).values():
            if route.get("retries") is None:
                route.pop("retries", None)
        self.configuration = digest(
            plain(
                {
                    "v4": config,
                    "implementation": VERSION,
                    "generation": GENERATION_VERSION,
                    "evaluation": evaluation,
                    "runtime": runtime_identity,
                    "reporting": self.reporting,
                }
            )
        )

    def context_view(
        self, key, contract, *, reader=None, refs=(), materials=(), include_evaluation=True
    ):
        view = (
            reader.view(key, key, refs=refs, extra=list(materials))
            if reader
            else ContextView(agent_id=key, phase=key, materials=list(materials))
        )
        if include_evaluation:
            # The runtime already carries the selected standard in its system instruction.
            evaluation = (
                {"prompt": self.evaluation["run_prompt"]}
                if "run_prompt" in self.evaluation
                else self.evaluation
            )
            view.materials.append(product_material("evaluation-context", evaluation))
        if contract.task_context:
            contract.task_context.collect([m.data for m in view.materials])
            contract.refresh(contract)
        return view

    def configuration_for(self, kind):
        if kind not in {"preparation", "question_generation"}:
            return self.configuration
        config = self.runtime.config
        return digest(
            plain(
                {
                    "kind": kind,
                    "model": config.routed_model(kind, formal=True) if config else {},
                    "route": config.task_routes[kind] if config else {},
                    "evaluation": self.evaluation,
                    "evaluation_core": config.evaluation_core if config else "",
                    "evaluation_skill": getattr(
                        getattr(self.runtime, "evaluation", None), "body", ""
                    ),
                    "context_tokens": self.config.context_tokens,
                    "generation": GENERATION_VERSION,
                }
            )
        )

    def preview(
        self,
        key,
        kind,
        objective,
        contract_factory,
        *,
        reader_factory=None,
        materials=(),
        skills=(),
        tier="normal",
        tools_factory=None,
    ):
        # Factories deliberately create a fresh scope and TaskContext for every trial.
        reader = reader_factory() if reader_factory else None
        contract = contract_factory(reader)
        view = self.context_view(
            key,
            contract,
            reader=reader,
            materials=materials,
            include_evaluation=kind != "question_generation",
        )
        tools = (
            tools_factory(reader) if tools_factory else reader.tools() if reader else ToolRegistry()
        )
        task = AgentTask(
            task_id=key,
            phase=key,
            kind=kind,
            objective=objective,
            expected_product="只提交本任务草稿；程序负责身份、版本与来源。",
            model_tier=tier,
            mode="direct",
            context_soft_tokens=self.config.context_tokens,
        )
        profile = RoleProfile(agent_id=key, responsibility=objective, required_skills=list(skills))
        return self.runtime.preview_formal(task, profile, view, contract, tools)

    async def call(
        self,
        key,
        kind,
        objective,
        contract,
        *,
        reader=None,
        refs=(),
        materials=(),
        tools=None,
        skills=(),
        tier="normal",
        research=False,
        reading=None,
        read_actions=None,
    ):
        view = self.context_view(
            key,
            contract,
            reader=reader,
            refs=refs,
            materials=materials,
            include_evaluation=kind != "question_generation",
        )
        registry = tools if tools is not None else reader.tools() if reader else ToolRegistry()
        native_prompt = contract.native_prompt
        if (
            native_prompt is None
            and kind == "specialist"
            and contract.task_context
            and contract.task_context.structured_refs
            and self.runtime.config
            and self.runtime.config.routed_model(kind, tier, formal=True).structured_output
            == "json_schema"
        ):
            native_prompt = contract.prompt(native=True)
        fingerprint = digest(
            plain(
                {
                    "configuration": self.configuration_for(kind),
                    "key": key,
                    "objective": objective,
                    "context": view,
                    "schema": contract.schema(),
                    **({"native_prompt": native_prompt} if native_prompt is not None else {}),
                    "skills": list(skills),
                    "tier": tier,
                }
            )
        )
        identity = {"task": key, "input": fingerprint}
        saved = self.records.receipt(identity)
        submission = None
        if kind == "question_generation":
            from .question_submission import QuestionSubmission

            submission = QuestionSubmission(self.records, identity, key, contract)
            recovered = submission.recover()
            if recovered is not None:
                self.receipts[key] = self.records.receipt(identity)
                self.tasks.append(recovered)
                return recovered
        if saved and saved.get("result", {}).get("status") == "completed":
            self.receipts[key] = saved
            result = TaskResult.model_validate(saved["result"])
            self.tasks.append(result)
            return result
        if contract.task_context:
            registry.argument_transform = contract.task_context.tool_arguments
        limits = getattr(self, "preparation_limits", None) if kind == "preparation" else None
        task = AgentTask(
            task_id=key,
            phase=key,
            kind=kind,
            objective=objective,
            expected_product="只提交本任务草稿；程序负责身份、版本与来源。",
            mode="research" if research else "direct",
            model_tier=tier,
            read_actions=read_actions
            if read_actions is not None
            else (limits.read_actions if limits else 6)
            if reader
            else 0,
            max_actions=self.config.task_actions,
            max_model_turns=limits.max_model_turns if limits else self.config.task_turns,
            max_repairs=0 if kind == "question_generation" else 1,
            context_soft_tokens=self.config.context_tokens,
        )
        profile = RoleProfile(
            agent_id=key,
            responsibility=objective,
            required_skills=list(skills),
            allowed_tools=[s["function"]["name"] for s in registry.schemas()],
        )
        write_json(
            self.records.root / "inputs" / (fingerprint + ".json"),
            {
                "task": task,
                "context": view,
                "contract": contract.metadata(),
            },
        )
        error = None
        if submission:
            submission.begin()
        try:
            result = await self.runtime.run_task(task, profile, view, registry, contract)
        except Exception as exc:
            # Admission refusal is not a failed scientific attempt; the coordinator
            # retains it as deferred. No model request or final receipt was produced.
            if isinstance(exc, ModelError) and exc.category == "exploration":
                if submission is None:
                    raise
                if exc.request_sent is False:
                    submission.not_sent()
                    raise
            logging.getLogger(__name__).exception("Task failed: %s", key)
            error = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            result = TaskResult(
                task_id=key, agent_id=key, status="failed", reason=f"{type(exc).__name__}: {exc}"
            )
        # This happens before any professional artifact conversion/publishing. A cached
        # final result always travels with its original read ledger and frozen directory.
        receipt = {
            **(self.records.receipt(identity) or {} if submission else {}),
            "configuration": self.configuration_for(kind),
            "result": result,
            "mapping": contract.task_context.mapping() if contract.task_context else {},
            "reading": reading()
            if reading
            else {"application": reader.coverage() if reader else {}},
            "input": fingerprint,
            "error": error,
        }
        self.records.receipt(identity, receipt)
        self.receipts[key] = self.records.receipt(identity)
        self.tasks.append(result)
        return result

    async def required(self, *args, **kwargs):
        result = await self.call(*args, **kwargs)
        if result.status != "completed" or not result.product:
            raise ValueError(f"{result.task_id}: {result.reason or result.status}")
        return result.product
