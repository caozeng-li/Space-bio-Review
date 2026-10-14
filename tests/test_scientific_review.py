import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from spacebio_review.agents.contracts import example_for
from spacebio_review.models.provider import ChatReply, ModelError
from spacebio_review.review.project import REVIEW_ASPECTS
from spacebio_review.v4.assessed_delivery import deliver_review_result
from spacebio_review.v4.manuscript_call import ManuscriptCall, ManuscriptConfig
from spacebio_review.v4.publication_compile import (
    compile_review_result,
    compile_scientific_publication,
)
from spacebio_review.v4.publication_document import PublicationDocument, walk_nodes
from spacebio_review.v4.publication_render import Renderer
from spacebio_review.v4.records import Records
from spacebio_review.v4.scientific_review import (
    RESULT_VERSION,
    decision_contract,
    decision_request,
    paragraph_ids,
    scientific_contract,
    scientific_paragraphs,
    scientific_request,
    synthesize_scientific,
)


def prose(text, cites=()):
    return {"paragraphs": [{"parts": [{"text": text, "cites": list(cites)}]}]}


def fixture():
    return {
        "evaluation": {"prompt": "Common evaluation standard"},
        "materials": {
            "application": ["E1"],
            "evidence": {
                "E1": {"source": "s1", "quote": "Application", "locator": "p1"},
                "E2": {"source": "s2", "quote": "Actual cited study", "locator": "p2"},
                "E3": {"source": "s3", "quote": "Unused original", "locator": "p3"},
            },
            "sources": {
                "s1": {"title": "Application", "kind": "application"},
                "s2": {"title": "Study", "kind": "paper"},
                "s3": {"title": "Unused", "kind": "paper"},
            },
            "opinions": {
                "O1": {
                    "context": "G1",
                    "text": {"parts": [{"text": "Unresolved expert draft", "cites": ["E3"]}]},
                },
            },
            "opinion_contexts": {"G1": {"scope": {"parts": []}}},
            "material_limits": ["Available texts only"],
        },
        "provenance": {"opinions": {}, "evidence": {"E1": [], "E2": [], "E3": []}},
    }


def science_fixture(frozen):
    value = example_for(scientific_contract(frozen).schema())
    value["project"] = prose("Application role.", ["E1"])
    value["scope"] = prose("Actual available evidence.")
    value["dimensions"] = {
        key: prose(f"Final {key} scientific reasoning.", ["E1"])
        for key in REVIEW_ASPECTS
    }
    value["topics"] = [{"title": "Specific comparison", "body": prose("Study conditions.", ["E2"])}]
    return value


def decision_fixture(frozen, scientific):
    value = example_for(decision_contract(frozen, scientific).schema())
    for key in ("summary", "overall", "support_scope", "alternatives"):
        value[key] = prose(f"Final {key} decision.", ["E1"])
    value["conditions"] = [
        {
            "timing": "implementation_milestone",
            "text": prose("Scoped action based on the discussed comparison."),
            "based_on": [paragraph_ids(scientific)[-1]],
        }
    ]
    value["unknowns"] = []
    return value


def test_scientific_contract_has_final_prose_but_no_decision_or_writer_plan():
    frozen = fixture()
    contract = scientific_contract(frozen)
    value = science_fixture(frozen)
    contract.parse(value)
    assert set(value) == {"project", "scope", "dimensions", "topics"}
    assert set(value["dimensions"]) == set(REVIEW_ASPECTS)
    assert set(value["topics"][0]) == {"title", "body"}
    prompt, materials = scientific_request(frozen)
    assert prompt.count("Common evaluation standard") == 1
    assert "Unresolved expert draft" in json.dumps(materials)
    assert "provenance" not in materials
    value["project"]["paragraphs"][0]["parts"][0]["cites"] = ["O1"]
    with pytest.raises(ValidationError):
        contract.parse(value)


