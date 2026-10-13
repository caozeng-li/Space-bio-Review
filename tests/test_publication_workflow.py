import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from markdown_it import MarkdownIt
from test_publication_delivery import Writer
from test_report_reference_relations import fixture

from spacebio_review.agents.context import build_messages
from spacebio_review.agents.types import (
    AgentTask,
    ContextView,
    RoleProfile,
    TaskResult,
    WorkingMemory,
)
from spacebio_review.context import product_material
from spacebio_review.v4.publication import build_publication
from spacebio_review.v4.publication_delivery import Delivery, text_batches, translation_fragment
from spacebio_review.v4.publication_document import (
    Node,
    Section,
    parse_text,
    summary_input,
    walk_nodes,
)
from spacebio_review.v4.publication_render import Renderer


def test_writer_messages_exclude_research_skills_audit_and_working_notes():
    task = AgentTask(
        task_id="INTERNAL",
        objective="Translate public units",
        phase="INTERNAL",
        expected_product="draft",
        kind="writer",
    )
    view = ContextView(
        agent_id="INTERNAL",
        phase="INTERNAL",
        materials=[product_material("public", {"units": {"U1": "Condition"}})],
    )
    messages = build_messages(
        task,
        RoleProfile(agent_id="id", responsibility="write"),
        view,
        SimpleNamespace(name="internal-evaluation", body="Do more research"),
        {},
        [{"internal": "tool"}],
        WorkingMemory(next_steps=[]),
    )
    assert "INTERNAL" not in str(messages)
    assert "internal-evaluation" not in str(messages)
    assert "working_notes" not in str(messages)
    assert "Condition" in str(messages)


def test_reference_brackets_are_visible_in_actual_markdown():
    _, records = fixture("unused")
    records.rows["assessment"]["blocks"][0]["text"] = "Fact[@exact] [@background]."
    records.rows["assessment"]["evidence"].update(records.rows["prior"]["evidence"])
    doc, _ = build_publication(records, "assessment")
    rendered = Renderer(doc).render("zh")
    assert 'href="#ref-1">[1]</a> <a href="#ref-2">[2]</a>' in MarkdownIt().render(rendered)


def test_summary_fixed_coverage_and_shared_scientific_limits():
    _, records = fixture("unused")
    doc, _ = build_publication(records, "assessment")
    for item in doc.coverage.values():
        item.status = "partially_assessed"
        item.analysis_limits = ["Only at 2 min."]
    view = summary_input(doc)
    assert len(view["analysis_limits"]) == 1
    assert len(view["analysis_limits"][0]["aspects"]) == 6
    for language in ("zh", "en"):
        rendered = Renderer(doc, language).render(language, summary="Scientific summary")
        assert ("科学价值" if language == "zh" else "Scientific value") in rendered
        assert ("项目管理" if language == "zh" else "Management") in rendered
        assert ("仅部分问题" if language == "zh" else "partially assessed") in rendered


def test_long_analysis_batches_at_paragraphs_not_references():
    section = Section(
        section_id="s",
        title="Scope",
        blocks=[Node(kind="block", children=parse_text("Alpha.\n\nBeta.\n\nGamma."))],
    )
    batches = text_batches(section, 8)
    assert len(batches) == 3
    assert all(batch[0].kind == "paragraph" for batch in batches)
    assert sum(len(batch) for batch in batches) == len(section.blocks[0].children)


@pytest.mark.parametrize(
    "text,body,prefix,suffix",
    [
        ("。条件仍不确定。", "条件仍不确定", ". ", "."),
        (".5 mM", ".5 mM", "", ""),
        ("-2 °C。", "-2 °C", "", "."),
        ("0万元 ", "0万元", "", " "),
    ],
)
def test_sentence_edges_preserve_decimal_sign_and_unit(text, body, prefix, suffix):
    assert translation_fragment(text) == (body, prefix, suffix)


@pytest.mark.asyncio
async def test_changed_public_paragraph_only_invalidates_affected_translation(tmp_path):
    _, records = fixture(tmp_path)
    ex = Writer(records)
    delivery = Delivery(ex, tmp_path)
    doc, _ = build_publication(records, "assessment")
    for section in doc.main:
        await delivery.translate(section)
    count = len(ex.calls)
    changed = doc.model_copy(deep=True)
    section = next(s for s in changed.main if s.section_id == "overall")
    node = next(n for n in walk_nodes(section.blocks) if n.kind == "text")
    node.text += " Changed scientific condition."
    for section in changed.main:
        await delivery.translate(section)
    assert len(ex.calls) == count + 1


@pytest.mark.asyncio
async def test_interruption_keeps_completed_sections_and_resume_reuses_calls(tmp_path):
    _, records = fixture(tmp_path)
    ex = Writer(records)
    delivery = Delivery(ex, tmp_path)
    manifest = delivery.assemble("assessment")
    original = delivery.translate
    calls = 0

    async def interrupted(section):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise asyncio.CancelledError()
        return await original(section)

    delivery.translate = interrupted
    with pytest.raises(asyncio.CancelledError):
        await delivery.run(manifest)
    checkpoint = json.loads((tmp_path / "delivery.json").read_text("utf-8"))
    assert set(checkpoint["translations"]) == {"summary", "scope"}
    assert checkpoint["status"] == "partial"
    completed = deepcopy(ex.calls)
    delivery.translate = original
    result = records.value(await delivery.run(manifest))
    assert result["status"] == "completed"
    assert ex.calls[: len(completed)] == completed
    assert len(ex.calls) == len(doc_sections(records, manifest)) + 2


def doc_sections(records, manifest):
    return records.value(manifest)["document"]["main"]


@pytest.mark.asyncio
async def test_summary_failure_stays_internal_and_receipt_remains_partial(tmp_path):
    _, records = fixture(tmp_path)
    ex = Writer(records)
    original = ex.call

    async def failed(key, *args, **kwargs):
        if "summary" in key:
            return TaskResult(
                task_id=key, agent_id="writer", status="failed", reason="RAW_TRACEBACK_SECRET_TASK"
            )
        return await original(key, *args, **kwargs)

    ex.call = failed
    delivery = Delivery(ex, tmp_path)
    result = records.value(await delivery.run(delivery.assemble("assessment")))
    assert result["status"] == "partial"
    assert result["en"]["status"] == "partial"
    english = next(d for d in result["publication"]["documents"] if d["role"] == "en")
    assert "summary" in english["missing_sections"]
    assert result["technical_limits"][0]["reason"] == "RAW_TRACEBACK_SECRET_TASK"
    assert "RAW_TRACEBACK" not in (tmp_path / "report.md").read_text("utf-8")
    assert "决策摘要尚未完成" in (tmp_path / "report.md").read_text("utf-8")
