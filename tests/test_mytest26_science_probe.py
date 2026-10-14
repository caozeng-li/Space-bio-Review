import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import probe_mytest25_interfaces as shared
import probe_mytest26_science as probe

from spacebio_review.agents.contracts import example_for
from spacebio_review.models.provider import ChatReply, ModelError
from spacebio_review.v4.manuscript_call import ManuscriptConfig


def native_amendment_fixture(session):
    config = probe.runtime_config()
    shared.write_json(
        session.output / "matrix-amendment-native-professional.json",
        {
            "source_hashes": {},
            "formal_models": {
                tier: config.routed_model("specialist", tier, formal=True).model_dump(mode="json")
                for tier in ("normal", "strong")
            },
        },
    )


def schema_amendment_fixture(session):
    native_amendment_fixture(session)
    shared.write_json(
        session.output / "matrix-amendment-schema-professional.json",
        shared.read(session.output / "matrix-amendment-native-professional.json"),
    )


def test_fixed_shared_budget_retains_failures_plus_seven_schema_and_forbids_embedding(tmp_path):
    assert len(probe.NATIVE_C) == 8
    assert len(probe.SCHEMA_C) == 7
    assert "C-schema-q2-2" not in probe.CASES
    assert "C-native-q2-2" not in probe.CASES
    assert sum(n for _, n in probe.CASES.values()) == 38
    assert probe.ScienceBudget.group_limits["G"] == 0
    assert not any(case.startswith("G-") for case in probe.CASES)
    with probe.ScienceSession(tmp_path) as session:
        result = probe.matrix_preview(session)
        assert result["output_matrix_fits"]
        assert result["retained_legacy_C_requests"] == 3
        assert result["retained_native_C_requests"] == 1
        assert result["new_schema_C_requests"] == 7
        assert result["limits"] == {"requests": 39, "input": 2_500_000, "output": 1_000_000}
        model = ManuscriptConfig.load().model.model_copy(update={"max_tokens": 100})
        with session.budget.case("B-TFEB-1", "v1"):
            with pytest.raises(ModelError, match="embedding_only_in_None"):
                session.budget.reserve(model, texts=["cached source"])
            ticket = session.budget.reserve(model, [{"role": "user", "content": "source"}])
            session.budget.settle(ticket, SimpleNamespace(prompt_tokens=100, completion_tokens=20))
        with session.budget.case("D25-labelled-1", "v1"):
            session.budget.reserve(model, [{"role": "user", "content": "source"}])
        assert session.budget.report()["models"]["requests"] == 2
    with probe.ScienceSession(tmp_path, "v2") as session:
        report = session.budget.report()["models"]
        assert report["requests"] == 2
        assert report["unknown_usage_requests"] == 1
        assert report["input"] is None


def test_native_amendment_preserves_initial_manifest_and_legacy_samples(tmp_path, monkeypatch):
    with probe.ScienceSession(tmp_path) as session:
        with pytest.raises(ValueError, match="explicit_matrix_amendment"):
            probe.configuration_for(session, "C-schema-q2-1")
        with pytest.raises(ValueError, match="explicit_reason"):
            probe.amend_matrix(session, reason="")
        initial = session.output / "manifest.json"
        shared.write_json(initial, {"initial_matrix": "unchanged"})
        before = probe.file_hash(initial)
        for case in (*probe.RETAINED_C, *probe.RETAINED_D):
            shared.write_json(
                session.output / "cases" / case / "result.json",
                {"status": "failed", "new_requests": 1, "wire_digest": case},
            )
        for name in ("fixture.json", "task-result.json"):
            shared.write_json(session.output / "cases/B-TFEB-1" / name, {"retained": True})
        monkeypatch.setattr(probe, "fixed_tfeb", lambda session: (None, {}))
        value = probe.amend_matrix(session, reason="User-authorized native matrix")
        assert probe.file_hash(initial) == before
        assert len(value["retained_legacy_C"]) == 3
        assert len(value["retained_D26"]) == 3
        assert value["new_labelled_D25"] == ["D25-labelled-1", "D25-labelled-2"]
        assert len(value["new_native_C"]) == 8
        assert value["group_limits"]["C"] == 11
        assert value["group_limits"]["G"] == 0
        assert value["fixed_choices"]["F26_new"] == [f"C-native-q{q}-1" for q in (2, 4, 5, 7)]
        assert probe.amend_matrix(session, reason="Do not replace") == value
        config = probe.configuration_for(session, "D25-labelled-1")
        model = config.routed_model("specialist", "normal", formal=True)
        assert model.structured_output == "json_schema"
        with session.budget.case("C-q4-1", "new-revision"):
            with pytest.raises(ModelError, match="legacy_case_is_read_only"):
                session.budget.reserve(model, [{"role": "user", "content": "source"}])
        assert session.budget.report()["models"]["requests"] == 0


