import json

import pytest

from spacebio_review.agents.contracts import example_for
from spacebio_review.models.provider import ChatReply, ModelError
from spacebio_review.review.project import REVIEW_ASPECTS
from spacebio_review.v4.assessed_delivery import run_downstream
from spacebio_review.v4.assessment import assessment_contract
from spacebio_review.v4.manuscript_call import ManuscriptCall, ManuscriptConfig
from spacebio_review.v4.publication_compile import compile_publication
from spacebio_review.v4.publication_document import walk_nodes
from spacebio_review.v4.publication_render import Renderer
from spacebio_review.v4.records import Records
from spacebio_review.v4.report_authoring import (
    author_materials,
    author_report,
    sized_writing_plan,
    writing_plan,
)


def prose(text, ids=()):
    return {"paragraphs": [{"parts": [{"text": text, "cites": list(ids)}]}]}


def frozen_fixture():
    return {
        "evaluation": {"prompt": "Common standard"},
        "materials": {
            "application": ["E1"],
            "evidence": {
                "E1": {
                    "source": "s1",
                    "locator": "p1",
                    "reading_scope": "text",
                    "quote": "Application fact",
                },
                "E2": {
                    "source": "s2",
                    "locator": "abstract",
                    "reading_scope": "abstract",
                    "quote": "External finding",
                },
            },
            "sources": {
                "s1": {"title": "项目申请材料", "kind": "application"},
                "s2": {"title": "Study", "kind": "paper", "url": "https://example.org/paper"},
            },
            "opinions": {
                "O1": {
                    "context": "G1",
                    "text": {"parts": [{"text": "Opinion", "cites": ["E2"]}]},
                    "related_opinions": [],
                }
            },
            "opinion_contexts": {
                "G1": {
                    "title": "Opinion context",
                    "scope": {
                        "parts": [{"text": "Only for this comparison", "cites": ["E1"]}],
                    },
                },
            },
        },
        "provenance": {
            "opinions": {"O1": {"artifact": "research-hash", "block": "b1"}},
            "evidence": {"E1": [], "E2": []},
        },
    }


def draft_fixture(frozen):
    value = example_for(assessment_contract(frozen).schema())
    value["appendix_topics"] = [
        {
            "title": "实验比较",
            "focus": "解释比较范围",
            "opinion_ids": ["O1"],
            "main_sections": [REVIEW_ASPECTS[0]],
        }
    ]
    for item in value["dimensions"].values():
        item["body"] = prose("本维度的最终论述。", ["E1"])
    for key in ("project", "scope", "overall", "support_scope", "alternatives"):
        value[key] = prose("主文的最终论述。", ["E1"])
    value["unknowns"] = []
    value["conditions"] = [
        {"timing": "implementation_milestone", "text": prose("实施条件。", ["E1"])}
    ]
    return value


def test_local_writer_gets_whole_scope_and_no_audit():
    frozen = frozen_fixture()
    assessment = draft_fixture(frozen)
    materials = author_materials(frozen, assessment, ["topic_1"])
    assert (
        materials["materials"]["opinion_contexts"]["G1"]["scope"]
        == frozen["materials"]["opinion_contexts"]["G1"]["scope"]
    )
    assert set(materials["materials"]["evidence"]) == {"E1", "E2"}
    assert "research-hash" not in json.dumps(materials)
    assert writing_plan(assessment) == [["summary"], ["topic_1"]]
    assert set(materials["main"]) == {REVIEW_ASPECTS[0]}
    summary = author_materials(frozen, assessment, ["summary"])
    assert not summary["materials"]["opinions"]
    assert "completed_dimensions" not in summary
    assert "appendix_topics" not in summary


def test_compile_has_one_grade_authority_and_shared_reference_numbers():
    frozen = frozen_fixture()
    assessment = draft_fixture(frozen)

    sections = {"summary": prose("完整的摘要。", ["E1"])}
    sections["topic_1"] = prose("专题中的外部依据。", ["E2", "E1"])
    assessment["project"]["paragraphs"].append({"parts": [{"text": "独立第二段。", "cites": []}]})
    doc, provenance = compile_publication(assessment, sections, frozen)
    zh, appendix = Renderer(doc).render("zh"), Renderer(doc).render("appendix")
    assert doc.numbering == {"s1": 1, "s2": 2}
    assert "ref-2" not in zh
    assert "[[2]](#ref-2) [[1]](#ref-1)" in appendix
    assert "仅摘要" not in appendix and "research-hash" not in appendix
    assert "实际评审覆盖" not in zh
    assert len(doc.grades) == 6
    assert "### 实施里程碑" in zh and "### 支持前需明确" not in zh
    assert "主文的最终论述。 [[1]](#ref-1)\n\n独立第二段。" in zh
    assert "本维度的最终论述。" in zh
    assert all(
        node.kind != "crossref"
        for section in doc.main + doc.appendix
        for node in walk_nodes(section.blocks)
    )
    assert provenance["opinions"]
    en = Renderer(doc, "en").render("en")
    assert "[[1]](#ref-1)" in en
    with pytest.raises(KeyError):
        compile_publication(
            assessment, {k: v for k, v in sections.items() if k != "summary"}, frozen
        )


