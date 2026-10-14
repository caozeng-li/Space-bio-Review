import importlib.util
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from spacebio_review.agents.contracts import example_for
from spacebio_review.models.provider import ModelError
from spacebio_review.v4.manuscript_call import ManuscriptConfig

SPEC = importlib.util.spec_from_file_location(
    "probe_review_quality", Path(__file__).parents[1] / "scripts/probe_review_quality.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def model(**changes):
    return ManuscriptConfig.load().model.model_copy(update={"max_tokens": 100, **changes})


def settle(budget, ticket, prompt=20, completion=10):
    budget.settle(ticket, SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion))


def test_group_and_revision_requests_use_one_ledger(tmp_path):
    first = probe.ProbeBudget(tmp_path, "A", "A24-1", "first")
    settle(first, first.reserve(model(), [{"role": "user", "content": "sample"}]))
    second = probe.ProbeBudget(tmp_path, "B", "B-method-1", "second")
    settle(second, second.reserve(model(), [{"role": "user", "content": "sample"}]))
    report = second.report()
    assert report["models"]["requests"] == 2
    assert report["models"]["input"] == 40
    assert report["groups"]["A"]["requests"] == report["groups"]["B"]["requests"] == 1
    assert [row["probe"]["revision"] for row in report["requests"]] == ["first", "second"]


def test_rejected_dispatch_does_not_consume_a_request(tmp_path):
    budget = probe.ProbeBudget(tmp_path, "A", "A24-2")
    settle(budget, budget.reserve(model(), [{"role": "user", "content": "sample"}]))
    with pytest.raises(ModelError, match="A_case_requests") as error:
        budget.reserve(model(), [{"role": "user", "content": "sample"}])
    assert error.value.request_sent is False
    assert len(budget.data["requests"]) == 1


def test_prior_revision_failure_still_consumes_group_budget(tmp_path, monkeypatch):
    monkeypatch.setitem(probe.LIMITS, "A", {"requests": 1, "input": 350000, "output": 160000})
    first = probe.ProbeBudget(tmp_path, "A", "A24-1", "first")
    first.reserve(model(), [{"role": "user", "content": "sample"}])
    second = probe.ProbeBudget(tmp_path, "A", "A24-1", "corrected")
    with pytest.raises(ModelError, match="probe_A_requests_limit"):
        second.reserve(model(), [{"role": "user", "content": "sample"}])
    assert second.report()["models"]["requests"] == 1
    assert second.report()["models"]["input"] is None


def test_partial_and_missing_usage_is_not_reported_as_zero(tmp_path):
    budget = probe.ProbeBudget(tmp_path, "A", "A24-1")
    ticket = budget.reserve(model(), [{"role": "user", "content": "sample"}])
    report = budget.report()["models"]
    assert report["input"] is None and report["output"] is None
    assert report["accounted"]["output"] == 100
    settle(budget, ticket, completion=None)
    report = budget.report()["models"]
    assert report["input"] == 20 and report["output"] is None
    assert report["unknown_usage_requests"] == 1


def test_probe_disallows_provider_retry_and_unrelated_embedding(tmp_path):
    budget = probe.ProbeBudget(tmp_path, "A", "A24-1")
    with pytest.raises(ModelError, match="retries_must_be_zero"):
        budget.reserve(model(retries=1), [{"role": "user", "content": "sample"}])
    with pytest.raises(ModelError, match="embedding_only_in_B"):
        budget.reserve(model(), texts=["sample"])
    assert not budget.data["requests"]


def test_embedding_does_not_consume_model_call_allowance(tmp_path):
    budget = probe.ProbeBudget(tmp_path, "B", "B-method-1")
    config = SimpleNamespace(model="embedding", retries=0)
    ticket = budget.reserve(config, texts=["sample"])
    settle(budget, ticket, prompt=2, completion=0)
    assert budget.report()["models"]["requests"] == 0
    assert budget.report()["embedding"]["requests"] == 1
    assert budget.remaining_calls == probe.TOTAL_LIMITS["requests"]


def small_fixture(_case):
    return {
        "evaluation": {"prompt": "Common scientific assessment criteria."},
        "materials": {
            "application": ["E1"],
            "evidence": {"E1": {"source": "S1", "quote": "Original experiment description"}},
            "sources": {"S1": {"kind": "application", "title": "Proposal"}},
            "opinions": {
                "O1": {"context": "G1", "text": {"parts": [{"text": "Opinion", "cites": ["E1"]}]}}
            },
            "opinion_contexts": {
                "G1": {"title": "Scope", "scope": {"parts": [{"text": "Scope", "cites": []}]}}
            },
        },
    }, {}


