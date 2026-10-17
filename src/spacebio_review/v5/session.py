"""One author researches, saves a public note, and submits in the same tool conversation."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from spacebio_review.agents.context import TokenEstimator
from spacebio_review.agents.diagnostics import digest
from spacebio_review.models.provider import ModelError
from spacebio_review.models.scheduling import RequestScheduler, ScheduledModel
from spacebio_review.persistence import write_json
from spacebio_review.prose import (
    bind_prose,
    cited_ids,
    prose_inputs,
)
from spacebio_review.tools import Tool, ToolRegistry, ToolResult

from .authoring import (
    DeliveryParser,
    research_note,
)
from .budget import CompletionReserve, SessionAllowance
from .records import (
    SessionOutcome,
    SessionSettings,
    SessionStore,
    SessionTask,
)

SCIENTIFIC_INSTRUCTION = """你是持续工作的科学研究作者，围绕任务阅读、比较并形成专业认识。
研究首先解释项目可能获得什么知识、为什么值得获取，以及其实现路线有哪些依据。
根据实际对象、比较、读出和研究阶段作判断；观察、描述与新环境中的方法应用也有科学
价值。合理的专业推断应说明来由，有依据时明确肯定，关键问题则解释它影响哪一步认识。
用申请原文说明实际安排，用外部研究的对象、条件和结果说明它与本项目的关系。
申请的计划和自我评价按其性质叙述；未提供的信息不等于不存在，未找到先例也不等于
全球首次。引用就近对应其支持的事实或解释。公开研究说明保存可共享认识及依据。
"""

INSTRUCTION = (
    SCIENTIFIC_INSTRUCTION
    + """研究阶段自主使用工具获取和比较材料。
