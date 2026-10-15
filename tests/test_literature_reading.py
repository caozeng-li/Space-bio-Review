"""Purpose-led opening, actual read scope, and one scoped image interpretation."""

import base64
import hashlib
import json
from types import SimpleNamespace

import httpx

from spacebio_review.agents.types import TaskResult
from spacebio_review.evidence.knowledge import AcquireSource, KnowledgeQuestion, LiteratureConfig
from spacebio_review.evidence.literature import KnowledgeSession, literature_task
from spacebio_review.evidence.session import VisualRequest
from spacebio_review.evidence.store import SourceStore
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument, Visual


def document():
    return SourceDocument(
        source_id="paper",
        candidate=SourceCandidate(title="Microscopy conditions"),
        availability="full_text",
        passages=[
            Passage(
                passage_id="p1",
                unit_id="u1",
                locator="Methods / p1",
                section="Methods",
                text="The 20x objective had numerical aperture 0.4.",
            ),
            Passage(
                passage_id="p2",
                unit_id="u1",
                locator="Methods / p2",
                section="Methods",
                text="The 0.84 micrometer estimate applies at a wavelength of 550 nm.",
            ),
            Passage(
                passage_id="p3",
                locator="Results / p3",
                section="Results",
                text="Results from a different instrument are described separately.",
            ),
        ],
    )


class MemoryStore:
    def __init__(self, doc):
        self.doc, self.acquisitions = doc, []

    def candidate(self, sid):
        assert sid == self.doc.source_id
        return self.doc.candidate

    def retained(self, sid):
        return self.doc if sid == self.doc.source_id else None

    async def acquire(self, sid, *, material_id=None, cutoff=None):
        self.acquisitions.append((sid, material_id, cutoff))
        return self.doc


def session_for(store, *, source_id="paper", actions=12, run_task=None):
    runtime = SimpleNamespace(models={"task/rerank/normal": None}, run_task=run_task)
    config = LiteratureConfig(max_actions=actions)
    researcher = SimpleNamespace(
        store=store, discovery=None, index=None, rerank=False, runtime=runtime, config=config
    )
    return KnowledgeSession(
        researcher,
        KnowledgeQuestion(
            question="Which optical conditions support the reported resolution?",
            existing_source_ids=[source_id],
        ),
    )


def select_first_passage(monkeypatch):
    calls = []

    async def find(docs, queries, model, **kwargs):
        calls.append((docs, queries))
        return {
            "hits": [{"source_id": docs[0].source_id, "passage": docs[0].passages[0].model_dump()}],
            "channels": {},
            "limitations": [],
        }

    monkeypatch.setattr("spacebio_review.evidence.literature.find_passages", find)
    return calls


async def test_first_purpose_open_delivers_original_complete_condition_and_records_only_it(
    monkeypatch,
):
    store = MemoryStore(document())
    session = session_for(store)
    calls = select_first_passage(monkeypatch)
    result = await session.acquire_source(
        AcquireSource(
            source_id="paper",
            reading_purpose="Read the optical conditions for the resolution estimate.",
        )
    )
    assert result.ok and result.reading_scope == "selected_passages"
    assert (
        len(store.acquisitions) == 1
    )  # Acquiring and purpose-reading share the acquired document.
    assert len(calls) == 1
    assert calls[0][1] == ["Read the optical conditions for the resolution estimate."]
    page = result.content["reading"]["sources"][0]
    assert page["returned_ids"] == ["p1", "p2"]
    assert [p["text"] for p in page["passages"]] == [p.text for p in store.doc.passages[:2]]
    assert session.read_locations() == {("paper", "p1", None), ("paper", "p2", None)}
    snapshots = session.references.snapshot_choices()
    assert {s["quote"] for s in snapshots.values()} == {p.text for p in store.doc.passages[:2]}
    assert "Results" in result.content["sections"]
    assert ("paper", "p3", None) not in session.read_locations()


async def test_directory_open_exposes_navigation_without_claiming_fulltext_was_read():
    store = MemoryStore(document())
    session = session_for(store)
    result = await session.acquire_source(AcquireSource(source_id="paper"))
    assert result.ok and result.content["availability"] == "full_text"
    assert result.content["total_passages"] == 3
    assert "reading" not in result.content
    assert result.content["passage_directory"]
    assert session.read_locations() == set()
    assert session.references.snapshot_choices() == {}