@pytest.mark.asyncio
async def test_schema_amendment_preserves_native_failure_without_relabelling_or_extra_samples(
    tmp_path,
):
    with probe.ScienceSession(tmp_path) as session:
        native_amendment_fixture(session)
        previous = session.output / "matrix-amendment-native-professional.json"
        failure = session.output / "cases/C-native-q2-1/result.json"
        shared.write_json(failure, {"status": "failed", "new_requests": 1, "wire_digest": "old"})
        before = {path: probe.file_hash(path) for path in (previous, failure)}
        with pytest.raises(ValueError, match="explicit_reason"):
            probe.amend_schema_matrix(session, reason="")
        value = probe.amend_schema_matrix(session, reason="Reassign seven unspent C samples")
        assert {path: probe.file_hash(path) for path in before} == before
        assert value["retained_native_C"]["C-native-q2-1"]["status"] == "failed"
        assert value["new_schema_C"] == list(probe.SCHEMA_C)
        assert len(value["cancelled_native_C"]) == 7
        assert value["fixed_choices"]["F26_new"] == [f"C-schema-q{q}-1" for q in (2, 4, 5, 7)]
        assert value["matrix_maximum_requests"] == 38
        assert value["group_limits"]["C"] == 11
        assert probe.amend_schema_matrix(session, reason="Do not replace") == value
        config = probe.configuration_for(session, "C-schema-q2-1")
        model = config.routed_model("specialist", "normal", formal=True)
        for case in probe.NATIVE_C:
            with pytest.raises(ValueError, match="preserve_existing_case|legacy_case_is_read_only"):
                await probe.run_case(session, case, live=True)
        with session.budget.case("C-native-q2-1", "new-revision"):
            with pytest.raises(ModelError, match="legacy_case_is_read_only"):
                session.budget.reserve(model, [{"role": "user", "content": "source"}])
        assert session.budget.report()["models"]["requests"] == 0
        shared.write_json(failure, {"status": "completed"})
        with pytest.raises(ValueError, match="retained_samples_changed"):
            probe.configuration_for(session, "C-schema-q2-1")


def test_first_sample_is_fixed_and_cannot_fall_back_to_second(tmp_path):
    with probe.ScienceSession(tmp_path) as session:
        shared.write_json(
            session.output / "cases/B-TFEB-2/task-result.json", {"status": "completed"}
        )
        with pytest.raises(FileNotFoundError, match="B-TFEB-1"):
            probe.fixed_tfeb(session)
        shared.write_json(session.output / "cases/C-schema-q2-1/result.json", {"status": "failed"})
        shared.write_json(
            session.output / "cases/C-native-q2-1/result.json", {"status": "completed"}
        )
        with pytest.raises(ValueError, match="first_professional_sample_incomplete:q2"):
            probe.synthesis_fixture(session, "26")


def test_retained_application_control_is_really_different_and_literature_is_original_only():
    fingerprints = []
    for sample in ("26", "25"):
        bundle, store, package, files = probe.archived_application(sample)
        assert bundle.sources and package["experts"]
        assert all(doc.passages for doc in store.documents.values())
        sid, expected = probe.ORIGINALS[sample]
        fingerprints.append(expected)
        assert (
            files[str((probe.ROOTS[sample] / "sources" / sid / "original.md").resolve())]
            == expected
        )
    assert len(set(fingerprints)) == 2
    for topic in probe.KNOWLEDGE:
        value = probe.literature_fixture(f"B-{topic}-1")
        assert set(value) == {"question", "evidence", "source_hashes"}
        assert "findings" not in value["question"]
        assert value["evidence"] and probe.source_unchanged(value["source_hashes"])


