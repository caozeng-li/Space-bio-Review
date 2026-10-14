import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="module")
def chain():
    scripts = Path(__file__).parents[1] / "scripts"
    spec = importlib.util.spec_from_file_location(
        "probe_mytest25_local_chain", scripts / "probe_mytest25_local_chain.py"
    )
    value = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(scripts))
    try:
        spec.loader.exec_module(value)
    finally:
        sys.path.remove(str(scripts))
    return value


def test_actual_mytest25_archive_flows_to_frozen_assessment_and_appendix_without_model(
    tmp_path, chain
):
    with chain.ProbeSession(tmp_path) as session:
        result = chain.archive_replay(session)
        assert result["status"] == "completed"
        assert len(result["selected_research"]) == 7
        # Only the knowledge answer actually cited as analysis is projected;
        # the other successful child is not added merely because it exists.
        assert result["context_origins"]["delegated"] == 1
        assert result["opinion_count"] == result["appendix_opinion_count"]
        assert result["sources_unchanged"]
        assert result["model_requests"] == 0
        assert session.budget.report()["models"]["requests"] == 0
        identities = list(result["opinion_identities"].values())
        assert sum(row["block"].startswith("f") for row in identities) >= 6
        assert (
            chain.read(session.output / "A/archive/appendix-selection.json")[
                "scientific_prose_generated"
            ]
            is False
        )


def test_F_models_receive_identical_real_originals_and_simple_text_interface(chain):
    for qid in (2, 6):
        current = chain.diagnosis_request(f"F-q{qid}-current")
        strong = chain.diagnosis_request(f"F-q{qid}-strong")
        assert current["wire"]["messages"] == strong["wire"]["messages"]
        assert current["source_hashes"] == strong["source_hashes"]
        assert current["wire"]["model"]["model"] != strong["wire"]["model"]["model"]
        assert current["wire"]["schema"] is None
        assert current["wire"]["tools"] is None
        assert (
            current["wire"]["model"]["max_tokens"] == strong["wire"]["model"]["max_tokens"] == 8192
        )
        assert current["wire"]["model"]["retries"] == strong["wire"]["model"]["retries"] == 0
        assert "22批次" not in current["wire"]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_F_requires_recorded_D_or_E_reason_before_any_model_access(tmp_path, chain):
    with chain.ProbeSession(tmp_path) as session:
        with pytest.raises(ValueError, match="requires_observed_D_or_E"):
            await chain.diagnosis_live(session, "F-q2-current", None)
        assert session.budget.report()["models"]["requests"] == 0


@pytest.mark.asyncio
async def test_E_preview_captures_actual_query_and_does_not_search_or_read_old_answers(
    tmp_path, chain
):
    with chain.ProbeSession(tmp_path) as session:
        result = await chain.chain_preview(session)
        assert result["status"] == "previewed"
        assert result["capacity"]["fits"]
        assert result["literature_requests"] == 16
        assert result["professional_formal_requests"] == 1
        assert not result["first_wire"]["tools"]
        assert result["first_wire"]["schema"]
        assert session.budget.report()["models"]["requests"] == 0
        assert session.budget.report()["embedding"]["requests"] == 0
        assert not (session.root / "evidence-network.json").exists()
        question = result["question"]
        assert question["question"] == chain.E_QUESTION["question"]
        assert not question.get("background")


def test_E_external_network_accounting_counts_attempts_and_strips_query_secrets(tmp_path, chain):
    ledger = chain.ChainNetworkLedger(tmp_path, chain.E_CASE, "initial")
    for n in range(80):
        ledger.reserve("discovery", f"https://example.org/search?api_key=secret&page={n}")
    with pytest.raises(Exception, match="probe_external_discovery_limit") as caught:
        ledger.reserve("discovery", "https://example.org/search")
    assert caught.value.request_sent is False
    assert len(ledger.data["requests"]) == 80
    assert all(row["url"] == "https://example.org/search" for row in ledger.data["requests"])
    ledger.reserve("acquisition", "https://example.org/paper")
    assert len(ledger.data["requests"]) == 81


