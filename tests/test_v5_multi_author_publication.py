"""Multi-author assembly and a single-section translation, without a review run."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from spacebio_review.models.config import ModelConfig
from spacebio_review.models.provider import ChatReply
from spacebio_review.models.scheduling import RequestScheduler
from spacebio_review.prose import bind_prose
from spacebio_review.publication import Renderer, compile_publication
from spacebio_review.publication.document import walk_nodes
from spacebio_review.publication.translation import translate_section
from spacebio_review.review.evaluation import GBT42048, GENERAL
from spacebio_review.review.scoring import finalize_assessment
from spacebio_review.v5.authoring import DeliveryParser, numbered_text, research_note
from spacebio_review.v5.budget import Budget
from spacebio_review.v5.delivery import deliver_assessment


def prose(text, *cites):
    return {"paragraphs": [{"parts": [{"text": text, "cites": list(cites)}]}]}


def original(key, source=None):
    return {
        "reference": {"source_id": source or key, "passage_id": "p1"},
        "title": key,
        "kind": "external",
        "locator": "Methods / p1",
        "reading_scope": "text",
        "quote": "Original observation.",
    }


def contribution(inquiry, text, *, ref=None):
    aliases = {"E1": "evidence-" + inquiry, "E2": "evidence-shared-" + inquiry}
    paragraphs = numbered_text(text)
    raw = {"paragraphs": paragraphs, "aliases": aliases}
    note = research_note(raw, title="科学问题 " + inquiry).model_dump(mode="json")
    note["body"] = bind_prose(note["body"], aliases)
    return {
        "inquiry_id": inquiry,
        "contribution_ref": ref or "note-" + inquiry,
        "note": note,
        "evidence": {
            aliases["E1"]: original(inquiry),
            aliases["E2"]: original("Shared methods", source="shared-source"),
        },
        "focus": "原稿回答的科学问题",
        "current_focus": "该作者现在的问题",
        "author_session_id": "session-" + inquiry,
        "author_text_ref": "author-" + inquiry,
        "paragraph_ids": [inquiry + "-" + row["id"] for row in paragraphs],
        "author_paragraph_ids": [row["id"] for row in paragraphs],
    }


def selection(rows, appendix=()):
    def refs(values):
        return [
            {key: row[key] for key in ("inquiry_id", "contribution_ref")} for row in values
        ]
    return {"main_sections": refs(rows), "appendix_sections": refs(appendix)}


def fixture(system=GENERAL):
    values = {
        "summary": prose("项目将区分在轨荧光与返回后分子测量。", "application"),
        "project": prose("在轨观测持续 3 天。"),
        "scope": prose("评审关注测量对象与阶段。"),
        "overall": prose("阶段分工带来互补认识，不能把相关解释写成因果证明。"),
        "dimensions": {
            item.id: {"body": prose("该贡献对" + item.label + "的影响。")}
            for item in system.criteria
        },
        "unknowns": [{"title": "机制问题", "text": prose("因果方向仍待辨认。")}],
    }
    if system.score_grades:
        for row, score in zip(values["dimensions"].values(), (22, 20, 15, 16, 5, 7)):
            row["score"] = score
        values["safety_veto"] = {"applies": False, "reason": prose("不适用安全否决。")}
    else:
        for row in values["dimensions"].values():
            row.update(grade="B", confidence="C")
        values.update(recommendation="support", support_scope=prose("支持分阶段研究。"))
    manuscripts = [
        contribution("A", "**在轨读出**保持对象与 0.5 μM 浓度。[E1]\n\n共享方法。[E2]"),
        contribution("B", "返回后的序列分析。[E1]\n\n共享方法限定。[E2]"),
        contribution("C", "附录保留完整测量条件。[E1]"),
    ]
    sources = {
        "summary": {
            "author_text_ref": "chief-raw", "parse_ref": "chief-parse", "selection": ["p3"]
        },
        "project": {
            "author_text_ref": "chief-raw", "parse_ref": "chief-parse", "selection": ["p1"]
        },
    }
    return {
        "assessment": finalize_assessment(values, system),
        "evidence": {"application": original("application")},
        "report_selection": selection(manuscripts[1::-1], manuscripts[2:]),
        "contributions": manuscripts,
        "author_sources": sources,
    }


@pytest.mark.parametrize("system", [GBT42048, GENERAL])
def test_order_full_scientific_prose_and_unchanged_scoring(system):
    inputs = fixture(system)
    original_inputs = deepcopy(inputs)
    document, audit = compile_publication(**inputs)
    assert [row.section_id for row in document.main[:3]] == ["summary", "project", "scope"]
    assert [row.title for row in document.main[3:5]] == ["科学问题 B", "科学问题 A"]
    assert [row.section_id for row in document.main[5:7]] == ["overall", "grades"]
    assert [row.title for row in document.appendix] == ["科学问题 C"]
    report = Renderer(document).render("zh")
    assert "**在轨读出**保持对象与 0.5 μM 浓度。" in report
    assert "返回后的序列分析。" in report
    assert "附录保留完整测量条件。" not in report
    assert "附录保留完整测量条件。" in Renderer(document).render("appendix")
    if system.score_grades:
        assert document.total_score == 85 and document.score_grade == "A"
        assert "| 总分 | 85 | 100 |" in report
    else:
        assert document.total_score is None and document.scores == {}
        assert document.recommendation == "support" and "支持分阶段研究" in report
    assert audit["report_selection"] == inputs["report_selection"]
    assert inputs == original_inputs


def test_local_aliases_merge_by_identity_and_paragraph_sources_are_kept():
    inputs = fixture()
    document, audit = compile_publication(**inputs)
    assert document.numbering == {"application": 1, "B": 2, "shared-source": 3, "A": 4, "C": 5}
    assert document.locations["evidence-A"].source == "A"
    assert document.locations["evidence-B"].source == "B"
    assert document.locations["evidence-shared-A"].source == "shared-source"
    assert document.locations["evidence-shared-B"].source == "shared-source"
    assert all(not key.startswith("E") for key in audit["evidence"])
    paragraph = document.main[4].blocks[0]
    source = audit["paragraphs"][paragraph.node_id]
    assert source["inquiry_id"] == "A" and source["contribution_ref"] == "note-A"
    assert source["author_text_ref"] == "author-A" and source["paragraph_id"] == "A-p1"
    assert source["author_paragraph_id"] == "p1"
    chief = audit["paragraphs"][document.main[0].blocks[1].node_id]
    assert chief["author_text_ref"] == "chief-raw" and chief["author_paragraph_id"] == "p3"
    assert audit["author_sources"] == inputs["author_sources"]


def test_markdown_blocks_keep_original_paragraph_identity():
    inputs = fixture()
    manuscript = contribution(
        "markdown", "### 测量阶段\n- **在轨**：0.5 μM[E1]\n- 返回后：序列检测\n\n"
        "| 阶段 | 对象 |\n| --- | --- |\n| 在轨 | 荧光[E2] |"
    )
    inputs.update(contributions=[manuscript], report_selection=selection([manuscript]))
    document, audit = compile_publication(**inputs)
    section = document.main[3]
    assert [node.kind for node in section.blocks] == ["heading", "bullet_list", "table"]
    paragraph_ids = [
        audit["paragraphs"][node.node_id]["author_paragraph_id"] for node in section.blocks
    ]
    assert paragraph_ids == ["p1", "p1", "p2"]
    text = Renderer(document).render("zh")
    assert "### 测量阶段" in text and "- **在轨**：0.5 μM [2]" in text
    assert "| 在轨 | 荧光 [3] |" in text


def test_citations_inside_literal_markdown_remain_reference_nodes():
    inputs = fixture()
    manuscript = contribution(
        "code", "正文 `0.5 μM[E1]` 保留代码标记。\n\n"
        "```text\n**literal** 3 days[E2]\n```\n\n<div>条件[E1]</div>"
    )
    inputs.update(contributions=[manuscript], report_selection=selection([manuscript]))
    document, _ = compile_publication(**inputs)
    section = document.main[3]
    assert [node.target for node in walk_nodes(section.blocks) if node.kind == "evidence"] == [
        "evidence-code", "evidence-shared-code", "evidence-code"
    ]
    rendered = Renderer(document).render("zh")
    assert "@[publication-" not in rendered
    assert "` 0.5 μM [2] `" in rendered
    assert "**literal** 3 days [3]" in rendered
    assert "<div>条件 [2]</div>" in rendered


async def test_saved_publication_rebuilds_exact_selected_version(tmp_path):
    inputs = fixture()
    runtime = SimpleNamespace(
        store=SimpleNamespace(root=tmp_path / "v5"),
        budget=Budget(tmp_path), scheduler=RequestScheduler(),
    )
    result = await deliver_assessment(runtime, **inputs)
    manifest_path = tmp_path / result["manifest"]
    saved = json.loads(manifest_path.read_text("utf-8"))
    first = (tmp_path / "report.zh.md").read_text("utf-8")
    selected_id = saved["document"]["main"][4]["section_id"]
    updated = contribution("A", "新的论述改变了问题。[E1]", ref="note-A-revised")
    inputs["contributions"][0] = updated
    inputs["report_selection"] = selection([updated, inputs["contributions"][1]])
    new = await deliver_assessment(runtime, **inputs)
    assert new["manifest"] != result["manifest"]
    assert json.loads(manifest_path.read_text("utf-8")) == saved
    frozen = {key: saved[key] for key in (
        "assessment", "evidence", "report_selection", "contributions", "author_sources", "admission"
    )}
    rebuilt, _ = compile_publication(**frozen)
    assert Renderer(rebuilt).render("zh") == first
    assert rebuilt.main[4].section_id == selected_id
    reordered = deepcopy(frozen)
    reordered["report_selection"]["main_sections"].reverse()
    reordered_doc, _ = compile_publication(**reordered)
    assert reordered_doc.main[3].section_id == selected_id
    assert runtime.budget.usage()["model_calls"] == 0


class FixedTranslation:
    def __init__(self, *, parser=False):
        self.parser, self.requests = parser, []
        self.config = ModelConfig(model="fixed", base_url="https://unused.invalid", max_tokens=512)

    async def complete(self, messages, tools=None, *, response_schema=None):
        payload = json.loads(messages[-1]["content"])
        self.requests.append(payload)
        if self.parser:
            text = json.dumps({"units": [
                {"unit_id": row["unit_label"], "paragraph_ids": [row["id"]]}
                for row in payload["paragraphs"]
            ], "missing_fields": []})
        else:
            text = "\n\n".join(
                f"## {key}\n\nEN {value}" for key, value in payload["units"].items()
            )
        return ChatReply(message={"role": "assistant", "content": text}, finish_reason="stop")


async def test_translate_only_one_selected_scientific_section(tmp_path):
    inputs = fixture()
    document, _ = compile_publication(**inputs)
    before = document.model_dump(mode="json")
    section = document.main[4]
    writer, mapper = FixedTranslation(), FixedTranslation(parser=True)
    translated = await translate_section(
        writer, section, parser=DeliveryParser(mapper), artifact_dir=tmp_path
    )
    assert len(writer.requests) == len(mapper.requests) == 1
    assert translated.title == "EN 科学问题 A"
    assert translated.section_id == section.section_id
    assert [node.target for node in walk_nodes(translated.blocks) if node.kind == "evidence"] == [
        "evidence-A", "evidence-shared-A"
    ]
    assert "0.5 μM" in Renderer(document, "en").section(translated, "en")
    assert document.model_dump(mode="json") == before
    assert len(list((tmp_path / "authors").glob("*.json"))) == 1
    assert len(list((tmp_path / "parses").glob("*.json"))) == 1
