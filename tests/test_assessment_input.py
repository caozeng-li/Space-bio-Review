from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from spacebio_review.agents.context import build_messages, evaluation_prompt
from spacebio_review.agents.diagnostics import digest
from spacebio_review.agents.skills import Skill
from spacebio_review.agents.types import AgentTask, RoleProfile, WorkingMemory
from spacebio_review.review.project import EvaluationContext
from spacebio_review.v4.assessment_input import (
    ReviewArchive,
    build_evaluation_context,
    freeze_review,
    select_materials,
)
from spacebio_review.v4.config import V4Config
from spacebio_review.v4.execution import Execution
from spacebio_review.v4.records import Records, content_references


def test_content_references_separates_structured_text_from_legacy_markers():
    content = {
        "part": {
            "text": "Literal [@not-a-reference]",
            "cites": ["source"],
            "scope": {"text": "Literal [@also-not-a-reference]", "cites": ["condition"]},
            "related_opinions": ["O1"],
        },
        "text": "Legacy prose [@legacy-source]",
        "basis": {
            "text": "Legacy explanation [@basis-source]",
            "evidence_refs": ["basis-identity"],
        },
        "result_refs": ["research-one::b1"],
        "scope_links": ["research-two::b2"],
    }
    assert content_references(content) == [
        "source",
        "condition",
        "legacy-source",
        "basis-source",
        "basis-identity",
        "research-one::b1",
        "research-two::b2",
    ]


@pytest.mark.parametrize("literal", ["[@E999]", "[@knowledge-missing::f1]"])
def test_structured_prose_literal_markers_survive_freeze_and_selection(tmp_path, literal):
    from spacebio_review.v4.handoff import knowledge_artifact

    records = Records(tmp_path)
    knowledge = records.put(
        "knowledge",
        knowledge_artifact(
            {
                "contract_version": "literature-4",
                "findings": [
                    {
                        "paragraphs": [
                            {"parts": [{"text": "Literal token " + literal, "cites": ["source"]}]}
                        ]
                    }
                ],
                "applicability": {
                    "paragraphs": [
                        {"parts": [{"text": "Scope contains " + literal, "cites": ["source"]}]}
                    ]
                },
                "unknowns": [],
                "evidence_resolution": {
                    "source": {
                        "reference": {"source_id": "paper", "passage_id": "p1"},
                        "title": "Original paper",
                        "quote": "Original evidence",
                        "reading_scope": "text",
                    }
                },
            },
            "child",
            "Knowledge",
            [],
        ),
    )
    parent = records.put(
        "research",
        {
            "title": "Professional interpretation",
            "scope_note": "Parent scope",
            "blocks": [
                {
                    "block_id": "b1",
                    "text": "Uses the delegated interpretation [@" + knowledge + "::f1]",
                    "result_refs": [knowledge + "::f1"],
                }
            ],
            "evidence": {},
        },
        [knowledge],
    )
    frozen = freeze_review(records, [parent], [], {})
    selected = select_materials(frozen, ["O1"])
    assert set(selected["opinions"]) == {"O1", "O2"}
    assert selected["opinions"]["O1"]["related_opinions"] == ["O2"]
    assert selected["opinions"]["O2"]["text"]["parts"] == [
        {"text": "Literal token " + literal, "cites": ["E1"]}
    ]
    assert selected["opinion_contexts"]["G2"]["scope"]["parts"] == [
        {"text": "Scope contains " + literal, "cites": ["E1"]}
    ]
    assert list(selected["evidence"]) == ["E1"]
    assert selected["evidence"]["E1"]["quote"] == "Original evidence"