@pytest.mark.asyncio
async def test_publication_pipeline_and_recovery_never_call_research(tmp_path):
    frozen, calls = frozen_fixture(), []

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, *, response_schema=None):
            assert not tools
            calls.append(messages)
            value = example_for(response_schema)
            if "dimensions" in value:
                value = draft_fixture(frozen)
            return ChatReply({"content": json.dumps(value)})

        async def close(self):
            pass

    caller = ManuscriptCall(Records(tmp_path), ManuscriptConfig.load(), model_factory=Model)
    ref = caller.records.put("frozen-review", frozen)
    first = await run_downstream(caller, ref, tmp_path, translate=False)
    assert len(calls) == 3
    assert await run_downstream(caller, ref, tmp_path, translate=False) == first
    assert len(calls) == 3
    assert [list(json.loads(m[1]["content"])["units"]) for m in calls[1:]] == [
        ["summary"],
        ["topic_1"],
    ]
    receipt = json.loads((tmp_path / "delivery.json").read_text("utf-8"))
    assert receipt["status"] == "local_completed"
    assert receipt["en"]["status"] == "not_run"
    assert (tmp_path / "report.md").exists()
    await run_downstream(caller, ref, tmp_path, translate=True)
    translated_calls = len(calls)
    await run_downstream(caller, ref, tmp_path, translate=True)
    assert len(calls) == translated_calls
    receipt = json.loads((tmp_path / "delivery.json").read_text("utf-8"))
    assert receipt["status"] == "completed"
    english = (tmp_path / "report.en.md").read_text("utf-8")
    assert "| Scientific value | A | A |" in english


@pytest.mark.asyncio
async def test_incomplete_authoring_is_recorded_without_archive_fallback(tmp_path):
    frozen, calls = frozen_fixture(), []

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, *, response_schema=None):
            calls.append(messages)
            if len(calls) == 3:
                raise ModelError("response_unknown")
            value = example_for(response_schema)
            if "dimensions" in value:
                value = draft_fixture(frozen)
            return ChatReply({"content": json.dumps(value)})

        async def close(self):
            pass

    caller = ManuscriptCall(Records(tmp_path), ManuscriptConfig.load(), model_factory=Model)
    ref = caller.records.put("frozen-review", frozen)
    with pytest.raises(ModelError):
        await run_downstream(caller, ref, tmp_path, translate=False)
    progress = json.loads((caller.records.root / "authorship.json").read_text("utf-8"))
    assert progress["completed"] == ["summary"]
    assert "topic_1" in progress["missing"] and progress["status"] == "incomplete"
    assert not (tmp_path / "report.md").exists()
    with pytest.raises(ValueError, match="already_attempted"):
        await run_downstream(caller, ref, tmp_path, translate=False)
    assert len(calls) == 3


def test_empty_appendix_costs_no_calls_and_batches_keep_topics_whole(tmp_path):
    frozen = frozen_fixture()
    assessment = draft_fixture(frozen)
    caller = ManuscriptCall(Records(tmp_path), ManuscriptConfig.load())
    topic = assessment["appendix_topics"][0]
    assessment["appendix_topics"] = []
    assert sized_writing_plan(caller, frozen, assessment) == [["summary"]]
    assessment["appendix_topics"] = [topic] * 5
    plan = sized_writing_plan(caller, frozen, assessment)
    assert [key for group in plan for key in group] == [
        "summary",
        *[f"topic_{i}" for i in range(1, 6)],
    ]


@pytest.mark.asyncio
async def test_unrelated_main_edit_reuses_exact_appendix_request(tmp_path):
    frozen, calls = frozen_fixture(), []

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, *, response_schema=None):
            calls.append(list(json.loads(messages[1]["content"])["units"]))
            return ChatReply({"content": json.dumps(example_for(response_schema))})

        async def close(self):
            pass

    caller = ManuscriptCall(Records(tmp_path), ManuscriptConfig.load(), model_factory=Model)
    ref = caller.records.put("frozen-review", frozen)
    assessment = draft_fixture(frozen)
    original = caller.records.put("global-assessment", assessment)
    await author_report(caller, ref, original)
    assessment["project"] = prose("Changed project explanation.", ["E1"])
    changed = caller.records.put("global-assessment", assessment)
    await author_report(caller, ref, changed)
    assert calls == [["summary"], ["topic_1"], ["summary"]]