@pytest.mark.asyncio
async def test_E_does_not_purchase_professional_after_failed_knowledge(
    tmp_path, chain, monkeypatch
):
    from spacebio_review.agents.types import TaskResult
    from spacebio_review.evidence.knowledge import KnowledgeRun

    professional_calls = []

    async def professional(*args, **kwargs):
        professional_calls.append((args, kwargs))
        raise AssertionError("invalid knowledge must not reach professional formal")

    monkeypatch.setitem(
        sys.modules,
        "probe_mytest25_professional",
        SimpleNamespace(professional_formal=professional),
    )

    class FailedLiterature:
        async def investigate(self, question, **_):
            return KnowledgeRun(
                research_id="test-failed-child",
                question=question,
                task_result=TaskResult(
                    task_id="test-failed-child",
                    agent_id="literature",
                    status="failed",
                    reason="controlled fixture failure",
                ),
            )

        async def close(self):
            pass

    with chain.ProbeSession(tmp_path) as session:
        await chain.chain_preview(session)
        monkeypatch.setattr(
            chain.LiteratureResearcher,
            "from_resources",
            lambda *_args, **_kwargs: FailedLiterature(),
        )
        result = await chain.chain_live(session)
        assert result["status"] == "failed"
        assert result["professional_status"] == "not_executed"
        assert result["sources_unchanged"]
        assert not professional_calls
        assert session.budget.report()["models"]["requests"] == 0
        originals = list((session.output / "E/case/sources").glob("*/source.json"))
        assert originals
        source = chain.read(originals[0])
        assert Path(source["original_path"]).is_relative_to(session.output / "E/case")


@pytest.mark.asyncio
async def test_E_actual_chain_keeps_originals_and_delegated_prose_through_downstream(
    tmp_path, chain, monkeypatch
):
    import httpx

    from spacebio_review.agents.contracts import example_for
    from spacebio_review.models.provider import ChatReply

    shared = sys.modules["probe_mytest25_interfaces"]
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    calls = []

    async def unexpected_network(*_args, **_kwargs):
        raise AssertionError("offline chain must not access the network")

    class Model:
        def __init__(self, config, *, budget, **_kwargs):
            self.config, self.budget = config, budget

        async def complete(self, messages, tools=None, *, response_schema=None):
            ticket = self.budget.reserve(self.config, messages, tools, response_schema)
            if response_schema and "findings" in response_schema["properties"]:
                data = {
                    "findings": [
                        {
                            "paragraphs": [
                                {"parts": [{"text": "Fixture explanation.", "cites": ["E1"]}]}
                            ]
                        }
                    ],
                    "scope": {
                        "paragraphs": [
                            {"parts": [{"text": "Fixture scope remains attached.", "cites": []}]}
                        ]
                    },
                    "unknowns": [],
                }
                content, kind = json.dumps(data), "literature-formal"
            elif response_schema and "scope_outcomes" in response_schema["properties"]:
                content, kind = (
                    json.dumps(
                        {
                            "title": "Scoped analysis",
                            "scope_note": {"parts": [{"text": "Local conditions", "cites": []}]},
                            "blocks": [
                                {
                                    "parts": [
                                        {
                                            "text": "The interpretation applies conditionally.",
                                            "cites": ["R1"],
                                        }
                                    ]
                                }
                            ],
                            "followups": [],
                            "scope_outcomes": [
                                {
                                    "member_id": "q3",
                                    "status": "analyzed",
                                    "explanation": "Scoped result",
                                    "block_indices": [1],
                                }
                            ],
                        }
                    ),
                    "professional-formal",
                )
            elif response_schema:
                content, kind = json.dumps(example_for(response_schema)), "query"
            else:
                content, kind = "Research can close on the available original.", "research"
            calls.append(kind)
            reply = ChatReply(
                {"role": "assistant", "content": content},
                "stop",
                prompt_tokens=500,
                completion_tokens=200,
            )
            self.budget.settle(ticket, reply)
            return reply

        async def close(self):
            pass

    monkeypatch.setattr(shared, "model_class", lambda _config: Model)
    monkeypatch.setattr(httpx.AsyncClient, "send", unexpected_network)
    with chain.ProbeSession(tmp_path) as session:
        preview = await chain.chain_preview(session)
        assert not calls
        result = await chain.chain_live(session)
        assert result["status"] == "completed"
        assert result["new_requests"] == 4
        assert calls == ["query", "research", "literature-formal", "professional-formal"]
        assert session.budget.report()["models"]["requests"] == 4
        assert session.budget.report()["embedding"]["requests"] == 0
        assert result["sources_unchanged"]
        assert result["downstream"]["opinion_count"] == 2
        assert result["downstream"]["context_origins"]["delegated"] == 1
        assert result["downstream"]["model_requests"] == 0
        first_wire = chain.read(session.output / "E/case/literature/request-001/wire.json")
        assert first_wire == preview["first_wire"]
        professional = session.output / "E/case/professional"
        assert chain.read(professional / "request-001/wire.json") == chain.read(
            professional / "preview/request-001/wire.json"
        )
        assert chain.read(professional / "knowledge-tool-result.json")["ok"]
        frozen = chain.read(session.output / "E/case/downstream/frozen-review.json")
        assert "Fixture scope remains attached." in json.dumps(frozen)
        assert "Fixture explanation." in json.dumps(frozen)
        assert not (session.root / "evidence-network.json").exists()
