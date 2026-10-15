import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_assessed_publication import frozen_fixture, prose

from spacebio_review.agents.contracts import example_for
from spacebio_review.models.provider import ChatReply
from spacebio_review.review.evaluation import GBT42048, GENERAL
from spacebio_review.v4.assessed_delivery import run_downstream
from spacebio_review.v4.assessment import assessment_contract
from spacebio_review.v4.budget import Budget
from spacebio_review.v4.config import ReportingConfig, V4Config
from spacebio_review.v4.manuscript_call import ManuscriptCall, ManuscriptConfig
from spacebio_review.v4.publication_compile import compile_publication, main_titles
from spacebio_review.v4.publication_document import PublicationDocument, walk_nodes
from spacebio_review.v4.publication_render import Renderer
from spacebio_review.v4.publication_translation import translate_section
from spacebio_review.v4.records import Records
from spacebio_review.v4.report_authoring import (
    author_contract,
    author_materials,
    sized_writing_plan,
    writing_plan,
)


def national_fixture(*, veto=True, skipped=True):
    frozen = frozen_fixture()
    frozen["evaluation"]["system"] = GBT42048.snapshot()
    frozen["materials"]["admission"] = {
        "kind": "proposal_content",
        "status": "skipped" if skipped else "completed",
        "conclusion": None if skipped else "A",
    }
    value = example_for(assessment_contract(frozen).schema())
    for key in ("project", "scope"):
        value[key] = prose("围绕现有载荷展开的实验评价。", ["E1"])
    for (key, row), score in zip(value["dimensions"].items(), (22, 20, 16, 16, 5, 9)):
        row.update(score=score, body=prose(f"{key} 的具体评分依据。", ["E1"]))
    value["safety_veto"] = {
        "applies": veto,
        "reason": prose("原文记载的密封失效可使有害物直接进入载人舱。", ["E1"])
        if veto
        else prose("现有证据未构成安全否决依据。", ["E1"]),
    }
    value["unknowns"] = []
    value["appendix_topics"] = []
    assessment = {
        **value,
        "evaluation_system": GBT42048.snapshot(),
        "total_score": 88,
        "score_grade": "A",
        "recommendation": "not_support" if veto else "priority_support",
    }
    return frozen, value, assessment


def authored_fixture():
    return {
        "overall": prose("依据六项评价解释已经确定的结论。", ["E1"]),
        "summary": prose("评分依据摘要。"),
    }


@pytest.mark.parametrize("veto", [False, True])
def test_national_publication_keeps_numeric_grade_and_final_outcome_distinct(veto):
    frozen, _, assessment = national_fixture(veto=veto)
    document, _ = compile_publication(assessment, authored_fixture(), frozen)
    assert document.format_version == "publication-3"
    assert document.grades == {}
    assert document.scores == dict(zip(GBT42048.aspects, (22, 20, 16, 16, 5, 9)))
    assert document.total_score == 88 and document.score_grade == "A"
    assert {"conditions", "alternatives"}.isdisjoint(s.section_id for s in document.main)
    zh, en = Renderer(document).render("zh"), Renderer(document, "en").render("en")
    assert "| 科学目标与意义 | 22 | 24 |" in zh
    assert "| Research foundation | 5 | 6 |" in en
    assert "| 总分 | 88 | 100 |" in zh and "| Total | 88 | 100 |" in en
    assert "分数等级：A" in zh and "Score grade: A" in en
    assert "置信度" not in zh and "Confidence" not in en
    assert "计划书内容形式审查：已跳过" in zh
    assert "Proposal-content formal review: skipped" in en
    assert "依据六项评价解释已经确定的结论。 [[1]](#ref-1)" in zh
    if veto:
        assert "**最终结论：不通过（安全否决）**" in zh
        assert "**Final outcome: Not passed (safety veto)**" in en
        assert "原文记载的密封失效可使有害物直接进入载人舱。 [[1]](#ref-1)" in zh
        assert "优先支持" not in zh
    else:
        assert "**最终结论：优秀，优先支持**" in zh
        assert "**Final outcome: Excellent; priority support**" in en
        assert "### 安全否决依据" not in zh


