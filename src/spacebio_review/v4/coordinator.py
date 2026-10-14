"""Execute a frozen, ranked question list; no expert proposals or semantic replanning."""

import asyncio
import re
import traceback
from copy import deepcopy

from spacebio_review.agents.diagnostics import digest
from spacebio_review.models.provider import ModelError
from spacebio_review.review.project import REVIEW_ASPECTS

from .questions import QUESTION_VERSION, research_task, select_questions
from .records import plain, statement_registry, statement_subset


class Coordinator:
    def __init__(self, execution, budget=None, *, manuscript=None):
        self.ex, self.records, self.budget = execution, execution.records, budget
        self.manuscript = manuscript
        self._state = self.records.load_state()

    @property
    def state(self):
        return deepcopy(self._state)

    def save(self):
        self.records.save_state(self._state)

    def metadata(self, **values):
        self._state["metadata"].update(plain(values))
        self.save()

    def fail(self, key, exc):
        self._state["tasks"].setdefault(key, {"task_id": key}).update(
            status="failed", reason=f"{type(exc).__name__}: {exc}"
        )
        self._state["events"].append(
            {
                "task": key,
                "phase": key.split("/")[0],
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        self.save()

    async def execute(self, key, inputs, operation, *, reuse=True, configuration=None):
        signature = digest(
            plain({"inputs": inputs, "configuration": configuration or self.ex.configuration})
        )
        previous = self._state["tasks"].get(key, {})
        if reuse and previous.get("input") == signature and previous["status"] == "committed":
            self.records.get(previous["artifact"])
            return previous["artifact"]
        identity = {"commit": key, "input": signature}
        receipt = self.records.receipt(identity) if reuse else None
        task = {
            "task_id": key,
            "input": signature,
            "inputs": plain(inputs),
            "status": "queued",
            "attempts": previous.get("attempts", 0) if previous.get("input") == signature else 0,
        }
        self._state["tasks"][key] = task
        self.save()
        try:
            if receipt:
                ref = receipt["artifact"]
            else:
                task.update(status="running", attempts=task["attempts"] + 1)
                self.save()
                ref = await operation()
                self.records.get(ref)
                self.records.receipt(identity, {"artifact": ref})
            task.update(status="committed", artifact=ref)
            self.save()
            return ref
        except Exception as exc:
            if isinstance(exc, ModelError) and exc.category == "exploration":
                task.update(status="deferred", reason=str(exc), attempts=task["attempts"] - 1)
                self.save()
                return None
            self.fail(key, exc)
            return None

    async def prepare(self, preparation):
        configuration = self.ex.configuration_for("preparation")

        async def stage(key, inputs, operation, kind):
            async def work():
                return self.records.put(kind, await operation())

            ref = await self.execute(key, inputs, work, configuration=configuration)
            return self.records.value(ref) if ref else None

        if "package" not in self._state:
            readings = await stage(
                "prepare/reading",
                {"application": preparation.bundle.model_dump(mode="json")},
                preparation.read,
                "application-readings",
            )
            if readings is None:
                return None
            project = await stage(
                "prepare/project",
                readings,
                lambda: preparation.map_project(readings),
                "project",
            )
            if project is None:
                return None
            experts = await stage(
                "prepare/team",
                {
                    "project": project,
                    "catalogue": preparation.catalogue.brief(),
                    "config": preparation.config.model_dump(mode="json"),
                },
                lambda: preparation.team(project),
                "expert-team",
            )
            if experts is None:
                return None
            self._state["package"] = {"project": project, "experts": experts}
            self.save()
        return await self.prepare_questions(preparation)

    async def prepare_questions(self, preparation):
        key = "prepare/questions"
        package = self._state["package"]
        signature = digest(
            {
                "project": package["project"],
                "experts": package["experts"],
                "version": QUESTION_VERSION,
            }
        )
        limit = self.ex.config.max_questions
        existing = self._state.get("question_selection")
        if existing:
            if existing["max_questions"] != limit or existing["input"] != signature:
                raise ValueError("frozen_questions_changed_use_new_output")
            return existing["batch"]
        if self._state.get("question_input", signature) != signature:
            raise ValueError("question_source_changed_use_new_output")
        self._state["question_input"] = signature
        self._state["tasks"][key] = {"task_id": key, "status": "running", "input": signature}
        self.save()
        try:
            if not self._state.get("question_batch"):
                batch = await preparation.questions(package).run()
                ref = self.records.put("question-batch", batch)
                self._state["question_batch"] = ref
                self._state["tasks"][key]["status"] = "generated"
                self.save()
            ref = self._state["question_batch"]
            batch = self.records.value(ref)
            previous = self._state
            selected = deepcopy(previous)
            selected["questions"] = select_questions(batch, limit)
            selected["package"]["question_evidence"] = batch.get("evidence", {})
            selected["question_selection"] = {
                "batch": ref,
                "max_questions": limit,
                "input": signature,
                "total": len(batch["questions"]),
                "selected": list(selected["questions"]),
            }
            selected["tasks"][key] = {
                "task_id": key,
                "input": signature,
                "status": "committed",
                "artifact": ref,
            }
            # The selection and its successful operation become visible in one state replacement.
            self.records.save_state(selected)
            self._state = selected
            return ref
        except Exception as exc:
            self.fail(key, exc)
            return None

    def results(self):
        return {
            ref: self.records.value(ref) for refs in self._state["heads"].values() for ref in refs
        }

    def coverage(self):
        if not self._state.get("question_batch"):
            rows = []
        else:
            rows = self.records.value(self._state["question_batch"])["questions"]
        result = {}
        for aspect in REVIEW_ASPECTS:
            ids, refs, outcomes, limits, unfinished = [], [], {}, [], {}
            for rank, proposal in enumerate(rows, 1):
                if aspect not in proposal["aspects"]:
                    continue
                qid = f"q{rank}"
                ids.append(qid)
                found = []
                if qid in self._state["questions"]:
                    for ref in self._state["heads"].get(qid, []):
                        artifact = self.records.value(ref)
                        refs.append(ref)
                        for outcome in artifact.get("scope_outcomes", []):
                            if outcome["member_id"] == qid:
                                found.append(
                                    {
                                        **outcome,
                                        "expert_id": proposal["expert_id"],
                                        "result_refs": [
                                            ref + "::" + b for b in outcome["block_ids"]
                                        ],
                                    }
                                )
                outcomes[qid] = found
                if not found or any(row["status"] != "analyzed" for row in found):
                    unfinished[qid] = "unassessed" if not found else "partial"
                    if not found:
                        limits.append("尚未形成评审结论：" + proposal["question"])
            result[aspect] = {
                "applicability": "applicable" if ids else "undetermined",
                "status": "assessed"
                if ids and not unfinished
                else "partially_assessed"
                if refs
                else "unassessed",
                "questions": ids,
                "result_refs": list(dict.fromkeys(refs)),
                "member_outcomes": outcomes,
                "analysis_limits": limits,
                "external_needs": [],
                "unfinished": unfinished,
            }
        return result

    def open_issues(self, refs):
        issues = []
        for ref in refs:
            source = self.records.value(ref)
            for index, request in enumerate(source.get("followups", [])):
                links = {k for text in request.values() for k in re.findall(r"\[@([^]]+)\]", text)}
                statements = statement_subset(statement_registry(source), links)
                evidence_ids = set(links)
                for value in statements.values():
                    for part in value.get("parts", []) + value.get("scope", []):
                        evidence_ids.update(part["cites"])
                issues.append(
                    {
                        "origin": ref,
                        "field": f"/followups/{index}",
                        "status": "unresolved",
                        "request": request,
                        "evidence": {
                            k: v for k, v in source.get("evidence", {}).items() if k in evidence_ids
                        },
                        "statements": statements,
                    }
                )
        return issues

    async def research(self, research, *, originals=(), application_refs=None):
        semaphore = asyncio.Semaphore(self.ex.config.parallel_questions)

        async def one(qid, question):
            async with semaphore:
                task = research_task(qid, question)
                key = task["task_id"]
                if application_refs:
                    task["application_refs"] = application_refs
                old = self._state["tasks"].get(key, {})
                if (
                    old.get("status") != "committed"
                    and self.budget
                    and (reason := self.budget.exploration_reason())
                ):
                    self._state["tasks"][key] = {
                        **old,
                        "task_id": key,
                        "status": "deferred",
                        "reason": reason,
                    }
                    self.save()
                    return
                if (
                    old.get("status") == "failed"
                    and old.get("attempts", 0) >= self.ex.config.question_attempts
                    and not self.records.has_final_receipt(key, self.ex.configuration)
                ):
                    return
                ref = await self.execute(
                    key,
                    {"task": task, "originals": originals},
                    lambda: research.run(task, originals=originals),
                )
                if ref:
                    self._state["heads"][qid] = [ref]
                self.save()

        questions = sorted(self.state["questions"].items(), key=lambda item: item[1]["source_rank"])
        await asyncio.gather(*(one(qid, row) for qid, row in questions))

    async def run(self, preparation, literature, output):
        from .research import Research

        if not await self.prepare(preparation):
            return self.public_result()
        package = self._state["package"]
        await self.research(Research(preparation, package, literature))
        if not self.results():
            return self.public_result()
        application = [
            doc.model_dump(mode="json") for doc in preparation.reader().docs.values() if doc
        ]
        return await self.publish(application, output)

    async def publish(self, application, output):
        """The sole downstream entry; it never schedules or resumes expert research."""
        from .assessed_delivery import deliver
        from .assessment import synthesize
        from .assessment_input import freeze_review

        caller = self.manuscript
        if caller is None:
            raise ValueError("manuscript_caller_required")
        frozen = self.records.put(
            "frozen-review",
            freeze_review(self.records, list(self.results()), application, self.ex.evaluation),
            list(self.results()),
        )
        # Exact-request receipts recover prior work; public pointers describe this invocation.
        for key in ("assessment", "delivery", "manifest"):
            self._state.pop(key, None)
        self._state["frozen_review"] = frozen
        self.save()
        ref = await self.execute(
            "assessment/current",
            {"frozen_review": frozen},
            lambda: synthesize(caller, frozen),
            reuse=False,
            configuration=caller.config.model_dump(mode="json"),
        )
        if not ref:
            return self.public_result()
        self._state["assessment"] = ref
        self.save()
        delivery_ref = await self.execute(
            "writer/delivery",
            {"assessment": ref, "frozen_review": frozen},
            lambda: deliver(caller, ref, frozen, output, reporting=self.ex.reporting),
            # Calls have their own exact-request receipts; always replay file publication.
            reuse=False,
            configuration=caller.config.model_dump(mode="json"),
        )
        if delivery_ref:
            self._state["delivery"] = delivery_ref
            self._state["manifest"] = self.records.value(delivery_ref)["manifest"]
        self.save()
        return self.public_result()

    def public_result(self):
        result_refs = self.results()
        assessment = (
            self.records.value(self._state["assessment"]) if self._state.get("assessment") else None
        )
        delivery = (
            self.records.value(self._state["delivery"]) if self._state.get("delivery") else {}
        )
        coverage = self.coverage() if "package" in self._state else {}
        research_started = any(
            k.startswith("research/") and task.get("attempts", 0) > 0
            for k, task in self._state["tasks"].items()
        )
        research_complete = bool(self._state["questions"]) and all(
            self._state["heads"].get(qid) for qid in self._state["questions"]
        )
        research_status = (
            "not_run"
            if not research_started
            else "completed"
            if research_complete
            else "partial"
            if result_refs
            else "failed"
        )
        complete = bool(assessment) and delivery.get("status") == "completed"
        assessment_status = (
            "completed"
            if assessment
            else (
                "failed"
                if self._state["tasks"].get("assessment/current", {}).get("status") == "failed"
                else "not_run"
            )
        )
        reporting_status = delivery.get("status") or (
            "failed"
            if self._state["tasks"].get("writer/delivery", {}).get("status") == "failed"
            else "not_run"
        )
        return {
            "schema_version": "4.4",
            "status": "completed" if complete else "partial" if result_refs else "failed",
            "stage": "complete" if complete else "incomplete",
            "scientific_status": "completed"
            if assessment
            else "partial"
            if result_refs
            else "not_run",
            "research_status": research_status,
            "assessment_status": assessment_status,
            "reporting_status": reporting_status,
            "coverage": coverage,
            "dimension_assessments": assessment["dimensions"] if assessment else {},
            "frozen_review": self._state.get("frozen_review"),
            "project": self._state.get("package", {}).get("project", {}),
            "questions": self.state["questions"],
            "results": result_refs,
            "assessment": assessment,
            "snapshot": self._state.get("assessment"),
            "delivery": delivery,
            "publication": delivery.get("publication", {}),
            "recommendation": assessment["recommendation"] if assessment else None,
            "tasks": list(self.state["tasks"].values()),
            "tasks_used": len(self._state["tasks"]),
            "technical_limits": delivery.get("technical_limits", [])
            + [t["reason"] for t in self._state["tasks"].values() if t["status"] == "failed"],
            "research_requests": self.open_issues(sorted(result_refs)),
            "research_history": {},
        }
