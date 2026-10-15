import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

from spacebio_review.models.provider import ModelError

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "probe_review_evidence", SCRIPTS / "probe_review_evidence.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
sys.path.remove(str(SCRIPTS))


def test_probe_config_preserves_model_capacity_and_closes_transport_retries(tmp_path):
    runtime, evidence = probe.configs(tmp_path)
    for kind in ("query", "literature", "rerank", "visual"):
        for formal in (False, True):
            assert runtime.routed_model(kind, formal=formal).retries == 0
    assert runtime.routed_model("literature", formal=True).max_tokens == 24576
    assert runtime.embedding.retries == 0
    assert runtime.embedding.cache_dir == (tmp_path / "embedding-cache").resolve()
    assert evidence.literature.max_model_turns == 18


def test_frozen_originals_use_actual_production_question_handoff():
    if not Path("runs/mytest24/v4/state.json").exists():
        pytest.skip("retained run is local test data")
    method = probe.case_fixture("B-method-1")
    assert method == probe.case_fixture("B-method-2")
    assert len(method["question"]["application_statements"]) == len(method["originals"])
    assert (
        list(method["question"]["application_statements"].values())[-1]["quote"]
        == method["originals"]["E21"]["quote"]
    )
    platform = probe.case_fixture("B-platform-1")
    assert platform == probe.case_fixture("B-platform-2")
    assert platform["request"]["question"] != method["request"]["question"]
    assert "opinions" not in method


@pytest.mark.asyncio
async def test_external_failed_attempts_and_cache_are_separate(tmp_path):
    ledger = probe.NetworkLedger(tmp_path, "B-method-1", "initial")

    async def network(*args):
        await ledger.on_request(httpx.Request("GET", "https://example.org/paper?api_key=private"))
        return "original"

    async def cached(*args):
        return "original"

    assert await ledger.wrap(network, "acquisition")() == "original"
    assert await ledger.wrap(cached, "acquisition")() == "original"
    assert [row["cache_hit"] for row in ledger.data["operations"]] == [False, True]
    assert ledger.data["requests"][0]["url"] == "https://example.org/paper"
    assert len(ledger.data["requests"]) == 1


def test_external_caps_include_prior_revisions_and_never_reuse_budget(tmp_path):
    first = probe.NetworkLedger(tmp_path, "B-method-1", "initial")
    for _ in range(40):
        first.reserve("discovery", "https://example.org/search")
    second = probe.NetworkLedger(tmp_path, "B-platform-1", "revised")
    with pytest.raises(ModelError, match="external_discovery_limit"):
        second.reserve("discovery", "https://example.org/search")
    second.reserve("acquisition", "https://example.org/paper")
    assert len(second.data["requests"]) == 41


@pytest.mark.asyncio
async def test_research_preview_captures_identical_actual_request_without_network(
    tmp_path, monkeypatch
):
    if not Path("runs/mytest24/v4/state.json").exists():
        pytest.skip("retained run is local test data")

    async def unexpected_network(*args, **kwargs):
        raise AssertionError("preview_must_not_send_network")

    def unexpected_provider(*args, **kwargs):
        raise AssertionError("preview_must_not_create_live_provider")

    monkeypatch.setattr(httpx.AsyncClient, "send", unexpected_network)
    monkeypatch.setattr(probe, "model_class", unexpected_provider)
    item = probe.case_fixture("B-method-1")
    runtime, evidence = probe.configs(tmp_path / "live")
    first = await probe.query_preview(item, runtime, evidence, tmp_path / "preview-1", "B-method-1")
    second = await probe.query_preview(
        item, runtime, evidence, tmp_path / "preview-2", "B-method-1"
    )
    assert first == second
    assert first["capacity"]["fits"]
    assert first["capacity"]["input_tokens"] > 0
    assert "ResearchPlan" not in str(first["request"]["messages"])
    tool_names = {tool["function"]["name"] for tool in first["request"]["tools"]}
    assert {"search_sources", "related_papers"} <= tool_names
    assert "application_statements" in str(first["request"]["messages"])
    assert "known_project_facts" not in str(first["request"]["messages"])
    assert first["request"]["model"]["retries"] == 0


@pytest.mark.asyncio
async def test_started_case_never_repeats_and_wire_change_stops_before_provider(
    tmp_path, monkeypatch
):
    if not Path("runs/mytest24/v4/state.json").exists():
        pytest.skip("retained run is local test data")
    case = "B-method-1"
    item = probe.case_fixture(case)
    output = tmp_path / "revisions" / "test"
    probe.write_json(
        output / "evidence-preview.json",
        {
            "cases": {case: item},
            "implementation": probe.implementation_hashes(),
        },
    )
    (output / "evidence-cases" / case).mkdir(parents=True)
    with pytest.raises(ValueError, match="already_started"):
        await probe.live(output, tmp_path, case)

    def unexpected_provider(*args, **kwargs):
        raise AssertionError("wire_change_must_stop_before_provider")

    monkeypatch.setattr(probe, "model_class", unexpected_provider)
    runtime, _ = probe.configs(tmp_path)
    model = probe.probe_models({}, expected={"messages": "different"})(
        runtime.routed_model("query")
    )
    with pytest.raises(ValueError, match="first_query_wire_changed"):
        await model.complete([{"role": "user", "content": "Original request"}])
