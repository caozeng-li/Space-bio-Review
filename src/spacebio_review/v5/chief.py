"""One continuing chief reads the application, steers topics, and absorbs their work."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from spacebio_review.models.provider import ModelError
from spacebio_review.persistence import plain, write_json
from spacebio_review.review.evaluation import EvaluationSystem, get_evaluation_system
from spacebio_review.tools import Tool, ToolResult

from .records import SessionTask
from .review_context import build_review_context
from .topic import TopicResearch, TopicTask

CHIEF_INSTRUCTION = """你是本项目持续工作的主审，自己阅读完整申请并组织科学研究。
先在自己的工作说明中理解目标、对象、比较、实施阶段、读出和拟支持的结论；新的认识
可以修订这份理解。评价标准给出判断语境，按当前任务的停止点提交研究稿或完成最终评价。
用 update_agenda 保存当前理解和简短研究议程：值得研究什么、为何影响判断、已有专题
及尚未解决的问题。议程可以增加、合并关注点或结束方向，不必按评价指标逐项派题。
可直接使用资料工具阅读或检索；需要独立投入时用 research_topics，将独立专题放在
同一批 requests 中。新专题提供 task，续问提供原 topic_id 与 followup，延续同一作者。
专题返回完整研究说明、关键原文和实际阅读范围。结合申请消化结果，将影响项目理解
的发现写入自己的研究说明，必要时回读原文或向原作者追问；不同结论可以保留分歧。
以研究价值决定后续投入；请求中说明科学问题及影响，不预设项目缺少或具备某条件。
研究议程优先弄清决定项目知识增量的环节：实际读出测到什么、对照区分哪些解释、
外部先例对本项目哪些条件有效。对关键方法，用方法名称及其信号来源查找直接验证，
再把结果带回申请；领域背景和其他器官的成功先例只能回答它们实际涉及的问题。
能力目录供按需选择，可用 read_skills 阅读方法；派题时选择相关 expertise 或 skills。
专题的技术中断和科学未知分别理解。已有工作稿可使用其明确依据，技术失败不构成
项目缺陷。研究稿新正文用首行「交付操作：submit_research」交付；最终评价任务以首行
「交付操作：submit_assessment」写完整普通评价正文，或按 save_assessment 分批保存；
研究与评价正文响应都不同时调用工具。评价提交后读取代码计算结果，
再形成综合评价与摘要。两种任务均保留有意义的未决事项，由当前同一主审连续完成。
"""


class AgendaItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str = Field(min_length=1)
    why: str
    topic_ids: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    disposition: Literal["active", "closed"] = "active"


class Agenda(BaseModel):
    model_config = ConfigDict(extra="forbid")
    working_understanding: str = ""
    items: list[AgendaItem] = Field(default_factory=list)


class TopicRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: TopicTask | None = None
    topic_id: str | None = None
    followup: str | None = None

    @model_validator(mode="after")
    def new_or_continued(self):
        if self.task is not None:
            if self.topic_id is not None or self.followup is not None:
                raise ValueError("new_topic_uses_task_only")
        elif not self.topic_id or not self.followup or not self.followup.strip():
            raise ValueError("continued_topic_requires_topic_id_and_followup")
        return self


class TopicBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requests: list[TopicRequest] = Field(min_length=1)


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
        application = self.topics.application_context(access)
        chief_id = self.runtime.create(
            SessionTask(
                role="chief",
                objective=objective,
                application=application,
                evaluation=json.dumps(
                    {"scientific_role": CHIEF_INSTRUCTION, **self.review_context},
                    ensure_ascii=False,
                ),
                skills=[self.system.skill],
            ),
            readings=access.references.snapshot_choices(selected=False),
            allowance=allowance,
        )
        write_json(
            self._path(chief_id),
            {
                "schema_version": "v5-chief-1",
                "chief_id": chief_id,
                "evaluation_system": self.system.id,
                "review_context": self.review_context,
                "cutoff": self.topics.cutoff.isoformat() if self.topics.cutoff else None,
                "agenda": Agenda().model_dump(),
                "topic_ids": [],
                "loaded_skills": {},
            },
        )
        access.state_path = self.root / "source-access" / (chief_id + ".json")
        access.save()
        self._access[chief_id] = access
        return chief_id

    def access(self, chief_id):
        if chief_id not in self._access:
            # The saved session, rather than a new caller's settings, owns this review.
            manifest = self._manifest(chief_id)
            state = self.runtime.load(chief_id)
            access = self.topics.source_access(
                state.task.objective,
                session_id=chief_id,
                cutoff=date.fromisoformat(manifest["cutoff"]) if manifest["cutoff"] else None,
            )
            if access.state_path.exists():
                access.restore_state(json.loads(access.state_path.read_text("utf-8")))
            access.receive(state.evidence)
            self._access[chief_id] = access
        return self._access[chief_id]

    def _coverage(self, chief_id):
        rows = []
        for topic_id in self._manifest(chief_id)["topic_ids"]:
            result = self.topics.result(topic_id)
            rows.append(
                {
                    "topic_id": topic_id,
                    "objective": self.runtime.load(topic_id).task.objective,
                    "status": result.status,
                    "reason": result.reason,
                    "note_ref": result.note_ref,
                    "title": result.note["title"] if result.note else None,
                    "unknowns": result.note["unknowns"] if result.note else [],
                    "reading_records": result.reading_records,
                    "turns_used": result.turns_used,
                }
            )
        return rows

    def _context(self, chief_id):
        manifest = self._manifest(chief_id)
        from .assessment import AssessmentSubmission

        return {
            "agenda": manifest["agenda"],
            "topics": self._coverage(chief_id),
            "loaded_skills": manifest["loaded_skills"],
            "assessment": AssessmentSubmission(self, chief_id).context()
            if manifest.get("mode") == "assessment"
            else None,
        }

    async def _research(self, chief_id, args):
        manifest = self._manifest(chief_id)
        access = self.access(chief_id)
        jobs, output = {}, [None] * len(args.requests)
        # Allocate identities before awaiting children. A failed allocation affects only
        # that request; independent topics still run. Same-topic followups are ordered.
        for index, request in enumerate(args.requests):
            try:
                if request.task is not None:
                    system = EvaluationSystem.model_validate(
                        manifest["review_context"]["evaluation"]
                    )
                    topic_id = self.topics.create(
                        request.task,
                        evaluation=system.prompt(system.aspects),
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
                jobs.setdefault(topic_id, []).append((index, request.followup))
            except (KeyError, ValueError, ModelError) as exc:
                output[index] = {
                    "topic_id": request.topic_id,
                    "status": "not_started",
                    "reason": str(exc),
                }

        async def work(topic_id, requests):
            for index, followup in requests:
                try:
                    result = await self.topics.run(topic_id, followup=followup)
                except Exception:
                    # The session has already saved its actual failure and public draft.
                    result = self.topics.result(topic_id)
                finally:
                    if self.runtime.budget:
                        # A stopped child no longer reserves a future closing request;
                        # continuation reactivates its reserve without resetting usage.
                        self.runtime.budget.finish(topic_id)
                access.receive(result.readings)
                # A returned directory is an entry point even when the author did not
                # finish a note. It makes sources obtainable, not already read.
                access.visible.update(item["source_id"] for item in result.materials)
                access.save()
                row = result.model_dump(mode="json")
                # Exact originals enter through the common evidence view below. The note
                # retains bound handles until the runtime translates them to chief E ids.
                row.pop("readings")
                output[index] = row

        await asyncio.gather(*(work(topic_id, requests) for topic_id, requests in jobs.items()))
        return ToolResult(
            ok=True,
            content={"topics": output, "resources": self.runtime.resources(chief_id)},
        )

    def tools(self, chief_id):
        access = self.access(chief_id)
        registry = access.tools()

        async def update_agenda(args):
            manifest = self._manifest(chief_id)
            referenced = {tid for row in args.items for tid in row.topic_ids}
            if referenced - set(manifest["topic_ids"]):
                return ToolResult(ok=False, error="unknown_chief_topic")
            manifest["agenda"] = args.model_dump(mode="json")
            write_json(self._path(chief_id), manifest)
            return ToolResult(ok=True, content={"agenda": manifest["agenda"]})

        async def read_skills(args):
            manifest = self._manifest(chief_id)
            loaded = {}
            for name in args.names:
                skill = self.runtime.skills.get(name)
                loaded[name] = {"body": skill.body, "references": skill.references}
            manifest["loaded_skills"].update(loaded)
            write_json(self._path(chief_id), manifest)
            return ToolResult(ok=True, content=loaded)

        async def research(args):
            return await self._research(chief_id, args)

        for tool in (
            Tool(
                "update_agenda",
                "保存当前项目理解和完整新议程；专题执行状态由代码提供。",
                Agenda,
                update_agenda,
                read_only=False,
            ),
            Tool(
                "read_skills",
                "按能力目录中的 skill 名称读取方法全文及参考。",
                ReadSkills,
                read_skills,
            ),
            Tool(
                "research_topics",
                "批量研究独立专题；新建用 task，续问用原 topic_id 和 followup。"
                "返回完整说明及关键原文。",
                TopicBatch,
                research,
                read_only=False,
                delegates=True,
                timeout_seconds=self.runtime.budget.remaining_seconds()
                if self.runtime.budget
                else 14400,
            ),
        ):
            registry.add(tool)
        return registry

    async def run(self, chief_id, *, followup=None, mode=None, allow_research=True):
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
            try:
                await self.runtime.run(
                    chief_id,
                    tools=self.tools(chief_id) if allow_research else None,
                    followup=followup,
                    context=lambda: self._context(chief_id),
                    submission=submission,
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
            "agenda": self._manifest(chief_id)["agenda"],
            "topics": self._coverage(chief_id),
            "turns_used": state.turns_used,
            "usage": self.runtime.budget.usage() if self.runtime.budget else None,
            **AssessmentSubmission(self, chief_id).result(),
        }

    async def close(self):
        await self.topics.close()
