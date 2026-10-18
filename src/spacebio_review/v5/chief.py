"""One continuing chief reads the application, steers topics, and absorbs their work."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from spacebio_review.models.provider import ModelError
from spacebio_review.persistence import plain, write_json
from spacebio_review.review.evaluation import get_evaluation_system
from spacebio_review.tools import Tool, ToolResult

from .records import SessionTask
from .review_context import build_review_context
from .source_reading import visible_note_refs
from .topic import ApplicationRef, TopicResearch, TopicTask

CHIEF_INSTRUCTION = """你是持续工作的主审，理解计划怎样产生有价值的认识，并依据本轮体系评价。
先将申请的目标、对象与安排、预期数据、可形成的知识及其价值连成项目理解，提出最值得
研究的问题。具体安排以原句和引用为依据，区分已具备的基础、拟开展工作和未说明条件。
对研究计划采用与其阶段相称的尺度：方法适配与预研可以是合理工作，描述性、探索性和
机制性研究各有价值。已有支持时明确肯定贡献；真正影响判断的困难说明其作用范围，
不把一条方法限制扩展为所有数据都没有价值，也不以全球首次作为认定贡献的唯一依据。
初始理解后形成专家研究安排，用 research_topics 交付：objective 提出影响知识产出的
科学疑问，impact 说明答案的用途，application_refs 选择相关原句，expertise 和 skills
配置相应专业能力。按科学联系组织问题，将可独立研究的问题放在同一批 requests。
程序执行这些专家任务，返回完整研究成果后进入主审综合。优先级由科学重要性和对评价的
作用决定，不预设查不到即首次或预期升降分。已有任务可用 topic_id 与 followup 续问。
专家结果返回后直接阅读完整意见、论文说明和必要原文，再选择回读、续问、研究新问题或
结束研究，不必先另写一份批次综述。技术中断前已形成的成果仍按其实际依据使用。
需要修正当前理解时用 update_understanding 说明原因，随后更新完整工作稿；历史原稿
留档，当前稿保留仍成立的认识与真正重要的未解问题，不复制所有论文说明。
规划时用 read_skills 了解专业能力；综合时可使用资料工具回读或补查，
read_source_notes 回读单篇解释，research_topics 安排新问题或续问原专家。
已有知识足以评价当前计划，或剩余信息不再值得外部投入时，用 finish_research 说明理由。
最终评价直接使用申请、当前理解和专业成果，明确解释科学价值、实现可能及主要限制。
"""


class TopicRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: TopicTask | None = Field(default=None, description="新建专题时只填写 task。")
    topic_id: str | None = Field(default=None, description="续问时填写已返回的 topic_id。")
    followup: str | None = Field(default=None, description="续问要进一步解决的问题。")
    application_refs: list[ApplicationRef] = Field(
        default_factory=list,
        description="仅续问时补充的新申请位置；新专题的原文依据填写 task.application_refs。",
    )

    @model_validator(mode="after")
    def new_or_continued(self):
        if self.task is not None:
            if self.topic_id is not None or self.followup is not None or self.application_refs:
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
        application = self.topics.application_context(access, role="chief")
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
        state = self.runtime.load(chief_id)
        state.coordination_phase = "planning"
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
                    "turns_used": result.turns_used,
                    "source_readings": [
                        self.topics.reader.view(row, full=False) for row in result.source_readings
                    ],
                }
            )
        return rows

    def _context(self, chief_id):
        manifest = self._manifest(chief_id)
        from .assessment import AssessmentSubmission

        seen = visible_note_refs(self.runtime.load(chief_id).observations)
        topics = []
        for topic_id in manifest["topic_ids"]:
            result = self.topics.result(topic_id)
            topics.append(
                {
                    **result.model_view(seen=seen),
                    "objective": self.runtime.load(topic_id).task.objective,
                }
            )
            seen.update(visible_note_refs(topics[-1]))
        return {
            "topics": topics,
            "loaded_skills": manifest["loaded_skills"],
            "assessment": AssessmentSubmission(self, chief_id).context()
            if manifest.get("mode") == "assessment"
            else None,
        }

    async def _research(self, chief_id, args):
        manifest = self._manifest(chief_id)
        batch = []
        # Allocate identities before awaiting children. A failed allocation affects only
        # that request; independent topics still run. Same-topic followups are ordered.
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
                batch.append({"topic_id": topic_id, "followup": request.followup, "result": None})
            except (KeyError, ValueError, ModelError) as exc:
                batch.append(
                    {
                        "result": {
                            "topic_id": request.topic_id,
                            "status": "not_started",
                            "reason": str(exc),
                        }
                    }
                )
        manifest["pending_batch"] = batch
        write_json(self._path(chief_id), manifest)
        return await self._execute_batch(chief_id)

    async def _execute_batch(self, chief_id):
        """Run the submitted arrangement, including saved work after interruption."""
        manifest = self._manifest(chief_id)
        batch = manifest["pending_batch"]
        access = self.access(chief_id)
        jobs = {}
        for row in batch:
            if row["result"] is None:
                jobs.setdefault(row["topic_id"], []).append(row)

        async def work(topic_id, requests):
            for row in requests:
                try:
                    followup = row["followup"]
                    # A resumed author already carries its active follow-up in saved state.
                    saved = self.runtime.load(topic_id)
                    if saved.followup == followup and saved.status in {
                        "running",
                        "partial",
                        "failed",
                    }:
                        followup = None
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
                # Originals enter once through the runtime evidence view; the complete
                # archive stays in deliveries rather than recurring in every chief turn.
                row["result"] = result.model_view()
                write_json(self._path(chief_id), manifest)

        await asyncio.gather(*(work(topic_id, requests) for topic_id, requests in jobs.items()))
        accepted = all(row["result"].get("status") != "not_started" for row in batch)
        return ToolResult(
            ok=accepted,
            error=None if accepted else "research_arrangement_not_submitted",
            content={
                "topics": [row["result"] for row in batch],
                "next": (
                    "专家成果已返回，可直接用于判断；按需要回读、续问、研究新问题或结束研究。"
                    if accepted
                    else "已执行任务的成果见 topics；根据提交失败的原因修订其余研究安排。"
                ),
                "resources": self.runtime.resources(chief_id),
            },
        )

    def _complete_batch(self, chief_id, state, result):
        if result.ok:
            state.coordination_phase = "synthesis"
        self.runtime.store.save(state)
        manifest = self._manifest(chief_id)
        manifest.pop("pending_batch", None)
        write_json(self._path(chief_id), manifest)

    def tools(self, chief_id, *, state=None):
        state = state or self.runtime.load(chief_id)
        access = self.access(chief_id)
        registry = access.tools()
        self.topics.reader.attach_notes(registry, access)

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
            result = await self._research(chief_id, args)
            self._complete_batch(chief_id, state, result)
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
        if state.coordination_phase == "planning":
            # Planning works from the application and capability catalogue. External
            # investigation belongs to the experts executing the resulting arrangement.
            return registry.select(
                [
                    "browse_materials",
                    "read_materials",
                    "read_skills",
                    "research_topics",
                ]
            )
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
            # Reattach finished child work after an interrupted parent, before its
            # context is built. Stable evidence handles receive this chief's aliases.
            access = self.access(chief_id)
            for topic_id in manifest["topic_ids"]:
                access.receive(self.topics.result(topic_id).readings)
            state = self.runtime.load(chief_id)
            try:
                if manifest.get("pending_batch"):
                    result = await self._execute_batch(chief_id)
                    readings = self.runtime._capture(
                        state, access.references.snapshot_choices(selected=False)
                    )
                    self.runtime._observe(
                        state,
                        {
                            "kind": "tool",
                            "name": "research_topics",
                            "output": self.runtime._tool_payload(result, readings, state.aliases),
                        },
                    )
                    self._complete_batch(chief_id, state, result)
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
            "initial_understanding_ref": state.initial_understanding_ref,
            "current_understanding_ref": state.note_ref,
            "topics": self._coverage(chief_id),
            "turns_used": state.turns_used,
            "usage": self.runtime.budget.usage() if self.runtime.budget else None,
            **AssessmentSubmission(self, chief_id).result(),
        }

    async def close(self):
        await self.topics.close()