def test_mytest25_delegated_finding_reaches_assessment_and_appendix_unchanged():
    from spacebio_review.v4.assessment import assessment_request
    from spacebio_review.v4.report_authoring import author_materials

    root = Path("runs/mytest25")
    if not root.exists():
        pytest.skip("local historical fixture")
    archive = ReviewArchive(root)
    paths = [*archive.root.glob("*.json"), *root.glob("sources/*/source.json")]
    before = {path: digest(path.read_bytes().hex()) for path in paths}
    frozen = freeze_review(archive, archive.selected(), archive.application(), {"prompt": "Review"})
    delegated = {
        key: row
        for key, row in frozen["provenance"]["opinions"].items()
        if row["artifact"].startswith("knowledge-")
    }
    assert delegated
    key, lineage = next(
        (key, row)
        for key, row in delegated.items()
        if row["artifact"].startswith("knowledge-b2f7d60") and row["block"] == "f1"
    )
    original = archive.block(lineage["artifact"] + "::f1")
    opinion = frozen["materials"]["opinions"][key]
    context = frozen["materials"]["opinion_contexts"][opinion["context"]]
    assert context["origin"] == "delegated"
    assert "Brassica rapa" in str(context)
    assert "申请方案实际设置了哪些地面组" in str(context["unknowns"])
    assert len(opinion["basis"]) == len(original["basis"])
    _, request = assessment_request(frozen)
    rendered = request["research"][opinion["context"]]["arguments"][key]
    assert rendered["text"] == opinion["text"]
    assert rendered["basis"] == opinion["basis"]
    assessment = {
        "overall": {"paragraphs": []},
        "recommendation": "revise",
        "appendix_topics": [
            {
                "title": "Control design",
                "focus": "Conditions",
                "main_sections": ["overall"],
                "opinion_ids": [key],
            }
        ],
    }
    appendix = author_materials(frozen, assessment, ["topic_1"])["materials"]
    assert appendix["opinions"][key] == opinion
    assert appendix["opinion_contexts"][opinion["context"]]["unknowns"] == context["unknowns"]
    for basis, retained in zip(original["basis"], opinion["basis"]):
        assert retained["quantity"] == basis["quantitative_basis"]
        assert basis["support_explanation"] in str(retained["support"])
        assert {appendix["evidence"][ref]["quote"] for ref in retained["evidence_ids"]} == {
            original["evidence"][ref]["quote"] for ref in basis["evidence_refs"]
        }
    assert "request_failed" not in str(request)
    assert {path: digest(path.read_bytes().hex()) for path in paths} == before


@pytest.mark.parametrize(
    "name,count", [("mytest24", 8), ("mytest23_complete", 10), ("mytest17_revised_by_me", 18)]
)
def test_historical_input_is_read_only(name, count):
    root = Path("runs") / name
    if not root.exists():
        pytest.skip("local historical fixture")
    archive = ReviewArchive(root)
    paths = [archive.path, *[archive.root / (ref + ".json") for ref in archive.selected()]]
    paths.extend(
        root / "sources" / row["source_id"] / "source.json"
        for row in archive.state["metadata"]["application"]["sources"]
    )
    before = {path: path.read_bytes() for path in paths}
    assert len(archive.selected()) == count
    frozen = freeze_review(
        archive, archive.selected(), archive.application(), {"date": "2026-10-13"}
    )
    assert frozen["materials"]["application"]
    assert frozen["materials"]["opinions"]
    assert all(row["quote"] for row in frozen["materials"]["evidence"].values())
    assert {path: path.read_bytes() for path in paths} == before
    with pytest.raises(TypeError):
        archive.save_state({})
    selected = select_materials(frozen, ["O1"])
    assert (
        selected["opinion_contexts"]["G1"]["scope"]
        == frozen["materials"]["opinion_contexts"]["G1"]["scope"]
    )
    assert "provenance" not in selected
    assert "evaluation" not in frozen["materials"]
    assert frozen["evaluation"] == {"date": "2026-10-13"}
    for key, context in frozen["materials"]["opinion_contexts"].items():
        assert context["opinion_ids"] == [
            oid
            for oid, opinion in frozen["materials"]["opinions"].items()
            if opinion["context"] == key
        ]