def test_decision_receives_full_manuscript_and_only_application_plus_used_originals():
    frozen = fixture()
    scientific = science_fixture(frozen)
    original = deepcopy(scientific)
    prompt, materials = decision_request(frozen, scientific)
    assert scientific == original
    assert materials["scientific"] == scientific_paragraphs(scientific)
    assert len(paragraph_ids(scientific)) == 9
    assert set(materials["application"]) == {"E1"}
    assert set(materials["available_originals"]) == {"E2"}
    assert set(materials["sources"]) == {"s1", "s2"}
    assert "Unresolved expert draft" not in json.dumps(materials)
    assert "Unused original" not in json.dumps(materials)
    assert "research" not in materials
    assert prompt.count("Common evaluation standard") == 1
    assert materials["material_limits"] == frozen["materials"]["material_limits"]

    contract = decision_contract(frozen, scientific)
    decision = decision_fixture(frozen, scientific)
    contract.parse(decision)
    assert not {"dimensions", "project", "scope", "topics"} & set(decision)
    decision["conditions"][0]["based_on"] = ["E2"]
    with pytest.raises(ValidationError):
        contract.parse(decision)
    decision = decision_fixture(frozen, scientific)
    decision["overall"]["paragraphs"][0]["parts"][0]["cites"] = ["E3"]
    with pytest.raises(ValidationError):
        contract.parse(decision)


def test_compile_preserves_science_and_does_not_expand_based_on_into_citations():
    frozen = fixture()
    for row in frozen["materials"]["evidence"].values():
        row["reading_scope"] = "text"
    scientific = science_fixture(frozen)
    decision = decision_fixture(frozen, scientific)
    scientific_before = deepcopy(scientific)
    document, audit = compile_scientific_publication(scientific, decision, frozen)
    PublicationDocument.model_validate(document.model_dump())
    assert scientific == scientific_before
    for section in document.main:
        if section.section_id in REVIEW_ASPECTS:
            assert section.blocks[1].children[0].text == (
                scientific["dimensions"][section.section_id]["paragraphs"][0]["parts"][0]["text"]
            )
    assert document.appendix[0].blocks[0].children[0].text == "Study conditions."
    conditions = next(row for row in document.main if row.section_id == "conditions")
    assert all(node.kind != "evidence" for node in walk_nodes(conditions.blocks))
    assert audit["decision_basis"] == [
        {"condition": 1, "paragraphs": [paragraph_ids(scientific)[-1]]}
    ]
    assert document.numbering == {"s1": 1, "s2": 2}
    assert document.grades == decision["grades"]
    zh = Renderer(document).render("zh")
    appendix = Renderer(document).render("appendix")
    assert "science.topic1.p1" not in zh + appendix
    assert "[[2]](#ref-2)" not in zh
    assert "[[2]](#ref-2)" in appendix
    assert "Unused" not in zh + appendix


