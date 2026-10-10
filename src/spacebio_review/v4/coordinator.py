"""The sole writer of review tasks, current versions and research requests."""

import asyncio
import re
import traceback
from contextlib import nullcontext
from copy import deepcopy

from spacebio_review.agents.diagnostics import digest
from spacebio_review.models.provider import ModelError
from spacebio_review.review.project import REVIEW_ASPECTS

from .records import plain


class Coordinator:
    def __init__(self, execution, budget=None):
        self.ex, self.records, self.budget = execution, execution.records, budget
        self._state = self.records.load_state()

    @property
    def state(self):
        return deepcopy(self._state)

    def save(self):
        self.records.save_state(self._state)

    def metadata(self, **values):
        self._state["metadata"].update(plain(values))
        self.save()

    async def execute(self, key, inputs, operation, *, reuse=True):
        signature = digest(plain({"inputs": inputs, "configuration": self.ex.configuration}))
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
            self.records.get(ref)
            task.update(status="committed", artifact=ref)
            self.save()
            return ref
        except Exception as exc:
            if isinstance(exc, ModelError) and exc.category == "exploration":
                task.update(status="deferred", reason=str(exc), attempts=task["attempts"] - 1)
                self.save()
                return None
            task.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
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
            return None

    def add_question(self, proposal, origin, *, persist=True):
        fingerprint = digest(plain(proposal))
        for qid, question in self._state["questions"].items():
            if question["fingerprint"] == fingerprint:
                return question.get("merged_into") or qid
        qid = "q" + str(len(self._state["questions"]) + 1)
        self._state["questions"][qid] = {
            "proposal": plain(proposal),
            "origin": origin,
            "fingerprint": fingerprint,
            "owners": [],
            "related": [],
            "disposition": "research",
            "reason": "",
            "revision": 0,
        }
        if persist:
            self.save()
        return qid

    def adopt_preparation(self, package, *, register_proposals=True):
        self._state["package"] = plain(package)
        if register_proposals:
            for proposal in package.get("proposals", []):
                self.add_question(proposal, "preparation")
        self.save()

    def apply_plan(self, plan):
        previous = self._state
        self._state = deepcopy(previous)
        try:
            self._apply_plan(plan)
            self.save()
        except Exception:
            self._state = previous
            raise

    def _apply_plan(self, plan):
        questions = self._state["questions"]
        before = {qid: self.work_scope(qid) for qid in questions}
        for allocation in plan["assignments"]:
            qid = allocation["question_id"]
            q = questions[qid]
            q.update(
                {
                    k: deepcopy(allocation[k])
                    for k in (
                        "expert_tasks",
                        "external_needs",
                        "retained_unknowns",
                        "related",
                        "reason",
                        "disposition",
                    )
                }
            )
            targets = allocation.get("merge_into", [])
            if len(targets) > 1:
                raise ValueError("invalid_question_merge")
            q.pop("merged_into", None)
            if targets:
                q["merged_into"] = targets[0]
        for row in plan["new_questions"]:
            qid = self.add_question(row["proposal"], "allocation", persist=False)
            q = questions[qid]
            q.update(
                {
                    k: deepcopy(row[k])
                    for k in (
                        "expert_tasks",
                        "external_needs",
                        "retained_unknowns",
                        "reason",
                        "disposition",
                    )
                }
            )
            for task in q["expert_tasks"]:
                task["member_ids"] = [qid if m == "self" else m for m in task["member_ids"]]
        # Resolve the entire graph before deriving any work unit. No incremental append.
        groups = {}
        for qid in questions:
            groups.setdefault(self.root_question(qid), []).append(qid)
        for root, members in groups.items():
            q = questions[root]
            q["member_ids"] = sorted(members)
            by_expert = {}
            for task in q.get("expert_tasks", []):
                eid = task["expert_id"]
                if eid not in by_expert:
                    by_expert[eid] = deepcopy(task)
                else:
                    target = by_expert[eid]
                    target["member_ids"] = sorted(set(target["member_ids"] + task["member_ids"]))
                    if target["analysis_scope"] != task["analysis_scope"]:
                        target["analysis_scope"] += "\n" + task["analysis_scope"]
            tasks = list(by_expert.values())
            for task in tasks:
                task["member_ids"] = sorted(set(task["member_ids"]))
                if not set(task["member_ids"]) <= set(members):
                    raise ValueError("expert_scope_outside_work_unit")
            q["expert_tasks"] = sorted(tasks, key=lambda t: t["expert_id"])
            q["owners"] = [t["expert_id"] for t in q["expert_tasks"]]
        for qid in before:
            if before[qid] != self.work_scope(qid) and self._state["heads"].get(qid):
                self.revise(qid, "planned_scope_changed", persist=False)
        package = self._state["package"]
        package.setdefault("coverage", {}).update(plan.get("coverage", {}))
        if "project_corrections" in plan:
            package["project"]["corrections"] = plan["project_corrections"]
        package["project"].setdefault("evidence", {}).update(plan.get("evidence", {}))

    def root_question(self, qid):
        seen = set()
        while self._state["questions"][qid].get("merged_into"):
            if qid in seen:
                raise ValueError("cyclic_semantic_merge")
            seen.add(qid)
            qid = self._state["questions"][qid]["merged_into"]
        return qid

    def work_scope(self, qid):
        q = self._state["questions"][qid]
        return deepcopy(
            {
                k: q.get(k)
                for k in (
                    "expert_tasks",
                    "member_ids",
                    "external_needs",
                    "retained_unknowns",
                    "merged_into",
                    "disposition",
                    "related",
                )
            }
        )

    def planning_gaps(self):
        gaps = {}
        for aspect in REVIEW_ASPECTS:
            declared = self._state["package"].get("coverage", {}).get(aspect, {})
            if declared.get("applicability") != "applicable" or declared.get("no_work_reason"):
                continue
            assigned = any(
                aspect in self._state["questions"][m]["proposal"]["aspects"]
                for q in self._state["questions"].values()
                if not q.get("merged_into") and q["disposition"] == "research"
                for task in q.get("expert_tasks", [])
                for m in task["member_ids"]
            )
            if not assigned:
                gaps[aspect] = {"reason": "no_executable_analysis", "declared": declared}
        return gaps

    async def allocate(self, preparation, suffix="initial", gaps=None):
        package = self._state["package"]

        async def work():
            plan = await preparation.allocate(
                package["project"],
                package["experts"],
                self.state["questions"],
                suffix=suffix,
                gaps=gaps,
                remaining_calls=self.budget.remaining_calls
                if self.budget
                else self.ex.config.max_model_calls,
            )
            return self.records.put("plan", plan)

        ref = await self.execute(
            "prepare/allocate/" + suffix,
            {"package": package, "questions": self.state["questions"], "gaps": gaps},
            work,
        )
        if ref:
            self.apply_plan(self.records.value(ref))
        return ref

    def results(self):
        return {
            ref: self.records.value(ref)
            for qid, refs in self._state["heads"].items()
            if not self._state["questions"][qid].get("merged_into")
            and self._state["questions"][qid]["disposition"] != "withdraw"
            for ref in refs
        }

    def coverage(self):
        result = {}
        questions = self._state["questions"]
        for aspect in REVIEW_ASPECTS:
            declared = self._state.get("package", {}).get("coverage", {}).get(aspect, {})
            ids = [
                qid
                for qid, q in questions.items()
                if q["disposition"] != "withdraw" and aspect in q["proposal"].get("aspects", [])
            ]
            refs, unfinished, outcomes, external = [], {}, {}, []
            for member in ids:
                root = self.root_question(member)
                q = questions[root]
                assigned = [t for t in q.get("expert_tasks", []) if member in t["member_ids"]]
                member_outcomes = []
                for task in assigned:
                    found = []
                    for ref in self._state["heads"].get(root, []):
                        artifact = self.records.value(ref)
                        if artifact["author"] != task["expert_id"]:
                            continue
                        for outcome in artifact.get("scope_outcomes", []):
                            if outcome["member_id"] == member:
                                found.append(
                                    {
                                        **outcome,
                                        "expert_id": task["expert_id"],
                                        "result_refs": [
                                            ref + "::" + b for b in outcome["block_ids"]
                                        ],
                                    }
                                )
                                if outcome["block_ids"]:
                                    refs.append(ref)
                    member_outcomes.extend(
                        found or [{"expert_id": task["expert_id"], "status": "pending"}]
                    )
                outcomes[member] = member_outcomes
                if not assigned or any(o["status"] != "analyzed" for o in member_outcomes):
                    unfinished[member] = q.get("reason") or "assigned_analysis_incomplete"
                external.extend(q.get("external_needs", []))
            result[aspect] = {
                "applicability": declared.get("applicability", "undetermined"),
                "status": "assessed"
                if ids and not unfinished
                else "partially_assessed"
                if refs
                else "unassessed",
                "result_refs": sorted(set(refs)),
                "questions": ids,
                "member_outcomes": outcomes,
                "external_needs": list(dict.fromkeys(external)),
                "reason": declared.get("no_work_reason")
                or declared.get("explanation", "本轮未形成该方面专业评审结果"),
                "unfinished": unfinished,
            }
        return result

    def requests(self, followups, origin):
        for request in followups:
            request = plain(request)
            links = {k for text in request.values() for k in re.findall(r"\[@([^]]+)\]", text)}
            sources = {}
            if links:
                source = self.records.value(origin)
                if "manifest" in source:
                    manifest = self.records.value(source["manifest"])
                    source = self.records.value(manifest["assessment"])
                sources = {
                    "evidence": {k: v for k, v in source.get("evidence", {}).items() if k in links},
                    "results": self.records.result_blocks(
                        sorted({k.split("::", 1)[0] for k in links if "::" in k})
                    ),
                }
            identity = digest(plain(request))
            old = next((r for r in self._state["requests"] if r["identity"] == identity), None)
            if old:
                if origin not in old["origins"]:
                    old["origins"].append(origin)
                continue
            self._state["requests"].append(
                {
                    "request_id": "request-" + identity[:16],
                    "identity": identity,
                    "request": plain(request),
                    "origins": [origin],
                    "status": "pending",
                    **sources,
                }
            )
        self.save()

    def defer_requests(self, reason):
        for row in self._state["requests"]:
            if row["status"] == "pending":
                row.update(status="deferred", reason=reason)
        self.save()

    def work_state(self):
        result = {}
        for qid, q in self._state["questions"].items():
            if q.get("merged_into"):
                continue
            tasks = {
                t["expert_id"]: self._state["tasks"].get(
                    f"research/{qid}/{t['expert_id']}/r{q['revision']}", {"status": "queued"}
                )
                for t in q.get("expert_tasks", [])
            }
            result[qid] = {
                "revision": q["revision"],
                "tasks": tasks,
                "heads": self._state["heads"].get(qid, []),
                "pending": q["disposition"] == "research"
                and bool(tasks)
                and any(t["status"] in {"queued", "running"} for t in tasks.values()),
            }
        return result

    async def route_requests(self, preparation):
        pending = [r for r in self._state["requests"] if r["status"] == "pending"]
        if not pending:
            return False
        if self.budget and (reason := self.budget.exploration_reason()):
            self.defer_requests(reason)
            return False
        package = self._state["package"]
        work_state = self.work_state()
        current = self.records.result_blocks(sorted(self.results()))
        inputs = {
            "pending": pending,
            "questions": self.state["questions"],
            "work": work_state,
            "results": current,
            "previous": [r for r in self.state["requests"] if r["status"] != "pending"],
        }

        async def work():
            routed = await preparation.route_requests(
                pending,
                inputs["questions"],
                package,
                work_state=work_state,
                results=current,
                previous_requests=inputs["previous"],
            )
            return self.records.put("routing", routed, sorted(self.results()))

        with self.budget.exploring() if self.budget else nullcontext():
            ref = await self.execute("prepare/requests/" + digest(inputs)[:16], inputs, work)
        if not ref:
            self.defer_requests(
                (self.budget.exploration_reason() if self.budget else None)
                or "request_routing_failed"
            )
            return False
        return self.apply_routing(ref)

    def apply_routing(self, ref):
        """Publish classification and an admitted nonempty research wave in one state write."""
        if ref in self._state.get("applied_routings", []):
            return False
        previous = self._state
        self._state = deepcopy(previous)
        try:
            scheduled = self._apply_routing(ref)
            self._state.setdefault("applied_routings", []).append(ref)
            self.save()
            return scheduled
        except Exception:
            self._state = previous
            raise

    def _apply_routing(self, ref):
        routed = self.records.value(ref)
        self._state["package"]["project"].setdefault("evidence", {}).update(
            routed.get("evidence", {})
        )
        requests = {r["request_id"]: r for r in self._state["requests"]}
        current_blocks = self.records.result_blocks(sorted(self.results()))
        candidates, seen = [], set()
        work = self.work_state()
        for row in sorted(routed["decisions"], key=lambda r: r["canonical_id"]):
            ids = row["request_ids"]
            if seen.intersection(ids) or any(requests[i]["status"] != "pending" for i in ids):
                raise ValueError("routing_request_identity")
            seen.update(ids)
            canonical = row["canonical_id"]
            target = requests[canonical]
            target.setdefault("decisions", []).append({"routing": ref, "decision": deepcopy(row)})
            target.update(
                canonical_id=canonical,
                status=row["action"],
                reason=row["reason"],
                outcome=deepcopy(row),
            )
            for rid in ids:
                requests[rid]["canonical_id"] = canonical
                if rid != canonical:
                    requests[rid].update(status="alias", reason="same_knowledge_gap")
            action = row["action"]
            if action == "reuse_result":
                if not set(row["result_refs"]) <= current_blocks.keys() or not row["result_refs"]:
                    raise ValueError("reuse_requires_current_argument")
                target["status"] = "partial" if row["remaining_gap"] else "answered"
            elif action == "attach_to_work":
                qid = row["question_id"]
                if not work[qid]["pending"]:
                    raise ValueError("attach_requires_admitted_pending_work")
                target.update(status="awaiting_work", question_id=qid)
            elif action in {"new_work", "revise_work"}:
                if not row["expert_tasks"]:
                    target.update(status="deferred", reason="no_executable_analysis")
                else:
                    candidates.append((canonical, row))
        for rid, req in requests.items():
            if req["status"] == "pending" and rid not in seen:
                req.update(status="deferred", reason="routing_omitted_request")
        output_reserve = 0
        runtime_config = getattr(getattr(self.ex, "runtime", None), "config", None)
        if candidates and runtime_config:
            output_reserve = max(
                runtime_config.routed_model(
                    "specialist",
                    self._state["package"]["experts"][task["expert_id"]].get(
                        "model_tier", "normal"
                    ),
                    formal=True,
                ).max_tokens
                for _, row in candidates
                for task in row["expert_tasks"]
            )
        reason = (
            self.budget.exploration_reason(output_tokens=output_reserve) if self.budget else None
        )
        if (
            not reason
            and self._state.get("supplemental_rounds", 0) >= self.ex.config.supplemental_rounds
        ):
            reason = "research_round_limit"
        if reason:
            for rid, row in candidates:
                requests[rid].update(status="deferred", reason=reason)
            return False
        if not candidates:
            return False
        assignments, new_questions = [], []
        for rid, row in candidates:
            if row["action"] == "revise_work":
                qid = row["question_id"]
                assignments.append(
                    {
                        **row,
                        "related": self._state["questions"][qid]["related"],
                        "merge_into": [],
                        "disposition": "research",
                    }
                )
            else:
                new_questions.append({**row, "disposition": "research"})
        # Distinct gaps may share one work unit without becoming the same scientific gap.
        by_root = {}
        for row in assignments:
            root = row["question_id"]
            if root not in by_root:
                by_root[root] = deepcopy(row)
                continue
            existing = by_root[root]
            for field in ("external_needs", "retained_unknowns"):
                existing[field] = sorted(set(existing[field] + row[field]))
            existing["reason"] += "\n" + row["reason"]
            tasks = {t["expert_id"]: t for t in existing["expert_tasks"]}
            for task in row["expert_tasks"]:
                if task["expert_id"] not in tasks:
                    tasks[task["expert_id"]] = deepcopy(task)
                else:
                    t = tasks[task["expert_id"]]
                    t["member_ids"] = sorted(set(t["member_ids"] + task["member_ids"]))
                    if task["analysis_scope"] != t["analysis_scope"]:
                        t["analysis_scope"] += "\n" + task["analysis_scope"]
            existing["expert_tasks"] = list(tasks.values())
        versions = {qid: q["revision"] for qid, q in self._state["questions"].items()}
        self._apply_plan({"assignments": list(by_root.values()), "new_questions": new_questions})
        selected, revised, scope_requests = set(), set(), {}
        for rid, row in candidates:
            qid = row.get("question_id") or self.add_question(row["proposal"], rid, persist=False)
            # Failed/empty old work needs a new revision too, otherwise its attempt cap persists.
            if row["action"] == "revise_work" and qid not in revised:
                if self._state["questions"][qid]["revision"] == versions[qid]:
                    self.revise(qid, row["reason"], persist=False)
                revised.add(qid)
            requests[rid].update(status="awaiting_work", question_id=qid)
            scope_requests.setdefault(qid, []).append(
                {
                    "request_id": rid,
                    "request": deepcopy(requests[rid]["request"]),
                    "reason": row["reason"],
                }
            )
            self._state["package"]["project"].setdefault("evidence", {}).update(
                requests[rid].get("evidence", {})
            )
            q = self._state["questions"][qid]
            selected.update(
                f"research/{qid}/{t['expert_id']}/r{q['revision']}" for t in q["expert_tasks"]
            )
        for qid, rows in scope_requests.items():
            self._state["questions"][qid]["scope_requests"] = rows
        selected = sorted(selected)
        wave_id = "wave-" + digest([ref, selected])[:16]
        self._state.setdefault("waves", {})[wave_id] = {"routing": ref, "tasks": selected}
        self._state["supplemental_rounds"] = self._state.get("supplemental_rounds", 0) + 1
        return True

    def refresh_requests(self):
        for req in self._state["requests"]:
            if req["status"] != "awaiting_work":
                continue
            qid = req["question_id"]
            work = self.work_state()[qid]
            if not work["pending"]:
                # Re-route against the new arguments; never infer an answer from task completion.
                req["status"] = "pending"
        self.save()

    def freeze_delivery(self, assessment):
        canonical = [
            r
            for r in self.state["requests"]
            if r.get("canonical_id", r["request_id"]) == r["request_id"]
        ]
        snapshot = {
            "assessment": assessment,
            "requests": canonical,
            "request_forms": self.state["requests"],
            "coverage": self.coverage(),
            "evidence": self._state["package"]["project"].get("evidence", {}),
        }
        return self.records.put("delivery-input", snapshot, [assessment, *sorted(self.results())])

    async def research(self, research, selected=None, *, originals=(), application_refs=None):
        semaphore = asyncio.Semaphore(self.ex.config.parallel_questions)
        jobs = []
        for qid, q in self.state["questions"].items():
            if selected is not None and qid not in selected:
                continue
            if q.get("merged_into") or q["disposition"] != "research":
                continue
            for scope in q.get("expert_tasks", []):
                jobs.append((qid, q, scope))

        # Give uncovered aspects an early opportunity, without another planning model call.
        covered = {a for a, item in self.coverage().items() if item["result_refs"]}
        ordered = []
        while jobs:

            def aspects(job):
                return {
                    a
                    for m in job[2]["member_ids"]
                    for a in self._state["questions"][m]["proposal"].get("aspects", [])
                }

            chosen = max(range(len(jobs)), key=lambda i: len(aspects(jobs[i]) - covered))
            job = jobs.pop(chosen)
            covered.update(aspects(job))
            ordered.append(job)
        jobs = ordered

        async def one(qid, q, scope):
            eid = scope["expert_id"]
            async with semaphore:
                key = f"research/{qid}/{eid}/r{q['revision']}"
                old = self._state["tasks"].get(key, {})
                if old.get("status") == "deferred":
                    return
                if (
                    old.get("status") != "committed"
                    and self.budget
                    and (reason := self.budget.exploration_reason())
                ):
                    self._state["tasks"][key] = {
                        "task_id": key,
                        "status": "deferred",
                        "reason": reason,
                    }
                    self._state["questions"][qid]["reason"] = reason
                    self.save()
                    return
                # Related scientific questions share readable results, not wait-for-neighbour edges.
                live_question = self._state["questions"][qid]
                if "related_input_versions" not in live_question:
                    live_question["related_input_versions"] = [
                        r
                        for k, refs in self._state["heads"].items()
                        if k in q["related"]
                        for r in refs
                    ]
                    self.save()
                related = list(live_question["related_input_versions"])
                task = {
                    "task_id": key,
                    "expert_id": eid,
                    "proposal": q["proposal"],
                    "members": {
                        m: self._state["questions"][m]["proposal"] for m in scope["member_ids"]
                    },
                    "analysis_scope": scope["analysis_scope"],
                    "requests": q.get("scope_requests", []),
                    "external_needs": q.get("external_needs", []),
                    "retained_unknowns": q.get("retained_unknowns", []),
                }
                if application_refs:
                    task["application_refs"] = application_refs
                old = self._state["tasks"].get(key, {})
                if (
                    old.get("status") == "failed"
                    and old.get("attempts", 0) >= self.ex.config.question_attempts
                    and not self.records.has_final_receipt(key, self.ex.configuration)
                ):
                    self._state["questions"][qid]["reason"] = "task_attempt_budget_exhausted"
                    self.save()
                    return
                ref = await self.execute(
                    key,
                    {"task": task, "related": related, "originals": originals},
                    lambda: research.run(task, related=related, originals=originals),
                )
                if ref:
                    refs = self._state["heads"].setdefault(qid, [])
                    # One current artifact per expert; superseded versions remain immutable.
                    refs[:] = [r for r in refs if self.records.value(r)["author"] != eid]
                    refs.append(ref)
                    self.requests(self.records.value(ref).get("followups", []), ref)
                else:
                    self._state["questions"][qid]["reason"] = self._state["tasks"][key]["reason"]
                self.save()

        await asyncio.gather(*(one(*job) for job in jobs))

    def revise(self, qid, reason, *, persist=True):
        old = set(self._state["heads"].pop(qid, []))
        self._state.setdefault("history", {}).setdefault(qid, []).extend(sorted(old))
        self._state["questions"][qid]["revision"] += 1
        self._state["questions"][qid].pop("related_input_versions", None)
        self._state["questions"][qid]["reason"] = reason
        changed = True
        while changed:
            changed = False
            for key, task in self._state["tasks"].items():
                ref = task.get("artifact")
                if (
                    task["status"] == "committed"
                    and ref
                    and set(self.records.get(ref)["dependencies"]) & old
                ):
                    task.update(status="deferred", reason="input_version_superseded")
                    old.add(ref)
                    changed = True
            for key, refs in list(self._state["heads"].items()):
                invalid = [r for r in refs if set(self.records.get(r)["dependencies"]) & old]
                if invalid:
                    old.update(invalid)
                    self._state.setdefault("history", {}).setdefault(key, []).extend(invalid)
                    self._state["heads"][key] = [r for r in refs if r not in invalid]
                    self._state["questions"][key]["revision"] += 1
                    self._state["questions"][key].pop("related_input_versions", None)
                    changed = True
        for name in ("assessment", "manifest", "delivery"):
            ref = self._state.get(name)
            if ref and (ref in old or set(self.records.get(ref)["dependencies"]) & old):
                self._state["events"].append({"invalidated": ref, "reason": reason})
                old.add(ref)
                del self._state[name]
        from .research import artifact_dependencies

        for request in self._state["requests"]:
            if request["status"] in {"answered", "partial"} and old.intersection(
                artifact_dependencies(request.get("outcome", {}))
            ):
                request.setdefault("previous_outcomes", []).append(request.pop("outcome"))
                request.update(status="pending", reason="answer_input_superseded")
        if persist:
            self.save()

    async def import_artifact(self, qid, ref):
        """Explicit same-format stage handoff; does not assert scientific acceptance."""
        value = self.records.value(ref)
        q = self._state["questions"][qid]
        author = value["author"]
        if author not in q["owners"]:
            q["owners"].append(author)
        saved = await self.execute("handoff/" + ref, {"artifact": ref}, lambda: self._existing(ref))
        if saved:
            heads = self._state["heads"].setdefault(qid, [])
            if saved not in heads:
                heads.append(saved)
            self.save()
        return saved

    async def _existing(self, ref):
        self.records.get(ref)
        return ref

    async def run(self, preparation, literature, output):
        """Default workflow; stages never invoke global research themselves."""
        from .delivery import Delivery
        from .research import Research
        from .synthesis import Synthesis

        async def prepare():
            package = await preparation.run()
            return self.records.put("preparation", package)

        preparation_ref = await self.execute(
            "prepare/project", {"application": preparation.bundle.model_dump(mode="json")}, prepare
        )
        if not preparation_ref:
            return self.public_result()
        if "package" not in self._state:
            self.adopt_preparation(self.records.value(preparation_ref))
        if not self._state.get("initial_plan"):
            initial_plan = await self.allocate(preparation)
            if not initial_plan:
                return self.public_result()
            self._state["initial_plan"] = initial_plan
            self.save()
        if "gap_plan_checked" not in self._state:
            gaps = self.planning_gaps()
            if gaps:
                self._state["gap_plan"] = await self.allocate(preparation, "gaps", gaps)
            self._state["gap_plan_checked"] = True
            self.save()
        while True:
            package = self._state["package"]
            research = Research(preparation, package, literature)
            await self.research(research)
            self.refresh_requests()
            results, coverage = self.results(), self.coverage()
            if not results:
                return self.public_result()
            synthesis = Synthesis(self.ex, preparation)
            ref = await self.execute(
                "synthesis/current",
                {
                    "results": sorted(results),
                    "coverage": coverage,
                    "project": {k: v for k, v in package["project"].items() if k != "evidence"},
                },
                lambda: synthesis.run(
                    list(results),
                    coverage,
                    package["project"],
                    open_requests=self.state["requests"],
                ),
            )
            if not ref:
                return self.public_result()
            self._state["assessment"] = ref
            self.requests(self.records.value(ref).get("followups", []), ref)
            if await self.route_requests(preparation):
                continue
            frozen = self.freeze_delivery(ref)
            self._state["delivery_input"] = frozen
            delivery = Delivery(self.ex, output)
            manifest = delivery.assemble(ref, delivery_input=frozen)
            self._state["manifest"] = manifest
            delivery_ref = await self.execute(
                "writer/delivery",
                {"manifest": manifest},
                lambda: delivery.run(manifest),
                reuse=not self._state.get("delivery")
                or self.records.value(self._state["delivery"])["status"] == "completed",
            )
            if delivery_ref:
                self._state["delivery"] = delivery_ref
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
        research_status = (
            "completed"
            if coverage
            and all(
                v["status"] == "assessed" or v["applicability"] == "not_applicable"
                for v in coverage.values()
            )
            else "partial"
            if result_refs
            else "failed"
        )
        complete = bool(assessment) and delivery.get("status") == "completed"
        return {
            "schema_version": "4.1",
            "status": "completed" if complete else "partial" if result_refs else "failed",
            "stage": "complete" if complete else "incomplete",
            "scientific_status": "completed"
            if assessment
            else "partial"
            if result_refs
            else "not_run",
            "research_status": research_status,
            "reporting_status": delivery.get("status", "not_run"),
            "coverage": coverage,
            "project": self._state.get("package", {}).get("project", {}),
            "questions": self.state["questions"],
            "results": result_refs,
            "assessment": assessment,
            "snapshot": self._state.get("assessment"),
            "delivery": delivery,
            "recommendation": assessment["recommendation"] if assessment else None,
            "tasks": list(self.state["tasks"].values()),
            "tasks_used": len(self._state["tasks"]),
            "technical_limits": [
                t["reason"] for t in self._state["tasks"].values() if t["status"] == "failed"
            ],
            "research_requests": self.state["requests"],
            "research_history": self.state.get("history", {}),
        }
