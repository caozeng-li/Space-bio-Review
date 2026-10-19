"""Check chief and research authoring plus assembly reserves without model requests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from fixtures.session import ScriptModel, runtime

from spacebio_review.persistence import write_json
from spacebio_review.v5 import SessionTask
from spacebio_review.v5.budget import Budget, BudgetLimits


def completion_plans(root):
    budget = Budget(root, BudgetLimits(max_model_calls=100))
    author = ScriptModel([])
    engine = runtime(root, author, budget=budget)
    chief = engine.load(
        engine.create(SessionTask(role="chief", objective="评价预算", application="PLAN"))
    )
    submission = SimpleNamespace(
        completion_operations=lambda state: (
            ["write_summary"]
            if state.overall_ref
            else ["write_overall", "write_summary"]
            if state.assessment_ref
            else ["submit_assessment", "write_overall", "write_summary"]
        ),
        completion_input=lambda state, operation: [
            {"role": "user", "content": operation + " Markdown"}
        ],
        synthesis_estimate=lambda state, expected_output_tokens, operation: [
            {"role": "user", "content": "评价正文" * expected_output_tokens}
        ],
    )

    def check(state, expected, submission=None, *, include_request=False):
        reason = engine._completion_plan(
            state,
            author,
            [],
            [],
            submission,
            include_request=include_request,
        )
        assert reason is None
        plan = budget.data["sessions"][state.session_id]["completion_plan"]
        assert plan["calls"] == expected, plan
        assert all("update_research" not in step for step in plan["steps"])
        return plan["steps"]

    results = {"chief_next_research": check(chief, 4, submission, include_request=True)}
    chief.assessment_ref = "fixed-accepted-decision"
    results["after_decision"] = check(chief, 2, submission)
    chief.evidence = {
        "evidence-local": {
            "reference": {"source_id": "source-local", "passage_id": "p1"},
            "quote": "完整原文",
            "title": "原文",
        }
    }
    results["with_sources"] = check(chief, 4, submission)
    chief.assessment_ref = None
    results["all_seven_requests"] = check(chief, 7, submission)
    chief.assessment_ref, chief.overall_ref = "fixed-decision", "fixed-overall"
    results["after_overall"] = check(chief, 2, submission)
    researcher = engine.load(
        engine.create(SessionTask(role="researcher", objective="研究预算", application="PLAN"))
    )
    results["research_unwritten"] = check(researcher, 1)
    assert not author.requests and not budget.data["requests"]
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    if output.exists():
        raise SystemExit("Choose a new output directory; prior probe results are retained.")
    summary = {"completion_plans": completion_plans(output), "real_model_requests": 0}
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
