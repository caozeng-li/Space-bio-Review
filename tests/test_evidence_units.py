from spacebio_review.agents.reading import PassageSelection, ReadingWindow
from spacebio_review.evidence.documents import add_passage
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument


def test_complete_read_keeps_split_caption_and_legacy_read_unchanged():
    doc = SourceDocument(source_id="paper", candidate=SourceCandidate(title="Paper"))
    add_passage(doc, "Experimental conditions. " * 120 + "Modulus 11.8 kPa.", kind="caption")
    ids = [p.passage_id for p in doc.passages]
    selection = PassageSelection(source_id="paper", passage_ids=[ids[0]])
    old = ReadingWindow(budget=300).read(selection, passages=doc.passages)
    assert old["returned_ids"] == [ids[0]]
    window = ReadingWindow(budget=300, whole_units=True)
    page = window.read(selection, passages=doc.passages)
    assert page["returned_ids"] == ids
    assert page["complete"]
    assert "11.8" in page["passages"][-1]["text"]
    assert "unit_id" not in old["passages"][0]


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
