"""One author researches, saves a public note, and submits in the same tool conversation."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from spacebio_review.agents.context import TokenEstimator
from spacebio_review.agents.reading import (
    BrowseSelection,
    PassageSelection,
    ReadingWindow,
    reading_output,
)
from spacebio_review.evidence.reading_render import render_originals
from spacebio_review.models.provider import ModelError
from spacebio_review.models.scheduling import RequestScheduler
from spacebio_review.prose import (
    cited_ids,
    prose_inputs,
)
from spacebio_review.tools import Tool, ToolRegistry, ToolResult

from .assembly import ManuscriptAssembly
from .budget import CompletionReserve, SessionAllowance
from .conversation import ActiveConversation
from .records import (
    ReportSelection,
    ResearchNote,
    SessionOutcome,
    SessionSettings,
    SessionStore,
    SessionTask,
)
from .research_context import (
    _once_per_note,
    application_originals,
    compact_tool_payload,
    composition_context,
    context_references,
    expand_captured_units,
    research_context,
    returned_originals,
)
from .scientific_roles import (
    RESEARCH_PLANNING,
    SOURCE_READING_OBJECTIVE,
    role_instruction,
    stage_instruction,
)
from .source_reading import visible_note_refs


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


class ReadReferences(BaseModel):
    references: list[str] = Field(min_length=1, max_length=12)
    reading_tokens: int | None = Field(default=None, gt=0)
    continuation: str | None = None


class FinishResearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(description="已有认识足以成稿或结束研究的理由及仍未解决的事项")
    wait_for: list[str] = Field(default_factory=list, description="本轮需等待答复的咨询请求标识。")


class PublishManuscript(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, description="完整专题稿，保留仍成立的认识及依据。")


class SetResearchFocus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    focus: str = Field(min_length=1)
    responsibility: str | None = None
    reason: str | None = None


class ReplyConsultation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    content: str = Field(min_length=1, description="只回答这次咨询问题，不替换完整专题稿。")


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
        assembly_route="assembly",
    ):
        self.store = SessionStore(root)
        self.assembly_route = assembly_route
        self.assembly = ManuscriptAssembly(self.store, self._call_assembly)
        self.models, self.routes = models, routes
        self.scheduler = scheduler or RequestScheduler()
        self.budget, self.skills = budget, skills
        self.allowances = allowances or {}
        self.settings = settings or SessionSettings()
        self.session_overrides = session_overrides or {}
        if self.settings.completion_turns >= self.settings.max_turns:
            raise ValueError("completion_turns_must_leave_research_turns")
        self._locks, self._conversations = {}, {}
        self._owned_models = []

    @classmethod
    def from_config(cls, path, root, *, env_file=".env"):
        from spacebio_review.agents.skills import SkillLibrary
        from spacebio_review.models.provider import DashScopeModel

        from .budget import Budget
        from .config import load_config

        config = load_config(path)
        budget = Budget(root, config.budget)
        scheduler = RequestScheduler(config.concurrency)
        scheduler.request_budget = budget
        models = {
            key: DashScopeModel(value, env_file=env_file, budget=budget, scheduler=scheduler)
            for key, value in config.models.items()
        }
        runtime = cls(
            root,
            models,
            config.roles,
            scheduler=scheduler,
            budget=budget,
            settings=config.session,
            session_overrides=config.session_overrides,
            skills=SkillLibrary(config.skills_dir),
            allowances=config.allowances,
            assembly_route=config.auxiliary["assembly"],
        )
        runtime._owned_models = list(models.values())
        return runtime

    def create(
        self,
        task: SessionTask,
        *,
        readings=None,
        allowance=None,
        budget_owner=None,
        inquiry_assignment=None,
        inquiry_responsibility=None,
        inquiry_id=None,
        model_role=None,
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
            completion_calls = max(
                settings.completion_turns, 3 if task.role == "chief" else 1
            ) * 2 + int(task.role == "chief")
            self.budget.register(
                state.session_id,
                allowance
                or self.allowances.get(task.role)
                or SessionAllowance(
                    completion_input_tokens=min(
                        settings.context_tokens,
                        model.config.context_window - model.config.max_tokens,
                    )
                    + (model.config.max_tokens + 2048 if task.role == "chief" else 0),
                    completion_output_tokens=model.config.max_tokens * completion_calls,
                    completion_calls=completion_calls,
                ),
            )
        self.store.save(state)
        if inquiry_id:
            self.store.assign_inquiry(inquiry_id, state.session_id)
        elif task.role != "source_reader":
            assignment = (
                inquiry_assignment
                if inquiry_assignment is not None
                else {"objective": task.objective}
            )
            inquiry = self.store.create_inquiry(
                assignment,
                responsibility=inquiry_responsibility or task.objective,
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

    def _research_scope(self, state):
        if state.task.role == "source_reader":
            # The request is another author's question, not established source facts
            # or a second independent statement of the reader's responsibility.
            return {
                "delegation": {
                    "source_role": "review_interpretation",
                    "question": state.task.objective,
                }
            }
        inquiry = self._inquiry(state)
        return {
            "current_focus": self._focus(state),
            "responsibility": inquiry.responsibility if inquiry else state.task.objective,
        }

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
        if not model.config.tools:
            raise ValueError("v5_session_requires_tool_capable_chat_route")

    def _capture(self, state, readings):
        added = {}
        for key, snapshot in readings.items():
            if key in state.evidence:
                continue
            state.evidence[key] = deepcopy(snapshot)
            added[key] = deepcopy(snapshot)
        return added

    def _observe(self, state, content):
        state.observation_index += 1
        state.observations.append({"index": state.observation_index, **content})
        self.store.save(state)
        return state.observation_index

    def _context(
        self, state, context=None, *, purpose="research", originals=None, observations=None
    ):
        inquiry = self._current_contribution(state)
        focus = self._focus(state)
        notes = []
        active = set()
        working_context = context() if context else {}
        # A failed topic can still hand over a reader's manuscript. Bring its exact
        # cited originals into the receiving author's evidence archive alongside that text.
        for ref in sorted(visible_note_refs(working_context)):
            self._capture(state, self.store.note(ref)["evidence"])
        readings = dict(state.evidence)
        active.update(
            ref
            for ref in context_references(
                working_context,
                readings,
                include_notes=purpose == "composition",
            )
        )
        active.update(
            ref
            for ref in context_references(
                state.task.model_dump(mode="json"),
                readings,
                include_notes=purpose == "composition",
            )
        )
        observations = deepcopy(state.observations if observations is None else observations)
        for row in observations:
            if row.get("kind") in {"tool", "author_delivery"} and "output" in row:
                output = row["output"]
                row["output"] = self._tool_payload(
                    ToolResult.model_validate(output["result"]),
                    output.get("readings", {}),
                    group_originals=False,
                )
                if row.get("author_text_ref") and not output["result"]["ok"]:
                    row["author_text"] = self.store.author_text(row["author_text_ref"])["content"]
        active.update(
            ref
            for ref in context_references(
                observations,
                readings,
            )
        )
        note_refs = [state.note_ref]
        for ref in dict.fromkeys(ref for ref in note_refs if ref):
            artifact = self.store.note(ref)
            note = deepcopy(artifact["note"])
            if purpose == "composition":
                active.update(cited_ids(note))
            raw_ref = artifact.get("author_text_ref")
            if raw_ref:
                # The successful author input records the already absorbed handles.
                # Captures obtained later remain readable even before a tool result.
                absorbed = set(self.store.author_text(raw_ref)["readings"])
                active.update(state.evidence.keys() - absorbed)
            entry = {
                "note_ref": ref,
                "note": note,
                "source_role": "review_interpretation",
                "author_session_id": artifact["session_id"],
            }
            if inquiry:
                entry.update(
                    focus=artifact.get("focus"),
                    relation=(
                        "current_focus" if artifact.get("focus") == focus else "previous_focus"
                    ),
                )
            notes.append(entry)
        # Start with complete originals. Subsequent input follows the current
        # manuscript, newly received material and explicitly requested readings.
        if not notes:
            active.update(state.evidence)
        active.update(expand_captured_units(state.evidence, active))
        if originals is not None:
            active = set(originals)
        application = state.task.application
        task = prose_inputs(state.task.model_dump(mode="json"))
        # Give each positioned application original its ready-to-use citation.
        for alias, passage in application_originals(task.get("application"), readings).items():
            passage["evidence_handle"] = alias
            passage["citation"] = f"[{alias}](evidence:{alias})"
        if inquiry:
            task["objective"] = focus or "依据申请原文独立确定当前科学问题，再开展研究。"
            task["responsibility"] = inquiry.responsibility
            task["current_focus"] = focus
        elif state.task.role == "source_reader":
            task["objective"] = SOURCE_READING_OBJECTIVE
            task.update(self._research_scope(state))
        application_ids = (
            {
                source["source_id"]
                for key in ("sources", "application_basis")
                for source in application.get(key, [])
            }
            if isinstance(application, dict)
            else set()
        )
        view = {
            "task": task,
            "skills": state.skill_bodies,
            "current_research": prose_inputs(notes),
            "originals": {
                handle: {**item, "citation": f"[{handle}](evidence:{handle})"}
                for handle, item in state.evidence.items()
                if handle in active
            },
            "available_readings": {
                handle: {
                    "citation": f"[{handle}](evidence:{handle})",
                    "locator": item.get("locator", ""),
                    "reading_scope": item.get("reading_scope", ""),
                    "reference": item.get("reference", {}),
                    "source_role": (
                        "application"
                        if item.get("reference", {}).get("source_id")
                        in application_ids
                        else "external_research"
                    ),
                }
                for handle, item in state.evidence.items()
            },
            "available_sources": {
                item["reference"]["source_id"]: {
                    key: item[key] for key in ("title", "url", "authors") if item.get(key)
                }
                for item in state.evidence.values()
            },
            "unabsorbed_observations": observations,
            "followup": None if inquiry or state.task.role == "source_reader" else state.followup,
            "working_context": self._tool_payload(ToolResult(ok=True, content=working_context), {})[
                "result"
            ]["content"],
            "resources": self.resources(state.session_id),
        }
        projection = composition_context if purpose == "composition" else research_context
        return projection(view, readings=readings)

    def resources(self, session_id):
        state = self.load(session_id)
        return {
            "session_model_turns_left": max(0, state.settings.max_turns - state.turns_used),
            "session_usage": self.budget.usage(self.budget_account(state)) if self.budget else None,
            "run_usage": self.budget.usage() if self.budget else None,
            "run_model_calls_left": self.budget.remaining_calls if self.budget else None,
            "run_seconds_left": self.budget.remaining_seconds() if self.budget else None,
        }

    @staticmethod
    def _tool_payload(result, readings, *, group_originals=True):
        """Keep returned originals once, with their E references beside source positions."""
        return compact_tool_payload(
            result.model_dump(mode="json"), readings, group_originals=group_originals
        )

    def _receive_tool_result(self, state, result, captured):
        """Retain actual tool originals and their native pixels in this live author."""
        conversation = self._conversations.setdefault(
            state.session_id,
            ActiveConversation(observation_index=state.observation_index),
        )
        readings = self._capture(state, captured)
        known = dict(state.evidence)
        output = result.model_dump(mode="json", exclude={"image_urls"})
        if result.image_urls:
            for visual, url in zip(
                result.content["original_visuals"], result.image_urls, strict=True
            ):
                readings.update(
                    conversation.remember_visual(
                        known,
                        visual["source_id"],
                        visual["visual_id"],
                        visual["image_digest"],
                        url,
                    )
                )
        # A reread renews the same original unit, even after it left the live prefix.
        reread = returned_originals(output, known)
        conversation.remember(reread, reread=True)
        readings.update(reread)
        conversation.remember(readings)
        return output, readings

    def receive_tool_result(self, session_id, result, readings, *, name):
        """Receive starting material through the same path as an ordinary tool read.

        Pixels stay in the author's in-memory conversation. The first run supplies
        its real schemas before selecting and pricing this queued reading.
        """
        state = self.load(session_id)
        output, received = self._receive_tool_result(state, result, readings)
        self._observe(
            state,
            {
                "kind": "source_reading_start",
                "name": name,
                "output": {"result": output, "readings": received},
            },
        )

    async def _prepare_original_pages(self, state, external):
        """Cache only pages of this author's received originals before selecting input."""
        if not self.models[state.model_alias].config.vision:
            return {}
        registry = external(state) if callable(external) else external
        if not registry or not registry.original_page_readers:
            return {}
        conversation = self._conversations.setdefault(
            state.session_id,
            ActiveConversation(observation_index=state.observation_index),
        )
        pending = {
            handle: item
            for handle, item in state.evidence.items()
            if handle not in conversation.prepared_page_handles
        }
        if not pending:
            return {}
        added = {}
        for reader in registry.original_page_readers:
            result = await reader(pending)
            captured = {
                key: item for view in registry.evidence_views for key, item in view().items()
            }
            added.update(self._capture(state, captured))
            known = dict(state.evidence)
            for visual, url in zip(
                result.content["original_visuals"], result.image_urls, strict=True
            ):
                conversation.cache_visual(
                    known,
                    visual["source_id"],
                    visual["visual_id"],
                    visual["image_digest"],
                    url,
                )
        conversation.prepared_page_handles.update(pending)
        conversation.prepared_page_handles.update(added)
        return added

    def release_author(self, session_id):
        """An explicit change of author ends this runtime's live conversation."""
        self._conversations.pop(session_id, None)

    def _research_task(self, state, submission=None):
        instruction = stage_instruction(state.task.role)
        if state.authoring_phase == "application_reading":
            instruction += (
                "\n当前只阅读申请正文。通读对象、对照、阶段和读出后调用 "
                "finish_research，随后安排开放的科学研究职责。"
            )
        elif state.coordination_phase == "planning":
            instruction += "\n" + RESEARCH_PLANNING
        elif submission:
            instruction += (
                "\n用 select_manuscripts 选择确切科学稿版本，finish_research 只负责结束或等待。"
            )
        return {
            "role": "user",
            "content": encoded(
                {
                    "stage": "research",
                    "instruction": instruction,
                    **self._research_scope(state),
                }
            ),
        }

    def _conversation(self, state, context=None, submission=None):
        conversation = self._conversations.get(state.session_id)
        if conversation is None or not conversation.messages:
            # Initial handoff uses actual captured originals, not the new author's
            # future citation choices. This is an in-memory lifecycle, not resume.
            pending = conversation is not None
            conversation = conversation or ActiveConversation(
                observation_index=state.observation_index,
            )
            readings = {
                handle: item
                for handle, item in state.evidence.items()
                if not pending or handle not in conversation.readings
            }
            view = self._context(
                state,
                context,
                originals=readings,
                observations=[
                    row
                    for row in state.observations
                    if row["index"] <= conversation.observation_index
                ],
            )
            if self.models[state.model_alias].config.vision:
                # The fixed application already has its text in the task. Other
                # inherited originals enter through the same priced window as tools.
                known = dict(state.evidence)
                application = application_originals(view["task"].get("application"), known)
                companions = expand_captured_units(known, application)
                readings = {
                    alias: item
                    for alias, item in known.items()
                    if alias in application
                    or alias in companions
                    and item["reference"].get("visual_id")
                }
                view["originals"] = []
            conversation.messages.extend(
                [
                    {"role": "system", "content": role_instruction(state.task.role)},
                    {"role": "user", "content": encoded(view)},
                    self._research_task(state, submission),
                ]
            )
            conversation.messages.extend(conversation.visual_messages(render_originals(readings)))
            conversation.remember(readings)
            conversation.shown_handles.update(readings)
            self._conversations[state.session_id] = conversation
        return conversation

    def _live_payload(self, state, result, readings, *, defer=False, pending_messages=()):
        conversation = self._conversations[state.session_id]
        seen_notes = set()
        for message in conversation.messages:
            content = message.get("content")
            if isinstance(content, str):
                try:
                    seen_notes.update(visible_note_refs(json.loads(content)))
                except json.JSONDecodeError:
                    pass
        # An asynchronously received text unit and its newly opened page enter as
        # one selection, even when the upstream handoff only carried text snapshots.
        known = dict(state.evidence)
        fresh = conversation.remember(expand_captured_units(known, readings))
        # The archive owns complete originals. A tool result contains references plus
        # only the complete native units that this receiver can actually read now.
        payload = compact_tool_payload(_once_per_note(result, seen_notes), conversation.readings)
        payload["readings"] = []
        model = self.models[state.model_alias]
        target = (
            min(
                state.settings.context_tokens, model.config.context_window - model.config.max_tokens
            )
            - 1024
        )

        def request_cost(originals):
            candidate = {**payload, "readings": originals}
            messages = [
                *conversation.messages,
                *pending_messages,
                {"role": "user", "content": encoded(candidate)},
                *conversation.visual_messages(originals),
                self._publication_task(state, final=True),
            ]
            return self._measure_input(state, messages, conversation.schemas)

        if defer and request_cost([]) > target and len(conversation.messages) > 3:
            # Keep the complete incoming event in the existing observation queue.
            # It has not entered this author's input and must not be marked absorbed.
            state.authoring_phase = "publish"
            state.phase_reason = "整理已有公开研究，再逐项接收新到的完整科学稿。"
            return None
        if fresh:
            payload["unexpanded_readings"] = {
                alias: item["reference"] for alias, item in fresh.items()
            }
            payload["reading_instruction"] = (
                "readings 是本轮展开的完整原文单元；unexpanded_readings 仅为已收到的原文入口，"
                "不代表正文已展示。需要时用 read_references 的 references "
                "回读对应 evidence_handle。"
            )
            base = request_cost([])
            # Source tools have already bounded each original read. A colleague's
            # delivery may combine many such reads; price that accumulated material
            # against this author's actual input capacity, not one source-read window.
            selected = conversation.select_readings(
                max(0, target - base),
                exclude=set(conversation.readings) - set(fresh),
                measure=lambda originals: request_cost(originals) - base,
            )
            payload["readings"] = render_originals(selected)
            payload["unexpanded_readings"] = {
                alias: item["reference"] for alias, item in fresh.items() if alias not in selected
            }
            if not payload["unexpanded_readings"]:
                del payload["unexpanded_readings"]
                del payload["reading_instruction"]
            conversation.shown_handles.update(selected)
        return payload

    def _measure_input(self, state, messages, schemas):
        model = self.models[state.model_alias]
        measured = (
            self.budget.measure(model.config, messages, schemas)
            if self.budget
            else TokenEstimator(
                image_tokens=model.config.image_tokens,
                image_patch_size=model.config.image_patch_size,
            ).measure(messages, schemas, output_reserve=model.config.max_tokens)
        )
        return measured["input_tokens"]

    def _sync_conversation(self, state, *, delivered=()):
        """Append only new public events; native tool messages are already paired."""
        conversation = self._conversations[state.session_id]
        for row in state.observations:
            if row["index"] <= conversation.observation_index:
                continue
            if row["index"] not in delivered:
                event = deepcopy(row)
                output = event.get("output")
                visual_originals = []
                if isinstance(output, dict) and "result" in output:
                    event["output"] = self._live_payload(
                        state,
                        output["result"],
                        output.get("readings", {}),
                        defer=True,
                    )
                    if event["output"] is None:
                        return
                    visual_originals = event["output"]["readings"]
                elif "readings" in event:
                    readings = event.pop("readings")
                    event = self._live_payload(state, event, readings, defer=True)
                    if event is None:
                        return
                    visual_originals = event["readings"]
                conversation.append({"role": "user", "content": encoded(prose_inputs(event))})
                conversation.messages.extend(conversation.visual_messages(visual_originals))
            conversation.ack(row["index"])
        incoming = {
            handle: item
            for handle, item in state.evidence.items()
            if handle not in conversation.readings
        }
        if incoming:
            payload = self._live_payload(state, {"event": "new_readings"}, incoming)
            conversation.append({"role": "user", "content": encoded(payload)})
            conversation.messages.extend(conversation.visual_messages(payload["readings"]))

    def _rebase_conversation(self, state, model, schemas, context=None, submission=None):
        """Replace capacity-heavy history with its complete manuscript and originals."""
        conversation = self._conversations[state.session_id]
        artifact = self.store.note(state.note_ref)
        raw = self.store.author_text(artifact["author_text_ref"])
        absorbed = raw["absorbed_through"]
        view = self._context(state, context, originals={}, observations=[])
        prefix = [
            {"role": "system", "content": role_instruction(state.task.role)},
            {"role": "user", "content": encoded(view)},
            self._research_task(state, submission),
        ]
        estimator = TokenEstimator(
            image_tokens=model.config.image_tokens, image_patch_size=model.config.image_patch_size
        )

        def measure(messages):
            return (
                self.budget.measure(model.config, messages, schemas)
                if self.budget
                else estimator.measure(messages, schemas, output_reserve=model.config.max_tokens)
            )

        application = application_originals(view["task"].get("application"), conversation.readings)
        conversation.rebase(prefix, absorbed, shown_handles=application)
        # The tail arrived after this manuscript's input boundary. Give it its
        # actual space first, then fill the remaining capacity with older units.
        self._sync_conversation(state)
        base_tokens = measure(conversation.messages)["input_tokens"]
        target = min(
            state.settings.context_tokens, model.config.context_window - model.config.max_tokens
        )
        publication_cost = measure(
            [*conversation.messages, self._publication_task(state, final=True)]
        )["input_tokens"]
        available = max(0, target - publication_cost - 1024)
        reading_room = min(conversation.reading_window.budget, available // 2)

        def original_cost(originals):
            candidate = list(conversation.messages)
            candidate[1] = {"role": "user", "content": encoded({**view, "originals": originals})}
            candidate.extend(conversation.visual_messages(originals))
            return measure(candidate)["input_tokens"] - base_tokens

        selected = conversation.select_readings(
            available - reading_room, exclude=conversation.shown_handles, measure=original_cost,
            preferred=cited_ids(artifact["note"]["body"]),
        )
        view["originals"] = render_originals(selected)
        conversation.messages[1] = {"role": "user", "content": encoded(view)}
        conversation.messages.extend(conversation.visual_messages(view["originals"]))
        conversation.shown_handles.update(selected)
        if conversation.pending_read:
            conversation.append(
                {
                    "role": "user",
                    "content": encoded(
                        {
                            "event": "continue_reading",
                            **conversation.pending_read,
                            "instruction": "空间已整理，继续刚才因阅读窗口不足而未完成的读取。",
                        }
                    ),
                }
            )
            conversation.pending_read["organized"] = True
        state.context_compactions += 1
        self.store.save(state)

    def _submit_note(self, state):
        if not state.note_ref:
            return ToolResult(ok=False, error="research_note_required")
        state.submitted_ref = state.note_ref
        state.status, state.reason = "submitted", None
        if state.note_ref not in state.submissions:
            state.submissions.append(state.note_ref)
        self.store.save(state)
        return ToolResult(ok=True, content={"submitted": state.note_ref})

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
    ):
        """Price each remaining scientific request; refresh synthesis after the decision."""
        if not self.budget:
            return None
        operations = submission.completion_operations(state) if submission else ["submit_research"]
        costs, names = [], []

        def author(operation):
            author_messages = (
                manuscript[1]
                if manuscript is not None and manuscript[0] == operation
                else submission.synthesis_estimate(
                    state,
                    max(1, model.config.max_tokens - model.config.reasoning_reserve),
                    operation,
                )
                if operation in {"write_overall", "write_summary"} and not state.assessment_ref
                else [
                    *self._conversation(state, context, submission).messages,
                    self._publication_task(state, final=True),
                ]
                if operation in {"update_research", "submit_research"}
                else self._formal_messages(state, operation, context, submission)
            )
            costs.append(
                self.budget.measure(
                    model.config,
                    author_messages,
                    schemas if operation in {"update_research", "submit_research"} else [],
                )
            )
            names.append(operation + ":author")

        for operation in operations:
            author(operation)
            assembly_model = self.models[self.assembly_route]
            projected_body = "科学正文及引用位置。" * max(1, model.config.max_tokens // 10)
            if operation == "submit_assessment":
                costs.append(
                    self.budget.measure(
                        assembly_model.config, [{"role": "user", "content": projected_body}], []
                    )
                )
                names.append(operation + ":assembly_layout")
            if state.evidence:
                costs.append(
                    self.budget.measure(
                        assembly_model.config,
                        [
                            {
                                "role": "user",
                                "content": encoded(
                                    {
                                        "body": projected_body,
                                        "citation_positions": projected_body,
                                        "originals": self.assembly.originals(state.evidence),
                                    }
                                ),
                            }
                        ],
                        [],
                    )
                )
                names.append(operation + ":assembly_bind")
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
        self,
        state,
        raw_ref,
        submission,
        *,
        collaboration=None,
        purpose="auxiliary",
    ):
        """Bind the author's text and publish it in the current execution."""
        raw = self.store.author_text(raw_ref)
        operation = raw["operation"]

        def record(result):
            if result.ok:
                state.authoring_phase = None
            self._observe(
                state,
                {
                    "kind": "author_delivery",
                    "name": operation,
                    "author_text_ref": raw_ref,
                    "output": {"result": result.model_dump(mode="json"), "readings": {}},
                },
            )
            return result

        if operation in {"update_research", "submit_research", "reply_consultation"}:
            previous = self.store.note(state.note_ref) if state.note_ref else None
            body, _ = await self.assembly.assemble(
                state,
                raw,
                previous=previous,
                purpose="auxiliary" if self._nested_reader(state) else purpose,
            )
            note = ResearchNote(title=raw.get("title") or self._research_title(state), body=body)
            if operation == "reply_consultation":
                ref = self.store.save_reply(state, raw, note)
                await collaboration.reply(state, raw["request_id"], ref)
                return ToolResult(ok=True, content={"reply_ref": ref})
            ref = self.store.save_note(
                state, note, absorbed_through=raw["absorbed_through"], author_text_ref=raw_ref
            )
            if collaboration:
                await collaboration.after_contribution(state, ref)
            result = (
                self._submit_note(state)
                if operation == "submit_research"
                else ToolResult(ok=True, content={"saved": ref, "revision": state.revision})
            )
            state.authoring_phase, state.phase_reason = None, None
            self.store.save(state)
            return result

        if submission is None:
            raise ValueError("assessment_delivery_requires_chief_submission")
        try:
            result = await submission.accept_authoring(state, raw_ref, raw)
        except (ValidationError, ValueError, KeyError) as exc:
            raise ModelError(
                f"{operation}_invalid:{type(exc).__name__}", category="exploration"
            ) from exc
        return record(result)

    def _research_title(self, state):
        return self._focus(state) or "当前科学问题"

    def _save_scientific_reply(
        self,
        state,
        content,
        *,
        absorbed_through,
        operation,
        finish_reason,
        request_id=None,
    ):
        return self.store.save_author_text(
            state,
            content,
            operation,
            absorbed_through=absorbed_through,
            phase="research",
            request_id=request_id,
            title=self._research_title(state),
            replaces=(state.note_ref,) if state.note_ref else (),
            focus=self._focus(state),
            reason=state.phase_reason,
            finish_reason=finish_reason,
        )

    async def _submit_scientific_reply(
        self,
        state,
        reply,
        *,
        absorbed_through,
        collaboration=None,
        context=None,
    ):
        """Preserve scientific prose and submit only a completed answer."""
        raw_ref = self._save_scientific_reply(
            state,
            reply.message["content"],
            absorbed_through=absorbed_through,
            operation=(
                "reply_consultation"
                if collaboration and collaboration.current_request
                else "update_research"
                if collaboration
                else "submit_research"
            ),
            request_id=collaboration.current_request if collaboration else None,
            finish_reason=reply.finish_reason,
        )
        if reply.finish_reason != "stop":
            self._observe(state, {"kind": "incomplete_manuscript", "author_text_ref": raw_ref})
            raise ModelError("model_finish_" + reply.finish_reason, category="exploration")
        result = await self._deliver_manuscript(
            state,
            raw_ref,
            None,
            collaboration=collaboration,
            purpose="auxiliary"
            if collaboration and collaboration.current_request
            else "completion",
        )
        if not result.ok:
            raise ModelError(result.error or "author_content_missing", category="exploration")
        if (
            collaboration
            and raw_ref
            and self.store.author_text(raw_ref)["operation"] == "reply_consultation"
        ):
            return False
        if collaboration:
            if await collaboration.before_model(state):
                # Keep the submitted draft tied to its actual input. Newly received
                # answers or a new consultation belong in the next research request.
                state.research_finished = False
                state.status, state.reason = "running", None
                self.store.save(state)
                return False
            working = context().get("collaboration", {}) if context else {}
            wait_for = working.get("waiting_for", [])
            reason = "本次科学解释已直接交付。"
            action = await collaboration.finish(state, reason, wait_for)
            if action != "finish":
                state.research_finished = False
                state.status = "waiting" if action == "waiting" else "running"
                state.reason = reason if action == "waiting" else None
                self.store.save(state)
                return False
            self._submit_note(state)
        state.research_finished = True
        self.store.save(state)
        return True

    def _tools(
        self,
        state,
        external,
        *,
        submission=None,
        collaboration=None,
    ):
        async def finish(args):
            if state.authoring_phase == "application_reading":
                state.authoring_phase = None
                self.store.save(state)
                return ToolResult(ok=True, content={"next": "research_planning"})
            if args.wait_for and collaboration is None:
                return ToolResult(ok=False, error="collaboration_required_for_waiting")
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
            return ToolResult(ok=True, content={"next": "publication"})

        async def set_focus(args):
            inquiry = self._inquiry(state)
            inquiry = self.store.set_focus(
                inquiry.inquiry_id,
                args.focus,
                reason=args.reason,
                responsibility=args.responsibility,
            )
            state.authoring_phase, state.phase_reason = None, None
            self.store.save(state)
            if collaboration:
                await collaboration.after_contribution(state, None)
            return ToolResult(
                ok=True,
                content={
                    "current_focus": inquiry.current_focus,
                    "responsibility": inquiry.responsibility,
                },
            )

        async def publish(args):
            return ToolResult(ok=True, content={})

        async def reply_consultation(args):
            if not collaboration or args.request_id != collaboration.current_request:
                return ToolResult(ok=False, error="consultation_not_current")
            return ToolResult(ok=True, content={"request_id": args.request_id})

        async def select_manuscripts(args):
            submission.prepare(state, args)
            return ToolResult(ok=True, content={"report_selection": args.model_dump(mode="json")})

        async def assignment_history(args):
            return ToolResult(ok=True, content=self._inquiry(state).assignment)

        async def read(args):
            content, requested = {}, {}
            reread = True
            for ref in args.references:
                if ref in state.evidence:
                    requested[ref] = state.evidence[ref]
                elif collaboration and ref.startswith("note-"):
                    content[ref], known = collaboration.read_contribution(state, ref)
                    reread = reread and known
                else:
                    return ToolResult(ok=False, error="unknown_reading_reference")
            if requested:
                sources = {item["reference"]["source_id"] for item in requested.values()}
                if len(sources) != 1:
                    return ToolResult(ok=False, error="read_one_source_at_a_time")
                source_id = next(iter(sources))
                captured = expand_captured_units(
                    dict(state.evidence),
                    requested,
                )

                def page_key(item):
                    return (
                        item.get("material", {}).get("material_id"),
                        item.get("reading_structure", {}).get("page"),
                    )

                visual_pages = {
                    page_key(item)
                    for item in requested.values()
                    if item["reference"].get("visual_id") and all(page_key(item))
                }
                wanted = [
                    alias
                    for alias, item in captured.items()
                    if item["reference"].get("passage_id")
                    and (alias in requested or page_key(item) in visual_pages)
                ]
                returned_pages = set()
                rows = []
                for alias, item in captured.items():
                    position = item["reference"].get("passage_id")
                    if position:
                        rows.append(
                            {
                                **item.get("reading_structure", {}),
                                "passage_id": position,
                                "text": item.get("quote", ""),
                                "locator": item.get("locator", position),
                                "evidence_handle": alias,
                                "material_version": item.get("material_version"),
                                "citation": f"[{alias}](evidence:{alias})",
                            }
                        )
                if wanted:
                    rows.sort(key=lambda row: row.get("order", 0))
                    materials = {}
                    for alias, item in captured.items():
                        metadata = item.get("material")
                        if metadata and item["reference"].get("passage_id"):
                            material = materials.setdefault(
                                metadata["material_id"],
                                {
                                    **metadata,
                                    "passage_ids": [],
                                },
                            )
                            material["passage_ids"].append(item["reference"]["passage_id"])
                    first = next(iter(requested.values()))
                    window = next(
                        (
                            window
                            for window in registry.reading_windows
                            if args.continuation in window.cursors
                        ),
                        next(iter(registry.reading_windows), None),
                    )
                    if window is None:
                        window = ReadingWindow()
                        registry.reading_windows.append(window)
                    selection = (
                        BrowseSelection(
                            source_id=source_id,
                            continuation=args.continuation,
                            reading_tokens=args.reading_tokens,
                        )
                        if args.continuation
                        else PassageSelection(
                            source_id=source_id,
                            passage_ids=wanted,
                            reading_tokens=args.reading_tokens,
                        )
                    )
                    result = window.read(
                        selection,
                        passages=rows,
                        source={
                            key: first[key] for key in ("title", "url", "authors") if key in first
                        },
                        materials=list(materials.values()),
                        position_key="evidence_handle",
                    )
                    content["reading"] = reading_output(result)
                    returned_pages = {
                        page_key(captured[alias])
                        for alias in result.get("returned_ids", [])
                        if all(page_key(captured[alias]))
                    }
                    if result.get("continuation"):
                        content["reading"]["continue_with"] = {
                            "tool": "read_references",
                            "references": args.references,
                            "continuation": result["continuation"],
                        }
                visuals = {
                    alias: item
                    for alias, item in captured.items()
                    if item["reference"].get("visual_id")
                    and (alias in requested or page_key(item) in returned_pages)
                }
                if visuals:
                    content["visual_readings"] = render_originals(visuals)
            return ToolResult(
                ok=True, content=content, reading_scope="reread" if reread else "research_note"
            )

        registry = ToolRegistry()
        registry.include(external(state) if callable(external) else external)
        if not registry.reading_windows:
            conversation = self._conversations.get(state.session_id)
            registry.reading_windows.append(
                conversation.reading_window if conversation else ReadingWindow()
            )
        if conversation := self._conversations.get(state.session_id):
            conversation.reading_window = registry.reading_windows[0]
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
        if state.task.role != "source_reader" and (
            state.coordination_phase != "planning" or state.authoring_phase == "application_reading"
        ):
            registry.add(
                Tool(
                    "finish_research",
                    "结束本轮研究；主审选择科学稿版本后撰写综合评价，作者提交当前科学稿。"
                    if submission
                    else "结束本轮研究；已有稿时直接提交，否则在当前会话交付正文。",
                    FinishResearch,
                    finish,
                    read_only=False,
                )
            )
        registry.add(
            Tool(
                "read_references",
                "按 evidence_handle 回读一个来源的已捕获原文单元；"
                "按 note_ref 回读科学稿及其累计实际阅读原文。",
                ReadReferences,
                read,
                manuscript_tokens=lambda arguments: sum(
                    collaboration.team.contribution_tokens(ref)
                    for ref in ReadReferences.model_validate_json(arguments).references
                    if collaboration and ref.startswith("note-")
                ),
            )
        )
        if self._inquiry(state) and state.authoring_phase != "application_reading":
            registry.add(
                Tool(
                    "publish_manuscript",
                    "发布完整专题稿并继续研究；仅此操作替换当前专题稿。",
                    PublishManuscript,
                    publish,
                    read_only=False,
                )
            )
            registry.add(
                Tool(
                    "set_research_focus",
                    "设置当前研究问题和持续职责，不发布正文。",
                    SetResearchFocus,
                    set_focus,
                    read_only=False,
                )
            )
            if collaboration:
                registry.add(
                    Tool(
                        "reply_consultation",
                        "回答当前咨询；答复归属于 request_id，完成这次咨询。",
                        ReplyConsultation,
                        reply_consultation,
                        read_only=False,
                    )
                )
            if submission:
                registry.add(
                    Tool(
                        "select_manuscripts",
                        "按科学价值选择主文及附录的确切专题稿版本。",
                        ReportSelection,
                        select_manuscripts,
                        read_only=False,
                    )
                )
            registry.add(
                Tool(
                    "read_assignment",
                    "需要时回读最初委派历史；实际研究以当前职责和问题为准。",
                    ReadAssignment,
                    assignment_history,
                )
            )
        return registry

    async def _call_assembly(self, state, stage, instruction, value, schema, *, purpose):
        model = self.models[self.assembly_route]
        messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": encoded(value)},
        ]

        async def call():
            reply = await model.complete(messages, response_schema=schema)
            if reply.finish_reason != "stop":
                raise ModelError("assembly_finish_" + reply.finish_reason, category="exploration")
            return json.loads(reply.message["content"])

        with (
            self.scheduler.task(state.session_id, "assembly", stage),
            self.scheduler.alias(self.assembly_route),
        ):
            if self.budget:
                owner = self.budget_account(state)
                with self.budget.scope(owner, purpose=purpose):
                    async with asyncio.timeout(
                        self.budget.remaining_seconds(session_id=owner, purpose=purpose)
                    ):
                        return await call()
            return await call()

    async def _call_model(
        self,
        state,
        model,
        messages,
        schemas,
        *,
        purpose,
        stage,
    ):
        async def call():
            conversation = (
                self._conversations.get(state.session_id)
                if state.task.role == "source_reader"
                else None
            )
            presented = set(conversation.shown_handles) if conversation else set()
            reply = await model.complete(messages, schemas)
            if conversation:
                conversation.presented_handles.update(presented)
            return reply

        state.turns_used += 1
        self.store.save(state)
        try:
            with (
                self.scheduler.task(state.session_id, state.task.role, stage),
                self.scheduler.alias(state.model_alias),
            ):
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

    def _formal_messages(self, state, operation, context, submission):
        return submission.completion_input(state, operation)

    def _publication_task(self, state, *, final=False):
        instruction = (
            "当前研究预算已到交付窗口。依据已经读过的材料交付完整科学正文。"
            if final
            else "当前会话需要腾出阅读空间。先发布已形成的完整科学稿，随后继续读取。"
        )
        instruction += (
            "直接回复普通正文，程序原样保存。"
            if state.task.role == "source_reader"
            else "调用 publish_manuscript 交付完整专题稿；当前工作为咨询时用 "
            "reply_consultation 回答该请求。已有稿足够且无咨询待答时用 finish_research。"
        )
        return {
            "role": "user",
            "content": encoded(
                {
                    "stage": "research",
                    "instruction": instruction,
                    **self._research_scope(state),
                    "reason": state.phase_reason,
                }
            ),
        }

    async def _formal_submission(
        self,
        state,
        model,
        *,
        operation,
        context=None,
        submission,
    ):
        """Formal scoring and report synthesis consume the completed scientific work."""
        state.authoring_phase = operation
        messages, readings = submission.authoring_input(state, operation)
        self._completion_plan(
            state,
            model,
            messages,
            [],
            submission,
            context=context,
            manuscript=(operation, messages),
        )
        measured = (
            self.budget.measure(model.config, messages, [])
            if self.budget
            else TokenEstimator(
                image_tokens=model.config.image_tokens,
                image_patch_size=model.config.image_patch_size,
            ).measure(
                messages,
                [],
                output_reserve=model.config.max_tokens,
            )
        )
        if measured["total_tokens"] > model.config.context_window:
            raise ModelError("context_window_exceeded", category="exploration", request_sent=False)
        reply = await self._call_model(
            state,
            model,
            messages,
            [],
            purpose="completion",
            stage=operation,
        )
        content = reply.message.get("content") or ""
        raw_ref = self.store.save_author_text(
            state,
            content,
            operation,
            absorbed_through=state.observation_index,
            phase=operation,
            focus=self._focus(state),
            reason=state.phase_reason,
            finish_reason=reply.finish_reason,
            readings=readings,
        )
        if reply.finish_reason != "stop" or reply.message.get("tool_calls"):
            self._observe(state, {"kind": "incomplete_manuscript", "author_text_ref": raw_ref})
            raise ModelError("incomplete_author_manuscript", category="exploration")
        if not isinstance(content, str) or not content.strip():
            raise ModelError("empty_author_manuscript", category="exploration")
        result = await self._deliver_manuscript(state, raw_ref, submission)
        if not result.ok:
            raise ModelError(result.error or "author_content_missing", category="exploration")

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
                and not (collaboration and collaboration.current_request)
                and (submission is None or submission.completed(state))
            ):
                return self._outcome(state)
            await self._prepare_original_pages(state, tools)
            conversation = self._conversation(state, context, submission)
            conversation.schemas = self._tools(
                state, tools or ToolRegistry(), submission=submission, collaboration=collaboration
            ).schemas()
            consulting = collaboration is not None and collaboration.consulting
            try:
                if self.budget and not self._nested_reader(state) and not consulting:
                    self.budget.activate(self.budget_account(state))
                if not consulting:
                    state.submitted_ref = None
                if collaboration and collaboration.current_request:
                    state.research_finished = False
                    state.authoring_phase, state.phase_reason = None, None
                if followup is not None:
                    state.followup = followup
                    if state.task.role == "source_reader":
                        state.task.objective = followup
                    state.report_ref = None
                    state.assessment_ref = None
                    state.overall_ref = None
                    if submission is None or submission.allow_research:
                        state.report_selection = None
                        state.report_candidates = []
                    state.research_finished = False
                    state.authoring_phase = None
                    state.phase_reason = None
                    self._observe(
                        state,
                        {
                            "kind": "followup",
                            "content": self._research_scope(state)
                            if state.task.role == "source_reader"
                            else state.followup,
                        },
                    )
                    self._sync_conversation(state)
                    conversation.append(self._research_task(state, submission))
                if submission and not submission.allow_research:
                    state.research_finished = True
                state.status, state.reason = "running", None
                self.store.save(state)
                await self._run(
                    state,
                    model,
                    tools or ToolRegistry(),
                    conversation,
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
            if row.get("kind") == "current_work_changed":
                continue
            if (
                row.get("kind") == "author_delivery"
                and row.get("author_text_ref")
                and row.get("author_text_ref") == artifact.get("author_text_ref")
            ):
                continue
            if row.get("kind") == "research_observation":
                calls = row.get("tool_calls") or []
                if (
                    not row.get("content")
                    or calls
                    and all(call["function"]["name"] == "finish_research" for call in calls)
                ):
                    # The finish response submits the existing contribution; its
                    # public closing text is not another research/writing stage.
                    continue
            if row.get("kind") == "tool":
                result = row.get("output", {}).get("result", {})
                if result.get("ok") and (
                    row.get("name") == "finish_research"
                    or row.get("name") == "read_references"
                    and result.get("reading_scope") == "reread"
                    or row.get("name") in {"set_research_focus", "select_manuscripts"}
                    or row.get("name") == "publish_manuscript"
                    and result.get("content", {}).get("saved") == state.note_ref
                ):
                    continue
            return False
        raw_ref = artifact.get("author_text_ref")
        if not raw_ref:
            return False
        raw = self.store.author_text(raw_ref)
        return not (set(state.evidence) - set(raw["readings"]))

    async def _run(
        self,
        state,
        model,
        external,
        conversation,
        context=None,
        submission=None,
        collaboration=None,
    ):
        estimator = TokenEstimator(
            image_tokens=model.config.image_tokens, image_patch_size=model.config.image_patch_size
        )
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
            conversation.append(self._research_task(state, submission))
        while state.turns_used < state.settings.max_turns:
            await self._prepare_original_pages(state, external)
            self._sync_conversation(state)
            messages = conversation.messages
            # Scientific authors publish when useful; only final delivery is forced.
            completion_turns = len(submission.completion_operations(state)) if submission else 1
            forced = (
                closing
                or not consulting
                and (state.turns_used >= state.settings.max_turns - completion_turns)
            )
            if forced and state.coordination_phase == "planning" and not state.research_finished:
                raise ModelError(
                    "research_planning_budget_exhausted", category="budget", request_sent=False
                )
            final = forced or state.research_finished
            if final:
                if submission and not state.assessment_ref:
                    submission.prepare(state)
                if collaboration and not finish_announced and state.task.role == "chief":
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
                if submission:
                    if collaboration and not state.assessment_ref:
                        state.authoring_phase = "submit_assessment"
                        await collaboration.before_model(state)
                        await self._prepare_original_pages(state, external)
                        self._sync_conversation(state)
                    operations = submission.completion_operations(state)
                    if not operations:
                        state.status, state.reason = "submitted", None
                        return
                    await self._formal_submission(
                        state,
                        model,
                        operation=operations[0],
                        context=context,
                        submission=submission,
                    )
                    if state.status == "submitted":
                        return
                    continue
                if not (
                    collaboration and collaboration.current_request
                ) and self._can_reuse_contribution(state):
                    self._submit_note(state)
                    return
            if collaboration:
                await collaboration.before_model(state)
                await self._prepare_original_pages(state, external)
                self._sync_conversation(state)
            publish_for_space = state.authoring_phase == "publish"
            if publish_for_space and self._can_reuse_contribution(state):
                state.authoring_phase = None
                self._rebase_conversation(
                    state,
                    model,
                    self._tools(
                        state, external, submission=submission, collaboration=collaboration
                    ).schemas(),
                    context,
                    submission,
                )
                continue
            registry = self._tools(
                state, external, submission=submission, collaboration=collaboration
            )
            research_schemas = registry.schemas()
            if final or publish_for_space:
                registry = registry.select(
                    []
                    if state.task.role == "source_reader"
                    else [
                        name
                        for name in ("publish_manuscript", "reply_consultation", "finish_research")
                        if name in {row["function"]["name"] for row in registry.schemas()}
                    ]
                )
                conversation.append(self._publication_task(state, final=final))
                messages = conversation.messages
            schemas = registry.schemas()
            conversation.schemas = research_schemas
            measured = (
                self.budget.measure(model.config, messages, schemas)
                if self.budget
                else estimator.measure(messages, schemas, output_reserve=model.config.max_tokens)
            )
            if (
                not final
                and not publish_for_space
                and measured["input_tokens"] > state.settings.context_tokens
                and len(messages) > 3
            ):
                if self._can_reuse_contribution(state):
                    self._rebase_conversation(state, model, research_schemas, context, submission)
                    continue
                state.authoring_phase = "publish"
                state.phase_reason = "将本段研究整理为当前完整工作稿，随后继续研究。"
                continue
            if measured["total_tokens"] > model.config.context_window:
                raise ModelError(
                    "context_window_exceeded", category="exploration", request_sent=False
                )
            reason = (
                None
                if consulting or final
                else self._completion_plan(
                    state,
                    model,
                    messages,
                    schemas,
                    submission,
                    include_request=True,
                    context=context,
                )
            )
            if reason:
                closing = True
                continue
            input_watermark = conversation.observation_index
            try:
                reply = await self._call_model(
                    state,
                    model,
                    messages,
                    schemas,
                    purpose="completion"
                    if final and not self._nested_reader(state) and not consulting
                    else "research",
                    stage="research_planning"
                    if state.coordination_phase == "planning"
                    else "research",
                )
            except ModelError as exc:
                if (
                    exc.category == "budget"
                    and not final
                    and not self._nested_reader(state)
                    and not consulting
                ):
                    closing = True
                    continue
                raise
            except TimeoutError:
                if final or self._nested_reader(state) or consulting:
                    raise
                closing = True
                continue
            if not self.budget:
                estimator.observe(measured, reply.prompt_tokens)
            calls = reply.message.get("tool_calls") or []
            if (
                not calls
                and reply.finish_reason != "tool_calls"
                and isinstance(reply.message.get("content"), str)
                and reply.message["content"].strip()
                and (state.task.role != "chief" or publish_for_space or final)
            ):
                messages.append(reply.message)
                if (
                    publish_for_space
                    and not final
                    and not (collaboration and collaboration.current_request)
                ):
                    raw_ref = self._save_scientific_reply(
                        state,
                        reply.message["content"],
                        absorbed_through=input_watermark,
                        operation="update_research",
                        finish_reason=reply.finish_reason,
                    )
                    if reply.finish_reason != "stop":
                        self._observe(
                            state, {"kind": "incomplete_manuscript", "author_text_ref": raw_ref}
                        )
                        raise ModelError(
                            "model_finish_" + reply.finish_reason, category="exploration"
                        )
                    await self._deliver_manuscript(
                        state, raw_ref, None, collaboration=collaboration
                    )
                    self._rebase_conversation(state, model, research_schemas, context, submission)
                    continue
                # Do not record the final body again as an unabsorbed observation.
                # The active input boundary leaves later arrivals for the next turn.
                finished = await self._submit_scientific_reply(
                    state,
                    reply,
                    absorbed_through=input_watermark,
                    collaboration=collaboration,
                    context=context,
                )
                if finished or state.status == "waiting":
                    return
                await self._prepare_original_pages(state, external)
                self._sync_conversation(state)
                conversation.append(self._research_task(state, submission))
                continue
            if reply.finish_reason not in {"stop", "tool_calls"}:
                for item in calls:
                    if item["function"]["name"] != "publish_manuscript":
                        continue
                    try:
                        args = registry.parse_arguments(
                            "publish_manuscript", item["function"]["arguments"]
                        )
                    except (ValidationError, ValueError, TypeError, KeyError):
                        # Incomplete argument JSON stays in the original model
                        # response; it is not interpreted as a manuscript.
                        continue
                    if args.content:
                        raw_ref = self._save_scientific_reply(
                            state,
                            args.content,
                            absorbed_through=input_watermark,
                            operation="update_research",
                            finish_reason=reply.finish_reason,
                        )
                        self._observe(
                            state,
                            {
                                "kind": "incomplete_manuscript",
                                "author_text_ref": raw_ref,
                            },
                        )
                raise ModelError("model_finish_" + reply.finish_reason, category="exploration")
            messages.append(reply.message)
            public_index = self._observe(
                state,
                {
                    "kind": "research_observation",
                    "content": reply.message.get("content") or "",
                    "tool_calls": deepcopy(calls),
                },
            )
            delivered = {public_index}
            if not calls:
                self._sync_conversation(state, delivered=delivered)
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "依据申请与专业能力目录，使用 research_topics 提交专家研究安排。"
                            if state.coordination_phase == "planning"
                            else "继续读取解释当前问题所需的来源原文；"
                            "材料已足够时，直接交付科学解释正文。"
                            if state.task.role == "source_reader"
                            else "继续使用研究工具；材料足够时直接交付科学正文，或调用"
                            " publish_manuscript，把完整科学正文放入 content，发布后继续研究。"
                        ),
                    }
                )
                continue
            coordination_phase = state.coordination_phase
            context_changed = False
            published_this_turn = False
            visual_messages = []
            for call_item in calls:
                function = call_item["function"]
                name = function["name"]
                # Bound the original before it enters the dialogue, leaving space
                # for this author's complete manuscript in the same conversation.
                publication_tail = self._publication_task(state, final=True)
                capacity_messages = [*messages, *visual_messages, publication_tail]
                capacity_measure = (
                    self.budget.measure(model.config, capacity_messages, schemas)
                    if self.budget
                    else estimator.measure(
                        capacity_messages, schemas, output_reserve=model.config.max_tokens
                    )
                )
                capacity = max(
                    0,
                    min(
                        state.settings.context_tokens,
                        model.config.context_window - capacity_measure["output_reserve"],
                    )
                    - capacity_measure["input_tokens"]
                    - 1024,
                )
                for window in registry.reading_windows:
                    window.capacity_tokens = capacity

                manuscript_tokens = registry.manuscript_tokens(name, function["arguments"])
                pending = conversation.pending_read or {}
                organized = (
                    pending.get("organized")
                    and pending.get("tool") == name
                    and pending.get("arguments") == function["arguments"]
                )
                if manuscript_tokens > capacity and not organized:
                    # Arrange space before requesting another author's full prose.
                    # The unexecuted call stays explicit in the provider tool protocol.
                    conversation.pending_read = {"tool": name, "arguments": function["arguments"]}
                    state.authoring_phase = "publish"
                    state.phase_reason = "先整理已有研究，为接收完整科学稿及其原文留出空间。"
                    messages.extend(
                        {
                            "role": "tool",
                            "tool_call_id": item["id"],
                            "content": encoded(
                                {"ok": False, "status": "awaiting_context_space", "executed": False}
                            ),
                        }
                        for item in calls[calls.index(call_item) :]
                    )
                    break
                if pending.get("tool") == name:
                    conversation.pending_read = None

                async def execute():
                    if manuscript_tokens > capacity:
                        return ToolResult(
                            ok=False,
                            error="manuscript_batch_exceeds_context",
                            content={
                                "executed": False,
                                "instruction": (
                                    "当前空间已整理。请逐篇回读科学稿，缩小本次稿件集合。"
                                ),
                            },
                        )
                    return await registry.execute(name, function["arguments"])

                kind = "delegation" if registry.delegates(name) else "tool"
                with self.scheduler.task(state.session_id, state.task.role, "research"):
                    if name in {
                        "finish_research",
                        "read_references",
                        "read_assignment",
                        "publish_manuscript",
                        "reply_consultation",
                        "set_research_focus",
                        "select_manuscripts",
                        "request_inquiry",
                    }:
                        result = await self.scheduler.call(execute, kind=kind, name=name)
                    elif kind == "delegation":
                        # Delegation releases tool permits, but visual/other direct
                        # auxiliary models still need the caller's budget owner.
                        # Real child runtimes override this scope with their owner.
                        if self.budget:
                            with self.budget.scope(self.budget_account(state), purpose="auxiliary"):
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
                if result.ok and name in {"publish_manuscript", "reply_consultation"}:
                    args = registry.parse_arguments(name, function["arguments"])
                    raw_ref = self._save_scientific_reply(
                        state,
                        args.content,
                        absorbed_through=input_watermark,
                        operation="reply_consultation"
                        if name == "reply_consultation"
                        else "update_research",
                        request_id=getattr(args, "request_id", None),
                        finish_reason=reply.finish_reason,
                    )
                    published = await self._deliver_manuscript(
                        state,
                        raw_ref,
                        None,
                        collaboration=collaboration,
                        purpose="completion" if final else "auxiliary",
                    )
                    if not published.ok:
                        raise ModelError(
                            published.error or "author_content_missing",
                            category="exploration",
                        )
                    published_this_turn = True
                    result.content.update(published.content)
                    if name == "publish_manuscript":
                        result.content["contribution_ref"] = published.content["saved"]
                    # This response is the saved manuscript, not new research
                    # awaiting another manuscript. Its input watermark stays exact.
                    for row in state.observations:
                        if row["index"] == public_index:
                            row.clear()
                            row.update(
                                index=public_index,
                                kind="author_delivery",
                                name="update_research",
                                author_text_ref=raw_ref,
                            )
                captured = {}
                for view in registry.evidence_views:
                    captured.update(view())
                received = self._capture(state, captured)
                received.update(await self._prepare_original_pages(state, registry))
                output, readings = self._receive_tool_result(state, result, captured)
                readings.update(received)
                payload = self._live_payload(
                    state,
                    output,
                    readings,
                    pending_messages=visual_messages,
                )
                visual_messages.extend(conversation.visual_messages(payload["readings"]))
                messages.append(
                    {"role": "tool", "tool_call_id": call_item["id"], "content": encoded(payload)}
                )
                delivered.add(
                    self._observe(
                        state,
                        {
                            "kind": "tool",
                            "name": name,
                            "output": {"result": output, "readings": readings},
                        },
                    )
                )
                reading = result.content if isinstance(result.content, dict) else {}
                reading = reading.get("reading", reading)
                limited = False
                if result.ok and reading.get("unread_reason") in {
                    "native_block_exceeds_read_budget",
                    "reading_context_exceeds_read_budget",
                }:
                    requested = getattr(
                        registry.parse_arguments(name, function["arguments"]),
                        "reading_tokens",
                        None,
                    )
                    normal_limit = min(
                        min(requested or window.budget, window.max_budget)
                        for window in registry.reading_windows
                    )
                    limited = reading["reading_tokens"] < normal_limit
                if limited:
                    conversation.pending_read = {"tool": name, "arguments": function["arguments"]}
                    state.authoring_phase = "publish"
                    state.phase_reason = (
                        "为继续读取当前完整资料单元，整理已有公开研究为完整工作稿。"
                    )
                if result.ok and name == "finish_research" and state.research_finished:
                    finish_announced = True
                context_changed |= result.ok and name in {
                    "publish_manuscript",
                    "reply_consultation",
                    "set_research_focus",
                    "select_manuscripts",
                    "finish_research",
                }
                if (
                    result.ok
                    and name in {"finish_research", "reply_consultation"}
                    or state.status in {"waiting", "partial"}
                    or state.research_finished
                    or state.authoring_phase == "publish"
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
            messages.extend(visual_messages)
            self._sync_conversation(state, delivered=delivered)
            if state.status in {"waiting", "partial"}:
                return
            if publish_for_space and published_this_turn:
                state.authoring_phase = None
                self._rebase_conversation(state, model, research_schemas, context, submission)
            if (
                final
                and not (collaboration and collaboration.current_request)
                and self._can_reuse_contribution(state)
            ):
                self._submit_note(state)
                return
            if (context_changed or state.coordination_phase != coordination_phase) and not (
                publish_for_space and published_this_turn
            ):
                if not state.research_finished and not state.authoring_phase:
                    conversation.append(self._research_task(state, submission))
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
        self._conversations.clear()
