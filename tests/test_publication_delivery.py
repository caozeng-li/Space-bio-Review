from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_report_reference_relations import fixture

from spacebio_review.agents.types import TaskResult
from spacebio_review.v4.publication import build_publication
from spacebio_review.v4.publication_delivery import Delivery, load_document
from spacebio_review.v4.publication_document import Section, parse_text, walk_nodes
from spacebio_review.v4.publication_render import Renderer


def test_renderer_numeric_citations_and_canonical_links(tmp_path):
    _, records = fixture(tmp_path)
    records.rows["assessment"]["blocks"][0]["text"] += "[@background]"
    records.rows["assessment"]["evidence"].update(records.rows["prior"]["evidence"])
    doc, _ = build_publication(records, "assessment")
    text = Renderer(doc).render("zh")
    assert "[[1]](#ref-1)" in text
    assert "[[2]](#ref-2)" in text
    assert "附录 A.1，第 1 段" in text
    assert "arguments.md" not in text
    assert "snapshot" not in text
    assert "v4/" not in text
    assert "相关论证" not in text
    assert "该论述所列" not in text
    appendix = Renderer(doc).render("appendix")
    assert appendix.count('id="analysis-1-1-p1"') == 1
    english = Renderer(doc, "en").render("en")
    assert "Appendix A.1, paragraph 1" in english
    assert "[申请" not in english


def test_markdown_preserves_table_list_emphasis_and_literal_code():
    doc, _ = build_publication(fixture_records(), "assessment")
    renderer = Renderer(doc)
    markdown = (
        "## Heading\n\n- **bold**\n- second\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n`[@literal]`"
    )
    rendered = renderer.blocks(parse_text(markdown), "zh")
    assert "- **bold**" in rendered
    assert "| 1 | 2 |" in rendered
    assert "[@literal]" in rendered


def fixture_records():
    _, records = fixture("unused")
    return records


class Writer:
    def __init__(self, records):
        self.records = records
        self.reporting = SimpleNamespace(material_batch_chars=18000)
        self.calls, self.cache = [], {}
        self.configuration = "test-writer"

    async def call(self, key, kind, objective, contract, **kwargs):
        data = kwargs["materials"][0].data
        cache_key = (key, str(data))
        if cache_key in self.cache:
            return self.cache[cache_key]
        self.calls.append((key, deepcopy(data)))
        if "units" in data:
            assert "[@" not in str(data)
            parsed = {
                "title": "Translated",
                "units": {k: "English " + v.strip() for k, v in data["units"].items()},
            }
        else:
            assert "reason" not in str(data)
            parsed = {"text": "Partial assessment; conditions remain."}
        product = contract.product(contract.parse(parsed)).model_dump()
        result = TaskResult(
            task_id=key, agent_id="writer", status="completed", reason="fixture", product=product
        )
        self.cache[cache_key] = result
        return result


@pytest.mark.asyncio
async def test_translation_keeps_targets_and_reuses_number_independent_text(tmp_path):
    _, records = fixture(tmp_path)
    ex = Writer(records)
    delivery = Delivery(ex, tmp_path)
    doc, _ = build_publication(records, "assessment")
    section = next(s for s in doc.main if s.section_id == "overall")
    before = [
        n.model_dump() for n in walk_nodes(section.blocks) if n.kind in {"evidence", "crossref"}
    ]
    translated = await delivery.translate(section)
    after = Section.model_validate(translated.product["section"])
    assert [
        n.model_dump() for n in walk_nodes(after.blocks) if n.kind in {"evidence", "crossref"}
    ] == before
    doc.numbering = {k: 99 - i for i, k in enumerate(doc.numbering)}
    await delivery.translate(section)
    assert len(ex.calls) == 1
    assert "English" not in str(section.model_dump())


@pytest.mark.asyncio
async def test_delivery_receipt_resume_and_selected_status(tmp_path):
    _, records = fixture(tmp_path)
    ex = Writer(records)
    delivery = Delivery(ex, tmp_path)
    manifest = delivery.assemble("assessment")
    first = records.value(await delivery.run(manifest, selected_sections=["overall"]))
    assert first["status"] == "partial"
    assert first["en"]["status"] == "selected_completed"
    assert "scope" in first["publication"]["missing_sections"]
    assert not (tmp_path / "arguments.md").exists()
    count = len(ex.calls)
    second = records.value(await delivery.run(manifest, selected_sections=["overall"]))
    assert len(ex.calls) == count
    assert first["publication"] == second["publication"]
    third = records.value(await delivery.run(manifest))
    assert third["status"] == "completed"
    assert third["publication"]["missing_sections"] == []
    assert third["paths"] == {d["role"]: d["path"] for d in third["publication"]["documents"]}
    count = len(ex.calls)
    (tmp_path / "report.md").write_text("Corrupted derived Markdown", "utf-8")
    delivery.export(third, load_document(records, records.value(manifest)))
    assert "Corrupted" not in (tmp_path / "report.md").read_text("utf-8")
    assert len(ex.calls) == count


@pytest.mark.asyncio
async def test_audit_only_changes_do_not_invalidate_summary(tmp_path):
    _, records = fixture(tmp_path)
    ex = Writer(records)
    delivery = Delivery(ex, tmp_path)
    manifest = delivery.assemble("assessment")
    await delivery.summarize(manifest)
    records.rows["assessment"]["task_id"] = "DIFFERENT"
    await delivery.summarize(delivery.assemble("assessment"))
    assert len(ex.calls) == 1


def test_legacy_manifest_adapter_reads_frozen_input(tmp_path):
    _, records = fixture(tmp_path)
    old_manifest = {"format_version": "v4-report-relations-2", "assessment": "assessment"}
    document = load_document(records, old_manifest)
    assert document.format_version == "publication-1"
