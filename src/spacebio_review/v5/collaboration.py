"""Directed scientific work within one running review.

An inquiry has one execution task and a stack of scientific work: its own research
can wait while its author answers a colleague. Queues live only in this process;
the existing session/note stores retain the public scientific record.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from spacebio_review.evidence.reading_render import render_originals
from spacebio_review.prose import cited_ids, compile_authored_body
from spacebio_review.tools import Tool, ToolRegistry, ToolResult

from .research_context import expand_captured_units
from .scientific_handoff import delivery_readings, manuscript_tokens
from .source_reading import SourceReading, visible_note_refs


class RequestInquiry(BaseModel):
    question: str = Field(min_length=1, description="具体科学问题，允许对方改写提问前提。")
    scientific_use: str = Field(min_length=1, description="答案将怎样影响当前科学解释或评价。")
    input_refs: list[str] = Field(
        default_factory=list, description="已取得的原文 evidence_handle 或 note_ref；保留来源版本。"
    )
    target: str | None = Field(
        default=None, description="已有 inquiry_id，或所需专业能力；未指定时交由主审安排。"
    )


class TransferInquiry(BaseModel):
    inquiry_id: str
    reason: str = Field(min_length=1)
    author: Literal["chief", "expert"] = "expert"
    skills: list[str] | None = None
    responsibility: str | None = Field(
        default=None, min_length=1, description="接任作者持续承担的新职责；省略则沿用当前职责。"
    )


@dataclass
class _Request:
    request_id: str
    sender: str | None
    target: str | None
    question: str | dict
    scientific_use: str | dict
    inputs: dict = field(default_factory=dict)
    readings: dict = field(default_factory=dict)
    expertise: str | None = None
    kind: str = "consultation"
    status: str = "pending"
    answer: dict | None = None

    def view(self, *, full=False):
        view = {
            "request_id": self.request_id, "kind": self.kind,
            "from_inquiry": self.sender, "target_inquiry": self.target,
            "question": self.question, "scientific_use": self.scientific_use,
            "required_expertise": self.expertise, "status": self.status,
            "answer": self.answer if full or self.answer is None else {
                key: value for key, value in self.answer.items() if key not in {"note", "reason"}
            },
        }
        if not full and self.status == "answered":
            view.pop("question")
            view.pop("scientific_use")
        return view


@dataclass
class _Work:
    request_id: str | None = None
    consultation: bool = False
    followup: str | None = None
    wait_for: set[str] = field(default_factory=set)
    announced: bool = False
    finished: bool = False


@dataclass
class _Actor:
    work: list[_Work] = field(default_factory=list)
    incoming: deque = field(default_factory=deque)
    deliveries: dict = field(default_factory=dict)
    task: asyncio.Task | None = None
    completed: bool = False
    mode: str | None = None
    allow_research: bool = True
    transferring: bool = False
    hook: _AuthorHook | None = None
    error: Exception | None = None
    received_contributions: set[str] = field(default_factory=set)


class _AuthorHook:
    def __init__(self, team, inquiry_id, *, consulting):
        self.team, self.inquiry_id, self.consulting = team, inquiry_id, consulting
        self.handoff = False

    @property
    def current_request(self):
        work = self.team._actor(self.inquiry_id).work
        return work[-1].request_id if work and work[-1].consultation else None

    async def reply(self, state, request_id, ref):
        self.team._answer(request_id, "咨询答复已交付。", state=state, reply_ref=ref)
        action = await self.finish(state, "咨询答复已交付。", [])
        state.research_finished = False
        state.status = "waiting" if action == "waiting" else "running"
        self.team.runtime.store.save(state)

    async def before_model(self, state):
        return self.team._receive(self.inquiry_id, state)

    async def after_contribution(self, state, note_ref):
        self.team.publish(self.inquiry_id, note_ref)

    async def finish(self, state, reason, wait_for):
        return await self.team._finish(self.inquiry_id, state, reason, wait_for)

    def read_contribution(self, state, ref):
        received = self.team._actor(self.inquiry_id).received_contributions
        reread = ref == state.note_ref or ref in received
        content, readings = self.team.contribution(ref)
        self.team._access(self.inquiry_id).receive(readings)
        self.team.runtime._capture(state, readings)
        received.add(ref)
        return {**content, "originals": render_originals(readings)}, reread


class ScientificCollaboration:
    def __init__(self, topics):
        self.topics, self.runtime = topics, topics.runtime
        self.actors: dict[str, _Actor] = {}
        self.requests: dict[str, _Request] = {}
        self.chief = self.chief_id = self.chief_inquiry = None
        self.closed = False
        self.selected_contributions: dict[str, str] = {}
        self._changed = asyncio.Event()
        self._idle_notice = None

    def bind_chief(self, chief, chief_id):
        self.chief, self.chief_id = chief, chief_id
        self.chief_inquiry = self.runtime.load(chief_id).inquiry_id
        self._actor(self.chief_inquiry)

    def _actor(self, inquiry_id):
        return self.actors.setdefault(inquiry_id, _Actor())

    def _state(self, inquiry_id):
        inquiry = self.runtime.store.inquiry(inquiry_id)
        return self.runtime.load(inquiry.current_owner_session_id)

    def _access(self, inquiry_id):
        return (
            self.chief.access(self.chief_id)
            if inquiry_id == self.chief_inquiry else self.topics.access(inquiry_id)
        )

    def _manuscript(self, inquiry_id, ref=None):
        if inquiry_id != self.chief_inquiry:
            result = self.topics.result(inquiry_id, note_ref=ref)
            view = result.model_view()
            view["contribution_ref"] = view.pop("note_ref")
            return view, result.readings
        inquiry = self.runtime.store.inquiry(inquiry_id)
        state = self._state(inquiry_id)
        ref = ref or inquiry.current_contribution_ref
        artifact = self.runtime.store.contribution(inquiry_id, ref) if ref else None
        readings = delivery_readings(self._access(inquiry_id), artifact=artifact)
        return {
            "inquiry_id": inquiry_id, "current_focus": inquiry.current_focus,
            "responsibility": inquiry.responsibility,
            "current_owner_session_id": inquiry.current_owner_session_id,
            "author_session_id": artifact.get("author_session_id") if artifact else None,
            "source_role": "review_interpretation",
            "status": state.status, "reason": state.reason,
            "contribution_ref": ref,
            "contribution_focus": artifact.get("focus") if artifact else None,
            "note": artifact["note"] if artifact else None,
            "source_readings": [],
        }, readings

    def contribution(self, ref):
        """Read an exact manuscript with the same originals as its normal delivery."""
        artifact = self.runtime.store.note(ref)
        if inquiry_id := artifact.get("inquiry_id"):
            return self._manuscript(inquiry_id, ref)
        result = self.topics.reader.result(artifact["session_id"], note_ref=ref)
        view = SourceReading.view(result)
        view["contribution_ref"] = view.pop("note_ref")
        return view, result["readings"]

    def contribution_tokens(self, ref):
        return manuscript_tokens(self.contribution(ref)[0])

    def inquiry_view(self, inquiry_id, *, seen=()):
        """Current scientific responsibility and actual progress, including work without a draft."""
        view, _ = self._manuscript(inquiry_id)
        if view["contribution_ref"] in seen:
            view.pop("note", None)
        view["source_readings"] = [
            SourceReading.view(row, seen=seen) for row in view["source_readings"]
        ]
        return view

    def context(self, inquiry_id):
        actor = self._actor(inquiry_id)
        current = actor.work[-1] if actor.work else None
        view = {
            "dispatch_open": not self.closed,
            "current_work": self.requests[current.request_id].view()
            if current and current.request_id else {"kind": "research"},
            "waiting_for": sorted(current.wait_for) if current else [],
            "my_requests": [r.view() for r in self.requests.values() if r.sender == inquiry_id],
            "awaiting_assignment": [
                r.view() for r in self.requests.values()
                if r.target is None and r.status == "pending"
            ] if inquiry_id == self.chief_inquiry else [],
            "team": [
                {key: value for key, value in self.inquiry_view(iid).items()
                 if key not in {"note", "source_readings"}}
                for iid in dict.fromkeys([*self.actors, *self.topics._topic_ids()])
                if iid != inquiry_id
            ],
        }
        if current and current.consultation:
            view["current_work"]["instruction"] = (
                "现在用 reply_consultation(request_id, content) 或直接回复正文回答本次咨询。"
                "答案归属该请求，不替换评审意见；如判断改变，另用 publish_manuscript 更新意见。"
                "需要他人解释可另行登记并明确等待；完成后继续原工作。"
            )
        elif view["current_work"]["kind"] == "research":
            # Once the author has framed its inquiry, the original assignment is history.
            view["current_work"] = {
                "kind": "research", "request_id": current.request_id if current else None,
                "responsibility": self.runtime.store.inquiry(inquiry_id).responsibility,
                "current_focus": self.runtime.store.inquiry(inquiry_id).current_focus,
            }
        return view

    def schedule(self, inquiry_id, followup=None):
        if self.closed:
            return {"status": "closed", "inquiry_id": inquiry_id}
        actor = self._actor(inquiry_id)
        # Re-observing a running arrangement is not another assignment.
        active = next((w for w in actor.work if not w.consultation and not w.finished), None)
        if active:
            if followup:
                active.wait_for.clear()
                self._deliver(inquiry_id, "arrangement", uuid4().hex, {
                    "event": "research_arrangement", "request_id": active.request_id,
                    "instruction": followup,
                }, {})
            return self.requests[active.request_id].view()
        request = _Request(
            "request-" + uuid4().hex, self.chief_inquiry, inquiry_id,
            followup or self.runtime.store.inquiry(inquiry_id).responsibility,
            "形成可用于项目整体评价的科学认识。", kind="research",
        )
        self.requests[request.request_id] = request
        work = _Work(request_id=request.request_id, followup=followup)
        if actor.work:
            actor.incoming.append(work)
        else:
            actor.work.append(work)
        actor.completed = False
        self._kick(inquiry_id)
        return request.view()

    def _inputs(self, inquiry_id, refs):
        state, notes, readings = self._state(inquiry_id), [], {}
        for ref in refs:
            handle = ref
            if handle in state.evidence:
                readings.update(expand_captured_units(state.evidence, [handle]))
            else:
                note, originals = self.contribution(ref)
                notes.append(note)
                readings.update(originals)
        return {"notes": notes}, readings

    @staticmethod
    def _cited_text(text, state):
        return compile_authored_body(text) if text else text

    def request(self, inquiry_id, args: RequestInquiry):
        if self.closed:
            return {"status": "closed"}
        state = self._state(inquiry_id)
        question = self._cited_text(args.question, state)
        scientific_use = self._cited_text(args.scientific_use, state)
        inputs, readings = self._inputs(
            inquiry_id, [*args.input_refs, *cited_ids(question), *cited_ids(scientific_use)]
        )
        known = set(self.actors) | set(self.topics._topic_ids())
        target = args.target if args.target in known else None
        request = _Request(
            "request-" + uuid4().hex, inquiry_id, target, question, scientific_use,
            inputs=inputs, readings=readings, expertise=args.target if target is None else None,
        )
        self.requests[request.request_id] = request
        if target:
            self.assign(request.request_id, target)
        elif self.chief_inquiry:
            self._deliver(self.chief_inquiry, "assignment", request.request_id,
                          {**request.view(), **inputs}, readings)
        result = request.view()
        if target:
            current, originals = self._manuscript(target)
            self._access(inquiry_id).receive(originals)
            if current["contribution_ref"]:
                self._actor(inquiry_id).received_contributions.add(current["contribution_ref"])
            result["current_contribution"] = {**current, "originals": render_originals(originals)}
        return result

    def assign(self, request_id, target_inquiry_id):
        request = self.requests[request_id]
        if self.closed:
            return {"status": "closed", "request_id": request_id}
        if request.status != "pending":
            return request.view()
        request.target, request.status = target_inquiry_id, "assigned"
        actor = self._actor(target_inquiry_id)
        state = self._state(target_inquiry_id)
        if not actor.work:
            actor.completed = state.status == "submitted"
        if not actor.work and state.status == "ready" and not state.note_ref:
            actor.work.append(_Work(request_id=request_id))
        else:
            actor.incoming.append(_Work(request_id=request_id, consultation=True))
        self._kick(target_inquiry_id)
        view = request.view()
        view["current_contribution"] = self._manuscript(target_inquiry_id)[0]
        return view

    def tools(self, inquiry_id):
        def estimate_request(arguments):
            args = RequestInquiry.model_validate_json(arguments)
            known = set(self.actors) | set(self.topics._topic_ids())
            return (
                manuscript_tokens(self._manuscript(args.target)[0]) if args.target in known else 0
            )

        async def request(args):
            return ToolResult(ok=True, content=self.request(inquiry_id, args))

        registry = ToolRegistry([Tool(
            "request_inquiry",
            "向已有作者请求科学解释，或请主审安排专业研究。登记后立即返回；"
            "已有稿可直接采用，需要等待时用 finish_research(wait_for=[request_id])。",
            RequestInquiry, request, read_only=False, manuscript_tokens=estimate_request,
        )])
        if inquiry_id == self.chief_inquiry:
            def estimate_transfer(arguments):
                args = TransferInquiry.model_validate_json(arguments)
                return manuscript_tokens(self._manuscript(args.inquiry_id)[0])

            async def transfer(args):
                session = await self.transfer(
                    args.inquiry_id, reason=args.reason, skills=args.skills,
                    model_role="chief" if args.author == "chief" else "researcher",
                    responsibility=args.responsibility,
                )
                return ToolResult(ok=True, content={
                    "inquiry_id": args.inquiry_id, "status": "owner_changed",
                    "work_pending": self._ready(args.inquiry_id),
                    "current_contribution": self._manuscript(args.inquiry_id)[0],
                    "author": args.author,
                }) if session else ToolResult(ok=True, content={"status": "closed"})

            registry.add(Tool(
                "transfer_inquiry", "转交现有职责给新作者或主审；保留原账户和材料。"
                "转交结束旧工作段，需通过 research_topics 的 followup 明确安排后续研究。",
                TransferInquiry, transfer, read_only=False, delegates=True,
                manuscript_tokens=estimate_transfer,
            ))
        return registry

    def _deliver(self, recipient, kind, key, content, readings):
        if self.closed:
            return
        actor = self._actor(recipient)
        actor.deliveries[(kind, key)] = (content, readings)
        if recipient == self.chief_inquiry:
            # A chief waits for useful progress, not unanimous final submission.
            for work in actor.work:
                work.wait_for.clear()
        self._changed.set()
        self._kick(recipient)

    def publish(self, inquiry_id, note_ref):
        if self.closed:
            return
        content, readings = self._manuscript(inquiry_id, note_ref)
        content["event"] = "contribution" if note_ref else "focus_changed"
        recipients = {
            r.sender for r in self.requests.values()
            if r.target == inquiry_id and r.sender and r.status != "cancelled"
        }
        if self.chief_inquiry:
            recipients.add(self.chief_inquiry)
        for recipient in recipients - {inquiry_id}:
            self._deliver(recipient, "contribution", inquiry_id, content, readings)

    def _ready(self, inquiry_id):
        actor = self._actor(inquiry_id)
        for work in actor.work:
            work.wait_for = {
                ref for ref in work.wait_for
                if self.requests[ref].status not in {"answered", "cancelled", "unavailable"}
            }
        incoming = any(w.consultation for w in actor.incoming) or (
            actor.incoming and not actor.work
        )
        return bool(incoming or actor.work and not actor.work[-1].wait_for)

    def _select_work(self, actor):
        if actor.incoming and (
            not actor.work or actor.work[-1].wait_for or not actor.work[-1].consultation
        ):
            # A new normal arrangement waits until the previous one has ended.
            index = next((i for i, w in enumerate(actor.incoming) if w.consultation), None)
            if index is not None:
                work = actor.incoming[index]
                del actor.incoming[index]
                actor.work.append(work)
            elif not actor.work:
                actor.work.append(actor.incoming.popleft())

    def _receive(self, inquiry_id, state, *, select_work=True):
        if self.closed:
            return False
        actor = self._actor(inquiry_id)
        basis_changed = (
            inquiry_id != self.chief_inquiry
            and self.topics.refresh_application_basis(inquiry_id, state)
        )
        if select_work and not state.authoring_phase:
            self._select_work(actor)
        deliveries = list(actor.deliveries.values())
        actor.deliveries.clear()
        work = actor.work[-1] if actor.work else None
        work_changed = bool(work and not work.announced)
        if work and not work.announced:
            work.announced = True
            current_work = self.context(inquiry_id)["current_work"]
            if work.followup:
                current_work["instruction"], work.followup = work.followup, None
            self.runtime._observe(state, {
                "kind": "current_work_changed", "current_work": current_work,
            })
            if work.request_id and self.requests[work.request_id].kind == "consultation":
                request = self.requests[work.request_id]
                # The question and its scientific use are a public work event. Originals
                # and other authors' notes are delivered separately with their identity.
                notes = [
                    note for note in request.inputs.get("notes", [])
                    if note["contribution_ref"] != state.note_ref
                ]
                if request.readings or notes:
                    deliveries.append(({
                        "event": "consultation_material", "request_id": work.request_id,
                        "notes": notes,
                    }, request.readings))
        for content, readings in deliveries:
            actor.received_contributions.update(visible_note_refs(content))
            self._access(inquiry_id).receive(readings)
            self.runtime._capture(state, readings)
            received = readings
            self.runtime._observe(state, {
                "kind": "author_delivery", "output": {
                    "result": ToolResult(ok=True, content=content).model_dump(mode="json"),
                    "readings": received,
                },
            })
        if basis_changed:
            self.runtime.store.save(state)
        return bool(deliveries) or work_changed or basis_changed

    def _answer(self, request_id, reason, *, status="answered", state=None, reply_ref=None):
        request = self.requests[request_id]
        request.status = status
        state = state or self._state(request.target)
        if reply_ref:
            artifact = self.runtime.store.reply(reply_ref)
            content = {"reply_ref": reply_ref, "reply": artifact["reply"],
                       "inquiry_id": request.target, "author_session_id": state.session_id,
                       "source_role": "review_interpretation"}
            readings = {**self._manuscript(request.target)[1], **artifact["evidence"]}
        elif request.kind == "consultation":
            content, readings = {"inquiry_id": request.target}, {}
            request.status = "unavailable"
        else:
            content, readings = self._manuscript(request.target)
        reason = self._cited_text(reason, state)
        readings = {**readings, **{key: state.evidence[key] for key in cited_ids(reason)}}
        request.answer = {**content, "reason": reason}
        recipients = {request.sender, self.chief_inquiry} - {None, request.target}
        for recipient in recipients:
            self._deliver(recipient, "answer", request_id, request.view(full=True), readings)

    async def _finish(self, inquiry_id, state, reason, wait_for):
        actor = self._actor(inquiry_id)
        work = actor.work[-1] if actor.work else None
        if wait_for:
            # IDs express explicit dependencies; unrelated outstanding questions do not block.
            missing = [ref for ref in wait_for if ref not in self.requests]
            if missing:
                raise ValueError("unknown_inquiry_request")
            work.wait_for = set(wait_for)
            if inquiry_id == self.chief_inquiry and actor.deliveries:
                work.wait_for.clear()
            return "continue" if self._ready(inquiry_id) else "waiting"
        if work and work.consultation:
            if self.requests[work.request_id].answer is None:
                self._answer(work.request_id, reason, status="unavailable", state=state)
            actor.work.pop()
            if actor.work:
                actor.work[-1].announced = False
            return "continue" if self._ready(inquiry_id) else "waiting"
        if inquiry_id == self.chief_inquiry:
            self._receive(inquiry_id, state, select_work=False)
            await self.close(chief_finalizing=True)
        if work:
            work.finished = True
        return "finish"

    def _kick(self, inquiry_id):
        actor = self._actor(inquiry_id)
        if (
            self.closed or actor.transferring or actor.task is not None
            or not self._ready(inquiry_id)
        ):
            return
        actor.task = asyncio.create_task(self._drive(inquiry_id))

    async def _drive(self, inquiry_id):
        actor = self._actor(inquiry_id)
        try:
            while not self.closed and self._ready(inquiry_id):
                self._select_work(actor)
                work = actor.work[-1]
                consulting = not any(not w.consultation for w in actor.work)
                hook = _AuthorHook(self, inquiry_id, consulting=consulting)
                actor.hook = hook
                followup, work.followup = work.followup, None
                if inquiry_id == self.chief_inquiry:
                    await self.chief._run_segment(
                        self.chief_id, collaboration=hook, followup=followup,
                        mode=actor.mode, allow_research=actor.allow_research,
                    )
                else:
                    await self.topics._run_segment(
                        inquiry_id, collaboration=hook, followup=followup,
                    )
                state = self._state(inquiry_id)
                if state.status in {"partial", "failed"}:
                    for item in [*actor.work, *actor.incoming]:
                        if item.request_id:
                            self._answer(item.request_id, state.reason, status="unavailable")
                    actor.work.clear()
                    actor.incoming.clear()
                    break
                if state.status == "submitted":
                    for item in actor.work:
                        if item.consultation:
                            self._answer(
                                item.request_id,
                                "本轮研究已收束，咨询尚未形成答复；当前稿可供参考。",
                                status="unavailable",
                            )
                        elif item.request_id:
                            self._answer(item.request_id, "本轮科学研究已提交。")
                    actor.work.clear()
                    actor.completed = True
                if not actor.work and not actor.incoming and actor.completed:
                    state.status = "submitted"
                    self.runtime.store.save(state)
        except Exception as exc:
            actor.error = exc
            for item in [*actor.work, *actor.incoming]:
                if item.request_id:
                    self._answer(item.request_id, type(exc).__name__, status="unavailable")
            actor.work.clear()
            actor.incoming.clear()
        finally:
            actor.task = None
            actor.hook = None
            self._changed.set()

    async def _wait(self, inquiry_id):
        actor = self._actor(inquiry_id)
        while True:
            self._changed.clear()
            if actor.error:
                raise actor.error
            if not actor.task and (actor.completed or self.closed):
                return
            if not any(a.task for a in self.actors.values()):
                snapshot = tuple((r.request_id, r.status) for r in self.requests.values())
                if (
                    inquiry_id == self.chief_inquiry and actor.work
                    and snapshot != self._idle_notice
                ):
                    self._idle_notice = snapshot
                    self._deliver(inquiry_id, "team_waiting", "team", {
                        "event": "team_waiting",
                        "instruction": "当前作者都已让出执行，待答问题与当前稿见协作上下文。"
                        "请决定如何推进科学问题：安排解释、转交职责，或用现有成果结束研究。",
                    }, {})
                    continue
                # No hidden polling or automatic retry: return explicit waiting to the caller.
                return
            await self._changed.wait()

    async def run_topic(self, inquiry_id, *, followup=None):
        actor = self._actor(inquiry_id)
        if actor.completed and followup is None:
            return self.topics.result(inquiry_id)
        self.schedule(inquiry_id, followup=followup)
        await self._wait(inquiry_id)
        return self.topics.result(inquiry_id)

    async def run_chief(self, chief_id, *, followup=None, mode=None, allow_research=True):
        if followup is not None and allow_research and self.closed:
            self.reopen()
        actor = self._actor(self.chief_inquiry)
        actor.mode, actor.allow_research = mode, allow_research
        if actor.completed and followup is None and mode != "assessment":
            return self.chief.result(chief_id)
        # Later report composition is the same chief, with research dispatch still closed.
        if self.closed:
            hook = _AuthorHook(self, self.chief_inquiry, consulting=False)
            return await self.chief._run_segment(
                chief_id, collaboration=hook, followup=followup,
                mode=mode, allow_research=allow_research,
            )
        actor.work.append(_Work(followup=followup))
        actor.completed = False
        self._kick(self.chief_inquiry)
        await self._wait(self.chief_inquiry)
        return self.chief.result(chief_id)

    def reopen(self):
        """Begin explicitly requested new research in this live team, retaining its knowledge."""
        self.closed = False
        self.selected_contributions.clear()
        self._idle_notice = None
        for actor in self.actors.values():
            actor.work.clear()
            actor.incoming.clear()
            actor.deliveries.clear()
            actor.error = None
        self._actor(self.chief_inquiry).completed = False
        self._changed.set()

    async def transfer(
        self, inquiry_id, *, reason, skills=None, model_role=None, responsibility=None,
    ):
        if self.closed:
            return None
        actor = self._actor(inquiry_id)
        actor.transferring = True
        try:
            if actor.task:
                if actor.hook:
                    actor.hook.handoff = True
                task = actor.task
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                actor.task = None
            session = self.topics.transfer_owner(
                inquiry_id, reason=reason, skills=skills, model_role=model_role,
                responsibility=responsibility,
            )
            for work in [*actor.work, *actor.incoming]:
                if work.request_id:
                    self._answer(
                        work.request_id,
                        "职责已转交，旧工作段已结束；现有稿和阅读成果可用，后续研究需明确安排。",
                        status="unavailable",
                    )
            actor.work.clear()
            actor.incoming.clear()
            actor.completed = False
            actor.error = None
        finally:
            actor.transferring = False
        self._changed.set()
        return session

    async def close(self, *, chief_finalizing=False):
        if not self.closed:
            self.closed = True
            self.selected_contributions = {
                iid: inquiry.current_contribution_ref for iid in self.topics._topic_ids()
                if (inquiry := self.runtime.store.inquiry(iid)).current_contribution_ref
            }
        # finish_research runs in a tool-handler child task; do not cancel its chief parent.
        current = asyncio.current_task()
        tasks = [
            a.task for iid, a in self.actors.items()
            if a.task and a.task is not current
            and not (chief_finalizing and iid == self.chief_inquiry)
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            for actor in self.actors.values():
                if actor.task in tasks:
                    actor.task = None
        for request in self.requests.values():
            if request.status in {"pending", "assigned"}:
                request.status = "cancelled"
        self._changed.set()
