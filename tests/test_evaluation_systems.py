from pathlib import Path

import pytest

from spacebio_review.agents.diagnostics import digest
from spacebio_review.agents.skills import SkillLibrary
from spacebio_review.models.config import load_config as load_runtime
from spacebio_review.review.evaluation import (
    GBT42048,
    GENERAL,
    configure_evaluation,
    system_from_context,
)
from spacebio_review.review.project import REVIEW_ASPECTS, load_preparation_config
from spacebio_review.v4.assessment_input import build_evaluation_context
from spacebio_review.v4.config import V4Config


def test_rubric_is_complete_and_grade_boundaries_are_unambiguous():
    assert V4Config().evaluation_system == "general"
    assert GENERAL.aspects == REVIEW_ASPECTS
    assert sum(c.max_score for c in GBT42048.criteria) == 100
    for criterion in GBT42048.criteria:
        scores = [n for band in criterion.bands for n in range(band.minimum, band.maximum + 1)]
        assert sorted(scores) == list(range(criterion.max_score + 1))
    assert [GBT42048.grade_for(n).id for n in (0, 59, 60, 84, 85, 100)] == [
        "C",
        "C",
        "B",
        "B",
        "A",
        "A",
    ]


def test_selection_replaces_general_core_and_freezes_actual_rules():
    runtime = load_runtime("config/runtime.yaml")
    preparation = load_preparation_config("config/preparation.yaml")
    runtime = runtime.model_copy(update={"evaluation_core": "old general criterion"})
    preparation.evaluation = preparation.evaluation.model_copy(
        update={"core": "old preparation criterion", "mission_context": "project background"}
    )
    selected_runtime, selected_prep = configure_evaluation(GBT42048, runtime, preparation)
    context = build_evaluation_context(
        SkillLibrary(Path("skills")).get(selected_runtime.evaluation_skill),
        selected_runtime.evaluation_core,
        selected_prep.evaluation,
        system=GBT42048,
    )
    assert "old general criterion" not in context["prompt"]
    assert "old preparation criterion" not in context["prompt"]
    assert "project background" in context["prompt"]
    assert system_from_context(context) == GBT42048
    assert digest(context["system"]) != digest(GENERAL.snapshot())
    assert runtime.evaluation_skill == "exploration-review"
    assert system_from_context({}) == GENERAL
    with pytest.raises(ValueError, match="skill"):
        configure_evaluation(GBT42048, runtime, preparation, "exploration-review")


def test_general_custom_skill_still_works():
    runtime = load_runtime("config/runtime.yaml")
    preparation = load_preparation_config("config/preparation.yaml")
    selected, prep = configure_evaluation(GENERAL, runtime, preparation, "custom-review")
    assert selected.evaluation_skill == "custom-review"
    assert prep is preparation


@pytest.mark.asyncio
async def test_standalone_bundle_binds_national_rules_before_any_stage(tmp_path, monkeypatch):
    from spacebio_review.agents import AgentRuntime
    from spacebio_review.application import ApplicationBundle
    from spacebio_review.runner import review_bundle
    from spacebio_review.v4.config import ReportingConfig
    from spacebio_review.v4.coordinator import Coordinator

    runtime = AgentRuntime({}, SkillLibrary(Path("skills")))
    observed = []

    async def inspect(coordinator, preparation, literature, output):
        observed.append(coordinator.ex.evaluation)
        assert coordinator.ex.system == GBT42048
        assert runtime.evaluation.name == GBT42048.skill
        assert preparation.config.evaluation.core == GBT42048.core
        return {"evaluation_system": coordinator.ex.system.snapshot()}

    monkeypatch.setattr(Coordinator, "run", inspect)
    try:
        result = await review_bundle(
            runtime,
            None,
            None,
            ApplicationBundle(sources=[]),
            tmp_path,
            V4Config(evaluation_system=GBT42048.id),
            load_preparation_config("config/preparation.yaml"),
            ReportingConfig(),
        )
        assert len(observed) == 1
        assert GBT42048.prompt(GBT42048.aspects) in observed[0]["prompt"]
        assert "总分 85" not in observed[0]["prompt"]
        assert result.evaluation_system == GBT42048.snapshot()
    finally:
        await runtime.close()
