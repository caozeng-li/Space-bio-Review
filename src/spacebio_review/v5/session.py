"""One author researches, saves a public note, and submits in the same tool conversation."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Literal

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
    ContributionSelection,
    ReportSelection,
    SessionOutcome,
    SessionSettings,
    SessionStore,
    SessionTask,
)
from .research_context import compact_tool_payload, composition_context, context_references

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
继续投入时，调用 finish_research 并简述结束理由及未决事项，完成本轮交付。
需要其他作者解释具体问题时用 request_inquiry，说明答案将怎样改变当前认识。
没有独立工作且正在等待答复时，finish_research 的 wait_for 列出相关请求标识；程序让出
执行机会，答复到达后继续。挂起前值得保存的新认识先用 contribute(write) 发布，不重复成稿。
收到一个咨询工作项时，结束说明可以直接引用已有稿回答该问题，不必为短答重写全文。
用 contribute 表达当前实际研究的问题：focus_only 先重新定题并继续研究，write 安排一次
完整科学稿写作。首次贡献需给出 focus，之后可沿用；新认识值得形成正文时才写，不必每轮更新。
当前问题可以替换初始委派的前提。正文连接对象、安排、读出、可得知识和实现依据。
current_research 是你已经保存的完整科学稿。它已表达当前认识且无需继续研究时，直接
finish_research，程序复用该稿。再次 contribute(write) 表示已有认识发生了实质变化，
需要修订正文；单纯结束本轮使用 finish_research。
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

RESEARCH_PLANNING = """当前阶段是确定开放的科学职责并安排作者。直接阅读完整申请，辨认实际
对象、实验安排、读出和待获得的知识，选出值得独立解释的问题，并选择相关专业能力。
此时不先写带有方法定论的评价草稿。可阅读申请原文和能力说明，用 research_topics 登记
职责及材料入口；作者从原文独立定题，成果一经发布即可供你综合，无需等待所有作者完成。
研究问题可以被质疑、重定题或转交，人数由实际科学联系决定。
research_topics 立即返回工作请求标识。希望先看作者进展时，用 finish_research 的 wait_for
列出这些 request_id 让出执行机会；收到进展后继续判断。wait_for=[] 表示正式结束研究，
程序会关闭本轮派发，随后进入成稿；不要把刚登记工作误当作全部研究已经完成。
"""


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


class ReadReferences(BaseModel):
    references: list[str] = Field(min_length=1, max_length=12)


class FinishResearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(description="已有认识足以成稿或结束研究的理由及仍未解决的事项")
    wait_for: list[str] = Field(default_factory=list, description="本轮需等待答复的咨询请求标识。")


class FinishAssessmentResearch(FinishResearch):
    main_sections: list[ContributionSelection] = Field(
        default_factory=list,
        description="正式结束研究时，按报告科学正文顺序选择 Inquiry 及其确切稿件版本。",
    )
    appendix_sections: list[ContributionSelection] = Field(
        default_factory=list, description="选入附录的确切稿件版本，不重复主文已经选入的稿件。",
    )


class Contribute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["write", "focus_only"] = "write"
    focus: str | None = Field(default=None, min_length=1, description="当前实际研究的科学问题。")
    reason: str | None = Field(default=None, description="本次定题或修订的目的，不填写正文。")


class ReadAssignment(BaseModel):
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

    def create(
        self, task: SessionTask, *, readings=None, allowance=None, budget_owner=None,
        inquiry_assignment=None, inquiry_id=None, model_role=None,
    ):
        alias = self.routes.get(model_role or task.role) or self.routes["researcher"]
        model = self.models[alias]
        self._check_model(model)
        bodies = {name: self.skills.get(name).body for name in task.skills} if self.skills else {}
        if inquiry_id:
            budget_owner = self.store.inquiry(inquiry_id).budget_account_id
        settings = self.settings_for(task.role)
        state = self.store.create(
            task=task,
            model_alias=alias,
            model_snapshot=model.config.model_dump(mode="json"),
            settings=settings,
            skill_bodies=bodies,
            budget_owner=budget_owner,
            inquiry_id=inquiry_id,
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
        if inquiry_id:
            self.store.assign_inquiry(inquiry_id, state.session_id)
        elif task.role != "source_reader":
            assignment = inquiry_assignment if inquiry_assignment is not None else {
                "objective": task.objective
            }
            inquiry = self.store.create_inquiry(
                assignment,
                owner_session_id=state.session_id,
                budget_account_id=budget_owner or state.session_id,
                application_anchors=assignment.get("application_refs", []),
            )
            if task.role == "chief":
                self.store.set_focus(inquiry.inquiry_id, task.objective)
        return state.session_id

    def _inquiry(self, state):
        return self.store.inquiry(state.inquiry_id) if state.inquiry_id else None

    def budget_account(self, state):
        inquiry = self._inquiry(state)
        return inquiry.budget_account_id if inquiry else state.budget_owner or state.session_id

    @staticmethod
    def _nested_reader(state):
        return state.task.role == "source_reader" and state.budget_owner is not None

    def _focus(self, state):
        inquiry = self._inquiry(state)
        return inquiry.current_focus if inquiry else state.task.objective

    def _current_contribution(self, state):
        inquiry = self._inquiry(state)
        if inquiry and inquiry.current_contribution_ref != state.note_ref:
            state.note_ref = inquiry.current_contribution_ref
            if state.note_ref:
                artifact = self.store.contribution(inquiry.inquiry_id, state.note_ref)
                self._capture(state, artifact["evidence"])
            self.store.save(state)
        return inquiry

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
                self.budget_account(state),
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

    def _context(self, state, context=None, *, purpose="research"):
        inquiry = self._current_contribution(state)
        focus = self._focus(state)
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
        active.update(
            state.aliases.get(ref, ref)
            for ref in context_references(
                observations, {alias: state.evidence[key] for alias, key in state.aliases.items()}
            )
        )
        note_refs = [state.note_ref]
        for ref in dict.fromkeys(ref for ref in note_refs if ref):
            artifact = self.store.note(ref)
            note = deepcopy(artifact["note"])
            active.update(cited_ids(note))
            reverse = {value: key for key, value in state.aliases.items()}
            note["body"] = bind_prose(note["body"], reverse)
            entry = {"note_ref": ref, "note": note}
            if inquiry:
                entry.update(
                    focus=artifact.get("focus"),
                    relation=(
                        "current_focus" if artifact.get("focus") == focus else "previous_focus"
                    ),
                )
            notes.append(entry)
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
        task = state.task.model_dump(mode="json")
        if inquiry:
            task["objective"] = focus or "依据申请原文独立确定当前科学问题，再开展研究。"
            if focus is None:
                task["initial_assignment"] = inquiry.assignment
        view = {
            "task": task,
            "skills": state.skill_bodies,
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
            "followup": None if inquiry else state.followup,
            "working_context": self._tool_payload(
                ToolResult(ok=True, content=working_context), {}, state.aliases
            )["result"]["content"],
            "resources": self.resources(state.session_id),
        }
        return composition_context(view) if purpose == "composition" else view

    def resources(self, session_id):
        state = self.load(session_id)
        return {
            "session_model_turns_left": max(0, state.settings.max_turns - state.turns_used),
            "session_usage": self.budget.usage(self.budget_account(state))
            if self.budget
            else None,
            "run_usage": self.budget.usage() if self.budget else None,
            "run_model_calls_left": self.budget.remaining_calls if self.budget else None,
            "run_seconds_left": self.budget.remaining_seconds() if self.budget else None,
        }

    @staticmethod
    def _tool_payload(result, readings, aliases):
        """Keep returned originals once, with their E references beside source positions."""
        return compact_tool_payload(prose_inputs(result.model_dump(mode="json")), readings, aliases)

    def _research_messages(self, state, context=None, submission=None):
        return [
            {
                "role": "system",
                "content": (
                    SCIENTIFIC_INSTRUCTION + "\n" + RESEARCH_PLANNING
                    if state.coordination_phase == "planning"
                    and state.authoring_phase != "application_reading"
                    else INSTRUCTION
                )
                + (
                    "\n当前只阅读申请正文。通读研究目标、对象、对照、阶段和读出后"
                    "调用 finish_research，随后直接安排开放的科学研究职责。"
                    if state.authoring_phase == "application_reading"
                    else ""
                )
                + (
                    "\n正式结束研究时，用 finish_research 的 main_sections 与 appendix_sections"
                    "按顺序选择确切科学稿版本。它们直接进入报告，你随后负责综合判断、全部指标"
                    "理由与决定。等待进展时只填 wait_for，尚不确定选文。"
                    if submission and state.authoring_phase != "application_reading"
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
        self,
        state,
        model,
        messages,
        schemas,
        submission=None,
        *,
        include_request=False,
        context=None,
        manuscript=None,
        author_text=None,
    ):
        """Price the remaining author/mapper steps, using actual saved text when available."""
        if not self.budget:
            return None
        operations = submission.completion_operations(state) if submission else ["submit_research"]
        costs, names = [], []

        def author(operation):
            author_messages = (
                manuscript[1]
                if manuscript is not None and manuscript[0] == operation
                else self._writing_messages(
                    state, "final_manuscript", operation, context, submission
                )
            )
            costs.append(self.budget.measure(model.config, author_messages, []))
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

        if author_text:
            parser(author_text["operation"], author_text)
            if author_text["operation"] in operations:
                operations = operations[operations.index(author_text["operation"]) + 1 :]
        for operation in operations:
            author(operation)
            if submission is None or operation in submission.parsed_operations:
                parser(operation)
        if self._nested_reader(state):
            if include_request:
                # Before another reading turn, also leave room for its scientific
                # manuscript. Parent completion remains protected.
                costs.insert(0, self.budget.measure(model.config, messages, schemas))
            return self.budget.research_capacity(self.budget_account(state), costs)
        owner = self.budget_account(state)
        allowance = self.budget.data["sessions"][owner]["allowance"]
        return self.budget.update_completion(
            owner,
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

    async def _deliver_manuscript(
        self, state, raw_ref, messages, submission, *, purpose, working_context=None,
        collaboration=None,
    ):
        """Bind the author's text and publish it in the current execution."""
        raw = self.store.author_text(raw_ref)
        operation = raw["operation"]

        def record(result):
            if result.ok:
                state.authoring_phase = None
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
            if collaboration:
                await collaboration.after_contribution(state, ref)
            result = (
                self._submit_note(state)
                if operation == "submit_research"
                else ToolResult(ok=True, content={"saved": ref, "revision": state.revision})
            )
            state.authoring_phase = None
            state.phase_reason = None
            self.store.save(state)
            return result

        schema, instruction, context = self._delivery_contract(raw, submission)
        if self.delivery_parser is None:
            raise ModelError("delivery_parser_route_required", category="exploration")

        async def parse():
            if self.budget:
                reason = self._completion_plan(
                    state, self.models[state.model_alias], messages, [], submission,
                    context=working_context, author_text=raw,
                )
                if reason and self._nested_reader(state):
                    raise ModelError(reason, category="budget", request_sent=False)
                owner = self.budget_account(state)
                with self.budget.scope(owner, purpose=purpose):
                    async with asyncio.timeout(
                        self.budget.remaining_seconds(session_id=owner, purpose=purpose)
                    ):
                        return await self.delivery_parser.parse(raw, schema, instruction, context)
            return await self.delivery_parser.parse(raw, schema, instruction, context)

        with self.scheduler.task(state.session_id, "delivery_parser", "authoring"):
            mapping = await parse()
        parse_ref = self.store.save_delivery_parse(
            raw_ref, self.delivery_parser.config.model_dump(mode="json"), mapping
        )
        if mapping.missing_fields:
            result = ToolResult(
                ok=False,
                error="delivery_mapping_incomplete",
                content={"missing_fields": mapping.missing_fields, "author_text_ref": raw_ref},
            )
        else:
            try:
                result = submission.accept_authoring(
                    state, raw_ref, raw, mapping, parse_ref=parse_ref
                )
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
                    raise ModelError(
                        f"delivery_mapping_invalid:{type(exc).__name__}", category="exploration"
                    ) from exc
        return record(result)

    def _research_title(self, state, raw):
        return {
            "organize_research": "当前研究理解",
        }.get(raw.get("phase"), self._focus(state) or "当前科学问题")

    def _tools(
        self, state, external, absorbed_through=None, *, completing=False, submission=None,
        collaboration=None,
    ):
        async def finish(args):
            if self._inquiry(state) and not self._focus(state):
                return ToolResult(
                    ok=False, error="current_focus_required",
                    content="先用 contribute 表达依据申请形成的当前科学问题。",
                )
            if state.authoring_phase == "application_reading":
                state.authoring_phase = None
                self.store.save(state)
                return ToolResult(ok=True, content={"next": "research_planning"})
            if args.wait_for and collaboration is None:
                return ToolResult(ok=False, error="collaboration_required_for_waiting")
            if submission and not args.wait_for:
                if not args.main_sections:
                    return ToolResult(
                        ok=False, error="main_sections_required",
                        content="从科学稿目录选择至少一篇确切版本作为主文，再结束研究。",
                    )
                selection = ReportSelection(
                    main_sections=args.main_sections,
                    appendix_sections=args.appendix_sections,
                )
                try:
                    submission.prepare(state, selection)
                except ModelError as exc:
                    state.status, state.reason = "partial", str(exc)
                    self.store.save(state)
                    return ToolResult(ok=False, error=str(exc))
            if collaboration:
                action = await collaboration.finish(state, args.reason, args.wait_for)
                if action != "finish":
                    state.research_finished = False
                    state.authoring_phase, state.phase_reason = None, None
                    state.status = "waiting" if action == "waiting" else "running"
                    state.reason = args.reason if action == "waiting" else None
                    self.store.save(state)
                    return ToolResult(ok=True, content={"next": action})
            state.research_finished = True
            state.phase_reason = args.reason
            self.store.save(state)
            return ToolResult(ok=True, content={"next": "scientific_manuscript"})

        async def contribute(args):
            inquiry = self._inquiry(state)
            focus = args.focus or self._focus(state)
            if not focus:
                return ToolResult(ok=False, error="current_focus_required")
            self.store.set_focus(inquiry.inquiry_id, focus, reason=args.reason)
            if args.mode == "focus_only":
                state.authoring_phase = None
                state.phase_reason = None
                self._messages.pop(state.session_id, None)
            else:
                state.authoring_phase = "contribution"
                state.phase_reason = args.reason
            self.store.save(state)
            if args.mode == "focus_only" and collaboration:
                await collaboration.after_contribution(state, None)
            return ToolResult(ok=True, content={
                "mode": args.mode,
                "current_focus": self._focus(state),
                "contribution_ref": inquiry.current_contribution_ref,
                "next": "scientific_manuscript" if args.mode == "write" else "research",
            })

        async def assignment_history(args):
            return ToolResult(ok=True, content=self._inquiry(state).assignment)

        async def read(args):
            content = {}
            reread = True
            for ref in args.references:
                if ref in state.aliases:
                    content[ref] = state.evidence[state.aliases[ref]]
                elif collaboration and ref.startswith("note-"):
                    content[ref], known = collaboration.read_contribution(state, ref)
                    reread = reread and known
                else:
                    return ToolResult(ok=False, error="unknown_reading_reference")
            return ToolResult(
                ok=True, content=content, reading_scope="reread" if reread else "research_note"
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
        if state.coordination_phase != "planning" or state.authoring_phase == "application_reading":
            registry.add(
                Tool(
                    "finish_research",
                    "结束本轮研究；主审选择科学稿版本后撰写综合评价，作者提交当前科学稿。"
                    if submission else "结束本轮研究；已有完整当前稿时直接提交，否则安排正文写作。",
                    FinishAssessmentResearch
                    if submission and state.authoring_phase != "application_reading"
                    else FinishResearch,
                    finish,
                    read_only=False,
                )
            )
        registry.add(Tool(
            "read_references", "按 E 编号回读原文，或按 note_ref 回读科学稿及其引用原文。",
            ReadReferences, read,
        ))
        if self._inquiry(state) and state.authoring_phase != "application_reading":
            registry.add(
                Tool(
                    "contribute",
                    "表达或替换当前问题；write 写完整科学稿，focus_only 先定题继续研究。",
                    Contribute,
                    contribute,
                    read_only=False,
                )
            )
            if self._inquiry(state).current_focus is not None:
                registry.add(Tool(
                    "read_assignment", "需要时回读最初委派历史；实际研究以当前问题为准。",
                    ReadAssignment, assignment_history,
                ))
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
                        self.budget_account(state), purpose="auxiliary"
                    ):
                        remaining = self.budget.remaining_seconds(
                            session_id=self.budget_account(state), purpose="auxiliary"
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
                    owner = self.budget_account(state)
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
                    "organize_research": "根据原申请、当前工作稿和新成果，更新完整研究理解。"
                    "连起可能获得的知识与价值、主要判断和下一步值得研究的问题。"
                    "修订此前不合适的理解，保留仍适用的认识和依据；"
                    "无需重复全部论文说明，本稿将成为当前工作理解。",
                    "contribution": "围绕当前问题写出完整科学论述，连接实际对象、安排、读出、"
                    "可获得的知识和实现依据。修订上一版不合适的解释，保留仍成立的认识。"
                    "把重要新结果整合进论证，不顺序拼接论文摘要。旧稿若针对之前的问题，"
                    "据当前问题重新组织正文与结语。",
                    "final_manuscript": "依据本任务实际读到的材料，形成完整研究说明"
                    "及尚未解决的问题。",
                }.get(phase, "")
            )
        )
        if phase in {"organize_research", "contribution"} and state.phase_reason:
            author_instruction += "\n本次更新目的：" + state.phase_reason
        return [
            {"role": "system", "content": SCIENTIFIC_INSTRUCTION + "\n" + author_instruction},
            {
                "role": "user",
                "content": encoded(self._context(state, context, purpose="composition")),
            },
        ]

    async def _write_phase(
        self, state, model, *, phase, operation, context=None, submission=None, instruction=None,
        collaboration=None,
    ):
        """One code-selected manuscript: no tool schemas and no author operation protocol."""
        state.authoring_phase = phase
        if collaboration:
            await collaboration.before_model(state)
        self._current_contribution(state)
        if operation in {"update_research", "submit_research"} and not self._focus(state):
            raise ModelError("current_focus_required", category="exploration", request_sent=False)
        self.store.save(state)
        messages = self._writing_messages(state, phase, operation, context, submission, instruction)
        absorbed_through = state.observation_index
        research = operation in {"update_research", "submit_research"}
        replaces = (
            (state.note_ref,)
            if research and state.note_ref
            else ()
        )
        reason = None
        if not (collaboration and collaboration.consulting):
            reason = self._completion_plan(
                state,
                model,
                messages,
                [],
                submission,
                include_request=phase != "final_manuscript",
                context=context,
                manuscript=(operation, messages) if phase == "final_manuscript" else None,
            )
        # Completion spends this owner's reserve; an oversized historical floor
        # must not stop a request that still fits the actual remaining allowance.
        if reason and (phase != "final_manuscript" or self._nested_reader(state)):
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
        purpose = "completion" if final and not self._nested_reader(state) else "research"
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
            focus=self._focus(state),
            reason=state.phase_reason,
        )
        # Persist the original before parsing, including a truncated original for inspection.
        if reply.finish_reason != "stop" or reply.message.get("tool_calls"):
            self._observe(state, {"kind": "incomplete_manuscript", "author_text_ref": raw_ref})
            raise ModelError("incomplete_author_manuscript", category="exploration")
        result = await self._deliver_manuscript(
            state, raw_ref, messages, submission,
            purpose="completion" if final and not self._nested_reader(state) else "auxiliary",
            working_context=context,
            collaboration=collaboration,
        )
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
        collaboration=None,
    ):
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            state = self.store.load(session_id)
            if state.status in {"partial", "failed"}:
                return self._outcome(state)
            inquiry = self._current_contribution(state)
            if inquiry and inquiry.current_owner_session_id != session_id:
                raise ValueError("session_is_not_current_inquiry_owner")
            model = self.models[state.model_alias]
            self._check_model(model)
            if (
                state.status == "submitted"
                and followup is None
                and not (collaboration and collaboration.consulting)
                and (submission is None or submission.completed(state))
            ):
                return self._outcome(state)
            messages = self._messages.get(session_id) or self._research_messages(
                state, context, submission
            )
            consulting = collaboration is not None and collaboration.consulting
            try:
                if self.budget and not self._nested_reader(state) and not consulting:
                    self.budget.activate(self.budget_account(state))
                if not consulting:
                    state.submitted_ref = None
                if consulting:
                    state.research_finished = False
                    state.authoring_phase, state.phase_reason = None, None
                if followup is not None:
                    state.followup = followup
                    state.report_ref = None
                    state.assessment_ref = None
                    if submission is None or submission.allow_research:
                        state.report_selection = None
                        state.report_candidates = []
                    state.research_finished = False
                    state.authoring_phase = None
                    state.phase_reason = None
                    self._observe(state, {"kind": "followup", "content": state.followup})
                    messages = self._research_messages(state, context, submission)
                if submission and not submission.allow_research:
                    state.research_finished = True
                state.status, state.reason = "running", None
                self.store.save(state)
                await self._run(
                    state,
                    model,
                    tools or ToolRegistry(),
                    messages,
                    context,
                    submission,
                    collaboration,
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
                    if (
                        not self._nested_reader(state)
                        and not consulting
                        and state.status != "waiting"
                        and not (collaboration and getattr(collaboration, "handoff", False))
                    ):
                        self.budget.finish(self.budget_account(state))
                    self.budget.save()
            return self._outcome(state)

    def _can_reuse_contribution(self, state):
        if not state.note_ref:
            return False
        artifact = self.store.note(state.note_ref)
        if self._inquiry(state) and artifact.get("focus") != self._focus(state):
            return False
        for row in state.observations:
            if row.get("kind") == "tool":
                result = row.get("output", {}).get("result", {})
                if result.get("ok") and (
                    row.get("name") == "finish_research"
                    or row.get("name") == "read_references"
                    and result.get("reading_scope") == "reread"
                    or row.get("name") == "contribute"
                    and result.get("content", {}).get("mode") == "focus_only"
                ):
                    continue
            return False
        raw_ref = artifact.get("author_text_ref")
        if not raw_ref:
            return False
        raw = self.store.author_text(raw_ref)
        return not (set(state.evidence) - set(raw["aliases"].values()))

    async def _run(
        self,
        state,
        model,
        external,
        messages,
        context=None,
        submission=None,
        collaboration=None,
    ):
        estimator = TokenEstimator(image_tokens=model.config.image_tokens)
        closing = False
        finish_announced = False
        consulting = collaboration is not None and collaboration.consulting
        if (
            state.task.role == "chief"
            and state.turns_used == 0
            and not state.authoring_phase
            and not state.research_finished
            and isinstance(state.task.application, dict)
            and state.task.application.get("full_text_in_context") is False
        ):
            state.authoring_phase = "application_reading"
            self.store.save(state)
            messages = self._research_messages(state, context, submission)
        while state.turns_used < state.settings.max_turns:
            # Scientific authors publish when useful; only final delivery is forced.
            forced = closing or not consulting and state.turns_used >= state.settings.max_turns - 1
            if forced and state.coordination_phase == "planning" and not state.research_finished:
                raise ModelError(
                    "research_planning_budget_exhausted", category="budget", request_sent=False
                )
            phase = state.authoring_phase
            if phase == "application_reading":
                phase = "final_manuscript" if forced else None
            if phase is None and (forced or state.research_finished):
                phase = "final_manuscript"
            if phase:
                operation = "update_research"
                if phase == "final_manuscript":
                    if submission:
                        submission.prepare(state)
                    if collaboration and not finish_announced and (
                        state.task.role == "chief" or collaboration.consulting
                    ):
                        action = await collaboration.finish(
                            state, state.phase_reason or "依据当前成果结束本轮研究。", []
                        )
                        if action != "finish":
                            state.research_finished = False
                            state.authoring_phase, state.phase_reason = None, None
                            state.status = "waiting" if action == "waiting" else "running"
                            self.store.save(state)
                            if state.status == "waiting":
                                return
                            closing = False
                            continue
                        finish_announced = True
                    operations = (
                        submission.completion_operations(state)
                        if submission
                        else ["submit_research"]
                    )
                    if not operations:
                        state.status, state.reason = "submitted", None
                        return
                    operation = operations[0]
                    if operation == "submit_research" and self._can_reuse_contribution(state):
                        self._submit_note(state)
                        return
                try:
                    await self._write_phase(
                        state,
                        model,
                        phase=phase,
                        operation=operation,
                        context=context,
                        submission=submission,
                        collaboration=collaboration,
                    )
                except ModelError as exc:
                    if (
                        exc.category == "budget"
                        and exc.request_sent is False
                        and phase != "final_manuscript"
                        and not consulting
                    ):
                        state.authoring_phase = None
                        closing = True
                        continue
                    raise
                if state.status == "submitted":
                    return
                messages = self._research_messages(state, context, submission)
                state.context_compactions += 1
                self.store.save(state)
                continue

            if collaboration and await collaboration.before_model(state):
                messages = self._research_messages(state, context, submission)
            registry = self._tools(
                state, external, submission=submission, collaboration=collaboration
            )
            schemas = registry.schemas()
            measured = (
                self.budget.measure(model.config, messages, schemas)
                if self.budget
                else estimator.measure(messages, schemas, output_reserve=model.config.max_tokens)
            )
            if measured["input_tokens"] > state.settings.context_tokens:
                restored = self._research_messages(state, context, submission)
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
                if (
                    measured["input_tokens"] > state.settings.context_tokens
                    and state.observations
                    and self._focus(state)
                ):
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
            reason = None if consulting else self._completion_plan(
                state, model, messages, schemas, submission, include_request=True, context=context
            )
            if reason:
                closing = True
                continue
            try:
                reply = await self._call_model(
                    state,
                    model,
                    messages,
                    schemas,
                    purpose="research",
                    stage="research_planning"
                    if state.coordination_phase == "planning"
                    else "research",
                )
            except ModelError as exc:
                if exc.category == "budget" and not self._nested_reader(state) and not consulting:
                    closing = True
                    continue
                raise
            except TimeoutError:
                if self._nested_reader(state) or consulting:
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
                        "content": (
                            "依据申请与专业能力目录，使用 research_topics 提交专家研究安排。"
                            if state.coordination_phase == "planning"
                            else "继续使用研究工具；已有材料足以成稿时调用 finish_research。"
                            "正式正文由随后单独写作请求完成。"
                        ),
                    }
                )
                continue
            coordination_phase = state.coordination_phase
            context_changed = False
            for call_item in calls:
                function = call_item["function"]
                name = function["name"]

                async def execute():
                    return await self._execute_tool(state, registry, name, function["arguments"])

                kind = "delegation" if registry.delegates(name) else "tool"
                with self.scheduler.task(state.session_id, state.task.role, "research"):
                    if name in {
                        "finish_research", "read_references", "read_assignment", "contribute",
                        "request_inquiry",
                    }:
                        result = await self.scheduler.call(execute, kind=kind, name=name)
                    elif kind == "delegation":
                        # Delegation releases tool permits, but visual/other direct
                        # auxiliary models still need the caller's budget owner.
                        # Real child runtimes override this scope with their owner.
                        if self.budget:
                            with self.budget.scope(
                                self.budget_account(state), purpose="auxiliary"
                            ):
                                result = await self.scheduler.call(execute, kind=kind, name=name)
                        else:
                            result = await self.scheduler.call(execute, kind=kind, name=name)
                    elif self.budget:
                        owner = self.budget_account(state)
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
                if state.status == "partial":
                    return
                if result.ok and name == "finish_research" and state.research_finished:
                    finish_announced = True
                context_changed = result.ok and name in {"contribute", "finish_research"}
                if (
                    context_changed
                    or state.status == "waiting"
                    or state.research_finished
                    or state.authoring_phase in {"organize_research", "contribution"}
                ):
                    remaining = calls[calls.index(call_item) + 1 :]
                    messages.extend(
                        {
                            "role": "tool",
                            "tool_call_id": item["id"],
                            "content": encoded({"ok": False, "error": "research_context_changed"}),
                        }
                        for item in remaining
                    )
                    break
            if state.status == "waiting":
                return
            if context_changed or state.coordination_phase != coordination_phase:
                messages = self._research_messages(state, context, submission)
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
