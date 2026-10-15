from spacebio_review.agents.reading import BrowseSelection, PassageSelection, ReadingWindow
from spacebio_review.evidence.documents import add_passage
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument


def test_complete_read_keeps_split_caption_and_legacy_read_unchanged():
    doc = SourceDocument(source_id="paper", candidate=SourceCandidate(title="Paper"))
    add_passage(doc, "Experimental conditions. " * 120 + "Modulus 11.8 kPa.", kind="caption")
    ids = [p.passage_id for p in doc.passages]
    selection = PassageSelection(source_id="paper", passage_ids=[ids[0]])
    old = ReadingWindow(budget=300, whole_units=False).read(selection, passages=doc.passages)
    assert old["returned_ids"] == [ids[0]]
    window = ReadingWindow()
    page = window.read(selection, passages=doc.passages)
    assert page["returned_ids"] == ids
    assert page["complete"]
    assert "11.8" in page["passages"][-1]["text"]
    assert "unit_id" not in old["passages"][0]


def test_oversized_unit_continues_without_repeating_or_losing_passages():
    doc = SourceDocument(source_id="paper", candidate=SourceCandidate(title="Paper"))
    add_passage(doc, "Experimental conditions. " * 400 + "Final condition: 11.8 kPa.")
    ids = [p.passage_id for p in doc.passages]
    window = ReadingWindow(budget=1200)
    selection = PassageSelection(source_id="paper", passage_ids=[ids[1]])
    pages = []
    for _ in range(len(ids) + 1):
        page = window.read(selection, passages=doc.passages)
        pages.append(page)
        assert page["returned_ids"]
        assert page["input_tokens_estimate"] <= window.budget
        if page["complete"]:
            break
        assert page["unit_continues"]
        selection = BrowseSelection(source_id="paper", continuation=page["continuation"])
    assert len(pages) > 1
    assert pages[-1]["complete"]
    assert not pages[-1]["unit_continues"]
    assert [key for page in pages for key in page["returned_ids"]] == ids
    assert "Final condition: 11.8 kPa." in pages[-1]["passages"][-1]["text"]


def test_complete_unit_waits_for_next_page_when_only_remaining_space_is_small():
    rows = [
        {"passage_id": "intro", "text": "Introduction. " * 80},
        {"passage_id": "method1", "unit_id": "method", "text": "A" * 1000},
        {"passage_id": "method2", "unit_id": "method", "text": "B" * 1000},
    ]
    window = ReadingWindow(budget=1100)
    first = window.read(BrowseSelection(source_id="paper"), passages=rows)
    assert first["returned_ids"] == ["intro"]
    assert not first["unit_continues"]
    second = window.read(
        BrowseSelection(source_id="paper", continuation=first["continuation"]), passages=rows
    )
    assert second["returned_ids"] == ["method1", "method2"]
    assert second["complete"]
    assert second["input_tokens_estimate"] <= window.budget


def test_browse_from_inside_unit_does_not_add_passages_before_requested_position():
    rows = [
        {"passage_id": "p1", "unit_id": "method", "text": "First part"},
        {"passage_id": "p2", "unit_id": "method", "text": "Second part"},
        {"passage_id": "p3", "text": "Results"},
    ]
    page = ReadingWindow().read(
        BrowseSelection(source_id="paper", start_passage_id="p2"), passages=rows
    )
    assert page["returned_ids"] == ["p2", "p3"]
    assert page["complete"]


def test_reduced_remaining_budget_defers_a_unit_that_fits_a_fresh_page():
    rows = [
        {"passage_id": "p1", "unit_id": "method", "text": "A" * 1000},
        {"passage_id": "p2", "unit_id": "method", "text": "B" * 1000},
    ]
    window = ReadingWindow(budget=1100)
    first = window.read(
        PassageSelection(source_id="paper", passage_ids=["p1"]), passages=rows, budget=600
    )
    assert first["returned_ids"] == []
    assert first["pending_count"] == 2
    assert not first["unit_continues"]
    second = window.read(
        BrowseSelection(source_id="paper", continuation=first["continuation"]), passages=rows
    )
    assert second["returned_ids"] == ["p1", "p2"]
    assert second["complete"]


def test_oversized_single_passage_remains_unread_until_a_larger_page_is_requested():
    rows = [{"passage_id": "p1", "unit_id": "method", "text": "Method. " * 400}]
    window = ReadingWindow(budget=300)
    first = window.read(PassageSelection(source_id="paper", passage_ids=["p1"]), passages=rows)
    assert first["returned_ids"] == []
    assert first["unread_reason"] == "passage_exceeds_read_budget"
    assert first["pending_count"] == 1
    assert not first["unit_continues"]
    second = window.read(
        BrowseSelection(source_id="paper", continuation=first["continuation"]),
        passages=rows,
        budget=8000,
    )
    assert second["returned_ids"] == ["p1"]
    assert second["complete"]


def test_old_passage_serialization_does_not_grow_optional_metadata():
    p = Passage(passage_id="p1", locator="p1", text="Original")
    assert "unit_id" not in p.model_dump()


def test_retained_caption_exactly_recovers_original_unit():
    import json
    from pathlib import Path

    import pytest

    from spacebio_review.evidence.documents import reading_passages

    path = Path("runs/mytest18/sources/src-9d693ae3217b/source.json")
    if not path.exists():
        pytest.skip("Local retained scientific fixture")
    doc = SourceDocument.model_validate(json.loads(path.read_text("utf-8")))
    rows = reading_passages(doc, True)
    page = ReadingWindow(whole_units=True).read(
        PassageSelection(source_id=doc.source_id, passage_ids=["p20"]), passages=rows
    )
    assert page["returned_ids"] == ["p20", "p21"]
    assert "11.8" not in page["passages"][0]["text"]
    assert "11.8" in page["passages"][1]["text"]
    assert all(p.unit_id is None for p in doc.passages)