@pytest.mark.asyncio
async def test_two_calls_are_durable_and_compile_without_authorship(tmp_path):
    frozen, calls = fixture(), []
    for row in frozen["materials"]["evidence"].values():
        row["reading_scope"] = "text"

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, *, response_schema=None):
            assert not tools
            calls.append(messages)
            science = science_fixture(frozen)
            value = (
                science if "dimensions" in response_schema["properties"]
                else decision_fixture(frozen, science)
            )
            return ChatReply({"content": json.dumps(value)})

        async def close(self):
            pass

    records = Records(tmp_path)
    caller = ManuscriptCall(records, ManuscriptConfig.load(), model_factory=Model)
    frozen_ref = records.put("frozen-review", frozen)
    ref = await synthesize_scientific(caller, frozen_ref)
    result = records.value(ref)
    assert result["format_version"] == RESULT_VERSION and result["status"] == "completed"
    assert records.value(result["scientific"])["format_version"] == "scientific-review-1"
    assert records.value(result["decision"])["format_version"] == "review-decision-1"
    assert len(calls) == 2
    assert compile_review_result(records, ref)[0].appendix
    assert await synthesize_scientific(caller, frozen_ref) == ref
    assert len(calls) == 2
    for pointer in (records.root / "receipts").glob("*.json"):
        saved = records.value(json.loads(pointer.read_text())["ref"])
        saved.pop("product")
        saved["status"] = "received"
        records.receipt(saved["identity"], saved)
    assert await synthesize_scientific(caller, frozen_ref) == ref
    assert len(calls) == 2
    with pytest.raises(ValueError, match="not_completed"):
        compile_review_result(records, result["scientific"])

    delivered = await deliver_review_result(caller, ref, tmp_path, translate=False)
    assert len(calls) == 2
    state = records.value(delivered)
    assert state["status"] == "local_completed"
    assert state["paths"]["appendix"] == "appendix.md"
    first_report = (tmp_path / "report.md").read_text("utf-8")
    assert await deliver_review_result(caller, ref, tmp_path, translate=False) == delivered
    assert (tmp_path / "report.md").read_text("utf-8") == first_report
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_decision_failure_keeps_science_but_never_publishes_completed_result(tmp_path):
    frozen, calls = fixture(), []

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, *, response_schema=None):
            calls.append(messages)
            if "grades" in response_schema["properties"]:
                raise ModelError("response_unknown")
            return ChatReply({"content": json.dumps(science_fixture(frozen))})

        async def close(self):
            pass

    records = Records(tmp_path)
    caller = ManuscriptCall(records, ManuscriptConfig.load(), model_factory=Model)
    frozen_ref = records.put("frozen-review", frozen)
    with pytest.raises(ModelError):
        await synthesize_scientific(caller, frozen_ref)
    assert list(records.root.glob("scientific-review-*.json"))
    assert not list(records.root.glob("review-result-*.json"))
    with pytest.raises(ValueError, match="already_attempted"):
        await synthesize_scientific(caller, frozen_ref)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_empty_appendix_and_translation_resume_use_only_current_public_nodes(
    tmp_path, monkeypatch
):
    frozen = fixture()
    for row in frozen["materials"]["evidence"].values():
        row["reading_scope"] = "text"
    scientific = science_fixture(frozen)
    records = Records(tmp_path)
    caller = ManuscriptCall(records, ManuscriptConfig.load())
    frozen_ref = records.put("frozen-review", frozen)

    def result_ref():
        science_ref = records.put("scientific-review", scientific)
        decision_ref = records.put("review-decision", decision_fixture(frozen, scientific))
        return records.put(
            "review-result",
            {
                "format_version": RESULT_VERSION,
                "status": "completed",
                "scientific": science_ref,
                "decision": decision_ref,
                "frozen_review": frozen_ref,
            },
        )

    await deliver_review_result(caller, result_ref(), tmp_path, translate=False)
    assert (tmp_path / "appendix.md").exists()
    scientific["topics"] = []
    empty_ref = result_ref()
    delivered = await deliver_review_result(caller, empty_ref, tmp_path, translate=False)
    state = records.value(delivered)
    assert state["appendix"] == {"status": "not_needed"}
    assert "appendix" not in state["paths"]
    assert not (tmp_path / "appendix.md").exists()
    assert "appendix.md" not in (tmp_path / "report.md").read_text("utf-8")

    calls = []

    async def translate(caller, section, **kwargs):
        calls.append(section.section_id)
        if len(calls) == 2:
            raise RuntimeError("Interrupted translation")
        section = section.model_copy(deep=True)
        for node in walk_nodes(section.blocks):
            if node.kind == "text":
                node.text = "Translated " + node.text
        return section

    monkeypatch.setattr("spacebio_review.v4.assessed_delivery.translate_section", translate)
    partial = await deliver_review_result(caller, empty_ref, tmp_path, translate=True)
    assert records.value(partial)["status"] == "partial"
    assert len(records.value(partial)["translations"]) == 1
    completed = await deliver_review_result(caller, empty_ref, tmp_path, translate=True)
    assert records.value(completed)["status"] == "completed"
    assert calls.count("summary") == 1
    calls_before = len(calls)
    await deliver_review_result(caller, empty_ref, tmp_path, translate=True)
    assert len(calls) == calls_before
    english = (tmp_path / "report.en.md").read_text("utf-8")
    assert "appendix.md" not in english
    assert "[[1]](#ref-1)" in english
    assert "| Scientific value | A | A |" in english
    assert "science." not in english

    scientific["project"] = prose("A revised scientific explanation.", ["E1"])
    changed = await deliver_review_result(caller, result_ref(), tmp_path, translate=False)
    state = records.value(changed)
    assert state["manifest"] != records.value(completed)["manifest"]
    assert state["en"]["status"] == "not_run"
    assert "en" not in state["paths"]
    assert not (tmp_path / "report.en.md").exists()
    assert "A revised scientific explanation." in (tmp_path / "report.md").read_text("utf-8")
    assert len(calls) == calls_before
