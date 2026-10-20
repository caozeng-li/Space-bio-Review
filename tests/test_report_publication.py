"""The report is one chief-authored document, not a selection of manuscripts."""

from spacebio_review.models.config import ModelConfig
from spacebio_review.models.provider import ChatReply
from spacebio_review.prose import compile_authored_body
from spacebio_review.publication import Renderer, compile_publication
from spacebio_review.publication.document import walk_nodes
from spacebio_review.publication.translation import markdown_batches, translate_section
from spacebio_review.review.evaluation import GENERAL


def test_report_prose_is_published_once_without_control_fields():
    assessment = {
        "evaluation_system": GENERAL.snapshot(),
        "recommendation": "support",
        "dimensions": {
            item.id: {"grade": "B", "confidence": "B", "reason": "INTERNAL_REASON"}
            for item in GENERAL.criteria
        },
        "support_scope": "INTERNAL_SCOPE",
        "body": compile_authored_body(
            "## 综合评价\n\n申请拟比较空间与地面培养结果。\n\n"
            "## 科学目标\n\n形态观察可以回答申请提出的描述性问题。"
        ),
    }
    document, _ = compile_publication(assessment, evidence={})
    report = Renderer(document).render("zh")
    assert report.count("申请拟比较空间与地面培养结果。") == 1
    assert report.count("## 综合评价") == 1
    assert "INTERNAL_" not in report
    assert "实际评审范围" not in report
    assert "专题论证" not in report
    assert [s.section_id for s in document.main] == ["body", "grades"]


async def test_whole_paragraph_translation_preserves_references_and_code_owned_scores():
    handle = "evidence-1234567890abcdef"
    assessment = {
        "evaluation_system": GENERAL.snapshot(),
        "recommendation": "support",
        "dimensions": {item.id: {"grade": "B", "confidence": "C"} for item in GENERAL.criteria},
        "body": compile_authored_body(
            f"## 综合评价\n\n在轨只观察**形态**，返回后测序。[来源](evidence:{handle})"
        ),
    }
    evidence = {
        handle: {
            "reference": {"source_id": "application", "passage_id": "p1"},
            "title": "计划书",
            "kind": "application",
            "locator": "实验方案",
        }
    }
    document, _ = compile_publication(assessment, evidence=evidence)

    class Translator:
        config = ModelConfig(model="local", base_url="https://local.invalid/v1")
        calls = 0

        async def complete(self, messages):
            self.calls += 1
            assert "只观察**形态**，返回后测序" in messages[-1]["content"]
            assert f"(evidence:{handle})" in messages[-1]["content"]
            return ChatReply(
                {
                    "role": "assistant",
                    "content": "## Overall assessment\n\nOnly **morphology** is observed in orbit; "
                    f"sequencing follows sample return.[](evidence:{handle})",
                },
                "stop",
            )

    model = Translator()
    sections = [await translate_section(model, section) for section in document.main]
    assert model.calls == 1
    assert {node.target for node in walk_nodes(sections[0].blocks) if node.kind == "evidence"} == {
        handle
    }
    result = Renderer(document, "en").render("en", sections)
    assert (
        "Only **morphology** is observed in orbit; sequencing follows sample return. [1]" in result
    )
    assert "evidence-" not in result and "[1]" in result
    assert "| B | C |" in result
    assert "Read locations" not in result
    assert document.locations[handle].locator == "实验方案"


def test_translation_batches_keep_chapters_and_tables_intact():
    from spacebio_review.authored_markdown import compile_blocks
    from spacebio_review.publication.document import Section

    section = Section(
        section_id="body",
        title="",
        blocks=compile_blocks(
            "## 综合评价\n\n一段完整文字。\n\n## 研究基础\n\n"
            "| 对象 | 依据 |\n| --- | --- |\n| 培养 | 前期实验 |"
        ),
    )
    batches = markdown_batches(section, 20)
    assert len(batches) == 2
    assert batches[1].startswith("## 研究基础")
    assert "| 培养 | 前期实验 |" in batches[1]
