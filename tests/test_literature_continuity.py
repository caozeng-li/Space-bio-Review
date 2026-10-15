"""Continue saved research with its originals, without inheriting unread evidence."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from spacebio_review.agents.reading import PassageSelection
from spacebio_review.agents.skills import SkillLibrary
from spacebio_review.agents.types import TaskResult
from spacebio_review.evidence.knowledge import KnowledgeQuestion
from spacebio_review.evidence.literature import LiteratureResearcher
from spacebio_review.evidence.research import EvidenceConfig, EvidenceResources
from spacebio_review.evidence.store import SourceStore
from spacebio_review.models.config import load_config
from spacebio_review.models.scheduling import RequestScheduler


@pytest.fixture
def offline_http(monkeypatch):
    requests = []
    client_type = httpx.AsyncClient

    def no_network(request):
        requests.append(request)
        raise AssertionError("Retained project research must not perform HTTP acquisition")

    def client(**kwargs):
        return client_type(transport=httpx.MockTransport(no_network), trust_env=False, **kwargs)

    monkeypatch.setattr("spacebio_review.evidence.research.httpx.AsyncClient", client)
    return requests


def runtime_for(root, handler):
    return SimpleNamespace(
        config=load_config("config/runtime.yaml"),
        skills=SkillLibrary(Path("skills")),
        scheduler=RequestScheduler(),
        models={"task/rerank/normal": None},
        recorder=SimpleNamespace(root=root),
        run_task=handler,
    )


def researcher_for(runtime, store):
    return LiteratureResearcher(
        runtime,
        store,
        SimpleNamespace(config=SimpleNamespace(enabled=[])),
        rerank=False,
    )


async def local_paper(store, root):
    path = root / "calibration.md"
    path.write_text(
        "# Methods\n\n"
        "Calibration used fixed cells at 37 C with a 20x objective.\n\n"
        "# Results\n\n"
        "The measured error was 0.8 micrometers; only fixed cells were measured.\n\n"
        "# Discussion\n\n"
        "Transfer to live cells or repeated freeze-thaw cycles was not studied.",
        encoding="utf-8",
    )
    sid = await store.add_local(path, kind="literature", title="Calibration microscopy")
    doc = store.retained(sid)
    assert len(doc.passages) == 3
    return sid, doc


async def read_originals(tools, sid, passage_ids):
    material = await tools.material_providers[0](sid)
    payload = tools.reading_windows[0].read(
        PassageSelection(source_id=sid, passage_ids=passage_ids),
        passages=material["passages"],
    )
    tools.record_read(payload)
    assert {p["passage_id"] for p in payload["passages"]} == set(passage_ids)
    return payload


def finish(task, profile, tools, contract, sid, pid, text):
    contract.refresh(contract)
    originals = tools.inquiry.references.submission_choices()
    handle = next(
        handle
        for handle, item in originals.items()
        if item["reference"]["source_id"] == sid and item["reference"]["passage_id"] == pid
    )
    draft = contract.draft_type.model_validate(
        {
            "findings": [
                {
                    "paragraphs": [
                        {
                            "parts": [
                                {
                                    "text": text,
                                    "cites": [contract.task_context.aliases[handle]],
                                }
                            ]
                        }
                    ]
                }
            ],
            "scope": None,
            "unknowns": ["Applicability to live cells remains unknown."],
        }
    )
    return TaskResult(
        task_id=task.task_id,
        agent_id=profile.agent_id,
        status="completed",
        reason="research_complete",
        product=contract.assemble(draft),
    )


async def test_saved_history_continues_originals_and_new_question_after_restart(
    tmp_path, offline_http
):
    observed = []

    async def run_task(task, profile, view, tools, contract):
        session = tools.inquiry
        observed.append((view, set(session.read_locations())))
        if not session.question.previous_research_id:
            await read_originals(tools, sid, pids)
            chosen = pids[0]
        else:
            assert session.read_locations() == {(sid, pids[0], None)}
            assert set(session.references.submission_choices()) == set(
                first.answer.evidence_resolution
            )
            await read_originals(tools, sid, [pids[1]])
            chosen = pids[1]
        return finish(task, profile, tools, contract, sid, chosen, "Fixed cells only.")

    runtime = runtime_for(tmp_path, run_task)
    async with httpx.AsyncClient() as client:
        store = SourceStore(tmp_path / "sources", client, browser_fallback=False)
        sid, original = await local_paper(store, tmp_path)
        pids = [p.passage_id for p in original.passages]
        researcher = researcher_for(runtime, store)
        first = await researcher.investigate(
            KnowledgeQuestion(
                question="What calibration conditions were measured?",
                conditions=["fixed cells"],
                existing_source_ids=[sid],
            )
        )
    assert first.study_id == first.research_id
    assert first.previous_research_id is None
    assert first.sources[sid].passage_ids == pids
    assert len(first.answer.evidence_resolution) == 1

    # A new resources/researcher instance has no old in-memory documents or answer cache.
    restored_runtime = runtime_for(tmp_path, run_task)
    resources = EvidenceResources.create(
        restored_runtime, tmp_path, EvidenceConfig(model_rerank=False, browser_fallback=False)
    )
    restored = LiteratureResearcher(
        restored_runtime, resources.store, resources.discovery, rerank=False
    )
    try:
        assert resources.store.retained(sid).model_dump() == original.model_dump()
        saved = restored.history(first.research_id)
        assert [run.research_id for run in saved] == [first.research_id]
        assert saved[0].answer.model_dump() == first.answer.model_dump()
        question = KnowledgeQuestion(
            question="What error was observed, and under what conditions?",
            previous_research_id=first.research_id,
        )
        second = await restored.investigate(question)
        assert question.existing_source_ids == []  # Caller-owned question remains unchanged.
        assert second.question.question == question.question
        assert second.question.existing_source_ids == [sid]
        assert second.research_id != first.research_id
        assert second.study_id == first.study_id
        assert second.previous_research_id == first.research_id
        assert second.sources[sid].passage_ids == pids[:2]
        assert [
            item["reference"]["passage_id"] for item in second.answer.evidence_resolution.values()
        ] == [pids[1]]
        inherited = next(
            m.data for m in observed[1][0].materials if m.source_id == first.research_id
        )
        assert inherited["answer"] == first.answer_view()
        assert inherited["question"]["conditions"] == ["fixed cells"]
        assert inherited["reading_records"][sid]["passage_ids"] == pids
        assert inherited["materials"][0]["sections"] == ["Methods", "Results", "Discussion"]
        delivered_quote = next(iter(first.answer.evidence_resolution.values()))["quote"]
        assert delivered_quote == original.passages[0].text
        assert delivered_quote in json.dumps(inherited)
        assert original.passages[2].text not in json.dumps(inherited)
        assert [r.research_id for r in restored.history(second.research_id)] == [
            first.research_id,
            second.research_id,
        ]
        fresh = await restored.investigate(
            KnowledgeQuestion(question="A separate calibration question", existing_source_ids=[sid])
        )
        assert fresh.study_id == fresh.research_id != first.study_id
        assert fresh.previous_research_id is None
        for run in (first, second, fresh):
            folder = tmp_path / "knowledge" / run.research_id
            assert {p.name for p in folder.iterdir()} >= {
                "answer.json",
                "metadata-readings.json",
                "evidence-readings.json",
            }
    finally:
        await resources.close()
    assert offline_http == []


@pytest.mark.parametrize("query", ["calibration", "freeze-thaw", "37"])
async def test_project_search_reuses_saved_answer_and_unmodified_source_identity(
    tmp_path, offline_http, query
):
    async def run_task(task, profile, view, tools, contract):
        await read_originals(tools, sid, pids)
        return finish(
            task, profile, tools, contract, sid, pids[0], "Measured in fixed cells at 37 C."
        )

    runtime = runtime_for(tmp_path, run_task)
    async with httpx.AsyncClient() as client:
        store = SourceStore(tmp_path / "sources", client, browser_fallback=False)
        sid, original = await local_paper(store, tmp_path)
        pids = [p.passage_id for p in original.passages]
        application = tmp_path / "application.md"
        application.write_text("Calibration application", encoding="utf-8")
        app_id = await store.add_local(application)
        first = await researcher_for(runtime, store).investigate(
            KnowledgeQuestion(
                question="Can calibration evidence transfer to freeze-thaw?",
                existing_source_ids=[sid],
            )
        )
    resources = EvidenceResources.create(runtime_for(tmp_path, run_task), tmp_path)
    try:
        researcher = LiteratureResearcher(
            resources.runtime, resources.store, resources.discovery, rerank=False
        )
        session = researcher.new_session(KnowledgeQuestion(question="Reuse calibration evidence"))
        tools = session.tools()
        result = await tools.execute(
            "search_sources",
            json.dumps({"queries": [{"provider": "project", "query": query}]}),
        )
        assert result.ok, result.error
        assert result.source_ids == [sid]
        assert [row["source_id"] for row in result.content["candidates"]] == [sid]
        assert set(row["source_id"] for row in resources.store.catalogue()) == {sid, app_id}
        existing = result.content["existing_research"]
        assert len(existing) == 1
        assert existing[0]["research_id"] == first.research_id
        assert existing[0]["answer"]["findings"] == first.answer_view()["findings"]
        for handle, original_item in first.answer.evidence_resolution.items():
            delivered = existing[0]["answer"]["evidence_resolution"][handle]
            assert delivered["quote"] == original_item["quote"]
            assert delivered["material_version"] == original_item["material_version"]
            assert delivered["reference"]["source_id"] == sid
            assert delivered["reference"]["passage_id"] == pids[0]
        assert existing[0]["reading_records"][sid]["passage_ids"] == pids
        assert session.read_locations() == {(sid, pids[0], None)}
        assert session.references.submission_choices() == first.answer.evidence_resolution
        assert result.content["candidates"][0]["retained_material"]["availability"] == "full_text"
        reread = await read_originals(tools, sid, [pids[1]])
        assert reread["passages"][0]["text"] == original.passages[1].text
        assert session.read_locations() == {(sid, pid, None) for pid in pids[:2]}
    finally:
        await resources.close()
    assert offline_http == []


async def test_continued_attempts_consume_one_parent_research_budget(tmp_path, offline_http):
    pools = []

    async def run_task(task, profile, view, tools, contract):
        pool = runtime.scheduler.research_pool.get()
        pools.append(pool)
        pool.claim(task.task_id)
        await read_originals(tools, sid, [pid])
        pool.claim(task.task_id, formal=True)
        return finish(task, profile, tools, contract, sid, pid, "Fixed cells at 37 C.")

    runtime = runtime_for(tmp_path, run_task)
    async with httpx.AsyncClient() as client:
        store = SourceStore(tmp_path / "sources", client, browser_fallback=False)
        sid, original = await local_paper(store, tmp_path)
        pid = original.passages[0].passage_id
        researcher = researcher_for(runtime, store)
        with runtime.scheduler.research_calls("parent-expert", 8, 2) as shared:
            shared.claim("parent-expert")
            first = await researcher.investigate(
                KnowledgeQuestion(question="Conditions?", existing_source_ids=[sid])
            )
            assert shared.remaining == 5
            second = await researcher.investigate(
                KnowledgeQuestion(
                    question="Clarify conditions?", previous_research_id=first.research_id
                )
            )
            assert shared.remaining == 3
            assert shared.limit == 8
            assert shared.reserves == {"parent-expert": 2}
            assert all(pool is shared for pool in pools)
            assert second.study_id == first.study_id
    assert offline_http == []