def test_preview_serializes_fixed_matrix_without_model_access(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "fixture", small_fixture)
    output = tmp_path / "revisions/initial"
    matrix = probe.preview(output, tmp_path)
    assert set(matrix["cases"]) == set(probe.A_CASES)
    assert matrix["requests_forecast"] == 10
    assert matrix["fits"]
    assert not (tmp_path / "v4/budget.json").exists()
    item = probe.read(output / "preview/A24-1/fixture.json")
    assert (
        "Common scientific assessment criteria."
        in item["assessment_request"]["messages"][0]["content"]
    )
    assert item["assessment_request"]["model"]["retries"] == 0
    assert item["author_forecast"][0]["keys"] == ["summary"]
    assert all(
        key == "summary" or key.startswith("topic_")
        for row in item["author_forecast"]
        for key in row["keys"]
    )
    with pytest.raises(ValueError, match="preserve_previous_preview"):
        probe.preview(output, tmp_path)


@pytest.mark.asyncio
async def test_delivery_cannot_select_a_better_repeat_or_skip_manual_review(tmp_path):
    for case, review in [("A24-2", "accepted"), ("A24-1", None)]:
        with pytest.raises(ValueError, match="only_fixed_delivery_cases"):
            await probe.live(tmp_path, tmp_path, case, "author", assessment_review=review)
    assert not (tmp_path / "v4/budget.json").exists()


def main_fixture():
    frozen, _ = small_fixture("A24-1")
    frozen["materials"]["evidence"]["E1"].update(locator="Method", reading_scope="text")
    frozen["provenance"] = {"opinions": {}, "evidence": {"E1": []}}
    assessment = example_for(probe.assessment_contract(frozen).schema())
    assessment["project"] = {
        "paragraphs": [
            {"parts": [{"text": "First complete paragraph retains E99 and O7.", "cites": ["E1"]}]},
            {"parts": [{"text": "A distinct second paragraph.", "cites": []}]},
        ]
    }
    assessment["conditions"] = [
        {"timing": "before_support", "text": probe.paragraph("Clarify the comparison.", ["E1"])},
        {"timing": "implementation_milestone", "text": probe.paragraph("Deliver the protocol.")},
    ]
    return assessment, frozen


def test_main_preview_preserves_received_prose_and_has_real_rendered_citations():
    assessment, frozen = main_fixture()
    before = deepcopy(assessment)
    markdown, html, observations = probe.main_preview(assessment, frozen)
    assert assessment == before
    assert "E99 and O7" in markdown and "E99 and O7" in html
    assert "A distinct second paragraph." in html
    assert observations["prose_paragraphs_by_section"]["project"] == 2
    assert observations["raw_alias_tokens_observed"] == ["E99", "O7"]
    assert observations["citation_labels_match_targets"]
    assert observations["visible_numeric_citations"] > 0
    assert not observations["missing_anchor_targets"]
    assert not observations["duplicate_anchor_ids"]
    assert observations["tables"] == 1 and observations["table_rows"] == 7
    assert observations["condition_groups"] == ["before_support", "implementation_milestone"]
    assert observations["ordered_lists"] == 2 and observations["list_items"] == 2
    assert "summary" not in observations["rendered_sections"]
    assert "appendix.md" not in markdown
    assert not any("摘要" in row["text"] for row in observations["headings"])


def test_render_main_is_zero_call_read_only_preview_not_official_delivery(tmp_path, monkeypatch):
    assessment, frozen = main_fixture()
    folder = tmp_path / "cases/A24-1"
    records = probe.Records(folder)
    result = {
        "case": "A24-1",
        "frozen_ref": records.put("frozen-review", frozen),
        "assessment": records.put("global-assessment", assessment),
    }
    probe.write_json(folder / "result.json", result)

    def forbidden(*args, **kwargs):
        raise AssertionError("Rendering must not create a model caller")

    monkeypatch.setattr(probe, "ManuscriptCall", forbidden)
    value = probe.render_main(tmp_path, "A24-1")
    assert value["preview_only"] and not value["official_delivery_completed"]
    assert value["model_requests"] == 0 and value["inputs_unchanged"]
    assert probe.read(folder / "result.json") == result
    assert (folder / "assessment-main-preview.md").is_file()
    assert (folder / "assessment-main-preview.html").is_file()
    assert not (folder / "report.md").exists()
    assert not (folder / "v4/budget.json").exists()
