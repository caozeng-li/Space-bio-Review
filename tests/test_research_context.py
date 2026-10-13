"""Research input lifecycle, using retained records and real message assembly."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from model_helpers import offline_model

from spacebio_review.agents.context import TokenEstimator
from spacebio_review.agents.reading import BrowseSelection, PassageSelection, ReadingWindow
from spacebio_review.v4.context import TaskContext


def test_checkpoint_keeps_counterevidence_and_allows_original_reread():
    context = TaskContext(compact_sources=True)
    context.add({f"ev-{i}": evidence(f"p{i}", f"Condition {i}") for i in range(8)}, "E")
    before = context.mapping()
    context.checkpoint(
        {
            "current_claims": ["Only conditionally [@E1]"],
            "counterevidence": ["Conflicting experiment [@E3]"],
            "active_refs": ["E4"],
        }
    )
    assert set(context.source_view()["evidence"]) == {"E1", "E3", "E4"}
    context.activate_read({"source_id": "paper", "passages": [{"passage_id": "p7"}]})
    assert "E8" in context.source_view()["evidence"]
    assert context.mapping() == before


@pytest.mark.asyncio
async def test_retrieval_pages_only_record_returned_reads_and_reuse_exact_query(monkeypatch):
    from spacebio_review.evidence.knowledge import KnowledgeQuestion, RetrieveKnowledge
    from spacebio_review.evidence.literature import KnowledgeSession
    from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument

    doc = SourceDocument(
        source_id="paper",
        candidate=SourceCandidate(title="Paper"),
        passages=[
            Passage(passage_id=f"p{i}", locator=f"p{i}", text="Condition. " * 40) for i in range(80)
        ],
    )

    class Store:
        def candidate(self, sid):
            return doc.candidate

        def retained(self, sid):
            return doc

        async def acquire(self, sid):
            return doc

    calls = []

    async def rank(docs, queries, model, **kwargs):
        calls.append(queries)
        return {
            "hits": [{"source_id": "paper", "passage": p.model_dump()} for p in doc.passages],
            "limitations": [],
            "channels": {},
        }

    monkeypatch.setattr("spacebio_review.evidence.literature.find_passages", rank)
    researcher = SimpleNamespace(
        store=Store(),
        discovery=None,
        index=None,
        rerank=False,
        runtime=SimpleNamespace(models={"task/rerank/normal": None}),
        config=SimpleNamespace(stagnation_batches=2),
    )
    session = KnowledgeSession(
        researcher, KnowledgeQuestion(question="Conditions?", existing_source_ids=["paper"])
    )
    session.reading.budget = 1200
    args = RetrieveKnowledge(source_ids=["paper"], queries=["Conditions?"])
    first = await session.retrieve(args)
    page = first.content["sources"][0]
    assert 0 < len(session.read["paper"]) < 80
    found = list(page["returned_ids"])
    while page["continuation"]:
        page = session.reading.read(
            BrowseSelection(source_id="paper", continuation=page["continuation"]),
            **await session.material("paper"),
        )
        session.record_context_read(page)
        found.extend(page["returned_ids"])
    assert found == [p.passage_id for p in doc.passages]
    assert len(calls) == 1
    for _ in range(2):
        session.before_batch()
        assert (await session.retrieve(args)).content["reused"]
        session.after_batch(True)
    assert session.state.stop_reason == "no_new_readable_information"
    assert len(calls) == 1
    await session.retrieve(args.model_copy(update={"queries": ["Different conditions?"]}))
    doc.passages[0].text = "Changed source version"
    await session.retrieve(args)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_runtime_checkpoints_history_and_formal_uses_scientific_context():
    from pydantic import BaseModel

    from spacebio_review.agents import AgentRuntime
    from spacebio_review.agents.types import AgentTask, ContextView, RoleProfile
    from spacebio_review.models.config import load_config
    from spacebio_review.tools import Tool, ToolRegistry, ToolResult
    from spacebio_review.v4.contracts import professional_contract

    requests = []
    quotes = {
        f"ev-{i}": evidence(f"p{i}", f"Condition {i}. " + "Evidence text. " * 30) for i in range(60)
    }
    contract = professional_contract(None, {}, {"q1": {}})

    class Empty(BaseModel):
        pass

    async def read(args):
        contract.task_context.add(quotes, "E")
        return ToolResult(
            ok=True,
            content={
                "discovery_marker": "OLD_DISCOVERY_TRANSCRIPT",
                "evidence": quotes,
                "answer": {
                    "findings": [{"text": "DELEGATED_FINDING"}],
                    "applicability": ["DELEGATED_SCOPE: only at 37 C"],
                    "unknowns": ["Other temperatures untested"],
                },
            },
        )

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        step = len(requests)
        if step <= 3:
            name, args = [
                ("read_fixture", {}),
                (
                    "record_memory",
                    {
                        "memory": {
                            "current_claims": ["Only conditionally [@E1]"],
                            "counterevidence": ["Exception [@E2]"],
                            "unresolved": ["Other conditions unverified"],
                            "active_refs": ["E1", "E2"],
                        }
                    },
                ),
                ("submit_result", {"reason": "Scoped answer ready", "unfinished": []}),
            ][step - 1]
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call{step}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ],
            }
        else:
            message = {
                "role": "assistant",
                "content": "title: Conditions\nscope_note: Conditions remain local\nblocks:\n"
                "  - text: Only conditionally [@E1]; counterevidence [@E2].\nscope_outcomes:\n"
                "  - member_id: q1\n    status: analyzed\n    explanation: Scoped answer\n"
                "    block_indices: [1]\nfollowups: []\n",
            }
        return httpx.Response(
            200,
            json={
                "id": "fixture",
                "object": "chat.completion",
                "created": 0,
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls" if step <= 3 else "stop",
                        "message": message,
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
            },
        )

    def factory(config):
        return offline_model(config, respond)

    runtime = AgentRuntime.from_config(load_config("config/runtime.yaml"), model_factory=factory)
    task = AgentTask(
        task_id="local-context",
        phase="local",
        objective="Compare conditions",
        expected_product="professional",
        mode="research",
        max_model_turns=8,
        max_repairs=0,
        context_soft_tokens=12000,
    )
    try:
        result = await runtime.run_task(
            task,
            RoleProfile(
                agent_id=task.task_id, responsibility="Compare", allowed_tools=["read_fixture"]
            ),
            ContextView(agent_id=task.task_id, phase=task.phase),
            ToolRegistry([Tool("read_fixture", "Read fixture", Empty, read)]),
            contract,
        )
    finally:
        await runtime.close()
    assert result.status == "completed", result.reason
    assert len(requests) == 4
    assert {s["function"]["name"] for s in requests[1]["tools"]} == {
        "record_memory",
        "submit_result",
    }
    assert len(contract.task_context.mapping()) == 60
    formal = json.dumps(requests[-1]["messages"], ensure_ascii=False)
    assert "OLD_DISCOVERY_TRANSCRIPT" not in formal
    assert "Only conditionally" in formal and "Exception" in formal
    assert "Condition 59." not in formal
    assert formal.count("DELEGATED_SCOPE: only at 37 C") == 1
    sizes = [
        TokenEstimator().measure(p["messages"], p.get("tools", []))["input_tokens"]
        for p in requests
    ]
    assert sizes[2] < sizes[1] * 0.65
    assert sizes[-1] < task.context_soft_tokens
    assert "YAML" in requests[-1]["messages"][-1]["content"]
    assert "task_sources" in requests[-1]["messages"][-2]["content"]


def test_shared_research_calls_protect_both_submissions_and_release_unused_reserve():
    from spacebio_review.models.provider import ModelError
    from spacebio_review.models.scheduling import RequestScheduler

    scheduler = RequestScheduler()
    with scheduler.research_calls("parent", 10, 2) as pool:
        pool.claim("parent")
        with scheduler.research_calls("child", 18, 2):
            assert pool.limit == 10
            for _ in range(5):
                pool.claim("child")
            with pytest.raises(ModelError, match="shared_research_completion_reserve"):
                pool.claim("child")
            pool.claim("child", formal=True)
        assert pool.remaining == 3
        pool.claim("parent")
        pool.claim("parent", formal=True)
        pool.claim("parent", formal=True)
        assert pool.remaining == 0
    assert scheduler.research_pool.get() is None


def test_completion_only_task_can_use_its_existing_small_budget():
    from spacebio_review.models.scheduling import RequestScheduler

    scheduler = RequestScheduler()
    with scheduler.research_calls("short", 3, 3) as pool:
        assert pool.exploration_reason()
        pool.claim("short", formal=True)
        assert pool.remaining == 2


@pytest.mark.asyncio
async def test_delegation_reserves_child_completion_before_its_query_plan(monkeypatch):
    from spacebio_review.evidence.literature import LiteratureResearcher
    from spacebio_review.models.scheduling import RequestScheduler

    scheduler = RequestScheduler()
    researcher = LiteratureResearcher.__new__(LiteratureResearcher)
    researcher.runtime = SimpleNamespace(
        scheduler=scheduler,
        config=SimpleNamespace(
            task_routes={"literature": SimpleNamespace(max_truncation_recoveries=1)}
        ),
    )
    researcher.config = SimpleNamespace(max_model_turns=18)

    async def scoped(question, context, key, **kwargs):
        pool = scheduler.research_pool.get()
        assert pool.reserves == {"parent": 2, key: 3}
        pool.claim(key)  # query plan is inside the same pool
        with scheduler.research_calls(key, 18, 3):
            assert pool.reserves[key] == 3
            pool.claim(key)
        assert pool.reserves[key] == 3  # the outer delegated scope owns the reserve
        return "answer"

    monkeypatch.setattr(researcher, "_investigate_scoped", scoped)
    with scheduler.research_calls("parent", 10, 2) as pool:
        assert await researcher._investigate(None, None) == "answer"
        assert pool.reserves == {"parent": 2}
        assert pool.remaining == 8


def test_literature_aliases_restore_to_immutable_evidence_and_scientific_schema():
    from spacebio_review.evidence.knowledge import KnowledgeAnswer
    from spacebio_review.evidence.submissions import scientific_contract

    entries = {
        "original-handle": evidence("p1", "Only at 37 C"),
        "counter-handle": evidence("p2", "Other condition"),
    }
    scope = SimpleNamespace(submission_choices=lambda: entries)
    session = SimpleNamespace(
        question=SimpleNamespace(assessment_focus=""), state=SimpleNamespace(stop_reason="")
    )
    contract = scientific_contract(scope, KnowledgeAnswer, session=session)
    draft = contract.draft_type.model_validate(
        {
            "findings": [
                {
                    "explanation": "Conditional finding [@E1]",
                    "basis": [
                        {
                            "evidence_handle": "E1",
                            "text": "Only at 37 C [@E1]",
                            "support_explanation": "Local condition",
                            "quantitative_basis": None,
                        }
                    ],
                }
            ],
            "applicability": ["Not transferable [@E2]"],
            "open_questions": ["Other temperatures?"],
        }
    )
    result = KnowledgeAnswer.model_validate(contract.assemble(draft))
    assert set(result.evidence_resolution) == {"original-handle", "counter-handle"}
    assert "[@original-handle]" in result.findings[0].explanation
    assert result.findings[0].basis[0].source_refs[0].passage_id == "p1"


@pytest.mark.asyncio
async def test_parent_plan_rerank_and_child_share_logical_calls_without_double_billing():
    from spacebio_review.models.scheduling import RequestScheduler, ScheduledModel

    class Model:
        config = SimpleNamespace(model="fake")

        async def complete(self, *args, **kwargs):
            return SimpleNamespace(prompt_tokens=10, completion_tokens=2)

    scheduler = RequestScheduler()
    model = ScheduledModel(Model(), scheduler, "fake")
    with scheduler.research_calls("parent", 8, 2) as pool:
        await model.complete([])
        await model.complete([])  # delegated query plan
        with scheduler.research_calls("child", 18, 2):
            await model.complete([])
            await model.complete([])  # child rerank
            assert pool.exploration_reason()
            with scheduler.formal_call():
                await model.complete([])
        with scheduler.formal_call():
            await model.complete([])
        assert pool.remaining == 2
    assert len(scheduler.records) == 6
    assert sum(r["prompt_tokens"] for r in scheduler.records) == 60


def evidence(pid, text, source="paper"):
    return {
        "reference": {"source_id": source, "passage_id": pid, "visual_id": None},
        "title": "A paper with conditions",
        "kind": "external",
        "locator": pid,
        "quote": text,
        "url": "https://example.org/paper",
        "material_version": "v1",
        "reading_scope": "text",
    }


def strings(value):
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return [value] if isinstance(value, str) else []


def decoded(messages):
    return [json.loads(m["content"]) for m in messages]


def test_one_copy_of_original_across_reading_delegation_and_directory():
    quote = "Only at 37 C; not established at room temperature."
    record = evidence("p1", quote)
    context = TaskContext(compact_sources=True)
    context.add({"evidence-1": record}, "E")
    before = context.mapping()
    messages = [
        {
            "role": "tool",
            "content": json.dumps(
                {
                    "source_id": "paper",
                    "passages": [
                        {"passage_id": "p1", "text": quote, "evidence_handle": "evidence-1"}
                    ],
                }
            ),
        },
        {
            "role": "tool",
            "content": json.dumps(
                {
                    "answer": {
                        "findings": [
                            {"text": "A conditional result", "evidence_handle": "evidence-1"}
                        ],
                        "evidence_resolution": {"evidence-1": record},
                    }
                }
            ),
        },
    ]
    rendered = decoded(context.messages(messages))
    assert strings(rendered).count(quote) == 1
    assert context.mapping() == before
    assert "E1" in strings(rendered)


def test_source_metadata_is_shared_but_passage_conditions_are_not_summarized():
    context = TaskContext(compact_sources=True)
    for i in range(20):
        context.add(
            {f"ev-{i}": evidence(f"p{i}", f"Complete condition {i}; no extrapolation.")}, "E"
        )
    view = context.source_view()
    assert strings(view).count("https://example.org/paper") == 1
    for i in range(20):
        assert strings(view).count(f"Complete condition {i}; no extrapolation.") == 1


def test_reading_window_accounts_for_wire_payload_and_continues_without_loss():
    rows = [
        {"passage_id": f"p{i}", "section": "Methods", "text": "Full condition " * 25}
        for i in range(80)
    ]
    window = ReadingWindow(budget=1200)
    selection = PassageSelection(source_id="paper", passage_ids=[p["passage_id"] for p in rows])
    found = []
    while True:
        page = window.read(selection, passages=rows)
        size = TokenEstimator().measure(
            [{"role": "tool", "content": json.dumps(page, ensure_ascii=False)}]
        )["input_tokens"]
        assert size <= window.budget
        found.extend(page["passages"])
        if not page["continuation"]:
            break
        selection = BrowseSelection(source_id="paper", continuation=page["continuation"])
    assert found == rows


def test_repeated_section_heading_does_not_select_other_equipment():
    rows = [
        {"passage_id": "p1", "section": "Interfaces", "text": "Biology equipment"},
        {"passage_id": "p2", "section": "Other equipment", "text": "An unrelated device"},
        {"passage_id": "p3", "section": "Interfaces", "text": "Unrelated device interface"},
    ]
    page = ReadingWindow().read(
        BrowseSelection(source_id="paper", section="Interfaces"), passages=rows
    )
    assert page["returned_ids"] == ["p1"]
    page = ReadingWindow().read(
        BrowseSelection(source_id="paper", section="Interfaces", start_passage_id="p3"),
        passages=rows,
    )
    assert page["returned_ids"] == ["p3"]


def test_indivisible_passage_is_complete_and_isolated_even_over_soft_reading_window():
    rows = [
        {"passage_id": "p1", "text": "Whole condition " * 500},
        {"passage_id": "p2", "text": "Next experiment"},
    ]
    window = ReadingWindow(budget=500)
    page = window.read(PassageSelection(source_id="paper", passage_ids=["p1", "p2"]), passages=rows)
    assert page["passages"] == rows[:1]
    assert page["continuation"]
    rest = window.read(
        BrowseSelection(source_id="paper", continuation=page["continuation"]), passages=rows
    )
    assert rest["passages"] == rows[1:]


def test_retained_q14_all_quotes_survive_compact_presentation():
    path = Path(
        "runs/mytest18/v4/receipt-017ddc5f68188abc330a6336883264b54feaefc782e4cf0285e50fc589022b1f.json"
    )
    if not path.exists():
        import pytest

        pytest.skip("retained run is local test data")
    receipt = json.loads(path.read_text(encoding="utf-8"))["value"]
    full, compact = TaskContext(), TaskContext(compact_sources=True)
    for alias, item in receipt["mapping"].items():
        for context in (full, compact):
            context.add({item["identity"]: item["value"]}, alias[0])
    view = compact.source_view()
    for record in compact.evidence.values():
        assert record["quote"] in strings(view)
    assert compact.mapping() == full.mapping()

    def estimate(value):
        return TokenEstimator().measure(
            [{"role": "user", "content": json.dumps(value, ensure_ascii=False)}]
        )["input_tokens"]

    assert estimate(view) < estimate(full.source_view()) * 0.6
