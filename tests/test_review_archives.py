import json

import pytest

from spacebio_review.v4.assessment_input import ReviewArchive
from spacebio_review.v4.records import VERSION, Records


def write_archive(root, version):
    records = Records(root)
    value = {
        "dimensions": {"scientific_value": {"grade": "B", "confidence": "medium"}},
        "recommendation": "revise",
    }
    reference = records.put("assessment", value)
    state = records.state
    state["format_version"] = version
    state["heads"] = {"assessment": [reference]}
    state["metadata"] = {"evaluation": {"skill": "exploration-review"}}
    records.save_state(state)
    return reference, value


def file_contents(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize(
    "version",
    ["v4.1-3", "v4.2-questions-1", "v4.3-publication-1", "v4.4-direct-publication-1"],
)
def test_historical_archive_remains_readable_without_migration_or_resume(tmp_path, version):
    reference, value = write_archive(tmp_path, version)
    before = file_contents(tmp_path)
    archive = ReviewArchive(tmp_path)
    assert archive.state["format_version"] == version
    assert archive.selected() == [reference]
    assert archive.value(reference) == value
    assert archive.state["metadata"] == {"evaluation": {"skill": "exploration-review"}}
    with pytest.raises(ValueError, match="legacy_checkpoint_read_only_use_new_output"):
        Records(tmp_path)
    assert file_contents(tmp_path) == before


@pytest.mark.parametrize("version", ["v4.4-direct-publication-1", VERSION])
def test_review_archive_rejects_all_write_entry_points(tmp_path, version):
    write_archive(tmp_path, version)
    archive = ReviewArchive(tmp_path)
    before = file_contents(tmp_path)
    with pytest.raises(TypeError, match="read_only_review_archive"):
        archive.put("assessment", {"replacement": True})
    with pytest.raises(TypeError, match="read_only_review_archive"):
        archive.save_state({"format_version": VERSION})
    with pytest.raises(TypeError, match="read_only_review_archive"):
        archive.receipt({"task": "assessment"}, {"result": "replacement"})
    assert file_contents(tmp_path) == before


def test_current_version_can_reopen_its_checkpoint_and_read_its_archives(tmp_path):
    assert VERSION == "v4.5-evaluation-systems-1"
    reference, value = write_archive(tmp_path, VERSION)
    reopened = Records(tmp_path)
    assert reopened.state["format_version"] == VERSION
    assert reopened.value(reference) == value
    new_reference = reopened.put("assessment", {"recommendation": "support"}, [reference])
    assert ReviewArchive(tmp_path).value(new_reference) == {"recommendation": "support"}


def test_unknown_archive_version_is_rejected_without_changing_files(tmp_path):
    path = tmp_path / "v4" / "state.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"format_version": "v9-unknown"}), "utf-8")
    before = file_contents(tmp_path)
    with pytest.raises(ValueError, match="unsupported_review_archive"):
        ReviewArchive(tmp_path)
    with pytest.raises(ValueError, match="legacy_checkpoint_read_only_use_new_output"):
        Records(tmp_path)
    assert file_contents(tmp_path) == before
