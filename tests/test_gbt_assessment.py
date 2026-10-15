import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from spacebio_review.agents.contracts import example_for
from spacebio_review.models.provider import ChatReply
from spacebio_review.review.evaluation import GBT42048, GENERAL
from spacebio_review.v4.assessment import (
    assessment_contract,
    assessment_request,
    assessment_system,
    finalize_assessment,
    main_sections,
    synthesize,
)
from spacebio_review.v4.manuscript_call import ManuscriptCall, ManuscriptConfig
from spacebio_review.v4.records import Records


def frozen(system=GBT42048):
    return {
        "evaluation": {"system": system.snapshot(), "prompt": system.prompt(system.aspects)},
        "materials": {
            "opinions": {},
            "opinion_contexts": {},
            "evidence": {"E1": {"source": "proposal", "quote": "已有载荷上的描述性研究"}},
            "application": ["E1"],
            "sources": {"proposal": {"title": "局部测试计划书", "kind": "application"}},
            "research_coverage": {key: {"status": "unassessed"} for key in system.aspects},
            "admission": {"status": "skipped", "scope": "计划书内容形式审查"},
        },
    }


def scored(total, *, veto=False):
    value = example_for(assessment_contract(frozen()).schema())
    remaining = total
    for criterion in GBT42048.criteria:
        score = min(remaining, criterion.max_score)
        value["dimensions"][criterion.id]["score"] = score
        remaining -= score
    value["safety_veto"]["applies"] = veto
    return value


@pytest.mark.parametrize(
    "total,grade,recommendation",
    [
        (0, "C", "not_support"),
        (59, "C", "not_support"),
        (60, "B", "support"),
        (84, "B", "support"),
        (85, "A", "priority_support"),
        (100, "A", "priority_support"),
    ],
)
def test_scores_determine_grade_and_conclusion(total, grade, recommendation):
    value = scored(total)
    parsed = assessment_contract(frozen()).parse(value)
    result = finalize_assessment(parsed.model_dump(mode="json"), GBT42048)
    assert (result["total_score"], result["score_grade"], result["recommendation"]) == (
        total,
        grade,
        recommendation,
    )
    assert assessment_system(result) == GBT42048
    assert result["dimensions"] == value["dimensions"]


def test_safety_veto_preserves_high_score_and_low_safety_score_is_not_a_veto():
    result = finalize_assessment(scored(88, veto=True), GBT42048)
    assert (result["total_score"], result["score_grade"], result["recommendation"]) == (
        88,
        "A",
        "not_support",
    )
    value = scored(100)
    value["dimensions"]["safety"]["score"] = 0
    result = finalize_assessment(value, GBT42048)
    assert (result["total_score"], result["score_grade"], result["recommendation"]) == (
        82,
        "B",
        "support",
    )


@pytest.mark.parametrize("score", [None, 1.5, "5", True, -1, 25])
def test_national_contract_rejects_noninteger_missing_or_out_of_range_scores(score):
    value = scored(60)
    value["dimensions"]["scientific_objectives"]["score"] = score
    with pytest.raises(ValidationError):
        assessment_contract(frozen()).parse(value)


def test_unresearched_dimensions_are_required_and_derived_results_are_not_model_fields():
    contract = assessment_contract(frozen())
    value = scored(60)
    assert set(value["dimensions"]) == set(GBT42048.aspects)
    assert set(value["dimensions"]["safety"]) == {"body", "score"}
    assert (
        not {
            "conditions",
            "alternatives",
            "overall",
            "recommendation",
            "total_score",
            "score_grade",
        }
        & value.keys()
    )
    incomplete = deepcopy(value)
    incomplete["dimensions"].pop("research_foundation")
    with pytest.raises(ValidationError):
        contract.parse(incomplete)
    value["recommendation"] = "priority_support"
    with pytest.raises(ValidationError):
        contract.parse(value)
    _, materials = assessment_request(frozen())
    assert materials["admission"]["status"] == "skipped"
    assert all(v["status"] == "unassessed" for v in materials["research_coverage"].values())


def test_contract_uses_frozen_rubric_not_current_global_definition():
    archived = frozen()
    archived["evaluation"]["system"]["version"] = "archived-rules"
    archived["evaluation"]["system"]["criteria"][0]["max_score"] = 23
    with pytest.raises(ValidationError):
        assessment_contract(archived).parse(scored(100))
    assert GBT42048.criteria[0].max_score == 24


def test_general_preserves_ratings_without_national_scoring_or_remediation_fields():
    value = example_for(assessment_contract(frozen(GENERAL)).schema())
    assert set(value["dimensions"]) == set(GENERAL.aspects)
    value["dimensions"]["rigor"].update(grade=None, confidence=None)
    assessment_contract(frozen(GENERAL)).parse(value)
    result = finalize_assessment(value, GENERAL)
    assert (
        not {"total_score", "score_grade", "safety_veto", "conditions", "alternatives"}
        & result.keys()
    )
    assert "conditions" not in main_sections(GENERAL)
    assert "alternatives" not in main_sections(GBT42048)


def test_json_object_request_supplies_the_contract_to_the_model(tmp_path):
    config = ManuscriptConfig.load()
    config = config.model_copy(
        update={"model": config.model.model_copy(update={"structured_output": "json_object"})}
    )
    caller = ManuscriptCall(Records(tmp_path), config)
    contract = assessment_contract(frozen())
    prompt, materials = assessment_request(frozen())
    request = caller.request(prompt, materials, contract, 4096)
    instruction, schema = request["messages"][0]["content"].rsplit(
        "按以下 JSON Schema 提交 JSON 对象：\n", 1
    )
    assert instruction.strip() == prompt.strip()
    assert json.loads(schema) == contract.schema()
    assert json.loads(request["messages"][1]["content"]) == materials


@pytest.mark.asyncio
async def test_same_request_reuses_receipt_and_systems_cannot_share_assessment(tmp_path):
    calls = []

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, *, response_schema=None):
            calls.append(messages)
            return ChatReply({"content": json.dumps(example_for(response_schema))})

        async def close(self):
            pass

    caller = ManuscriptCall(Records(tmp_path), ManuscriptConfig.load(), model_factory=Model)
    national = caller.records.put("frozen-review", frozen())
    first = await synthesize(caller, national)
    assert await synthesize(caller, national) == first
    general = caller.records.put("frozen-review", frozen(GENERAL))
    second = await synthesize(caller, general)
    assert first != second and len(calls) == 2
    assert caller.records.value(first)["evaluation_system"]["id"] == GBT42048.id
    assert caller.records.value(second)["evaluation_system"]["id"] == GENERAL.id
