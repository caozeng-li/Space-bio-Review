"""The native knowledge answer and its immutable original-material handoff."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from spacebio_review.agents.types import TaskResult
from spacebio_review.evidence.knowledge import KnowledgeAnswer, KnowledgeQuestion, KnowledgeRun
from spacebio_review.evidence.literature import LiteratureResearcher
from spacebio_review.evidence.submissions import scientific_contract
from spacebio_review.models.config import load_config
from spacebio_review.v4.assessment_input import freeze_review
from spacebio_review.v4.handoff import knowledge_artifact
from spacebio_review.v4.records import Records


def prose(text, *refs):
    return {"paragraphs": [{"parts": [{"text": text, "cites": list(refs)}]}]}


def contract():
    originals = {
        "exact-reading": {
            "kind": "external",
            "reference": {"source_id": "paper", "passage_id": "p1"},
            "quote": "Measured in target cells at 20 pg/uL.",
            "material_version": "v1",
            "title": "Method",
            "locator": "Methods",
            "reading_scope": "text",
        }
    }
    result = scientific_contract(
        SimpleNamespace(submission_choices=lambda: originals),
        KnowledgeAnswer,
        session=SimpleNamespace(state=SimpleNamespace(stop_reason="done")),
    )
    return result, originals


def test_knowledge_draft_has_only_content_and_uses_native_route():
    output, _ = contract()
    schema = output.schema()
    assert set(schema["properties"]) == {"findings", "scope", "unknowns"}
    assert set(schema["required"]) == set(schema["properties"])
    serialized = json.dumps(schema)
    for name in (
        "basis",
        "support_explanation",
        "quantitative_basis",
        "evidence_resolution",
        "open_questions",
    ):
        assert name not in serialized
    assert output.completion == "natural"
    assert not output.task_context.structured_refs
    runtime = load_config("config/runtime.yaml")
    formal = runtime.routed_model("literature", formal=True)
    assert formal.request_protocol == "messages"
    assert formal.structured_output == "json_schema"
    assert formal.max_tokens == 24576 and formal.retries == 0
    assert "enable_thinking" not in formal.extra_body
    assert runtime.routed_model("literature").structured_output == "json_object"


@pytest.mark.parametrize(
    "findings,unknowns,scope",
    [
        ([], ["No applicable measurements acquired."], None),
        ([prose("Interpretation.")], [], prose("Scope.")),
    ],
)
def test_empty_findings_or_unknowns_are_authored_not_defaulted(findings, unknowns, scope):
    output, _ = contract()
    data = {
        "findings": findings,
        "scope": scope,
        "unknowns": unknowns,
    }
    saved = output.product(output.parse(data)).model_dump(mode="json")
    assert saved["findings"] == findings
    assert saved["unknowns"] == unknowns
    assert saved["scope"] == scope
    assert "applicability" not in saved
    assert saved["evidence_resolution"] == {}
    del data["unknowns"]
    with pytest.raises(ValidationError):
        output.parse(data)


def test_new_knowledge_survives_persistence_parent_and_freeze(tmp_path):
    output, originals = contract()
    answer = output.product(
        output.parse(
            {
                "findings": [prose("Measured only in target cells at 20 pg/uL.", "E1")],
                "scope": prose("Transfer to other targets remains open.", "E1"),
                "unknowns": ["Other targets"],
            }
        )
    )
    run = KnowledgeRun(
        research_id="child",
        question=KnowledgeQuestion(question="Conditions?"),
        answer=answer,
        task_result=TaskResult(
            task_id="child", agent_id="child", status="completed", reason="done"
        ),
    )
    restored = KnowledgeRun.model_validate_json(run.model_dump_json())
    material = restored.answer.model_dump(mode="json")
    assert material["evidence_resolution"] == originals
    assert "basis" not in material["findings"][0]
    records = Records(tmp_path)
    child = records.put("knowledge", knowledge_artifact(material, "child", "Method", []))
    block = next(iter(records.result_blocks([child]).values()))
    assert block["knowledge_contract_version"] == "literature-5"
    assert block["scope_kind"] == "external_research_scope"
    root = records.put(
        "research",
        {
            "title": "Review",
            "scope_note": "Scope",
            "scope_links": [],
            "evidence": {},
            "blocks": [
                {
                    "block_id": "b1",
                    "text": "The conditions matter.",
                    "result_refs": [child + "::f1"],
                }
            ],
        },
        [child],
    )
    frozen = freeze_review(records, [root], [], {})
    assert len(frozen["materials"]["opinions"]) == 2
    assert frozen["materials"]["evidence"]["E1"]["quote"] == originals["exact-reading"]["quote"]
    assert any(c["unknowns"] for c in frozen["materials"]["opinion_contexts"].values())


def test_unknown_strings_cannot_acquire_citation_identity(tmp_path):
    output, _ = contract()
    answer = output.product(
        output.parse(
            {
                "findings": [prose("A finding.", "E1")],
                "scope": prose("Limited scope."),
                "unknowns": ["Literal [@E1] is not a citation field."],
            }
        )
    ).model_dump(mode="json")
    records = Records(tmp_path)
    key = records.put("knowledge", knowledge_artifact(answer, "child", "Knowledge", []))
    frozen = freeze_review(records, [key], [], {})
    unknown = next(iter(frozen["materials"]["opinion_contexts"].values()))["unknowns"][0]
    assert unknown["parts"][0]["cites"] == []
    assert "E1" in unknown["parts"][0]["text"]


def test_legacy_applicability_remains_historical_not_new_scope():
    legacy = {
        "contract_version": "literature-4",
        "findings": [prose("Historical interpretation.")],
        "applicability": prose("The application should adopt this condition."),
        "unknowns": [],
        "reading_scope": "Abstract only",
        "stop_reason": "done",
    }
    restored = KnowledgeAnswer.model_validate(legacy).model_dump(mode="json")
    assert restored["applicability"] == legacy["applicability"]
    assert "scope" not in restored
    artifact = knowledge_artifact(restored, "old", "Old answer", [])
    assert artifact["knowledge_contract_version"] == "literature-4"
    assert artifact["scope_kind"] == "legacy_project_applicability"
    assert artifact["scope_note"] == "The application should adopt this condition."


def test_new_scope_keeps_abstract_depth_and_multiple_read_sources():
    output, originals = contract()
    originals["exact-reading"]["reading_scope"] = "metadata/abstract"
    originals["second-reading"] = {
        **originals["exact-reading"],
        "reference": {"source_id": "other", "passage_id": "p2"},
    }
    output.refresh(output)
    answer = output.product(
        output.parse(
            {
                "findings": [
                    prose("Abstract observation.", "E1"),
                    prose("Second abstract observation.", "E2"),
                ],
                "scope": None,
                "unknowns": ["Methods not acquired."],
            }
        )
    )
    material = answer.model_dump(mode="json")
    artifact = knowledge_artifact(material, "child", "Abstracts", [])
    assert artifact["scope_note"] == ""
    assert "scope_parts" not in artifact
    assert len(artifact["evidence"]) == 2
    assert all(
        item["reading_scope"] == "metadata/abstract" for item in artifact["evidence"].values()
    )
    assert answer.project_implications == []


@pytest.mark.parametrize("version", ["literature-4", "literature-5"])
def test_saved_knowledge_display_roundtrips_without_changing_formal_prose(tmp_path, version):
    output, _ = contract()
    canonical = output.product(
        output.parse(
            {
                "findings": [prose("Measured only under these conditions.", "E1")],
                "scope": prose("These materials share the measured conditions."),
                "unknowns": ["Other conditions not acquired."],
            }
        )
    ).model_dump(mode="json")
    if version == "literature-4":
        canonical["contract_version"] = version
        canonical["applicability"] = canonical.pop("scope")
    issues = [
        {"location": ["findings", 0], "type": "unresolved_reference", "resolution": "unknown"}
    ]
    run = KnowledgeRun(
        research_id="saved-run",
        question=KnowledgeQuestion(question="Which conditions were measured?"),
        answer=KnowledgeAnswer.model_validate(canonical),
        task_result=TaskResult(
            task_id="saved-run",
            agent_id="reader",
            status="completed",
            reason="done",
            product=canonical,
            citation_issues=issues,
        ),
    )
    researcher = LiteratureResearcher(
        SimpleNamespace(
            config=load_config("config/runtime.yaml"), recorder=SimpleNamespace(root=tmp_path)
        ),
        SimpleNamespace(root=tmp_path / "sources"),
        None,
    )
    researcher._save_run(
        run,
        SimpleNamespace(
            _metadata={},
            references=SimpleNamespace(snapshot_choices=lambda: canonical["evidence_resolution"]),
        ),
    )
    path = tmp_path / "knowledge" / "saved-run" / "answer.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["answer"]["findings"][0]["citation_status"] == "provisional"
    restored = KnowledgeRun.model_validate(saved)
    assert restored.model_dump(mode="json") == run.model_dump(mode="json")
    assert restored.answer_view() == saved["answer"]
    assert "citation_status" in saved["answer"]["findings"][0]
    with pytest.raises(ValidationError):
        KnowledgeAnswer.model_validate(saved["answer"])
    saved["answer"]["findings"][0]["unexpected_model_field"] = "Not a display annotation"
    with pytest.raises(ValidationError):
        KnowledgeRun.model_validate(saved)


def test_unversioned_historical_delegation_preserves_scope_without_guessing_version(tmp_path):
    records = Records(tmp_path)
    artifact = {
        "origin": "delegated",
        "title": "Historical knowledge",
        "scope_note": "Old project implication kept verbatim.",
        "blocks": [{"block_id": "f1", "text": "An old observation."}],
    }
    ref = records.put("knowledge", artifact)
    block = records.result_blocks([ref])[ref + "::f1"]
    assert block["scope_kind"] == "historical_unspecified"
    assert "knowledge_contract_version" not in block
    assert block["scope_note"] == artifact["scope_note"]
    assert records.value(ref) == artifact
