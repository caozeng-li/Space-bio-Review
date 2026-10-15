import json
from types import SimpleNamespace

import pytest
from test_assessed_publication import draft_fixture, frozen_fixture

from spacebio_review.agents.contracts import example_for
from spacebio_review.models.provider import ChatReply, ModelError
from spacebio_review.review.evaluation import GENERAL
from spacebio_review.review.project import REVIEW_ASPECTS
from spacebio_review.runner import ReviewResult, export, read_result
from spacebio_review.v4.config import ReportingConfig, V4Config
from spacebio_review.v4.coordinator import Coordinator
from spacebio_review.v4.manuscript_call import ManuscriptCall, ManuscriptConfig
from spacebio_review.v4.records import Records


def setup_entry(tmp_path, *, fail=None):
    records, calls = Records(tmp_path), []
    frozen = frozen_fixture()

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, *, response_schema=None):
            assert not tools
            data = json.loads(messages[1]["content"])
            calls.append(data)
            stage = (
                "translation"
                if "units" in data and "materials" not in data
                else ("writer" if "units" in data else "assessment")
            )
            if stage == fail:
                raise ModelError("INTERNAL_TEST_FAILURE", request_sent=True)
            value = example_for(response_schema)
            if "dimensions" in value:
                value = draft_fixture(frozen)
                value["recommendation"] = "support"
            return ChatReply({"content": json.dumps(value)})

        async def close(self):
            pass

    ex = SimpleNamespace(
        records=records,
        config=V4Config(),
        reporting=ReportingConfig(),
        configuration="research-configuration",
        evaluation={"prompt": "Evaluate six dimensions"},
        system=GENERAL,
    )
    caller = ManuscriptCall(records, ManuscriptConfig.load(), model_factory=Model)
    coordinator = Coordinator(ex, manuscript=caller)
    research = records.put(
        "research",
        {
            "title": "Professional opinion",
            "scope_note": "Only for this comparison",
            "blocks": [{"block_id": "b1", "text": "Opinion [@external]"}],
            "evidence": {
                "external": {
                    "reference": {"source_id": "s2", "passage_id": "p1"},
                    "title": "Study",
                    "kind": "paper",
                    "quote": "External finding",
                    "locator": "abstract",
                    "reading_scope": "abstract",
                }
            },
            "followups": [],
            "scope_outcomes": [],
        },
    )
    coordinator._state.update(
        package={"project": {}, "experts": {}},
        questions={"q1": {"source_rank": 1}},
        heads={"q1": [research]},
        tasks={"research/q1": {"status": "committed", "attempts": 1, "artifact": research}},
    )
    coordinator.save()
    application = [
        {
            "source_id": "s1",
            "candidate": {"title": "Application"},
            "passages": [{"passage_id": "p1", "locator": "p1", "text": "Application fact"}],
        }
    ]
    return coordinator, application, calls


@pytest.mark.asyncio
async def test_default_coordinator_runs_only_new_downstream_and_replays_exports(tmp_path):
    c, application, calls = setup_entry(tmp_path)
    upstream = []

    async def prepare(_):
        upstream.append("prepare")
        return "prepared"

    async def research(_):
        upstream.append("research")

    c.prepare, c.research = prepare, research
    prep = SimpleNamespace(
        ex=c.ex,
        reader=lambda: SimpleNamespace(
            docs={"s1": SimpleNamespace(model_dump=lambda **_: application[0])}
        ),
    )
    result = await c.run(prep, None, tmp_path)
    assert upstream == ["prepare", "research"]
    assert result["status"] == "completed"
    assert result["assessment_status"] == "completed"
    assert result["research_status"] == "completed"
    assert len(result["dimension_assessments"]) == 6
    assert all(result["coverage"][key]["status"] == "unassessed" for key in REVIEW_ASPECTS)
    assert "assessment/current" in c.state["tasks"]
    assert not any(k.startswith("synthesis/") for k in c.state["tasks"])
    count = len(calls)
    (tmp_path / "report.md").unlink()
    restored = Coordinator(c.ex, manuscript=c.manuscript)
    replay = await restored.publish(application, tmp_path)
    assert replay["status"] == "completed"
    assert len(calls) == count
    assert (tmp_path / "report.md").is_file()
    assert upstream == ["prepare", "research"]
    public = ReviewResult.model_validate(replay)
    export(tmp_path, public)
    saved = read_result(tmp_path / "review.json")
    assert saved["data"]["report_path"] == "report.md"
    assert saved["data"]["publication"]["status"] == "completed"
    assert saved["resolved_paths"]["report_path"] == str((tmp_path / "report.md").resolve())


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["assessment", "writer", "translation"])
async def test_failures_have_no_legacy_fallback_or_placeholder_reports(tmp_path, failure):
    c, application, calls = setup_entry(tmp_path, fail=failure)
    result = await c.publish(application, tmp_path)
    assert result["status"] == "partial"
    assert result["technical_limits"]
    assert result["assessment_status"] == ("failed" if failure == "assessment" else "completed")
    export(tmp_path, ReviewResult.model_validate(result))
    if failure == "translation":
        assert result["publication"]["status"] == "partial"
        assert (tmp_path / "report.md").exists()
        assert "INTERNAL_TEST_FAILURE" not in (tmp_path / "report.md").read_text("utf-8")
    else:
        assert not (tmp_path / "report.md").exists()
    assert not (tmp_path / "report.en.md").exists()
    count = len(calls)
    await Coordinator(c.ex, manuscript=c.manuscript).publish(application, tmp_path)
    assert len(calls) == count
