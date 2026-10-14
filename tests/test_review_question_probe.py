import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from spacebio_review.agents.contracts import example_for
from spacebio_review.application import ApplicationBundle, ApplicationSource
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument
from spacebio_review.models.provider import ChatReply


@pytest.fixture(scope="module")
def probe():
    scripts = Path(__file__).parents[1] / "scripts"
    spec = importlib.util.spec_from_file_location(
        "probe_review_questions", scripts / "probe_review_questions.py"
    )
    value = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(scripts))
    try:
        spec.loader.exec_module(value)
    finally:
        sys.path.remove(str(scripts))
    return value


def small_fixture(probe):
    def fixture(case, _folder):
        text = (
            "Sample 3: muscle stem cell division and chromatin structure."
            if case.startswith("D3-")
            else "Sample 2: organoid formation and mitochondrial signals."
        )
        doc = SourceDocument(
            source_id="proposal",
            candidate=SourceCandidate(title="Proposal", kind="application"),
            passages=[Passage(passage_id="p1", section="Method", locator="Method", text=text)],
            availability="full_text",
        )
        bundle = ApplicationBundle(
            sources=[
                ApplicationSource(
                    source_id="proposal",
                    title="Proposal",
                    role="main",
                    availability="full_text",
                    sections=["Method"],
                )
            ]
        )
        return (
            bundle,
            probe.FrozenStore({"proposal": doc}),
            {
                "experts": {
                    "expert-1": {
                        "purpose": "Evaluate measurement and interpretation.",
                        "profile_labels": ["Biology"],
                        "method_skill_ids": ["measurement-analysis"],
                    }
                }
            },
            {},
        )

    return fixture


@pytest.mark.asyncio
async def test_five_case_preview_is_question_only_and_has_no_api_usage(
    tmp_path, monkeypatch, probe
):
    monkeypatch.setattr(probe, "fixture", small_fixture(probe))
    output = tmp_path / "revisions/initial"
    matrix = await probe.preview(output, tmp_path)
    assert matrix["fits"]
    assert [row["case"] for row in matrix["cases"]] == ["D24-1", "D24-2", "D24-3", "D3-1", "D3-2"]
    assert not (tmp_path / "v4/budget.json").exists()
    wires = [probe.read(output / "D/preview" / case / "wire.json") for case in probe.CASES]
    assert wires[0] == wires[1] == wires[2]
    assert wires[3] == wires[4] and wires[3] != wires[0]
    assert all(set(wire["schema"]["properties"]) == {"questions"} for wire in wires)
    assert all(wire["model"]["retries"] == 0 for wire in wires)


@pytest.mark.asyncio
async def test_fresh_cases_are_independent_and_recovery_costs_zero(tmp_path, monkeypatch, probe):
    monkeypatch.setattr(probe, "fixture", small_fixture(probe))
    output = tmp_path / "revisions/initial"
    await probe.preview(output, tmp_path)
    calls = []

    class Model:
        def __init__(self, config, *, budget, **_):
            self.config, self.budget = config, budget

        async def complete(self, messages, tools=None, *, response_schema=None):
            ticket = self.budget.reserve(self.config, messages, tools, response_schema)
            calls.append(messages)
            self.budget.settle(ticket, SimpleNamespace(prompt_tokens=500, completion_tokens=200))
            return ChatReply(
                {"content": json.dumps(example_for(response_schema))},
                prompt_tokens=500,
                completion_tokens=200,
            )

        async def close(self):
            pass

    monkeypatch.setattr(probe, "model_class", lambda config: Model)
    first = await probe.run_case(output, tmp_path, "D24-1", live=True)
    replay = await probe.run_case(output, tmp_path, "D24-1", live=True)
    other = await probe.run_case(output, tmp_path, "D24-2", live=True)
    assert first["attempts"][-1]["new_requests"] == 1
    assert replay["attempts"][-1]["new_requests"] == 0
    assert replay["attempts"][-1]["cache_or_local_replay"]
    assert other["attempts"][-1]["new_requests"] == 1
    assert len(calls) == 2
    usage = probe.read(tmp_path / "usage.json")
    assert usage["groups"]["D"]["requests"] == 2
    assert usage["models"]["input"] == 1000
    assert first["selected"] == first["generated"]


def test_sample3_fixture_uses_real_original_and_fixed_discipline_profiles(tmp_path, probe):
    bundle, store, package, files = probe.fixture("D3-1", tmp_path)
    assert Path("data/sample3.md").resolve().as_posix() in {Path(path).as_posix() for path in files}
    doc = store.retained(bundle.sources[0].source_id)
    assert len(doc.passages) > 20
    assert any("ChIP-seq" in paragraph.text for paragraph in doc.passages)
    assert set(package) == {"experts"}
    assert len(package["experts"]) == 4
    assert all(
        set(row) == {"purpose", "profile_labels", "method_skill_ids"}
        for row in package["experts"].values()
    )