def test_conclusion_writer_receives_final_scores_without_an_earlier_overall(tmp_path):
    frozen, _, assessment = national_fixture()
    assert "overall" not in assessment
    assert "overall" not in assessment_contract(frozen).schema()["properties"]
    keys = writing_plan(assessment)[0]
    assert keys == ["overall", "summary"]
    material = author_materials(frozen, assessment, keys)
    assert material["evaluation_system"] == GBT42048.snapshot()
    assert material["recommendation"] == "not_support"
    assert material["scoring"] == {"total_score": 88, "score_grade": "A", "safety_veto": True}
    assert material["main"]["safety_veto"] == assessment["safety_veto"]
    assert "E1" in material["materials"]["evidence"]
    assert {"conditions", "alternatives", "overall"}.isdisjoint(material["main"])
    assert list(material["units"]) == ["overall", "summary"]
    assert (
        list(author_contract(keys, material["materials"]["evidence"]).schema()["properties"])
        == keys
    )
    caller = ManuscriptCall(Records(tmp_path), ManuscriptConfig.load())
    assert sized_writing_plan(caller, frozen, assessment) == [["overall", "summary"]]


def test_publication_uses_frozen_criterion_labels_and_explicit_review_status():
    frozen, _, assessment = national_fixture(skipped=False)
    assessment["evaluation_system"]["criteria"][0]["label"] = "本轮科学目标与意义"
    document, _ = compile_publication(assessment, authored_fixture(), frozen)
    zh = Renderer(document).render("zh")
    assert "| 本轮科学目标与意义 | 22 | 24 |" in zh
    assert "计划书内容形式审查：已启用" in zh and "已跳过" not in zh
    assert list(main_titles(GBT42048))[4:10] == list(GBT42048.aspects)
    assert set(GENERAL.aspects).issubset(main_titles(GENERAL))


def test_old_publication_document_remains_readable_as_general():
    old = {
        "format_version": "publication-2",
        "main": [],
        "appendix": [],
        "recommendation": "revise",
        "grades": {key: {"grade": "B", "confidence": "C"} for key in GENERAL.aspects},
    }
    document = PublicationDocument.model_validate(old)
    renderer = Renderer(document)
    assert "| 科学价值 | B | C |" in renderer.grade_table()
    assert renderer.decision_header() == "**整体结论：当前方案尚不足以支持**"


@pytest.mark.asyncio
@pytest.mark.parametrize("veto", [False, True])
async def test_score_section_translation_spends_one_request_only_for_veto_reason(tmp_path, veto):
    frozen, _, assessment = national_fixture(veto=veto)
    document, _ = compile_publication(assessment, authored_fixture(), frozen)
    section = next(item for item in document.main if item.section_id == "grades")
    records = Records(tmp_path)
    budget = Budget(records, V4Config(), ReportingConfig())

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, *, response_schema=None):
            ticket = budget.reserve(self.config, messages, response_schema=response_schema)
            data = json.loads(messages[1]["content"])
            assert list(data["units"].values()) == ["原文记载的密封失效可使有害物直接进入载人舱"]
            budget.settle(ticket, None)
            return ChatReply({"content": json.dumps(data)})

        async def close(self):
            pass

    caller = ManuscriptCall(records, ManuscriptConfig.load(), budget=budget, model_factory=Model)
    translated = await translate_section(caller, section)
    assert len(budget.data["requests"]) == int(veto)
    assert all(row["writing"] for row in budget.data["requests"])
    assert await translate_section(caller, section) == translated
    assert len(budget.data["requests"]) == int(veto)
    english = Renderer(document, "en").render("en", [translated])
    assert "| Total | 88 | 100 |" in english and "Score grade: A" in english


