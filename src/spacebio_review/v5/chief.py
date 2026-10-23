"""One continuing chief reads the application, steers topics, and absorbs their work."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from spacebio_review.models.provider import ModelError
from spacebio_review.persistence import plain, write_json
from spacebio_review.review.evaluation import get_evaluation_system
from spacebio_review.tools import Tool, ToolResult

from .records import SessionTask
from .review_context import build_research_context, build_review_context, review_skill_context
from .source_reading import visible_note_refs
from .topic import ApplicationRef, TopicResearch, TopicTask


class TopicRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: TopicTask | None = Field(default=None, description="委派给专家的评审问题。")
    topic_id: str | None = Field(
        default=None, description="续问时填写已返回的 topic_id，即稳定的 inquiry_id。"
    )
    followup: str | None = Field(default=None, description="续问要进一步解决的问题。")
    contribution_lead: bool = Field(
        default=False,
        description="把项目整体价值评价交给此专题；已有专题可仅指定负责人。",
    )
    request_id: str | None = Field(
        default=None, min_length=1,
        description="将已有待安排咨询分配给本专题；不再重复填写 followup。",
    )
    application_refs: list[ApplicationRef] = Field(
        default_factory=list,
        description="仅续问时补充的新申请位置；新专题的原文依据填写 task.application_refs。",
    )

    @model_validator(mode="after")
    def new_or_continued(self):
        if self.request_id and self.followup is not None:
            raise ValueError("assigned_request_does_not_repeat_followup")
        if self.task is not None:
            if self.topic_id is not None or self.followup is not None or self.application_refs:
                raise ValueError("new_topic_uses_task_only")
        else:
            if not self.topic_id or not (
                self.contribution_lead
                or self.request_id or self.followup and self.followup.strip()
            ):
                raise ValueError("continued_topic_requires_followup_request_or_contribution_lead")
        return self


class TopicBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requests: list[TopicRequest] = Field(min_length=1)


def topic_batch_type(catalogue):
    """Expose actual catalogue choices in the generation schema."""
    if catalogue is None:
        return TopicBatch
    task = create_model(
        "TopicAssignment", __base__=TopicTask,
        expertise=(list[Literal[tuple(catalogue.entries)]], Field(
            default_factory=list, description=TopicTask.model_fields["expertise"].description,
        )),
    )
    request = create_model(
        "TopicRequest", __base__=TopicRequest, task=(task | None, None),
    )
    return create_model(
        "TopicBatch", __base__=TopicBatch, requests=(list[request], Field(min_length=1)),
    )


class ReadSkills(BaseModel):
    model_config = ConfigDict(extra="forbid")
    names: list[str] = Field(min_length=1)


class ChiefReview:
    def __init__(self, topics, *, evaluation_system="general", admission):
        self.topics, self.runtime = topics, topics.runtime
        self.root = self.runtime.store.root
        self.system = get_evaluation_system(evaluation_system)
        self.review_context = build_review_context(evaluation_system, topics.catalogue, admission)
        self._access, self._locks = {}, {}

    @classmethod
    async def from_config(cls, path, root, application, *, admission, env_file=".env", cutoff=None):
        from .config import load_config

        config = load_config(path)
        topics = await TopicResearch.from_config(
            path, root, application, env_file=env_file, cutoff=cutoff
        )
        try:
            return cls(topics, evaluation_system=config.evaluation_system, admission=admission)
        except BaseException:
            await topics.close()
            raise

    def _path(self, chief_id):
        return self.runtime.store._path("chiefs", chief_id)

    def _manifest(self, chief_id):
        return json.loads(self._path(chief_id).read_text("utf-8"))

    def create(self, objective, *, allowance=None):
        access = self.topics.source_access(objective)
        application = self.topics.application_context(access, role="chief")
        chief_id = self.runtime.create(
            SessionTask(
                role="chief",
                objective=objective,
                application=application,
                evaluation=build_research_context(self.review_context),
                skills=[self.system.skill],
            ),
            readings=access.references.snapshot_choices(selected=False),
            allowance=allowance,
        )
        state = self.runtime.load(chief_id)
        state.coordination_phase = "planning"
        state.contribution_inquiry_id = state.inquiry_id
        self.runtime.store.save(state)
        write_json(
            self._path(chief_id),
            {
                "chief_id": chief_id,
                "evaluation_system": self.system.id,
                "review_context": self.review_context,
                "cutoff": self.topics.cutoff.isoformat() if self.topics.cutoff else None,
                "topic_ids": [],
                "loaded_skills": {},
            },
        )
        access.state_path = self.root / "source-access" / (chief_id + ".json")
        access.save()
        self._access[chief_id] = access
        self.topics.collaboration.bind_chief(self, chief_id)
        return chief_id

    def access(self, chief_id):
        state = self.runtime.load(chief_id)
        inquiry = self.runtime.store.inquiry(state.inquiry_id)
        objective = inquiry.current_focus or state.task.objective
        self._access[chief_id].objective = objective
        return self.topics.configure_access(self._access[chief_id], state)

    def _coverage(self, chief_id):
        rows = []
        for topic_id in self._manifest(chief_id)["topic_ids"]:
            result = self.topics.result(topic_id)
            rows.append(
                {
                    "topic_id": topic_id,
                    "responsibility": result.responsibility,
                    "current_focus": result.current_focus,
                    "contribution_focus": result.contribution_focus,
                    "status": result.status,
                    "reason": result.reason,
                    "note_ref": result.note_ref,
                    "title": result.note["title"] if result.note else None,
                    "turns_used": result.turns_used,
                    "source_readings": [
                        self.topics.reader.view(row, full=False) for row in result.source_readings
                    ],
                }
            )
        return rows

    def contribution_context(self, state, *, seen=()):
        """The current owner and scientific materials remain visible even without a draft."""
        return self.topics.collaboration.inquiry_view(
            state.contribution_inquiry_id, seen=seen
        )

    def _context(self, chief_id):
        self.access(chief_id)
        manifest = self._manifest(chief_id)
        from .assessment import AssessmentSubmission

        state = self.runtime.load(chief_id)
        seen = visible_note_refs(state.observations)
        if state.note_ref:
            seen.add(state.note_ref)
        contribution = self.contribution_context(state, seen=seen)
        seen.update(visible_note_refs(contribution))
        topics = []
        for topic_id in manifest["topic_ids"]:
            result = self.topics.result(topic_id)
            topics.append(result.model_view(seen=seen))
            seen.update(visible_note_refs(topics[-1]))
        return {
            "core_contribution": contribution,
            "topics": topics,
            "loaded_skills": manifest["loaded_skills"],
            "collaboration": self.topics.collaboration.context(
                state.inquiry_id
            ),
            "assessment": AssessmentSubmission(self, chief_id).context()
            if manifest.get("mode") == "assessment"
            else None,
        }

    async def _research(self, chief_id, args, *, state=None):
        state = state or self.runtime.load(chief_id)
        manifest = self._manifest(chief_id)
        progress = []
        for request in args.requests:
            try:
                if request.task is not None:
                    topic_id = self.topics.create(
                        request.task,
                        cutoff=date.fromisoformat(manifest["cutoff"])
                        if manifest["cutoff"]
                        else None,
                    )
                    manifest["topic_ids"].append(topic_id)
                    write_json(self._path(chief_id), manifest)
                else:
                    topic_id = request.topic_id
                    if topic_id not in manifest["topic_ids"]:
                        raise ValueError("topic_not_owned_by_chief")
                    self.topics.supplement_application(topic_id, request.application_refs)
                if request.contribution_lead:
                    state.contribution_inquiry_id = topic_id
                    self.runtime.store.save(state)
                if request.request_id:
                    row = self.topics.collaboration.assign(request.request_id, topic_id)
                elif request.task is not None or request.followup:
                    row = self.topics.collaboration.schedule(topic_id, followup=request.followup)
                else:
                    row = {"inquiry_id": topic_id, "status": "lead_assigned"}
                progress.append({
                    "topic_id": topic_id, "contribution_lead": request.contribution_lead, **row
                })
            except (KeyError, ValueError, ModelError) as exc:
                progress.append({
                    "topic_id": request.topic_id, "status": "not_started", "reason": str(exc),
                })
        accepted = all(row.get("status") != "not_started" for row in progress)
        return ToolResult(
            ok=accepted,
            error=None if accepted else "research_arrangement_not_submitted",
            content={
                "topics": progress,
                "contribution_inquiry_id": state.contribution_inquiry_id,
                "next": (
                    "安排已登记；有 request_id 的工作可等待进展。仅指定核心贡献负责人"
                    "保持现有工作状态，需要新研究时明确提供 followup。"
                    if accepted
                    else "已执行任务的成果见 topics；根据提交失败的原因修订其余研究安排。"
                ),
                "resources": self.runtime.resources(chief_id),
            },
        )

    def tools(self, chief_id, *, state=None):
        state = state or self.runtime.load(chief_id)
        access = self.access(chief_id)
        registry = access.tools()
        self.topics.reader.attach_notes(registry, access)
        registry.include(self.topics.collaboration.tools(state.inquiry_id))

        async def read_skills(args):
            manifest = self._manifest(chief_id)
            loaded = {}
            for name in args.names:
                skill = self.runtime.skills.get(name)
                loaded[name] = review_skill_context(skill)
            manifest["loaded_skills"].update(loaded)
            write_json(self._path(chief_id), manifest)
            return ToolResult(ok=True, content=loaded)

        async def research(args):
            result = await self._research(chief_id, args, state=state)
            if result.ok:
                state.coordination_phase = "synthesis"
            self.runtime.store.save(state)
            return result

        for tool in (
            Tool(
                "read_skills",
                "按能力目录中的 skill 名称读取方法全文及参考。",
                ReadSkills,
                read_skills,
            ),
            Tool(
                "research_topics",
                "登记独立专题后立即返回进展；新建用 task，续问用 topic_id 和 followup。"
                "为待安排咨询指定作者时填写 request_id；contribution_lead=true 将核心"
                "贡献职责交给该专题，已有专题可以仅指定负责人。",
                topic_batch_type(self.topics.catalogue),
                research,
                read_only=False,
                delegates=True,
                timeout_seconds=self.runtime.budget.remaining_seconds()
                if self.runtime.budget
                else 14400,
            ),
        ):
            registry.add(tool)
        if state.coordination_phase == "planning":
            # Planning works from the application and capability catalogue. External
            # investigation belongs to the experts executing the resulting arrangement.
            return registry.select(
                [
                    "browse_materials",
                    "read_materials",
                    "read_skills",
                    "research_topics",
                    "request_inquiry",
                    "transfer_inquiry",
                ]
            )
        return registry

    async def run(self, chief_id, *, followup=None, mode=None, allow_research=True):
        return await self.topics.collaboration.run_chief(
            chief_id, followup=followup, mode=mode, allow_research=allow_research
        )

    async def _run_segment(
        self, chief_id, *, collaboration, followup=None, mode=None, allow_research=True
    ):
        async with self._locks.setdefault(chief_id, asyncio.Lock()):
            from .assessment import AssessmentSubmission

            manifest = self._manifest(chief_id)
            mode = mode or manifest.get("mode", "research")
            if mode not in {"research", "assessment"}:
                raise ValueError("unknown_chief_mode")
            manifest["mode"] = mode
            write_json(self._path(chief_id), manifest)
            submission = AssessmentSubmission(self, chief_id) if mode == "assessment" else None
            if submission:
                submission.allow_research = allow_research
            access = self.access(chief_id)
            state = self.runtime.load(chief_id)
            try:
                self.runtime._capture(state, access.references.snapshot_choices(selected=False))
                self.runtime.store.save(state)
                await self.runtime.run(
                    chief_id,
                    tools=(lambda state: self.tools(chief_id, state=state))
                    if allow_research
                    else None,
                    followup=followup,
                    context=lambda: self._context(chief_id),
                    submission=submission,
                    collaboration=collaboration,
                )
            finally:
                self.access(chief_id).save()
                write_json(
                    self.root / "chief-results" / (chief_id + ".json"), self.result(chief_id)
                )
            return self.result(chief_id)

    async def assess(self, chief_id, *, allow_research=False, followup=None):
        return await self.run(
            chief_id, followup=followup, mode="assessment", allow_research=allow_research
        )

    def result(self, chief_id):
        from .assessment import AssessmentSubmission

        state, access = self.runtime.load(chief_id), self.access(chief_id)
        artifact = self.runtime.store.note(state.note_ref) if state.note_ref else None
        return {
            "chief_id": chief_id,
            "status": state.status,
            "reason": state.reason,
            "note_ref": state.note_ref,
            "note": deepcopy(artifact["note"]) if artifact else None,
            "readings": deepcopy(artifact["evidence"]) if artifact else {},
            "reading_records": plain(access.reading_records()),
            "current_understanding_ref": state.note_ref,
            "core_contribution": self.contribution_context(state),
            "topics": self._coverage(chief_id),
            "turns_used": state.turns_used,
            "usage": self.runtime.budget.usage() if self.runtime.budget else None,
            **AssessmentSubmission(self, chief_id).result(),
        }

    async def close(self):
        await self.topics.close()
