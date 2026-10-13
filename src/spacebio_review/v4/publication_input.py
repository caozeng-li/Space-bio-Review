"""Reader-facing projections; routing and execution explanations stay in records."""

from pydantic import BaseModel, ConfigDict, Field

from spacebio_review.review.project import REVIEW_ASPECTS


class PublicValue(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Coverage(PublicValue):
    status: str
    applicability: str = "undetermined"
    assessment_status: str = "unassessed"
    analysis_limits: list[str] = Field(default_factory=list)
    external_needs: list[str] = Field(default_factory=list)


class OpenIssue(PublicValue):
    question: str
    decision_impact: str = ""
    status: str
    dependency: str = ""
    impact: str = ""
    uncertainty: str = ""
    remaining_gap: str = ""
    result_refs: list[str] = Field(default_factory=list)


class Analysis(PublicValue):
    ref: str
    perspective: str
    purpose: str = ""


class Topic(PublicValue):
    title: str
    members: dict[str, str] = Field(default_factory=dict)
    analyses: list[Analysis] = Field(default_factory=list)


class PublicationInput(PublicValue):
    version: str = "publication-input-1"
    assessment: str
    coverage: dict[str, Coverage]
    issues: list[OpenIssue]
    topics: list[Topic]


def coverage_view(coverage):
    if "status" not in coverage:
        return {aspect: coverage_view(value) for aspect, value in coverage.items()}
    limits = list(coverage.get("analysis_limits", []))
    for rows in coverage.get("member_outcomes", {}).values():
        limits.extend(
            row["explanation"]
            for row in rows
            if row.get("status") != "analyzed" and row.get("explanation")
        )
    return Coverage(
        status=coverage["status"],
        applicability=coverage.get("applicability", "undetermined"),
        assessment_status=coverage.get("assessment_status", "unassessed"),
        analysis_limits=list(dict.fromkeys(limits)),
        external_needs=list(coverage.get("external_needs", [])),
    ).model_dump()


def request_view(requests):
    rows = []
    for row in requests:
        if row.get("status") == "alias":
            continue
        request, outcome = row.get("request", {}), row.get("outcome", {})
        if row.get("status") == "unresolved":
            outcome = {"remaining_gap": request.get("missing_information", "")}
        rows.append(
            OpenIssue(
                question=request.get("question", ""),
                decision_impact=request.get("decision_impact", ""),
                status=row.get("status", "pending"),
                **{
                    key: outcome[key]
                    for key in (
                        "dependency",
                        "impact",
                        "uncertainty",
                        "remaining_gap",
                        "result_refs",
                    )
                    if key in outcome
                },
            ).model_dump()
        )
    return rows


def publication_catalog(state, selected):
    """Freeze only selected analyses and their existing question/discipline labels."""
    if "planning" in state:
        return planned_catalog(state, selected)
    selected = set(selected)
    topics, found = [], set()
    questions = state.get("questions", {})
    experts = state.get("package", {}).get("experts", {})
    tasks = state.get("tasks", {})
    for qid, question in questions.items():
        if question.get("merged_into"):
            continue
        analyses = []
        scopes = (
            [{"expert_id": question["proposal"]["expert_id"]}]
            if "expert_id" in question["proposal"]
            else question.get("expert_tasks", [])
        )
        for task in scopes:
            eid = task["expert_id"]
            refs = [
                value.get("artifact")
                for key, value in tasks.items()
                if key.startswith(f"research/{qid}/{eid}/")
            ]
            for ref in refs:
                if ref not in selected or ref in found:
                    continue
                expert = experts.get(eid, {})
                labels = expert.get("profile_labels", [])
                analyses.append(
                    Analysis(
                        ref=ref,
                        perspective=" / ".join(labels) or f"专业视角 {len(analyses) + 1}",
                        purpose=expert.get("purpose", ""),
                    )
                )
                found.add(ref)
        if analyses:
            members = {
                member: questions[member]["proposal"]["question"]
                for member in question.get("member_ids", [qid])
                if member in questions
            }
            topics.append(
                Topic(title=question["proposal"]["question"], members=members, analyses=analyses)
            )
    return [topic.model_dump() for topic in topics]


def planned_catalog(state, selected):
    selected, found, topics = set(selected), set(), []
    experts = state["package"]["experts"]
    for wid, unit in state["planning"]["units"].items():
        analyses = []
        for aid, assignment in state.get("assignments", {}).items():
            if aid.split("/")[0] != wid:
                continue
            expert = experts[assignment["input"]["expert_id"]]
            for ref in [assignment.get("head"), *assignment.get("history", [])]:
                if ref not in selected or ref in found:
                    continue
                analyses.append(
                    Analysis(
                        ref=ref,
                        perspective=" / ".join(expert.get("profile_labels", []))
                        or f"专业视角 {len(analyses) + 1}",
                        purpose=expert.get("purpose", ""),
                    )
                )
                found.add(ref)
        if analyses:
            members = {q: state["questions"][q]["proposal"]["question"] for q in unit["members"]}
            topics.append(
                Topic(title=next(iter(members.values())), members=members, analyses=analyses)
            )
    return [topic.model_dump() for topic in topics]


def project_input(assessment_ref, assessment, frozen):
    coverage = {}
    for aspect in REVIEW_ASPECTS:
        old = assessment.get("coverage", {}).get(aspect, {"status": "unassessed"})
        latest = {**old, **frozen.get("coverage", {}).get(aspect, {})}
        latest["assessment_status"] = old.get("assessment_status", "unassessed")
        coverage[aspect] = Coverage(**coverage_view(latest))
    return PublicationInput(
        assessment=assessment_ref,
        coverage=coverage,
        issues=[OpenIssue(**row) for row in request_view(frozen.get("requests", []))],
        topics=[Topic(**row) for row in frozen.get("publication_catalog", [])],
    )
