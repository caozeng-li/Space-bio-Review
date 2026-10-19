"""RAG contracts: relevant originals survive layout, windowing and handoff."""

from pathlib import Path

import httpx
import pytest
from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    GroupLabel,
    ProvenanceItem,
    Size,
)

from spacebio_review.agents.reading import BrowseMaterials, PassageSelection, ReadingWindow
from spacebio_review.evidence.access import SourceAccess
from spacebio_review.evidence.documents import append_docling_document
from spacebio_review.evidence.knowledge import AcquireSource
from spacebio_review.evidence.navigation import source_navigation
from spacebio_review.evidence.retrieval import hybrid_rank, retrieval_chunks
from spacebio_review.evidence.store import SourceStore
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument, SourceMaterial
from spacebio_review.models.provider import ChatReply
from spacebio_review.v5.conversation import ActiveConversation


def test_retrieval_priority_survives_reading_window():
    rows = [
        Passage(passage_id="early", locator="earlier chapter", text="unrelated material " * 400),
        Passage(passage_id="target", locator="requested experiment", text="培养温度为37℃。"),
    ]
    window = ReadingWindow(budget=500)
    result = window.read(
        PassageSelection(source_id="s", passage_ids=["target", "early"]), passages=rows
    )
    assert "target" in result["returned_ids"], result
    assert "early" not in result["returned_ids"]
    assert result["continuation"]


def layout_fixture():
    doc = DoclingDocument(name="native list and parameter areas")
    doc.add_page(page_no=1, size=Size(width=600, height=800))

    def prov(y, x=100, width=200):
        return ProvenanceItem(
            page_no=1,
            charspan=(0, 0),
            bbox=BoundingBox(l=x, r=x + width, t=y, b=y + 12, coord_origin=CoordOrigin.TOPLEFT),
        )

    listing = doc.add_group(label=GroupLabel.LIST)
    doc.add_list_item(text="样本冷藏", parent=listing, prov=prov(100))
    cold = doc.add_group(label=GroupLabel.KEY_VALUE_AREA)
    doc.add_text(label=DocItemLabel.TEXT, text="温度范围：", parent=cold, prov=prov(120, 110, 80))
    doc.add_text(label=DocItemLabel.TEXT, text="4℃±2℃", parent=cold, prov=prov(120, 210, 80))
    doc.add_list_item(text="样本离心处理", parent=listing, prov=prov(160))
    spin = doc.add_group(label=GroupLabel.KEY_VALUE_AREA)
    # The detector grouped labels before their values, as in the actual manual.
    doc.add_text(label=DocItemLabel.TEXT, text="离心力：", parent=spin, prov=prov(180, 110, 80))
    doc.add_text(label=DocItemLabel.TEXT, text="样品数量：", parent=spin, prov=prov(200, 110, 80))
    doc.add_text(label=DocItemLabel.TEXT, text="0～2000g", parent=spin, prov=prov(180, 210, 80))
    doc.add_text(label=DocItemLabel.TEXT, text="不少于6个", parent=spin, prov=prov(200, 210, 80))
    return doc


def test_pdf_native_lists_do_not_pull_later_label_before_parameters(tmp_path: Path):
    output = SourceDocument(source_id="s", candidate=SourceCandidate(title="manual"))
    append_docling_document(output, layout_fixture(), tmp_path)
    text = "\n".join(p.text for p in output.passages)
    assert text.index("样本冷藏") < text.index("4℃±2℃") < text.index("样本离心处理")
    assert text.index("离心力") < text.index("0～2000g") < text.index("样品数量")


def test_page_selection_keeps_file_boundary_and_page_context():
    rows = [
        Passage(passage_id="heading", locator="page26", page=26, text="生物技术实验柜"),
        Passage(passage_id="cell", locator="page27", page=27, text="细胞培养37℃"),
        Passage(passage_id="other", locator="attachment page27", page=27, text="附件植物17～28℃"),
    ]
    materials = [
        SourceMaterial(material_id="main", passage_ids=["heading", "cell"]),
        SourceMaterial(material_id="supplement", passage_ids=["other"]),
    ]
    result = ReadingWindow().read(
        BrowseMaterials(source_id="s", pages=[27], material_id="main"),
        passages=rows,
        materials=materials,
    )
    assert result["returned_ids"] == ["heading", "cell"]
    assert result["complete"]


def test_navigation_exposes_late_table_and_index_preserves_its_context():
    doc = SourceDocument(
        source_id="s",
        candidate=SourceCandidate(title="实验手册"),
        passages=[
            *[
                Passage(passage_id=f"p{i}", locator=f"page{i}", page=i, text=f"其他章节{i}")
                for i in range(1, 45)
            ],
            Passage(
                passage_id="table",
                locator="page45",
                page=45,
                kind="table",
                text="表5.4 细胞组织实验\n\n| 培养温度 | 37℃ |",
            ),
            Passage(passage_id="note", locator="page45", page=45, text="以上条件依具体配置选配。"),
        ],
    )
    entries = source_navigation(doc)
    assert entries[-1]["tables"] == ["表5.4 细胞组织实验"]
    assert entries[-1]["start_passage_id"] == "table"
    chunks = retrieval_chunks(doc)
    note = next(text for text, positions in chunks if positions == [45])
    assert "细胞组织实验" in note and "依具体配置选配" in note