def test_no_task_status_or_ancestry_controls_input():
    artifact = {
        "title": "Opinion",
        "scope_note": "Only under this condition.",
        "blocks": [{"block_id": "b1", "text": "Claim [@material]"}],
        "evidence": {
            "material": {
                "reference": {"source_id": "s", "passage_id": "p1"},
                "title": "Source",
                "quote": "Actual excerpt",
            }
        },
        "followups": [],
        "scope_outcomes": [{"explanation": "INTERNAL", "status": "partial"}],
    }

    class Archive(Records):
        def __init__(self):
            pass

        def value(self, key):
            assert key == "selected"
            return deepcopy(artifact)

    first = freeze_review(Archive(), ["selected"], [], {})
    artifact["scope_outcomes"] = []
    artifact["purpose"] = "ANOTHER INTERNAL TASK"
    second = freeze_review(Archive(), ["selected"], [], {})
    assert first == second
    assert "INTERNAL" not in str(first["materials"])
    assert select_materials(first, ["O1"])["evidence"]["E1"]["quote"] == "Actual excerpt"


def test_legacy_opinion_relation_is_not_expanded_into_a_citation():
    class Archive(Records):
        def __init__(self):
            pass

        def value(self, key):
            return {
                "title": "Analysis",
                "scope_note": "Common condition",
                "blocks": [
                    {
                        "block_id": "b1",
                        "text": "Original claim"
                        if key == "a"
                        else "Further interpretation [@a::b1]",
                    }
                ],
                "evidence": {},
                "followups": [],
            }

    frozen = freeze_review(Archive(), ["a", "b"], [], {})
    assert frozen["materials"]["opinions"]["O2"]["related_opinions"] == ["O1"]
    assert "[@" not in str(frozen["materials"]["opinions"])
    assert frozen["materials"]["evidence"] == {}


def test_common_standard_snapshot_retains_source_and_changes_identity():
    skill = Skill("evaluation", "Description", "COMMON_STANDARD", {"example.md": "EXAMPLE"})
    config = EvaluationContext(core="RUN_PURPOSE", historical_as_of=date(2026, 10, 13))
    snapshot = build_evaluation_context(skill, "RUN_SUPPLEMENT", config)
    assert evaluation_prompt(skill, "RUN_SUPPLEMENT") in snapshot["prompt"]
    assert "EXAMPLE" in snapshot["prompt"]
    assert snapshot["prompt"].count(config.prompt()) == 1
    assert snapshot["configuration"] == config.model_dump(mode="json")
    assert snapshot["run_prompt"] == config.prompt()
    for changed in (
        build_evaluation_context(replace(skill, body="NEW_STANDARD"), "RUN_SUPPLEMENT", config),
        build_evaluation_context(skill, "NEW_SUPPLEMENT", config),
        build_evaluation_context(
            skill, "RUN_SUPPLEMENT", config.model_copy(update={"core": "NEW"})
        ),
    ):
        assert digest(snapshot) != digest(changed)

    class Archive(Records):
        def __init__(self):
            pass

        def value(self, key):
            raise AssertionError("No research requested")

    frozen = freeze_review(Archive(), [], [], snapshot)
    snapshot["prompt"] = "Later changed caller context"
    assert frozen["evaluation"]["prompt"] != snapshot["prompt"]
    assert "evaluation" not in frozen["materials"]


def test_expert_keeps_same_standard_once_and_question_context_excludes_it(tmp_path):
    skill = Skill("evaluation", "Description", "COMMON_STANDARD", {})
    config = EvaluationContext(core="RUN_PURPOSE", historical_as_of=date(2026, 10, 13))
    snapshot = build_evaluation_context(skill, "", config)
    runtime = SimpleNamespace(config=None, evaluation=skill)
    ex = Execution(runtime, Records(tmp_path), V4Config(), snapshot)
    contract = SimpleNamespace(task_context=None)
    profile = RoleProfile(agent_id="test", responsibility="Specialist")
    for kind in ("specialist", "question_generation"):
        task = AgentTask(
            task_id="test", objective="Task", phase="test", expected_product="Result", kind=kind
        )
        view = ex.context_view("test", contract, include_evaluation=kind != "question_generation")
        messages = build_messages(task, profile, view, skill, {}, [], WorkingMemory())
        text = str(messages)
        if kind == "specialist":
            assert text.count("COMMON_STANDARD") == 1
            assert text.count("RUN_PURPOSE") == 1
            assert "2026-10-13" in text
        else:
            assert "COMMON_STANDARD" not in text
            assert "RUN_PURPOSE" not in text
            assert "2026-10-13" not in text


