from copy import deepcopy

from spacebio_review.v4.contracts import professional_contract
from spacebio_review.v4.publication_input import (
    coverage_view,
    project_input,
    publication_catalog,
    request_view,
)


def test_public_projection_does_not_depend_on_execution_explanations():
    coverage = {
        "status": "partially_assessed",
        "reason": "INTERNAL",
        "synthesis_limit": "TRACEBACK",
        "unfinished": {"q1": "TIMEOUT"},
        "member_outcomes": {
            "q1": [{"status": "partial", "explanation": "Only at 2 min.", "expert_id": "x"}]
        },
    }
    expected = coverage_view(coverage)
    changed = deepcopy(coverage)
    changed.update(reason="OTHER", synthesis_limit="ERROR", unfinished={"new-id": "FAILED"})
    changed["member_outcomes"] = {"new-id": changed["member_outcomes"]["q1"]}
    assert coverage_view(changed) == expected
    assert expected["analysis_limits"] == ["Only at 2 min."]
    changed["member_outcomes"]["new-id"][0]["explanation"] = "Only at 3 min."
    assert coverage_view(changed) != expected


def test_gap_projection_uses_outcome_not_route_reason():
    for status in ("answered", "partial", "external_dependency", "retain_unknown", "deferred"):
        row = {
            "status": status,
            "reason": "Multiple R arguments justify this decision",
            "request": {"question": "Calibration?", "decision_impact": "Comparability"},
            "outcome": {"remaining_gap": "Timing", "result_refs": ["a::b1"]},
        }
        result = request_view([row])[0]
        assert "reason" not in result
        assert result["remaining_gap"] == "Timing"
        assert result["result_refs"] == ["a::b1"]
        assert result["status"] == status


def test_latest_scope_and_final_assessment_status_are_independent():
    result = project_input(
        "assessment",
        {"coverage": {"rigor": {"status": "assessed", "assessment_status": "completed"}}},
        {"coverage": {"rigor": {"status": "partially_assessed"}}},
    )
    assert result.coverage["rigor"].status == "partially_assessed"
    assert result.coverage["rigor"].assessment_status == "completed"


def test_catalog_uses_question_order_and_does_not_deduplicate_expert_perspectives():
    state = {
        "questions": {
            "q2": {
                "proposal": {"question": "Same question"},
                "expert_tasks": [{"expert_id": "e1"}, {"expert_id": "e2"}],
            }
        },
        "package": {"experts": {"e1": {"profile_labels": ["Optics"]}, "e2": {}}},
        "tasks": {
            "research/q2/e1/r0": {"artifact": "z"},
            "research/q2/e2/r0": {"artifact": "a"},
            "research/q2/e2/r1": {"artifact": "unused"},
        },
    }
    topics = publication_catalog(state, ["a", "z"])
    assert [a["ref"] for a in topics[0]["analyses"]] == ["z", "a"]
    assert topics[0]["analyses"][0]["perspective"] == "Optics"
    assert "tasks" not in str(topics)


def test_professional_wire_schema_has_public_scope_descriptions():
    for structured in (False, True):
        contract = professional_contract(None, {}, {"q1": {}}, structured_refs=structured)
        schema = contract.schema()
        assert "科学" in schema["properties"]["scope_note"]["description"]
        assert (
            "不写执行过程"
            in schema["$defs"]["MemberOutcome"]["properties"]["explanation"]["description"]
        )