def test_unpaged_source_with_one_heading_exposes_later_native_blocks():
    doc = SourceDocument(
        source_id="s",
        candidate=SourceCandidate(title="HTML article"),
        passages=[
            Passage(passage_id="intro", locator="body", heading_path=["Study"], text="Background"),
            Passage(
                passage_id="method", locator="body", heading_path=["Study"], text="Exposure at 37℃"
            ),
            Passage(
                passage_id="result",
                locator="body",
                heading_path=["Study"],
                text="Measured response",
            ),
        ],
    )
    entries = source_navigation(doc)
    assert [entry["start_passage_id"] for entry in entries] == ["intro", "method", "result"]
    assert entries[1]["opening"] == "Exposure at 37℃"


def test_rebase_keeps_manuscript_basis_before_recent_background():
    def snapshot(handle, text):
        return {
            "reference": {"source_id": "s", "passage_id": handle},
            "quote": text,
            "locator": handle,
            "material_version": handle,
            "reading_structure": {"unit_id": handle},
        }

    conversation = ActiveConversation()
    conversation.remember({"basis": snapshot("basis", "细胞组织培养37℃，依具体配置。")})
    conversation.remember({"noise": snapshot("noise", "后来读到的背景研究。")})
    selected = conversation.select_readings(
        1,
        preferred=["basis"],
        measure=lambda originals: sum(len(m["units"]) for s in originals for m in s["materials"]),
    )
    assert list(selected) == ["basis"]
    assert selected["basis"]["quote"] == "细胞组织培养37℃，依具体配置。"
    # Preferred is a priority, not a citations-only filter.
    assert len(conversation.select_readings(10000, preferred=["basis"])) == 2


@pytest.mark.parametrize("entry", ["question", "acquire"])
async def test_question_opens_selected_body_before_scientific_interpretation(tmp_path, entry):
    class Navigator:
        async def complete(self, messages):
            assert "细胞组织实验" in messages[1]["content"]
            return ChatReply({"role": "assistant", "content": '{"ids":[2]}'}, "stop")

    async with httpx.AsyncClient() as client:
        store = SourceStore(tmp_path / "sources", client)
        sid = store.register(SourceCandidate(title="手册"))
        doc = SourceDocument(
            source_id=sid,
            candidate=store.candidate(sid),
            availability="full_text",
            passages=[
                Passage(passage_id="intro", locator="page1", page=1, text="封面"),
                Passage(passage_id="plant", locator="page2", page=2, text="植物培养17～28℃"),
                Passage(
                    passage_id="cell",
                    locator="page3",
                    page=3,
                    kind="table",
                    text="表5.4 细胞组织实验\n\n| 培养温度 | 37℃ |",
                ),
            ],
            materials=[SourceMaterial(material_id="main", passage_ids=["intro", "plant", "cell"])],
        )
        store._save(doc)
        access = SourceAccess(
            store,
            None,
            visible=[sid],
            rerank_model=Navigator(),
            external_research_enabled=False,
            local_source_ids=[sid],
        )
        result = (
            await access.open_question(sid, "类器官的培养条件？")
            if entry == "question"
            else await access.acquire_source(
                AcquireSource(
                    source_id=sid,
                    reading_purpose="类器官的培养条件？",
                )
            )
        )
        assert result.ok
        assert "cell" in result.content["returned_ids"]
        captured = access.references.snapshot_choices(selected=False)
        assert any("37℃" in item["quote"] for item in captured.values())


def test_pdf_reading_order_keeps_columns(tmp_path):
    doc = DoclingDocument(name="two-column paper")
    doc.add_page(page_no=1, size=Size(width=600, height=800))
    for text, x, y in [
        ("Left start", 40, 100),
        ("Left end", 40, 140),
        ("Right start", 340, 100),
        ("Right end", 340, 140),
    ]:
        doc.add_text(
            label=DocItemLabel.TEXT,
            text=text,
            prov=ProvenanceItem(
                page_no=1,
                charspan=(0, len(text)),
                bbox=BoundingBox(l=x, r=x + 200, t=y, b=y + 20, coord_origin=CoordOrigin.TOPLEFT),
            ),
        )
    output = SourceDocument(source_id="s", candidate=SourceCandidate(title="paper"))
    append_docling_document(output, doc, tmp_path)
    assert [p.text for p in output.passages] == [
        "Left start",
        "Left end",
        "Right start",
        "Right end",
    ]


async def test_distinct_question_reaches_reranker_even_when_common_topic_dominates():
    class DenseIndex:
        async def rank_groups(self, texts, groups):
            common = list(range(len(texts)))
            return [
                [19, *common[:-1]] if group == ["rare question"] else common for group in groups
            ]

    class Reranker:
        async def complete(self, messages):
            import json

            candidates = json.loads(messages[1]["content"])["candidates"]
            assert any(item["id"] == 19 for item in candidates)
            return ChatReply({"role": "assistant", "content": '{"ids":[19,0]}'}, "stop")

    order, channels, _, _ = await hybrid_rank(
        [f"source-{i}" for i in range(20)],
        ["common question", "rare question"],
        Reranker(),
        index=DenseIndex(),
        limit=2,
    )
    assert order == [19, 0] and channels["dense"] == "used"