@pytest.mark.asyncio
async def test_local_national_delivery_reuses_calls_and_preserves_code_owned_scores(tmp_path):
    frozen, draft, _ = national_fixture()
    captured = []

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, *, response_schema=None):
            assert not tools
            data = json.loads(messages[1]["content"])
            captured.append(data)
            value = example_for(response_schema)
            if "dimensions" in value:
                assert "overall" not in value
                value = draft
            elif "summary" in value:
                assert list(value) == ["overall", "summary"]
                assert data["scoring"]["total_score"] == 88
                assert data["scoring"]["safety_veto"] is True
                assert "overall" not in data["main"]
                value["overall"] = prose("六项证据形成当前得分，安全否决决定不通过。", ["E1"])
                value["summary"] = prose("总分较高，但安全否决使项目不通过。", ["E1"])
            return ChatReply({"content": json.dumps(value)})

        async def close(self):
            pass

    caller = ManuscriptCall(Records(tmp_path), ManuscriptConfig.load(), model_factory=Model)
    frozen_ref = caller.records.put("frozen-review", frozen)
    _, delivery_ref = await run_downstream(caller, frozen_ref, tmp_path, translate=False)
    assert len(captured) == 2
    await run_downstream(caller, frozen_ref, tmp_path, translate=False)
    assert len(captured) == 2
    await run_downstream(caller, frozen_ref, tmp_path, translate=True)
    completed = len(captured)
    await run_downstream(caller, frozen_ref, tmp_path, translate=True)
    assert len(captured) == completed
    english = (tmp_path / "report.en.md").read_text("utf-8")
    assert "| Total | 88 | 100 |" in english
    assert "**Final outcome: Not passed (safety veto)**" in english
    manifest = caller.records.value(caller.records.value(delivery_ref)["manifest"])
    document = PublicationDocument.model_validate(manifest["document"])
    overall = next(section for section in document.main if section.section_id == "overall")
    assert overall.blocks[0].children[0].text == "六项证据形成当前得分，安全否决决定不通过。"
    assert all(
        node.kind != "text" or "88 / 100" not in node.text
        for section in document.main
        for node in walk_nodes(section.blocks)
    )
    assert all("scoring" not in row for row in captured[2:])


@pytest.mark.asyncio
async def test_writer_probe_constructs_new_input_without_editing_old_assessment(
    tmp_path, monkeypatch
):
    spec = importlib.util.spec_from_file_location(
        "probe_gbt_stage", Path(__file__).parents[1] / "scripts" / "probe_gbt_stage.py"
    )
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    frozen, _, assessment = national_fixture()
    assessment["overall"] = prose("旧综合稿不进入本次撰写输入。")
    source, output = tmp_path / "source", tmp_path / "writer"
    source.mkdir()
    (source / "frozen.json").write_text(json.dumps(frozen), "utf-8")
    original = json.dumps(assessment)
    (source / "assessment.json").write_text(original, "utf-8")
    captured = []

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, *, response_schema=None):
            data = json.loads(messages[1]["content"])
            captured.append(data)
            assert list(data["units"]) == ["overall", "summary"]
            assert "旧综合稿" not in str(data)
            return ChatReply({"content": json.dumps(authored_fixture())})

        async def close(self):
            pass

    monkeypatch.setattr(
        probe,
        "ManuscriptCall",
        lambda records, config, **kwargs: ManuscriptCall(
            records, config, model_factory=Model, **kwargs
        ),
    )
    records = Records(output)
    budget = Budget(records, V4Config(), ReportingConfig())
    await probe.run_summary(
        SimpleNamespace(from_case=source, assessment_config="config/assessment.yaml"),
        output,
        records,
        budget,
    )
    assert len(captured) == 1
    assert (source / "assessment.json").read_text("utf-8") == original
    assert "overall" not in json.loads((output / "writer-assessment.json").read_text("utf-8"))
    assert list(json.loads((output / "summary.json").read_text("utf-8"))) == ["overall", "summary"]
