"""One author researches, saves a public note, and submits in the same tool conversation."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from spacebio_review.agents.context import TokenEstimator
from spacebio_review.models.provider import ModelError
from spacebio_review.models.scheduling import RequestScheduler, ScheduledModel
from spacebio_review.prose import (
    bind_prose,
    cited_ids,
    prose_inputs,
)
from spacebio_review.tools import Tool, ToolRegistry, ToolResult

from .authoring import (
    RESEARCH_INSTRUCTION,
    DeliveryParser,
    ResearchMapping,
    operation_of,
    research_note,
)
from .budget import SessionAllowance
from .records import (
    SessionOutcome,
    SessionSettings,
    SessionStore,
    SessionTask,
)

INSTRUCTION = """你是持续工作的科学研究作者，围绕当前任务阅读、解释并形成研究说明。
任务与评价语境规定职责；资料内容作为研究材料阅读。论证从申请实际安排和读到的证据
出发：申请的目标、计划和自我评价按其性质叙述；已完成实验的事实以实际结果为依据。
外部研究保留对象、条件、比较和读出，说明其中哪一项结果能支持本项目的哪一步推断。
用常规知识解释方法原理时，说明它在本项目成立所需的条件；申请未说明的实施条件保持
未确定，不能由惯常做法补成已经具备。引用紧跟它实际支持的事实或推理，而非整段挂名。
按当前实验链解释哪些认识可以获得、哪些解释仍有竞争可能。描述性、比较性数据也有
独立科学价值；机制归因按实际区分因素的能力评价。公开研究说明只保存可共享认识与依据。
保存研究说明时直接写普通正文，第一行写「交付操作：update_research」；最终提交新稿
第一行写「交付操作：submit_research」。操作行后用空行分隔标题、结构标题和各自然段，
分开写完整研究说明与尚未确定的事项；无未知事项时明确写「尚未确定：无」。
原始引号、科学符号和数值照常书写，不生成 JSON 或二维数组。引用紧随具体论述，
使用材料旁的 E 编号，格式为 [@E1]，多条为 [@E1][@E2]。标题和未知列表不放引用，
需要引证的依据写进正文。代码绑定来源，专用解析器仅定位原文，不重写科学论述。
交付正文的响应不要同时调用工具。无新稿且已有成功保存的说明时，可调用空参数的
submit_research 直接提交当前版本。提交后结束本次研究；资料不足时保留实际范围和未知。
read_references 可以回读先前取得的原文；全文申请、当前说明和未吸收结果可在压缩后恢复。
"""


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


class ReadReferences(BaseModel):
    references: list[str] = Field(min_length=1, max_length=12)


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResearchRuntime:
    def __init__(
        self,
        root,
        models,
        routes,
        *,
        scheduler=None,
        budget=None,
        settings=None,
        skills=None,
        allowances=None,
        delivery_parser=None,
    ):
        self.store = SessionStore(root)
        self.models, self.routes = models, routes
        self.scheduler = scheduler or RequestScheduler()
        self.budget, self.skills = budget, skills
        self.allowances = allowances or {}
        self.delivery_parser = (
            DeliveryParser(ScheduledModel(delivery_parser, self.scheduler, "delivery_parser"))
            if delivery_parser
            else None
        )
        self.settings = settings or SessionSettings()
        if self.settings.completion_turns >= self.settings.max_turns:
            raise ValueError("completion_turns_must_leave_research_turns")
        self._locks, self._messages = {}, {}
        self._owned_models = []

    @classmethod
    def from_config(cls, path, root, *, env_file=".env"):
        from spacebio_review.agents.skills import SkillLibrary
        from spacebio_review.models.provider import model_class

        from .budget import Budget
        from .config import load_config

        config = load_config(path)
        budget = Budget(root, config.budget)
        models = {
            key: model_class(value)(value, env_file=env_file, budget=budget)
            for key, value in config.models.items()
        }
        runtime = cls(
            root,
            models,
            config.roles,
            scheduler=RequestScheduler(config.concurrency),
            budget=budget,
            settings=config.session,
            skills=SkillLibrary(config.skills_dir),
            allowances=config.allowances,
            delivery_parser=models[config.auxiliary["delivery_parser"]],
        )
        runtime._owned_models = list(models.values())
        runtime.scheduler.request_budget = budget
        return runtime

    def create(self, task: SessionTask, *, readings=None, allowance=None):
        alias = self.routes[task.role]
        model = self.models[alias]
        self._check_model(model)
        bodies = {name: self.skills.get(name).body for name in task.skills}
        state = self.store.create(
            task=task,
            model_alias=alias,
            model_snapshot=model.config.model_dump(mode="json"),
            settings=self.settings,
            skill_bodies=bodies,
        )
        self._capture(state, readings or {})
        if self.budget:
            self.budget.register(
                state.session_id,
                allowance
                or self.allowances.get(task.role)
                or SessionAllowance(
                    completion_input_tokens=min(
                        self.settings.context_tokens,
                        model.config.context_window - model.config.max_tokens,
                    )
                    + (model.config.max_tokens + 2048 if self.delivery_parser else 0),
                    completion_output_tokens=model.config.max_tokens
                    + (self.delivery_parser.config.max_tokens if self.delivery_parser else 0),
                    completion_calls=self.settings.completion_turns + bool(self.delivery_parser),
                ),
            )
        self.store.save(state)
        return state.session_id

    def load(self, session_id):
        return self.store.load(session_id)

    def extend(self, session_id, *, turns=0, input_tokens=0, output_tokens=0):
        """Explicit allocation for continued work; consumed resources never reset."""
        if min(turns, input_tokens, output_tokens) < 0:
            raise ValueError("additional_resources_must_be_nonnegative")
        if session_id in self._locks and self._locks[session_id].locked():
            raise ValueError("session_is_running")
        state = self.store.load(session_id)
        state.settings.max_turns += turns
        if self.budget:
            self.budget.extend(session_id, input_tokens=input_tokens, output_tokens=output_tokens)
        self.store.save(state)

    @staticmethod
    def _check_model(model):
        # The current Messages adapter only implements schema-only requests.
        if not model.config.tools or model.config.request_protocol != "chat_completions":
            raise ValueError("v5_session_requires_tool_capable_chat_route")

    def _capture(self, state, readings):
        added = {}
        for key, snapshot in readings.items():
            if key in state.evidence:
                continue
            alias = f"E{len(state.aliases) + 1}"
            state.evidence[key] = deepcopy(snapshot)
            state.aliases[alias] = key
            added[alias] = deepcopy(snapshot)
        return added

    def _observe(self, state, content):
        state.observation_index += 1
        state.observations.append({"index": state.observation_index, **content})
        self.store.save(state)

    def _context(self, state, context=None):
        note = None
        active = set()
        working_context = context() if context else {}
        active.update(state.aliases.get(ref, ref) for ref in cited_ids(working_context))
        observations = deepcopy(state.observations)
        for row in observations:
            if row.get("kind") in {"tool", "author_delivery"} and "output" in row:
                output = row["output"]
                row["output"] = self._tool_payload(
                    ToolResult.model_validate(output["result"]),
                    output.get("readings", {}),
                    state.aliases,
                )
                if row.get("author_text_ref") and not output["result"]["ok"]:
                    row["author_text"] = self.store.author_text(row["author_text_ref"])["content"]
        if state.note_ref:
            note = deepcopy(self.store.note(state.note_ref)["note"])
            active.update(cited_ids(note))
            reverse = {value: key for key, value in state.aliases.items()}
            note["body"] = bind_prose(note["body"], reverse)
        # Initial readings are supplied in full. Later, cited originals stay beside the note;
        # unabsorbed readings remain in observations and every original stays re-readable.
        if not note:
            active.update(state.evidence)
        # Originals already present in a tool observation or in the full application
        # need only their citation directory, not another verbatim copy on restoration.
        present = {
            state.aliases[alias]
            for row in state.observations
            for alias in row.get("output", {}).get("readings", {})
            if alias in state.aliases
        }
        application = state.task.application
        if isinstance(application, dict):
            passages = {
                (source["source_id"], passage["passage_id"]): passage["text"]
                for source in application.get("sources", [])
                for passage in source.get("passages", [])
            }
            present.update(
                key
                for key, item in state.evidence.items()
                if passages.get(
                    (item["reference"]["source_id"], item["reference"].get("passage_id"))
                )
                == item.get("quote")
            )
        return {
            "task": state.task.model_dump(mode="json"),
            "skills": state.skill_bodies,
            "current_research": prose_inputs(note),
            "originals": {
                alias: state.evidence[key]
                for alias, key in state.aliases.items()
                if key in active - present
            },
            "available_readings": {
                alias: {
                    "title": state.evidence[key].get("title", ""),
                    "locator": state.evidence[key].get("locator", ""),
                    "reading_scope": state.evidence[key].get("reading_scope", ""),
                    "reference": state.evidence[key].get("reference", {}),
                }
                for alias, key in state.aliases.items()
            },
            "unabsorbed_observations": observations,
            "followup": state.followup,
            "working_context": prose_inputs(working_context),
            "resources": self.resources(state.session_id),
        }

    def resources(self, session_id):
        state = self.load(session_id)
        return {
            "session_model_turns_left": max(0, state.settings.max_turns - state.turns_used),
            "session_usage": self.budget.usage(session_id) if self.budget else None,
            "run_usage": self.budget.usage() if self.budget else None,
            "run_model_calls_left": self.budget.remaining_calls if self.budget else None,
            "run_seconds_left": self.budget.remaining_seconds() if self.budget else None,
        }

    @staticmethod
    def _tool_payload(result, readings, aliases):
        """Keep returned originals once, with their E references beside source positions."""
        output = prose_inputs(result.model_dump(mode="json"))
        reverse = {handle: alias for alias, handle in aliases.items()}

        def link(value):
            if isinstance(value, dict):
                if isinstance(value.get("cites"), list):
                    value["cites"] = [reverse.get(key, key) for key in value["cites"]]
                alias = reverse.get(value.get("evidence_handle"))
                if alias:
                    value["evidence_handle"] = alias
                    original = readings.get(alias)
                    if original and value.get("text") == original.get("quote"):
                        value.pop("text")
                        value["text_reference"] = alias
                for child in value.values():
                    link(child)
            elif isinstance(value, list):
                for child in value:
                    link(child)

        link(output)
        return {"result": output, "readings": readings}

    def _restored_messages(self, state, context=None, submission=None):
        return [
            {
                "role": "system",
                "content": INSTRUCTION + ("\n" + submission.instruction if submission else ""),
            },
            {"role": "user", "content": encoded(self._context(state, context))},
        ]

    def _submit_note(self, state):
        if not state.note_ref:
            return ToolResult(ok=False, error="research_note_required")
        state.submitted_ref = state.note_ref
        state.status, state.reason = "submitted", None
        if state.note_ref not in state.submissions:
            state.submissions.append(state.note_ref)
        self.store.save(state)
        return ToolResult(ok=True, content={"submitted": state.note_ref})

    @staticmethod
    def _author_operations(state, completing, submission):
        operations = set()
        if not completing and (submission is None or submission.allow_research):
            operations.add("update_research")
        if submission is None:
            operations.add("submit_research")
        elif hasattr(submission, "author_operations"):
            operations.update(submission.author_operations(state, completing=completing))
        return operations

    async def _finish_delivery(self, state, messages, submission):
        """Finish one persisted author draft, without spending another scientific turn."""
        pending = state.pending_delivery
        raw_ref = pending["raw_ref"]
        raw = self.store.author_text(raw_ref)
        operation = pending["operation"]
        if operation in {"update_research", "submit_research"}:
            schema, instruction, context = ResearchMapping, RESEARCH_INSTRUCTION, None
        elif submission:
            schema, instruction, context = submission.authoring_contract(raw)
        else:
            raise ValueError("assessment_delivery_requires_chief_submission")
        if pending.get("parse_ref"):
            mapping = schema.model_validate(
                self.store.delivery_parse(pending["parse_ref"])["mapping"]
            )
        else:
            if self.delivery_parser is None:
                raise ModelError("delivery_parser_route_required", category="exploration")

            async def parse(purpose):
                if self.budget:
                    with self.budget.scope(state.session_id, purpose=purpose):
                        async with asyncio.timeout(
                            self.budget.remaining_seconds(
                                session_id=state.session_id, purpose=purpose
                            )
                        ):
                            return await self.delivery_parser.parse(
                                raw, schema, instruction, context
                            )
                return await self.delivery_parser.parse(raw, schema, instruction, context)

            with self.scheduler.task(state.session_id, "delivery_parser", "authoring"):
                try:
                    mapping = await parse(pending["purpose"])
                except ModelError as exc:
                    if (
                        pending["purpose"] != "auxiliary"
                        or exc.category != "budget"
                        or exc.request_sent is not False
                    ):
                        raise
                    # The author already produced the draft. Use the closing reserve
                    # to finish it, rather than asking the author to write it again.
                    pending["purpose"] = "completion"
                    self.store.save(state)
                    mapping = await parse("completion")
            pending["parse_ref"] = self.store.save_delivery_parse(
                raw_ref, self.delivery_parser.config.model_dump(mode="json"), mapping
            )
            self.store.save(state)
        if mapping.missing_fields:
            result = ToolResult(
                ok=False,
                error="author_content_missing",
                content={"missing_fields": mapping.missing_fields, "author_text_ref": raw_ref},
            )
        else:
            try:
                if operation in {"update_research", "submit_research"}:
                    note = research_note(raw, mapping)
                    ref = self.store.save_note(
                        state,
                        note,
                        absorbed_through=raw["absorbed_through"],
                        aliases=raw["aliases"],
                        author_text_ref=raw_ref,
                    )
                    result = (
                        self._submit_note(state)
                        if operation == "submit_research"
                        else ToolResult(ok=True, content={"saved": ref, "revision": state.revision})
                    )
                else:
                    result = submission.accept_authoring(state, raw_ref, raw, mapping)
            except (ValidationError, ValueError, KeyError) as exc:
                if str(exc).startswith(
                    ("unknown_author_citation:", "author_citation_requires_preceding_text")
                ):
                    result = ToolResult(
                        ok=False,
                        error="author_content_missing",
                        content={"missing_fields": [str(exc)], "author_text_ref": raw_ref},
                    )
                else:
                    # Keep the original and failed mapping, but a resumed attempt must
                    # obtain a usable mapping rather than repeatedly applying this one.
                    pending["parse_ref"] = None
                    self.store.save(state)
                    raise ModelError(
                        f"delivery_mapping_invalid:{type(exc).__name__}", category="exploration"
                    ) from exc
        state.pending_delivery = None
        payload = self._tool_payload(result, {}, state.aliases)
        self._observe(
            state,
            {
                "kind": "author_delivery",
                "name": operation,
                "author_text_ref": raw_ref,
                "output": payload,
            },
        )
        messages.append(
            {"role": "user", "content": encoded({"event": "author_delivery", **payload})}
        )
        return result

    def _tools(self, state, external, absorbed_through, *, completing, submission=None):
        async def submit(args):
            return self._submit_note(state)

        async def read(args):
            if any(key not in state.aliases for key in args.references):
                return ToolResult(ok=False, error="unknown_reading_reference")
            return ToolResult(
                ok=True,
                content={key: state.evidence[state.aliases[key]] for key in args.references},
            )

        registry = ToolRegistry()
        if not completing:
            registry.include(external)
        for tool in (
            Tool(
                "submit_research",
                "无新正文时提交已经成功保存的当前研究说明；新稿用普通正文交付。",
                NoArguments,
                submit,
                read_only=False,
            ),
            Tool("read_references", "按 E 编号回读已取得的原文。", ReadReferences, read),
        ):
            # Closing is a submission operation. Reading or saving an intermediate
            # version here would require another author request that is not reserved.
            if submission and (tool.name == "submit_research" or not submission.allow_research):
                continue
            if not completing or (not submission and tool.name == "submit_research"):
                registry.add(tool)
        if submission:
            registry.include(submission.tools(state, absorbed_through, completing=completing))
        return registry

    async def run(self, session_id, *, tools=None, followup=None, context=None, submission=None):
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            state = self.store.load(session_id)
            model = self.models[state.model_alias]
            self._check_model(model)
            if state.model_snapshot != model.config.model_dump(mode="json"):
                raise ValueError("session_model_configuration_changed")
            if (
                state.status == "submitted"
                and not state.pending_delivery
                and followup is None
                and (submission is None or submission.completed(state))
            ):
                return self._outcome(state)
            messages = self._messages.get(session_id) or self._restored_messages(
                state, context, submission
            )
            if submission and session_id in self._messages:
                messages.append({"role": "user", "content": submission.instruction})
            if followup is not None:
                state.followup, state.submitted_ref = followup, None
                messages.append({"role": "user", "content": followup})
            state.status, state.reason = "running", None
            self.store.save(state)
            try:
                if self.budget:
                    self.budget.activate(session_id)
                await self._run(
                    state, model, tools or ToolRegistry(), messages, context, submission, followup
                )
            except asyncio.CancelledError:
                state.status, state.reason = "partial", "session_cancelled"
                raise
            except ModelError as exc:
                state.status = "partial" if exc.category in {"budget", "exploration"} else "failed"
                state.reason = str(exc)
            except TimeoutError:
                state.status, state.reason = "partial", "session_time_exhausted"
            except Exception as exc:
                state.status, state.reason = "failed", type(exc).__name__
                raise
            finally:
                self.store.save(state)
                if state.status != "submitted":
                    # Interrupted tool batches are reconstructed from public observations,
                    # never replayed as dangling tool calls or as private model reasoning.
                    self._messages.pop(session_id, None)
                if self.budget:
                    if state.status == "submitted":
                        self.budget.finish(session_id)
                    self.budget.save()
            return self._outcome(state)

    async def _run(
        self, state, model, external, messages, context=None, submission=None, followup=None
    ):
        estimator = TokenEstimator(image_tokens=model.config.image_tokens)
        settings = state.settings
        closing = False
        if state.pending_delivery:
            pending = state.pending_delivery
            raw = self.store.author_text(pending["raw_ref"])
            messages.append({"role": "assistant", "content": raw["content"]})
            await self._finish_delivery(state, messages, submission)
            closing = pending["purpose"] == "completion"
            if state.status == "submitted":
                if followup is None:
                    self._messages[state.session_id] = messages
                    return
                # A continuation is new work, even when the previous submission
                # was interrupted just before clearing its pending marker.
                state.status, state.submitted_ref = "running", None
                closing = False
                self.store.save(state)
                if self.budget:
                    self.budget.finish(state.session_id)
                    self.budget.activate(state.session_id)
                messages.append({"role": "user", "content": followup})
        while state.turns_used < settings.max_turns:
            reserve = submission.completion_turns if submission else settings.completion_turns
            completing = closing or state.turns_used >= settings.max_turns - reserve
            observed = state.observation_index
            registry = self._tools(
                state, external, observed, completing=completing, submission=submission
            )
            schemas = registry.schemas()
            request_messages = messages
            if completing:
                request_messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": submission.closing_instruction(state)
                        if submission
                        else (
                            "这是本次研究的提交轮。请直接输出普通正文，首行写"
                            "「交付操作：submit_research」，以已有材料完成当前认识及未知。"
                            "无新正文时可用空参数 submit_research 提交已保存版本。"
                        ),
                    },
                ]
            measured = estimator.measure(
                request_messages, schemas, output_reserve=model.config.max_tokens
            )
            if measured["input_tokens"] > settings.context_tokens or (
                measured["total_tokens"] > model.config.context_window
            ):
                messages = self._restored_messages(state, context, submission)
                state.context_compactions += 1
                request_messages = messages
                if completing:
                    request_messages = [
                        *messages,
                        {
                            "role": "user",
                            "content": submission.closing_instruction(state)
                            if submission
                            else (
                                "请以已有材料输出普通正文，首行「交付操作：submit_research」，"
                                "保留未解决问题。"
                            ),
                        },
                    ]
                measured = estimator.measure(
                    request_messages, schemas, output_reserve=model.config.max_tokens
                )
            if measured["total_tokens"] > model.config.context_window:
                state.status, state.reason = "partial", "context_window_exceeded"
                return
            state.turns_used += 1
            self.store.save(state)

            async def call():
                return await self.scheduler.call(
                    lambda: model.complete(request_messages, schemas),
                    name=state.model_alias,
                    model_id=model.config.model,
                )

            purpose = "completion" if completing else "research"
            try:
                with self.scheduler.task(state.session_id, state.task.role, "research"):
                    if self.budget:
                        with self.budget.scope(state.session_id, purpose=purpose):
                            async with asyncio.timeout(
                                self.budget.remaining_seconds(
                                    session_id=state.session_id, purpose=purpose
                                )
                            ):
                                reply = await call()
                    else:
                        reply = await call()
            except ModelError as exc:
                if exc.category == "budget" and exc.request_sent is False:
                    state.turns_used -= 1
                if exc.category == "budget" and not completing:
                    closing = True
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "研究资源已到收束预留，请依据现有材料直接提交，保留尚未解决的问题。"
                            ),
                        }
                    )
                    continue
                raise
            except TimeoutError:
                if completing:
                    raise
                closing = True
                messages.append(
                    {
                        "role": "user",
                        "content": submission.instruction
                        if submission
                        else ("研究时间已进入收束预留，请依据已有材料直接提交当前说明及未知。"),
                    }
                )
                continue
            estimator.observe(measured, reply.prompt_tokens)
            if reply.finish_reason not in {"stop", "tool_calls"}:
                state.status, state.reason = "partial", "model_finish_" + reply.finish_reason
                return
            # Provider-specific reasoning fields may be needed for the next tool turn.
            # They remain in this in-memory conversation only, never in the session archive.
            messages.append(reply.message)
            calls = reply.message.get("tool_calls") or []
            content = reply.message.get("content") or ""
            operation = operation_of(content)
            if operation:
                raw_ref = self.store.save_author_text(
                    state, content, operation, absorbed_through=observed
                )
                if calls:
                    result = ToolResult(ok=False, error="deliver_text_without_tool_calls")
                    messages.extend(
                        {
                            "role": "tool",
                            "tool_call_id": item["id"],
                            "content": encoded(result.model_dump(mode="json")),
                        }
                        for item in calls
                    )
                    self._observe(
                        state,
                        {
                            "kind": "author_delivery",
                            "author_text_ref": raw_ref,
                            "output": self._tool_payload(result, {}, state.aliases),
                        },
                    )
                    messages.append({"role": "user", "content": "请单独输出带操作首行的交付正文。"})
                    continue
                allowed = self._author_operations(state, completing, submission)
                if operation not in allowed:
                    result = ToolResult(
                        ok=False,
                        error="author_operation_unavailable",
                        content={"available_operations": sorted(allowed)},
                    )
                    self._observe(
                        state,
                        {
                            "kind": "author_delivery",
                            "author_text_ref": raw_ref,
                            "output": self._tool_payload(result, {}, state.aliases),
                        },
                    )
                    messages.append({"role": "user", "content": encoded(result.model_dump())})
                    continue
                pending = {
                    "raw_ref": raw_ref,
                    "operation": operation,
                    "purpose": "completion"
                    if completing or operation in {"submit_research", "submit_assessment"}
                    else "auxiliary",
                    "parse_ref": None,
                }
                state.pending_delivery = pending
                self.store.save(state)
                await self._finish_delivery(state, messages, submission)
                closing = closing or pending["purpose"] == "completion"
                if state.status == "submitted":
                    self._messages[state.session_id] = messages
                    return
                continue
            if not calls:
                if content:
                    self._observe(state, {"kind": "author_text", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": submission.instruction
                        if submission
                        else (
                            "请输出普通正文，首行用「交付操作：update_research」保存完整进展，"
                            "或用「交付操作：submit_research」提交最终研究说明。"
                        ),
                    }
                )
                continue
            for call_item in calls:
                function = call_item["function"]
                name = function["name"]

                async def execute():
                    return await registry.execute(name, function["arguments"])

                kind = "delegation" if registry.delegates(name) else "tool"
                with self.scheduler.task(state.session_id, state.task.role, "research"):
                    local = {"update_research", "submit_research", "read_references"}
                    if submission:
                        local.update(submission.local_tools)
                    if name in local:
                        # Saving or returning already-held text needs no research allocation.
                        # In particular, timeout(0) at the reserve must not cancel this commit.
                        result = await self.scheduler.call(execute, kind=kind, name=name)
                    elif kind == "delegation":
                        # Child sessions enforce their own deadlines and protect this
                        # author's completion reserve. An outer auxiliary deadline would
                        # subtract the children's reserves again and cancel their closing.
                        result = await self.scheduler.call(execute, kind=kind, name=name)
                    elif self.budget:
                        with self.budget.scope(state.session_id, purpose="auxiliary"):
                            try:
                                async with asyncio.timeout(
                                    self.budget.remaining_seconds(
                                        session_id=state.session_id, purpose="auxiliary"
                                    )
                                ):
                                    result = await self.scheduler.call(
                                        execute, kind=kind, name=name
                                    )
                            except TimeoutError:
                                closing = True
                                result = ToolResult(ok=False, error="research_time_reserve_reached")
                    else:
                        result = await self.scheduler.call(execute, kind=kind, name=name)
                readings = {}
                for view in registry.evidence_views:
                    readings.update(self._capture(state, view()))
                payload = self._tool_payload(result, readings, state.aliases)
                messages.append(
                    {"role": "tool", "tool_call_id": call_item["id"], "content": encoded(payload)}
                )
                if name not in {"update_research", "submit_research"} or not result.ok:
                    observation = {"kind": "tool", "name": name, "output": payload}
                    if name in local - {"read_references"} and not result.ok:
                        # A rejected public draft remains available to its author on restart.
                        # It is not parsed, repaired, or treated as a submitted note.
                        observation["attempted_arguments"] = function["arguments"]
                    self._observe(state, observation)
                else:
                    self.store.save(state)
                if state.status == "submitted":
                    # Submission ends execution. Resolve any remaining envelope entries
                    # without running more tools after the final scientific artifact.
                    remaining = calls[calls.index(call_item) + 1 :]
                    messages.extend(
                        {
                            "role": "tool",
                            "tool_call_id": c["id"],
                            "content": encoded({"ok": False, "error": "session_already_submitted"}),
                        }
                        for c in remaining
                    )
                    self._messages[state.session_id] = messages
                    return
        state.status, state.reason = "partial", "session_turn_budget_exhausted"

    @staticmethod
    def _outcome(state):
        return SessionOutcome(
            session_id=state.session_id,
            status=state.status,
            note_ref=state.note_ref,
            reason=state.reason,
            turns_used=state.turns_used,
        )

    async def close(self):
        for model in self._owned_models:
            await model.close()
        if self.budget:
            self.budget.save()