def test_grouped_opinions_keep_both_explanations_and_share_evidence():
    class Archive(Records):
        def __init__(self):
            pass

        def value(self, key):
            return {
                "title": "Sampling times",
                "scope_note": "Fixed timepoints [@source]",
                "blocks": [
                    {"block_id": "limit", "text": "Limited trajectory inference [@source]"},
                    {"block_id": "value", "text": "Useful state comparison [@source]"},
                ],
                "evidence": {
                    "source": {
                        "reference": {"source_id": "paper", "passage_id": "abstract"},
                        "title": "Study in the actual organism",
                        "reading_scope": "abstract",
                        "quote": "Fixed timepoint observations under specified conditions.",
                        "locator": "Abstract",
                    }
                },
            }

    frozen = freeze_review(Archive(), ["selected"], [], {})
    materials = frozen["materials"]
    assert materials["opinion_contexts"]["G1"]["opinion_ids"] == ["O1", "O2"]
    assert len(materials["evidence"]) == 1
    assert materials["evidence"]["E1"]["reading_scope"] == "abstract"
    assert "Limited trajectory" in str(materials["opinions"]["O1"])
    assert "Useful state" in str(materials["opinions"]["O2"])
    selected = select_materials(frozen, ["O2"])
    assert selected["opinion_contexts"]["G1"]["opinion_ids"] == ["O2"]
    assert materials["opinion_contexts"]["G1"]["opinion_ids"] == ["O1", "O2"]
    assert selected["evidence"] == materials["evidence"]


@pytest.mark.asyncio
async def test_runner_resume_rejects_changed_standard_before_model_creation(tmp_path, monkeypatch):
    import spacebio_review.runner as runner
    from spacebio_review.models.scheduling import RequestScheduler

    created = []
    skill = Skill("exploration-review", "Description", "INITIAL_STANDARD", {})
    monkeypatch.setattr(runner.SkillLibrary, "get", lambda self, name: skill)

    async def close():
        pass

    def runtime(config, **kwargs):
        created.append(config)
        return SimpleNamespace(
            config=config, evaluation=skill, scheduler=RequestScheduler(), close=close
        )

    resources = SimpleNamespace(store=SimpleNamespace(restore=lambda: None), close=close)
    monkeypatch.setattr(runner.AgentRuntime, "from_config", runtime)
    monkeypatch.setattr(runner.EvidenceResources, "create", lambda *a, **k: resources)

    async def application(*args):
        return runner.ApplicationBundle(sources=[])

    async def admission(*args):
        return runner.AdmissionRun(
            status="completed",
            decision={"decision": "reject", "reason_code": "empty", "rationale": "Empty fixture"},
        )

    monkeypatch.setattr(runner, "load_application", application)
    monkeypatch.setattr(runner.ProposalAdmission, "check", admission)
    source = tmp_path / "application.txt"
    source.write_text("Original application", "utf-8")
    config = runner.ReviewConfig(output=tmp_path / "output")
    assert (await runner.run_review([source], config)).status == "rejected"
    config.resume = True
    assert (await runner.run_review([source], config)).status == "rejected"
    assert len(created) == 2
    skill = replace(skill, body="REVISED_STANDARD")
    with pytest.raises(ValueError, match="Resume input/configuration differs"):
        await runner.run_review([source], config)
    assert len(created) == 2