@pytest.mark.asyncio
async def test_B_D_previews_are_real_requests_and_do_not_construct_provider(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("preview_must_not_construct_provider")

    monkeypatch.setattr(shared, "model_class", forbidden)
    with probe.ScienceSession(tmp_path) as session:
        native_amendment_fixture(session)
        for case in ("B-TFEB-1", "B-TFEB-2", "B-AllProtect-1", "D25-labelled-1"):
            result = await probe.run_case(session, case)
            assert result["status"] == "previewed"
            assert result["capacity"]["fits"]
            assert result["new_requests"] == 0
            assert result["sources_unchanged"]
            wire = shared.read(session.output / "preview" / case / "request-001/wire.json")
            assert not wire["tools"] and wire["schema"]
            if case.startswith("D"):
                assert set(wire["schema"]["properties"]) == {"questions"}
        one = shared.read(session.output / "preview/B-TFEB-1/result.json")
        two = shared.read(session.output / "preview/B-TFEB-2/result.json")
        assert one["wire_digest"] == two["wire_digest"]
        assert session.budget.report()["models"]["requests"] == 0


@pytest.mark.asyncio
async def test_question_independent_repetitions_send_independently_and_completed_replay_is_free(
    tmp_path, monkeypatch
):
    sent = []

    class Model:
        def __init__(self, config, budget, **kwargs):
            self.config, self.budget = config, budget

        async def complete(self, messages, tools=None, *, response_schema=None):
            ticket = self.budget.reserve(self.config, messages, tools, response_schema)
            sent.append(messages)
            self.budget.settle(ticket, SimpleNamespace(prompt_tokens=50, completion_tokens=20))
            return ChatReply(
                {"role": "assistant", "content": json.dumps(example_for(response_schema))},
                "stop",
                prompt_tokens=50,
                completion_tokens=20,
            )

        async def close(self):
            pass

    monkeypatch.setattr(shared, "model_class", lambda config: Model)
    with probe.ScienceSession(tmp_path) as session:
        probe.manifest(session)
        native_amendment_fixture(session)
        for case in ("D25-labelled-1", "D25-labelled-2"):
            assert (await probe.run_case(session, case))["status"] == "previewed"
            assert (await probe.run_case(session, case, live=True))["new_requests"] == 1
        replay = await probe.run_case(session, "D25-labelled-1", live=True)
        assert replay["new_requests"] == 0 and replay["cache_or_local_replay"]
        assert len(sent) == 2
        assert sent[0] == sent[1]


@pytest.mark.asyncio
async def test_professional_formal_replays_existing_knowledge_and_keeps_original_question(
    tmp_path, monkeypatch
):
    def forbidden(*args, **kwargs):
        raise AssertionError("preview_must_not_construct_provider")

    monkeypatch.setattr(shared, "model_class", forbidden)
    with probe.ScienceSession(tmp_path) as session:
        schema_amendment_fixture(session)
        for case in ("C-schema-q2-1", "C-schema-q5-1", "C-schema-q7-1"):
            result = await probe.run_case(session, case)
            assert result["status"] == "previewed"
            assert result["new_requests"] == 0
            assert result["capacity"]["fits"]
            folder = session.output / "preview" / case
            wire = shared.read(folder / "request-001/wire.json")
            assert not wire["tools"]
            fixture = shared.read(folder / "fixture.json")
            assert fixture["whole_original_supplied"]
            assert (
                fixture["provided_originals"]
                or fixture["provided_knowledge"]
                or case == "C-schema-q7-1"
            )
            assert all(
                "B-AllProtect" not in research_id for research_id in fixture["provided_knowledge"]
            )


def test_F26_old_replay_retains_all_original_blocks_and_sources(tmp_path):
    with probe.ScienceSession(tmp_path) as session:
        value, files = probe.synthesis_fixture(session, "26", historical=True)
        assert len(value["materials"]["opinions"]) == 45
        assert len(value["materials"]["sources"]) == 33
        assert len(value["materials"]["application"]) == 41
        assert probe.source_unchanged(files)
        with pytest.raises(FileNotFoundError, match="C-schema-q2-1"):
            probe.synthesis_fixture(session, "26")


@pytest.mark.asyncio
async def test_Q4_uses_designated_first_B_through_real_knowledge_handoff(tmp_path, monkeypatch):
    class Model:
        def __init__(self, config, budget, **kwargs):
            self.config, self.budget = config, budget

        async def complete(self, messages, tools=None, *, response_schema=None):
            ticket = self.budget.reserve(self.config, messages, tools, response_schema)
            self.budget.settle(ticket, SimpleNamespace(prompt_tokens=50, completion_tokens=20))
            return ChatReply(
                {"role": "assistant", "content": json.dumps(example_for(response_schema))},
                "stop",
                prompt_tokens=50,
                completion_tokens=20,
            )

        async def close(self):
            pass

    monkeypatch.setattr(shared, "model_class", lambda config: Model)
    with probe.ScienceSession(tmp_path) as session:
        probe.manifest(session)
        await probe.run_case(session, "B-TFEB-1")
        assert (await probe.run_case(session, "B-TFEB-1", live=True))["status"] == "completed"
        schema_amendment_fixture(session)
        for case in ("C-schema-q4-1", "C-schema-q4-2"):
            value = await probe.run_case(session, case)
            assert value["status"] == "previewed"
            fixture = shared.read(session.output / "preview" / case / "fixture.json")
            assert fixture["provided_knowledge"] == ["fixed-B-TFEB-1"]
            assert value["sources_unchanged"]
            tool = shared.read(session.output / "preview" / case / "knowledge-tool-result.json")
            assert tool["ok"] and tool["content"]["answer"]


@pytest.mark.asyncio
async def test_E_pair_and_F_use_production_steps_and_T_publishes_without_model(
    tmp_path, monkeypatch
):
    sent = []

    class Model:
        def __init__(self, config, budget, **kwargs):
            self.config, self.budget = config, budget

        async def complete(self, messages, tools=None, *, response_schema=None):
            ticket = self.budget.reserve(self.config, messages, tools, response_schema)
            sent.append(response_schema)
            self.budget.settle(ticket, SimpleNamespace(prompt_tokens=50, completion_tokens=20))
            return ChatReply(
                {"role": "assistant", "content": json.dumps(example_for(response_schema))},
                "stop",
                prompt_tokens=50,
                completion_tokens=20,
            )

        async def close(self):
            pass

    monkeypatch.setattr(shared, "model_class", lambda config: Model)
    with probe.ScienceSession(tmp_path) as session:
        # Tiny synthetic prose tests only the wiring; this is not a paid scientific sample.
        frozen, files = probe.synthesis_fixture(session, "26", historical=True)
        monkeypatch.setattr(probe, "synthesis_fixture", lambda *args, **kwargs: (frozen, files))
        probe.manifest(session)
        for case, count in (("E-S26-1", 1), ("E-T26-1", 2), ("F-S26-1", None)):
            assert (await probe.run_case(session, case))["status"] == "previewed"
            result = await probe.run_case(session, case, live=True)
            assert result["status"] == "completed"
            assert result["new_requests"] == (count if count else len(result["plan"]))
        author_count = len(result["plan"])
        assert (session.output / "cases/F-S26-1/publication/report.md").exists()
        before = len(sent)
        result = await probe.publish_scientific(session, "E-T26-1")
        assert result["new_requests"] == 0 and len(sent) == before
        assert (session.output / "cases/E-T26-1/publication/report.md").exists()
        assert not (session.output / "cases/E-T26-1/publication/report.en.md").exists()
        assert session.budget.report()["groups"]["E"]["requests"] == 3
        assert session.budget.report()["groups"]["F"]["requests"] == author_count