async def test_selected_supplement_is_fetched_once_and_read_from_the_same_acquisition(
    tmp_path, monkeypatch
):
    requested = []

    def respond(request):
        requested.append(request.url.path)
        if request.url.path == "/paper.xml":
            return httpx.Response(
                200,
                headers={"content-type": "application/xml"},
                text="""
              <article xmlns:xlink="http://www.w3.org/1999/xlink"><body><sec>
              <title>Results</title><p>Main results.</p></sec></body><back>
              <supplementary-material xlink:href="https://example.org/methods.txt">
              <label>Supplementary methods</label></supplementary-material></back></article>""",
            )
        assert request.url.path == "/methods.txt"
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="Calibrated NA 0.4, wavelength 550 nm, estimated resolution 0.84 um.",
        )

    async def find(docs, queries, model, **kwargs):
        selected = next(p for p in docs[0].passages if "Calibrated NA" in p.text)
        return {
            "hits": [{"source_id": docs[0].source_id, "passage": selected.model_dump()}],
            "channels": {},
            "limitations": [],
        }

    monkeypatch.setattr("spacebio_review.evidence.literature.find_passages", find)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        store = SourceStore(tmp_path, client, browser_fallback=False)
        sid = store.register(SourceCandidate(title="Paper", url="https://example.org/paper.xml"))
        session = session_for(store, source_id=sid)
        directory = await session.acquire_source(AcquireSource(source_id=sid))
        material = next(m for m in directory.content["materials"] if m["role"] == "supplement")
        calls, acquire = [], store.acquire

        async def tracked(sid, **kwargs):
            calls.append(kwargs)
            return await acquire(sid, **kwargs)

        monkeypatch.setattr(store, "acquire", tracked)
        request = AcquireSource(
            source_id=sid,
            material_id=material["material_id"],
            reading_purpose="Which NA and wavelength support the resolution?",
        )
        result = await session.acquire_source(request)
        assert result.ok and requested == ["/paper.xml", "/methods.txt"]
        assert len(calls) == 1 and calls[0]["material_id"] == material["material_id"]
        page = result.content["reading"]["sources"][0]
        assert len(page["passages"]) == 1
        assert "methods.txt" in page["passages"][0]["locator"]
        assert "0.84" in page["passages"][0]["text"]
        again = await session.acquire_source(request)
        assert again.content["reading"]["reused"]
        assert requested == ["/paper.xml", "/methods.txt"]


async def test_visual_receives_pixels_question_and_caption_before_becoming_read_evidence():
    store = MemoryStore(document())
    pixels = b"the-provided-original-pixels"
    store.doc.visuals = [Visual(visual_id="fig1", locator="Figure 1B", caption="Scale bar: 1 um.")]
    actual_pixels, tasks = [], []

    async def visual_bytes(sid, visual):
        actual_pixels.append((sid, visual.visual_id))
        return pixels, "image/png"

    async def run_task(task, profile, view, tools, answer_type):
        tasks.append(task)
        assert task.objective == "What is the scale bar and what objects are shown?"
        assert task.max_model_turns == 1 and task.max_repairs == 0
        assert "Which optical conditions" in view.materials[0].text
        assert "Figure 1B" in view.materials[0].text
        assert "Scale bar: 1 um." in view.materials[0].text
        encoded = view.materials[0].image_urls[0]
        assert encoded.startswith("data:image/png;base64,")
        assert base64.b64decode(encoded.split(",", 1)[1]) == pixels
        return TaskResult(
            task_id=task.task_id,
            agent_id=profile.agent_id,
            status="completed",
            reason="done",
            product={"readability": "readable", "interpretation": "Visible scale bar: 1 um."},
        )

    store.visual_bytes = visual_bytes
    session = session_for(store, run_task=run_task)
    await session.acquire_source(AcquireSource(source_id="paper"))
    assert not session.references.snapshot_choices()  # A displayed caption is not an image read.
    result = await session.visual(
        VisualRequest(
            source_id="paper",
            visual_id="fig1",
            question="What is the scale bar and what objects are shown?",
        )
    )
    assert result.ok and len(tasks) == 1
    assert actual_pixels == [("paper", "fig1")]
    assert session.read_locations() == {("paper", None, "fig1")}
    snapshot = next(iter(session.references.snapshot_choices().values()))
    assert snapshot["reading_scope"] == "interpreted_visual"
    assert snapshot["image_digest"] == hashlib.sha256(pixels).hexdigest()


async def test_two_directory_batches_allow_subsequent_read_and_full_action_budget(monkeypatch):
    store = MemoryStore(document())
    session = session_for(store, actions=3)
    select_first_passage(monkeypatch)
    tools = session.tools()
    task, _ = literature_task("reading-fixture", session.researcher.config)
    assert task.max_actions == 3
    for _ in range(2):
        session.before_batch()
        result = await tools.execute("acquire_source", json.dumps({"source_id": "paper"}))
        session.after_batch(True)
        assert result.ok and not session.read_locations()
    assert session.state.stop_reason == ""
    result = await tools.execute(
        "acquire_source",
        json.dumps({"source_id": "paper", "reading_purpose": "Read the resolution conditions."}),
    )
    assert result.ok and session.read_locations()
    assert session.actions == 3 and len(store.acquisitions) == 3
    exhausted = await tools.execute("acquire_source", json.dumps({"source_id": "paper"}))
    assert not exhausted.ok and exhausted.error == "research_closing"
    assert session.state.stop_reason == "action_budget_exhausted"
    assert len(store.acquisitions) == 3