已有材料足以形成当前意见，或剩余问题不再值得
继续投入时，调用 finish_research 并简述结束理由及未决事项，程序随后安排科学正文写作。
主审需要修订当前工作理解时可调用 update_understanding，程序安排一次普通正文写作。
专家结果返回后直接继续研究或评价，不要求每批重新写一份吸收稿。
研究工具返回和工具伴随文字属于过程观察；正式科学成果在专门写作阶段保存。
read_references 可以回读先前取得的原文；申请、已完成科学说明和未吸收结果持续可读。
"""
)

RESEARCH_AUTHOR_INSTRUCTION = """现在写作公开研究说明。直接输出普通中文正文，不写操作口令、
JSON 或工具调用。用空行分隔自然段，完整表达科学认识、依据及仍待研究的问题。
原样使用引号、数值和科学符号。引用紧随实际支持的论述，使用 [E1]、[@E1]
或 [@E1, @E2]。整份原稿由程序直接保存，标题和小标题也保留在正文内。
"""

INITIAL_UNDERSTANDING = """阅读申请，形成初始项目理解：解释实际研究对象、实验安排、
测量读出、拟获得的认识及科学价值，连起已有基础和计划采用的实现路径。
背景文献只说明申请如何引用它；尚未阅读外部原文时，其样本和具体结论保持为待研究内容。
据此说明哪些外部研究最能帮助判断知识增量和实现可能。申请原文是项目事实依据。
"""


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


class ReadReferences(BaseModel):
    references: list[str] = Field(min_length=1, max_length=12)


class FinishResearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(description="已有认识足以成稿或结束研究的理由及仍未解决的事项")


class UpdateUnderstanding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, description="本次需要更新或修订工作理解的原因。")


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
        session_overrides=None,
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
        self.session_overrides = session_overrides or {}
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
            session_overrides=config.session_overrides,
            skills=SkillLibrary(config.skills_dir),
            allowances=config.allowances,
            delivery_parser=models[config.auxiliary["delivery_parser"]],
        )
        runtime._owned_models = list(models.values())
        runtime.scheduler.request_budget = budget
        return runtime

    def create(self, task: SessionTask, *, readings=None, allowance=None, budget_owner=None):
        alias = self.routes.get(task.role) or self.routes["researcher"]
        model = self.models[alias]
        self._check_model(model)
        bodies = {name: self.skills.get(name).body for name in task.skills} if self.skills else {}
        settings = self.settings_for(task.role)
        state = self.store.create(
            task=task,
            model_alias=alias,
            model_snapshot=model.config.model_dump(mode="json"),
            settings=settings,
            skill_bodies=bodies,
            budget_owner=budget_owner,
        )
        self._capture(state, readings or {})
        if self.budget and budget_owner is None:
            parser_required = task.role == "chief" and self.delivery_parser is not None
            self.budget.register(
                state.session_id,
                allowance
                or self.allowances.get(task.role)
                or SessionAllowance(
                    completion_input_tokens=min(
                        settings.context_tokens,
                        model.config.context_window - model.config.max_tokens,
                    )
                    + (model.config.max_tokens + 2048 if parser_required else 0),
                    completion_output_tokens=model.config.max_tokens
                    + (self.delivery_parser.config.max_tokens if parser_required else 0),
                    completion_calls=settings.completion_turns + parser_required,
                ),
            )
        self.store.save(state)
        return state.session_id

    def settings_for(self, role):
        override = self.session_overrides.get(role)
        if override is None and role == "source_reader":
            override = self.session_overrides.get("researcher")
        values = override.model_dump(exclude_none=True) if override else {}
        settings = self.settings.model_copy(update=values)
        if settings.completion_turns >= settings.max_turns:
            raise ValueError("completion_turns_must_leave_research_turns")
        return settings

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
            self.budget.extend(
                state.budget_owner or session_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
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
        notes = []
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
        note_refs = [state.note_ref]
        for ref in dict.fromkeys(ref for ref in note_refs if ref):
            note = deepcopy(self.store.note(ref)["note"])
            active.update(cited_ids(note))
            reverse = {value: key for key, value in state.aliases.items()}
            note["body"] = bind_prose(note["body"], reverse)
            notes.append({"note_ref": ref, "note": note})
        # Initial readings are supplied in full. Later, cited originals stay beside the note;
        # unabsorbed readings remain in observations and every original stays re-readable.
        if not notes:
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
            "initial_understanding_ref": state.initial_understanding_ref,
            "current_research": prose_inputs(notes),
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
            "working_context": self._tool_payload(
                ToolResult(ok=True, content=working_context), {}, state.aliases
            )["result"]["content"],
            "resources": self.resources(state.session_id),
        }

    def resources(self, session_id):
        state = self.load(session_id)
        return {
            "session_model_turns_left": max(0, state.settings.max_turns - state.turns_used),
            "session_usage": self.budget.usage(state.budget_owner or session_id)
            if self.budget
            else None,
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
                "content": INSTRUCTION
                + (
                    "\n当前只阅读申请正文。通读研究目标、对象、对照、阶段和读出后"
                    "调用 finish_research，"
                    "程序将先安排项目理解写作，再开放外部研究。"
                    if state.authoring_phase == "application_reading"
                    else ""
                ),
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

    def _delivery_contract(self, raw, submission):
        if submission:
            return submission.authoring_contract(raw)
        raise ValueError("assessment_delivery_requires_chief_submission")

    def _completion_plan(
        self, state, model, messages, schemas, submission=None, *, include_request=False
    ):
        """Price the remaining author/mapper steps, using actual saved text when available."""
        if not self.budget:
            return None
        operations = submission.completion_operations(state) if submission else ["submit_research"]
        costs, names = [], []

        def author(operation):
            # A submission may provide the context for its final manuscript.
            specific = (
                submission.completion_input(state, operation)
                if submission and hasattr(submission, "completion_input")
                else None
            )
            costs.append(self.budget.measure(model.config, specific or messages, []))
            names.append(operation + ":author")

        def parser_cost(operation, raw=None):
            if operation in {"update_research", "submit_research"} or not self.delivery_parser:
                return
            preview = raw or {
                "operation": operation,
                "paragraphs": [],
                "aliases": state.aliases,
            }
            schema, instruction, context = self._delivery_contract(preview, submission)
            parser_messages, response_schema = self.delivery_parser.request(
                preview, schema, instruction, context
            )
            cost = self.budget.measure(
                self.delivery_parser.config, parser_messages, response_schema=response_schema
            )
            # Before authorship, only its output is unknown; the parser never receives
            # the research history. Once saved, measure the exact numbered manuscript.
            if raw is None:
                cost["input_tokens"] += int(model.config.max_tokens * 1.1)
            return cost

        def parser(operation, raw=None):
            cost = parser_cost(operation, raw)
            if cost is None:
                return
            costs.append(cost)
            names.append(operation + ":parser")

        pending = state.pending_delivery
        if pending:
            raw = self.store.author_text(pending["raw_ref"])
            if not pending.get("parse_ref"):
                parser(raw["operation"], raw)
            if raw["operation"] in operations:
                operations = operations[operations.index(raw["operation"]) + 1 :]
        for operation in operations:
            author(operation)
            if submission is None or operation in submission.parsed_operations:
                parser(operation)
        if state.budget_owner:
            if include_request:
                # Before another reading turn, also leave room for its scientific
                # manuscript. Parent completion remains protected.
                costs.insert(0, self.budget.measure(model.config, messages, schemas))
            return self.budget.research_capacity(state.budget_owner, costs)
        allowance = self.budget.data["sessions"][state.session_id]["allowance"]
        return self.budget.update_completion(
            state.session_id,
            CompletionReserve(
                input_tokens=sum(cost["input_tokens"] for cost in costs),
                output_tokens=sum(cost["output_reserve"] for cost in costs),
                calls=len(costs),
                seconds=allowance["completion_seconds"]
                * len(costs)
                / max(1, allowance["completion_calls"]),
                steps=names,
            ),
        )

    async def _finish_delivery(self, state, messages, submission):
        """Finish one persisted author draft, without spending another scientific turn."""
        pending = state.pending_delivery
        raw_ref = pending["raw_ref"]
        raw = self.store.author_text(raw_ref)
        operation = pending["operation"]

        def record(result):
            if result.ok:
                state.pending_delivery = None
                state.authoring_phase = None
            else:
                # Failed mappings stay in the artifact history, not as an applicable
                # mapping. A later explicit resume parses this same author manuscript.
                pending["parse_ref"] = None
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

        if operation in {"update_research", "submit_research"}:
            try:
                note = research_note(
                    raw, title=raw.get("title") or self._research_title(state, raw)
                )
            except ValueError as exc:
                raise ModelError(str(exc), category="exploration") from exc
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
            state.pending_delivery = None
            state.authoring_phase = None
            state.phase_reason = None
            self.store.save(state)
            return result

        schema, instruction, context = self._delivery_contract(raw, submission)
        if pending.get("parse_ref"):
            try:
                mapping = schema.model_validate(
                    self.store.delivery_parse(pending["parse_ref"])["mapping"]
                )
            except (ValidationError, ValueError, KeyError) as exc:
                pending["parse_ref"] = None
                self.store.save(state)
                raise ModelError(
                    f"delivery_mapping_invalid:{type(exc).__name__}", category="exploration"
                ) from exc
        else:
            if self.delivery_parser is None:
                raise ModelError("delivery_parser_route_required", category="exploration")

            async def parse(purpose):
                if self.budget:
                    reason = self._completion_plan(
                        state, self.models[state.model_alias], messages, [], submission
                    )
                    if reason:
                        raise ModelError(reason, category="budget", request_sent=False)
                    owner = state.budget_owner or state.session_id
                    if self.budget.remaining_seconds(session_id=owner, purpose=purpose) <= 0:
                        raise ModelError(
                            "delivery_time_reserve_reached", category="budget", request_sent=False
                        )
                    with self.budget.scope(owner, purpose=purpose):
                        async with asyncio.timeout(
                            self.budget.remaining_seconds(session_id=owner, purpose=purpose)
                        ):
                            return await self.delivery_parser.parse(
                                raw, schema, instruction, context
                            )
                return await self.delivery_parser.parse(raw, schema, instruction, context)

            with self.scheduler.task(state.session_id, "delivery_parser", "authoring"):
                mapping = await parse(pending["purpose"])
            pending["parse_ref"] = self.store.save_delivery_parse(
                raw_ref, self.delivery_parser.config.model_dump(mode="json"), mapping
            )
            self.store.save(state)
        if mapping.missing_fields:
            result = ToolResult(
                ok=False,
                error="delivery_mapping_incomplete",
                content={"missing_fields": mapping.missing_fields, "author_text_ref": raw_ref},
            )
        else:
            try:
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
        return record(result)

    @staticmethod
    def _research_title(state, raw):
        return {
            "initial_understanding": "项目初始理解",
            "organize_research": "当前研究理解",
        }.get(raw.get("phase"), state.task.objective)

    def _tools(self, state, external, absorbed_through=None, *, completing=False, submission=None):
        async def finish(args):
            if state.authoring_phase == "application_reading":
                state.authoring_phase = "initial_understanding"
            else:
                state.research_finished = True
            state.phase_reason = args.reason
            self.store.save(state)
            return ToolResult(ok=True, content={"next": "scientific_manuscript"})

        async def update(args):
            state.authoring_phase = "organize_research"
            state.phase_reason = args.reason
            self.store.save(state)
            return ToolResult(ok=True, content={"next": "update_working_manuscript"})

        async def read(args):
            if any(key not in state.aliases for key in args.references):
                return ToolResult(ok=False, error="unknown_reading_reference")
            return ToolResult(
                ok=True,
                content={key: state.evidence[state.aliases[key]] for key in args.references},
            )

        registry = ToolRegistry()
        if completing:
            return registry
        registry.include(external(state) if callable(external) else external)
        if state.authoring_phase == "application_reading":
            # A long application must be read before the initial manuscript; this
            # stage has no external search or scientific delegation responsibilities.
            allowed = {"browse_materials", "read_materials", "locate_materials"}
            registry = registry.select(
                [
                    row["function"]["name"]
                    for row in registry.schemas()
                    if row["function"]["name"] in allowed
                ]
            )
        registry.add(
            Tool(
                "finish_research",
                "结束开放研究并由程序安排科学正文写作。",
                FinishResearch,
                finish,
                read_only=False,
            )
        )
        registry.add(Tool("read_references", "按 E 编号回读已取得的原文。", ReadReferences, read))
        if state.task.role == "chief" and state.authoring_phase != "application_reading":
            registry.add(
                Tool(
                    "update_understanding",
                    "按需要更新当前工作理解；程序随后安排普通正文写作。",
                    UpdateUnderstanding,
                    update,
                    read_only=False,
                )
            )
        return registry

    async def _execute_tool(self, state, registry, name, arguments):
        result = await registry.execute(name, arguments)
        # This concrete delegation boundary also produced malformed long JSON in
        # local author probes. Only syntax failures use the existing parser route;
        # identity/schema and scientific decisions remain with their normal owners.
        syntax_error = result.error == "invalid_arguments" and any(
            row.get("type") == "json_invalid" for row in result.content or []
        )
        if name != "research_topics" or not syntax_error or self.delivery_parser is None:
            return result
        from .chief import TopicBatch
        from .delegation import parse_delegation_arguments

        raw = {"session_id": state.session_id, "tool": name, "arguments": arguments}
        raw_ref = "delegation-" + digest(raw)
        write_json(self.store._path("delegation-inputs", raw_ref), raw)
        try:
            with self.scheduler.task(state.session_id, "delivery_parser", "delegation_structure"):
                if self.budget:
                    with self.budget.scope(
                        state.budget_owner or state.session_id, purpose="auxiliary"
                    ):
                        remaining = self.budget.remaining_seconds(
                            session_id=state.budget_owner or state.session_id, purpose="auxiliary"
                        )
                        if remaining <= 0:
                            raise ModelError(
                                "delegation_time_reserve_reached",
                                category="budget",
                                request_sent=False,
                            )
                        async with asyncio.timeout(remaining):
                            parsed = await parse_delegation_arguments(
                                self.delivery_parser, arguments, TopicBatch
                            )
                else:
                    parsed = await parse_delegation_arguments(
                        self.delivery_parser, arguments, TopicBatch
                    )
        except (ModelError, TimeoutError) as exc:
            return ToolResult(
                ok=False,
                error=str(exc) or "delegation_parse_timeout",
                content={"delegation_input_ref": raw_ref},
            )
        write_json(
            self.store._path("delegation-parses", raw_ref),
            {
                "delegation_input_ref": raw_ref,
                "parser": self.delivery_parser.config.model_dump(mode="json"),
                "arguments": parsed.model_dump(mode="json"),
            },
        )
        return await registry.execute(name, parsed.model_dump_json())

    async def _call_model(self, state, model, messages, schemas, *, purpose, stage):
        async def call():
            return await self.scheduler.call(
                lambda: model.complete(messages, schemas),
                name=state.model_alias,
                model_id=model.config.model,
            )

        state.turns_used += 1
        self.store.save(state)
        try:
            with self.scheduler.task(state.session_id, state.task.role, stage):
                if self.budget:
                    owner = state.budget_owner or state.session_id
                    with self.budget.scope(owner, purpose=purpose):
                        async with asyncio.timeout(
                            self.budget.remaining_seconds(session_id=owner, purpose=purpose)
                        ):
                            return await call()
                return await call()
        except ModelError as exc:
            if exc.category == "budget" and exc.request_sent is False:
                state.turns_used -= 1
            raise

    def _writing_messages(self, state, phase, operation, context, submission, instruction=None):
        specific = (
            submission.completion_input(state, operation)
            if submission and operation not in {"update_research", "submit_research"}
            else None
        )
        if specific:
            return specific
        author_instruction = (
            submission.instruction + "\n" + submission.closing_instruction(state)
            if submission and operation not in {"update_research", "submit_research"}
            else RESEARCH_AUTHOR_INSTRUCTION
            + "\n"
            + (
                instruction
                or {
                    "initial_understanding": INITIAL_UNDERSTANDING,
                    "organize_research": "根据原申请、当前工作稿和新成果，更新完整研究理解。"
                    "连起可能获得的知识与价值、主要判断和下一步值得研究的问题。"
                    "修订此前不合适的理解，保留仍适用的认识和依据；"
                    "无需重复全部论文说明，本稿将成为当前工作理解。",
                    "final_manuscript": "依据本任务实际读到的材料，形成完整研究说明"
                    "及尚未解决的问题。",
                }.get(phase, "")
            )
        )
        if phase == "organize_research" and state.phase_reason:
            author_instruction += "\n本次更新目的：" + state.phase_reason
        return [
            {"role": "system", "content": SCIENTIFIC_INSTRUCTION + "\n" + author_instruction},
            {"role": "user", "content": encoded(self._context(state, context))},
        ]

    async def _write_phase(
        self, state, model, *, phase, operation, context=None, submission=None, instruction=None
    ):
        """One code-selected manuscript: no tool schemas and no author operation protocol."""
        state.authoring_phase = phase
        self.store.save(state)
        messages = self._writing_messages(state, phase, operation, context, submission, instruction)
        absorbed_through = state.observation_index
        research = operation in {"update_research", "submit_research"}
        replaces = (
            (state.note_ref,)
            if research and phase == "organize_research" and state.note_ref
            else ()
        )
        reason = self._completion_plan(
            state,
            model,
            messages,
            [],
            submission,
            include_request=phase != "final_manuscript",
        )
        if reason:
            raise ModelError(reason, category="budget", request_sent=False)
        if self.budget:
            measured = self.budget.measure(model.config, messages, [])
        else:
            measured = TokenEstimator(image_tokens=model.config.image_tokens).measure(
                messages, [], output_reserve=model.config.max_tokens
            )
        if measured["total_tokens"] > model.config.context_window:
            raise ModelError("context_window_exceeded", category="exploration", request_sent=False)
        final = phase == "final_manuscript"
        purpose = "completion" if final and not state.budget_owner else "research"
        reply = await self._call_model(state, model, messages, [], purpose=purpose, stage=phase)
        content = reply.message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ModelError("empty_author_manuscript", category="exploration")
        raw_ref = self.store.save_author_text(
            state,
            content,
            operation,
            absorbed_through=absorbed_through,
            phase=phase,
            title=self._research_title(state, {"phase": phase}) if research else None,
            replaces=replaces,
        )
        # Persist the original before parsing, including a truncated original for inspection.
        if reply.finish_reason != "stop" or reply.message.get("tool_calls"):
            self._observe(state, {"kind": "incomplete_manuscript", "author_text_ref": raw_ref})
            raise ModelError("incomplete_author_manuscript", category="exploration")
        state.pending_delivery = {
            "raw_ref": raw_ref,
            "operation": operation,
            "purpose": "completion" if final and not state.budget_owner else "auxiliary",
            "parse_ref": None,
        }
        self.store.save(state)
        result = await self._finish_delivery(state, messages, submission)
        if not result.ok:
            raise ModelError(result.error or "author_content_missing", category="exploration")
        self._messages.pop(state.session_id, None)

    async def run(
        self,
        session_id,
        *,
        tools=None,
        followup=None,
        context=None,
        submission=None,
        initial_instruction=None,
    ):
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
            messages = self._messages.get(session_id) or self._restored_messages(state, context)
            if followup is not None:
                state.followup, state.submitted_ref = followup, None
                state.research_finished = False
                state.authoring_phase = None
                messages.append({"role": "user", "content": followup})
            if submission and not submission.allow_research:
                state.research_finished = True
            state.status, state.reason = "running", None
            self.store.save(state)
            try:
                if self.budget and not state.budget_owner:
                    self.budget.activate(session_id)
                await self._run(
                    state,
                    model,
                    tools or ToolRegistry(),
                    messages,
                    context,
                    submission,
                    followup,
                    initial_instruction,
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
                    self._messages.pop(session_id, None)
                if self.budget:
                    if state.status == "submitted" and not state.budget_owner:
                        self.budget.finish(session_id)
                    self.budget.save()
            return self._outcome(state)

    async def _run(
        self,
        state,
        model,
        external,
        messages,
        context=None,
        submission=None,
        followup=None,
        initial_instruction=None,
    ):
        estimator = TokenEstimator(image_tokens=model.config.image_tokens)
        closing = False
        if (
            state.task.role == "chief"
            and not state.initial_understanding_ref
            and not state.authoring_phase
            and not state.pending_delivery
            and not state.research_finished
            and isinstance(state.task.application, dict)
            and state.task.application.get("full_text_in_context") is False
        ):
            state.authoring_phase = "application_reading"
            self.store.save(state)
            messages = self._restored_messages(state, context)
        if state.pending_delivery:
            result = await self._finish_delivery(state, messages, submission)
            if not result.ok:
                raise ModelError(result.error or "author_content_missing", category="exploration")
            if state.status == "submitted":
                if followup is None:
                    return
                state.status, state.submitted_ref = "running", None
                state.research_finished = False
                self.store.save(state)
            messages = self._restored_messages(state, context)

        while state.turns_used < state.settings.max_turns:
            # Initial and final manuscripts are explicit; intermediate writing is optional.
            forced = closing or state.turns_used >= state.settings.max_turns - 1
            phase = state.authoring_phase
            if phase == "application_reading":
                phase = "final_manuscript" if forced else None
            if phase is None and not forced and not (submission and not submission.allow_research):
                if (
                    state.task.role == "chief"
                    and not state.initial_understanding_ref
                    and state.authoring_phase != "application_reading"
                ):
                    phase = "initial_understanding"
            if phase is None and (forced or state.research_finished):
                phase = "final_manuscript"
            if phase:
                operation = "update_research"
                if phase == "final_manuscript":
                    operations = (
                        submission.completion_operations(state)
                        if submission
                        else ["submit_research"]
                    )
                    if not operations:
                        state.status, state.reason = "submitted", None
                        return
                    operation = operations[0]
                instruction = initial_instruction if phase == "initial_understanding" else None
                try:
                    await self._write_phase(
                        state,
                        model,
                        phase=phase,
                        operation=operation,
                        context=context,
                        submission=submission,
                        instruction=instruction,
                    )
                except ModelError as exc:
                    if (
                        exc.category == "budget"
                        and exc.request_sent is False
                        and phase != "final_manuscript"
                        and not state.pending_delivery
                    ):
                        state.authoring_phase = None
                        closing = True
                        continue
                    raise
                if state.status == "submitted":
                    return
                messages = self._restored_messages(state, context)
                state.context_compactions += 1
                self.store.save(state)
                continue

            registry = self._tools(state, external)
            schemas = registry.schemas()
            measured = (
                self.budget.measure(model.config, messages, schemas)
                if self.budget
                else estimator.measure(messages, schemas, output_reserve=model.config.max_tokens)
            )
            if measured["input_tokens"] > state.settings.context_tokens:
                restored = self._restored_messages(state, context)
                restored_measure = (
                    self.budget.measure(model.config, restored, schemas)
                    if self.budget
                    else estimator.measure(
                        restored, schemas, output_reserve=model.config.max_tokens
                    )
                )
                if restored_measure["input_tokens"] < measured["input_tokens"]:
                    messages, measured = restored, restored_measure
                    state.context_compactions += 1
                if measured["input_tokens"] > state.settings.context_tokens and state.observations:
                    # save_note retains only observations beyond its absorbed_through
                    # watermark. New results can justify another organization even
                    # when no intervening manuscript has changed the note revision.
                    state.authoring_phase = "organize_research"
                    state.phase_reason = "将新增研究材料整理为当前完整工作理解。"
                    continue
            if measured["total_tokens"] > model.config.context_window:
                raise ModelError(
                    "context_window_exceeded", category="exploration", request_sent=False
                )
            reason = self._completion_plan(
                state, model, messages, schemas, submission, include_request=True
            )
            if reason:
                closing = True
                continue
            try:
                reply = await self._call_model(
                    state, model, messages, schemas, purpose="research", stage="research"
                )
            except ModelError as exc:
                if exc.category == "budget" and not state.budget_owner:
                    closing = True
                    continue
                raise
            except TimeoutError:
                if state.budget_owner:
                    raise
                closing = True
                continue
            if not self.budget:
                estimator.observe(measured, reply.prompt_tokens)
            if reply.finish_reason not in {"stop", "tool_calls"}:
                raise ModelError("model_finish_" + reply.finish_reason, category="exploration")
            messages.append(reply.message)
            calls = reply.message.get("tool_calls") or []
            if not calls:
                content = reply.message.get("content") or ""
                if content:
                    self._observe(state, {"kind": "research_observation", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": "继续使用研究工具；已有材料足以成稿时调用 finish_research。"
                        "正式正文由随后单独写作请求完成。",
                    }
                )
                continue
            for call_item in calls:
                function = call_item["function"]
                name = function["name"]

                async def execute():
                    return await self._execute_tool(state, registry, name, function["arguments"])

                kind = "delegation" if registry.delegates(name) else "tool"
                with self.scheduler.task(state.session_id, state.task.role, "research"):
                    if name in {"finish_research", "read_references"}:
                        result = await self.scheduler.call(execute, kind=kind, name=name)
                    elif kind == "delegation":
                        # Delegation releases tool permits, but visual/other direct
                        # auxiliary models still need the caller's budget owner.
                        # Real child runtimes override this scope with their owner.
                        if self.budget:
                            with self.budget.scope(
                                state.budget_owner or state.session_id, purpose="auxiliary"
                            ):
                                result = await self.scheduler.call(execute, kind=kind, name=name)
                        else:
                            result = await self.scheduler.call(execute, kind=kind, name=name)
                    elif self.budget:
                        owner = state.budget_owner or state.session_id
                        with self.budget.scope(owner, purpose="auxiliary"):
                            try:
                                async with asyncio.timeout(
                                    self.budget.remaining_seconds(
                                        session_id=owner, purpose="auxiliary"
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
                self._observe(state, {"kind": "tool", "name": name, "output": payload})
                if state.research_finished or state.authoring_phase in {
                    "initial_understanding",
                    "organize_research",
                }:
                    remaining = calls[calls.index(call_item) + 1 :]
                    messages.extend(
                        {
                            "role": "tool",
                            "tool_call_id": item["id"],
                            "content": encoded({"ok": False, "error": "manuscript_scheduled"}),
                        }
                        for item in remaining
                    )
                    break
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
