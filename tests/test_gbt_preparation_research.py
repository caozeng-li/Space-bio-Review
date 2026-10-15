"""Selected criteria reach preparation, native questions and one expert task."""

import json
from types import SimpleNamespace

import pytest
from test_question_generation import package, preparation, question, runtime_with_response

from spacebio_review.review.evaluation import GBT42048, GENERAL
from spacebio_review.review.project import load_preparation_config
from spacebio_review.v4.config import V4Config
from spacebio_review.v4.execution import Execution
from spacebio_review.v4.preparation import Preparation
from spacebio_review.v4.questions import question_contract, research_task, select_questions
from spacebio_review.v4.records import Records
from spacebio_review.v4.research import Research


def execution(tmp_path, runtime, system):
    return Execution(
        runtime,
        Records(tmp_path),
        V4Config(evaluation_system=system.id, literature_enabled=False),
        {"system": system.snapshot()},
    )


@pytest.mark.asyncio
async def test_question_request_changes_system_and_reuses_only_matching_receipt(tmp_path):
    response = {"questions": [question(evidence=["E1"])]}
    runtime, requests = runtime_with_response(tmp_path, response=response)
    try:
        for system in (GENERAL, GBT42048):
            response["questions"][0]["aspects"] = [system.aspects[0]]
            ex = execution(tmp_path, runtime, system)
            generator = preparation(ex).questions(package())
            result = await generator.run()
            await generator.run()
            assert result["questions"][0]["aspects"] == [system.aspects[0]]
            request = requests[-1]
            schema = request["schema"]["$defs"]["ReviewQuestion"]["properties"]["aspects"]
            assert schema["items"]["enum"] == list(system.aspects)
            materials = {
                row["source_id"]: row["content"]
                for row in json.loads(request["messages"][1]["content"])
            }
            assert list(materials["question-aspects"]) == list(system.aspects)
            text = json.dumps(request["messages"], ensure_ascii=False)
            assert text.count("SOURCE_ONLY_ONCE") == 1
            assert "OLD_MAPPED_INTERPRETATION" not in text
            assert "共同评价语境" not in text
            if system is GBT42048:
                criteria = materials["evaluation-criteria"]["prompt"]
                assert "research_foundation" in criteria
                assert "预期成果与效益" in criteria
                assert "17—24 分" in criteria
                assert "对项目评价结论的影响" in request["messages"][0]["content"]
                assert schema["minItems"] == 1
            else:
                assert "evaluation-criteria" not in materials
        assert len(requests) == 2
    finally:
        await runtime.close()


@pytest.mark.parametrize("aspects", [["rigor"], ["feasibility"], ["management"], []])
def test_gbt_questions_cannot_submit_general_or_empty_aspects(aspects):
    item = question()
    item["aspects"] = aspects
    contract = question_contract(package()["experts"], system=GBT42048)
    with pytest.raises(ValueError):
        contract.parse({"questions": [item]})


@pytest.mark.asyncio
async def test_gbt_project_facts_keep_sources_and_reach_team_configuration(tmp_path):
    runtime, requests = runtime_with_response(tmp_path)
    ex = execution(tmp_path, runtime, GBT42048)
    prep = Preparation(ex, None, None, load_preparation_config("config/preparation.yaml"))
    source = {
        "reference": {"source_id": "application", "passage_id": "p1", "visual_id": None},
        "title": "计划书",
        "kind": "application",
        "locator": "实验基础与安排",
        "quote": "已有培养载荷支持密闭单元，已完成地面预实验，拟开展在轨培养并返回分析。",
        "reading_scope": "text",
    }
    readings = {
        "notes": [{"observations": [], "evidence": {"source-fact": source}}],
        "limits": ["未明确回收时长。"],
        "coverage": {},
    }
    stages = []
    profile = next(iter(prep.catalogue.entries))

    async def required(key, kind, objective, contract, **kwargs):
        stages.append(key)
        if key == "prepare/map":
            assert "review_context" in contract.schema()["properties"]
            assert "计划开展的实验不计为已完成的研究基础" in objective
            draft = {
                "title": "已有培养载荷实验",
                "project_objective": "认识在轨培养的生长变化",
                "paths": [
                    {
                        "question": "在轨培养的生长如何变化？",
                        "activity": "在轨培养并返回分析",
                        "observation_or_artifact": "生长观测",
                        "claimed_knowledge": "生长变化",
                        "claimed_value": "认识培养过程",
                        "dependencies": ["回收"],
                        "evidence_handles": ["E1"],
                    }
                ],
                "review_context": [
                    {
                        "kind": "research_foundation",
                        "statement": "已完成地面预实验，在轨培养属于拟开展工作。",
                        "evidence_handles": ["E1"],
                    },
                    {
                        "kind": "existing_payload",
                        "statement": "已有培养载荷支持密闭单元。",
                        "evidence_handles": ["E1"],
                    },
                ],
                "understanding_limits": [],
            }
        else:
            project = next(m.data for m in kwargs["materials"] if m.source_id == "project")
            assert project["review_context"][0]["evidence_handles"] == ["source-fact"]
            assert project["evidence"]["source-fact"] == source
            assert project["understanding_limits"] == ["未明确回收时长。"]
            assert "安全能力" in objective and "研究基础和成果效益" in objective
            draft = {
                "experts": [
                    {
                        "purpose": "分析已有培养实验与在轨实施条件",
                        "model_tier": "normal",
                        "profile_ids": [profile],
                        "method_skill_ids": [],
                    }
                ]
            }
        return contract.product(contract.parse(draft)).model_dump(mode="json")

    ex.required = required
    try:
        project = await prep.map_project(readings)
        experts = await prep.team(project)
        assert stages == ["prepare/map", "prepare/team"]
        assert len(experts) == 1
        assert not requests
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_expert_receives_only_its_gbt_criteria_and_originals(tmp_path):
    runtime, requests = runtime_with_response(tmp_path)
    ex = execution(tmp_path, runtime, GBT42048)
    prep = preparation(ex)
    prep.store = prep.reader().references.store
    prep.config = SimpleNamespace(evaluation=SimpleNamespace(historical_as_of=None))
    source_reader = prep.reader()
    doc = prep.store.retained("application")
    source_reader.record_context_read(
        {"source_id": "application", "passages": [p.model_dump() for p in doc.passages]}
    )
    originals = source_reader.references.snapshot_choices()
    value = package()
    value["question_evidence"] = originals
    item = question(evidence=list(originals))
    item["aspects"] = ["research_foundation", "experiment_feasibility"]
    task = research_task("q1", select_questions({"questions": [item]}, 1)["q1"])

    class Captured(Exception):
        pass

    async def call(key, kind, objective, contract, **kwargs):
        work = next(m.data for m in kwargs["materials"] if m.source_id == "professional-work")
        criteria = work["evaluation_criteria"]
        assert "research_foundation：" in criteria
        assert "experiment_feasibility：" in criteria
        assert "scientific_objectives：" not in criteria
        assert "safety：" not in criteria
        assert work["members"]["q1"] == item
        assert kwargs["refs"][0].source_id == "application"
        assert "不独立给指标分数" in objective
        assert "不生成整改、改进方案或行动清单" in objective
        assert "以本题涉及的知识贡献和观测解释为中心" not in objective
        assert "research_knowledge" not in {
            row["function"]["name"] for row in kwargs["tools"].schemas()
        }
        raise Captured()

    ex.call = call
    try:
        with pytest.raises(Captured):
            await Research(prep, value, SimpleNamespace()).run(task)
        assert not requests
    finally:
        await runtime.close()
