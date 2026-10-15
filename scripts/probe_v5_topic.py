"""Exercise the real v5 topic entry with fixed sources; no live models or review pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_session import (  # noqa: E402
    ScriptModel,
    assert_private_not_saved,
    note,
    reply,
    require_content,
    runtime,
    tool_call,
)

from spacebio_review.agents.skills import SkillLibrary  # noqa: E402
from spacebio_review.evidence.discovery import DiscoveryResult  # noqa: E402
from spacebio_review.evidence.store import SourceStore  # noqa: E402
from spacebio_review.evidence.types import SourceCandidate  # noqa: E402
from spacebio_review.models.provider import ChatReply, ModelError  # noqa: E402
from spacebio_review.persistence import plain, write_json  # noqa: E402
from spacebio_review.v5 import SessionSettings  # noqa: E402
from spacebio_review.v5.sources import VisualReader  # noqa: E402
from spacebio_review.v5.topic import TopicResearch, TopicTask  # noqa: E402

BASE_URL = "https://fixture.invalid/"
APPLICATION = """# 固定计划书（仅供流程验证）

本项目使用 Huh7，比较在轨与地面对照。培养方案尚未给出血清类型。
研究终点包括细胞内甘油三酯，不直接测定正常人肝细胞的生理功能。
"""
METHOD = (
    "The fixed experiment compared Huh7 with HepG2 in 2% human serum or 10% fetal bovine serum."
)
RESULT = "In this fixed experiment, Huh7 had higher intracellular TAG than HepG2 under both sera."
SUPPLEMENT = "Supplementary calibration used independent standards for the intracellular TAG assay."
VISUAL = "The fixed figure compares Huh7 and HepG2 under the two stated serum conditions."
ABSTRACT = "Fixed offline Huh7 HepG2 serum comparison: synthetic data for runtime checks only."
FOLLOWUP_SOURCE = (
    "The follow-up source B describes a different assay; its relevance remains unsettled."
)
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c02"
    "0000000b4944415478da6364f80f00010501012718e3660000000049454e44ae426082"
)


def source_xml():
    paragraphs = "".join(
        f"<p>{METHOD} Method segment {i}: "
        + "Serum exposure and culture duration belong to the comparison conditions. " * 6
        + "</p>"
        for i in range(1, 7)
    )
    return (
        '<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>'
        "<title-group><article-title>Fixed Huh7 HepG2 serum experiment</article-title>"
        f"</title-group><abstract><p>{ABSTRACT}</p></abstract></article-meta></front>"
        f"<body><sec><title>Methods</title>{paragraphs}</sec>"
        f"<sec><title>Results</title><p>{RESULT}</p>"
        '<fig id="figure-1"><caption><p>Huh7 and HepG2 TAG comparison.</p></caption>'
        f'<graphic xlink:href="{BASE_URL}figure.png"/></fig></sec>'
        '<supplementary-material id="supplement-1"><label>Supplementary calibration</label>'
        f'<media xlink:href="{BASE_URL}supplement.xml"/></supplementary-material>'
        "</body></article>"
    ).encode()


class FixedDiscovery:
    """Replace only the search provider; source ranking and acquisition stay real."""

    def __init__(self, candidate):
        self.candidate = candidate
        self.queries = []

    async def search(self, query, provider, cutoff, limit=6):
        self.queries.append({"query": query, "provider": provider})
        return DiscoveryResult(candidates=[self.candidate], channels={provider: "fixed_fixture"})


class Fixture:
    def __init__(self, root):
        self.root = root
        self.requests, self.visual_requests = [], []
        self.candidate = SourceCandidate(
            title="Fixed Huh7 HepG2 serum experiment",
            url=BASE_URL + "article.xml",
            fulltext_urls=[BASE_URL + "article.xml"],
            abstract=ABSTRACT,
        )
        self.discovery = FixedDiscovery(self.candidate)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.respond))
        self.store = SourceStore(root / "sources", self.client, browser_fallback=False)

    def respond(self, request):
        self.requests.append(str(request.url))
        if request.url.path == "/article.xml":
            return httpx.Response(200, content=source_xml(), headers={"content-type": "text/xml"})
        if request.url.path == "/supplement.xml":
            return httpx.Response(
                200,
                content=(
                    "<article><body><sec><title>Calibration</title>"
                    f"<p>{SUPPLEMENT}</p></sec></body></article>"
                ).encode(),
                headers={"content-type": "text/xml"},
            )
        if request.url.path == "/followup.xml":
            return httpx.Response(
                200,
                content=(
                    "<article><body><sec><title>Follow-up assay</title>"
                    f"<p>{FOLLOWUP_SOURCE}</p></sec></body></article>"
                ).encode(),
                headers={"content-type": "text/xml"},
            )
        if request.url.path == "/figure.png":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        raise AssertionError(f"Unexpected external acquisition: {request.url}")

    async def setup(self):
        path = self.root / "fixed-application.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(APPLICATION, encoding="utf-8")
        self.application_id = await self.store.add_local(path)
        self.source_id = self.store.register(self.candidate)
        write_json(
            self.root / "input.json",
            {"application": APPLICATION, "source": self.candidate, "fictional": True},
        )
        return self

    async def complete(self, messages, tools=None, *, response_schema=None):
        """Replace the visual provider, preserving the production pixel reader and binding."""
        assert response_schema and not tools
        parts = messages[-1]["content"]
        assert any(
            part.get("image_url", {}).get("url", "").startswith("data:image/png;base64,")
            for part in parts
        )
        self.visual_requests.append(deepcopy(messages))
        return ChatReply(
            {
                "role": "assistant",
                "content": json.dumps({"readability": "readable", "interpretation": VISUAL}),
            },
            "stop",
        )

    def topics(self, model, *, settings=None):
        engine = runtime(
            self.root,
            model,
            settings=settings or SessionSettings(max_turns=24),
            skills=SkillLibrary(ROOT / "skills"),
        )
        service = TopicResearch(
            engine,
            self.store,
            self.discovery,
            application_ids=[self.application_id],
            visual_reader=VisualReader(self, self.store),
        )
        return engine, service

    async def close(self):
        await self.client.aclose()


def objects(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from objects(item)


def payloads(messages):
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            try:
                yield json.loads(content)
            except ValueError:
                continue


def aliases(messages, *needles):
    found = {}
    for payload in payloads(messages):
        for mapping in objects(payload):
            for alias, snapshot in mapping.items():
                if alias.startswith("E") and alias[1:].isdigit() and isinstance(snapshot, dict):
                    if any(needle in snapshot.get("quote", "") for needle in needles):
                        found[alias] = snapshot
    assert len(found) >= len(needles), (needles, found)
    return list(found)


def latest_rows(messages, key):
    for payload in reversed(list(payloads(messages))):
        matches = [obj for obj in objects(payload) if key in obj]
        if matches:
            return matches
    raise AssertionError(f"Missing tool output key: {key}")


def search(provider="europe_pmc"):
    return tool_call(
        "search_sources",
        {
            "queries": [
                {
                    "query": "Huh7 HepG2 serum",
                    "information_need": "培养条件与比较对象",
                    "provider": provider,
                }
            ]
        },
    )


async def direct_sources_and_delivery(root):
    fixture = await Fixture(root).setup()
    saved_text = "固定材料比较 Huh7 与 HepG2；培养条件和定量方法限定其解释范围。"

    def begin(messages):
        require_content(messages, "本项目使用 Huh7", "研究终点包括细胞内甘油三酯")
        return reply(search())

    def open_paper(messages):
        require_content(messages, ABSTRACT)
        return reply(
            tool_call(
                "acquire_source",
                {
                    "source_id": fixture.source_id,
                    "reading_purpose": "Huh7 HepG2 serum Methods Results",
                },
            )
        )

    def supplement(messages):
        require_content(messages, METHOD, RESULT)
        material = next(
            m for m in fixture.store.retained(fixture.source_id).materials if m.role == "supplement"
        )
        return reply(
            tool_call(
                "acquire_source",
                {
                    "source_id": fixture.source_id,
                    "material_id": material.material_id,
                    "reading_purpose": "calibration standards",
                },
            )
        )

    def figure(messages):
        require_content(messages, SUPPLEMENT)
        visual_id = fixture.store.retained(fixture.source_id).visuals[0].visual_id
        return reply(
            tool_call(
                "read_literature_visual",
                {
                    "source_id": fixture.source_id,
                    "visual_id": visual_id,
                    "question": "Read the fixed comparison groups.",
                },
            )
        )

    def save(messages):
        require_content(messages, VISUAL)
        return reply(
            tool_call(
                "update_research",
                note(
                    saved_text,
                    aliases(messages, METHOD, RESULT, SUPPLEMENT, VISUAL),
                    unknowns=["计划书没有给出血清类型。"],
                ),
            )
        )

    model = ScriptModel(
        [
            begin,
            open_paper,
            supplement,
            figure,
            save,
            reply(tool_call("submit_research", {"note": None})),
        ]
    )
    engine, topics = fixture.topics(model)
    topic_id = topics.create(
        TopicTask(objective="理解培养条件、测量与项目外推", impact="判断模型可解释的范围")
    )
    try:
        result = await topics.run(topic_id)
        assert result.status == "submitted", result
        assert result.topic_id == topic_id and result.note
        require_content([{"content": json.dumps(result.note, ensure_ascii=False)}], saved_text)
        quotes = "\n".join(row["quote"] for row in result.readings.values())
        for text in (METHOD, RESULT, SUPPLEMENT, VISUAL):
            assert text in quotes
        assert all(row.get("locator") for row in result.readings.values())
        records = plain(result.reading_records)
        assert records[fixture.source_id]["availability"] == "full_text"
        assert records[fixture.source_id]["visual_ids"] == [
            fixture.store.retained(fixture.source_id).visuals[0].visual_id
        ]
        assert fixture.application_id in records
        assert len(fixture.visual_requests) == 1
        assert fixture.requests.count(BASE_URL + "article.xml") == 1
        assert fixture.requests.count(BASE_URL + "supplement.xml") == 1
        assert not list(root.glob("knowledge/*/answer.json"))
        assert not model.steps
        write_json(root / "delivery.json", result)
        assert_private_not_saved(root)
        return {
            "topic_id": topic_id,
            "author_requests": len(model.requests),
            "originals_delivered": len(result.readings),
            "fixture_http_requests": fixture.requests,
            "scientific_quality_validated": False,
        }
    finally:
        await engine.close()
        await fixture.close()


async def project_reuse_and_followup(root):
    fixture = await Fixture(root).setup()
    await fixture.store.acquire(fixture.source_id)
    first_text = "已有专题认识：Huh7 与 HepG2 的比较仍受培养条件约束。"

    def save_first(messages):
        return reply(
            tool_call(
                "submit_research",
                {
                    "note": note(
                        first_text, aliases(messages, RESULT), unknowns=["尚无另一种血清的结果。"]
                    )
                },
            )
        )

    model = ScriptModel(
        [
            reply(
                tool_call(
                    "acquire_source",
                    {"source_id": fixture.source_id, "reading_purpose": "Huh7 TAG Results"},
                )
            ),
            save_first,
        ]
    )
    engine, topics = fixture.topics(model)
    first_id = topics.create(TopicTask(objective="Huh7 TAG 原专题", source_ids=[fixture.source_id]))
    try:
        assert fixture.source_id not in plain(topics.access(first_id).reading_records())
        first = await topics.run(first_id)
        assert first.status == "submitted"
        requests_before = len(fixture.requests)
        original_ref = first.note_ref
        legacy = root / "knowledge/old-research/answer.json"
        write_json(legacy, {"answer": "LEGACY_ANSWER_MUST_NOT_BE_REUSED"})

        def reuse(messages):
            require_content(messages, first_text, RESULT)
            assert "LEGACY_ANSWER_MUST_NOT_BE_REUSED" not in str(messages)
            return reply(
                tool_call(
                    "submit_research",
                    {"note": note("第二专题已收到原研究与其实际原文。", aliases(messages, RESULT))},
                )
            )

        model.steps.extend([reply(search("project")), reuse])
        second_id = topics.create(TopicTask(objective="Huh7 TAG 项目内复用"))
        assert fixture.source_id not in plain(topics.access(second_id).reading_records())
        second = await topics.run(second_id)
        assert second.status == "submitted"
        assert fixture.source_id in plain(second.reading_records)
        assert len(fixture.requests) == requests_before
        await engine.close()

        def continue_same(messages):
            require_content(messages, first_text, RESULT, "尚无另一种血清的结果", "换一种培养血清")
            return reply(
                tool_call(
                    "submit_research",
                    {
                        "note": note(
                            first_text + "续问关注换血清后的适用条件，固定资料未测该变体。",
                            aliases(messages, RESULT),
                        )
                    },
                )
            )

        resumed_model = ScriptModel([continue_same])
        engine, resumed = fixture.topics(resumed_model)
        continued = await resumed.run(first_id, followup="换一种培养血清后，原观察能支持什么？")
        assert continued.status == "submitted" and continued.topic_id == first_id
        assert continued.note_ref != original_ref
        assert engine.store.note(original_ref)["note"]["title"]
        assert engine.load(first_id).revision == 2
        assert engine.load(first_id).turns_used == 3
        assert len(fixture.requests) == requests_before
        assert len(resumed.completed()) == 2
        write_json(root / "first-delivery.json", first)
        write_json(root / "continued-delivery.json", continued)
        assert_private_not_saved(root)
        return {
            "stable_topic_id": first_id,
            "second_topic_id": second_id,
            "original_note_retained": original_ref,
            "revisions": 2,
            "reuse_acquisition_requests": 0,
            "legacy_index_excluded": True,
        }
    finally:
        await engine.close()
        await fixture.close()


async def continuation_and_interruption(root):
    fixture = await Fixture(root).setup()
    await fixture.store.acquire(fixture.source_id)
    calls, read_pages = [], []
    draft_text = "已读全部方法章节，培养条件的解释已保存；进一步分析尚未完成。"

    def advance(messages):
        rows = latest_rows(messages, "continuation")
        page = next(
            row for row in rows if row.get("source_id") == fixture.source_id and "passages" in row
        )
        read_pages.append(deepcopy(page))
        if page.get("continuation"):
            return reply(
                tool_call(
                    "browse_materials",
                    {
                        "reads": [
                            {"source_id": fixture.source_id, "continuation": page["continuation"]}
                        ]
                    },
                )
            )
        return reply(tool_call("update_research", note(draft_text, aliases(messages, METHOD))))

    # Let the real continuation decide the number of pages, then fail on the next request.
    class InterruptingModel(ScriptModel):
        def __init__(self):
            super().__init__([])

        async def complete(self, messages, tools=None, *, response_schema=None):
            calls.append(deepcopy(messages))
            if len(calls) == 1:
                return reply(
                    tool_call(
                        "browse_materials",
                        {"reads": [{"source_id": fixture.source_id, "section": "Methods"}]},
                    )
                )
            if any(
                "saved" in row for payload in payloads(messages[-1:]) for row in objects(payload)
            ):
                raise ModelError("fixed_topic_timeout", category="timeout", request_sent=True)
            return advance(messages)

    model = InterruptingModel()
    engine, topics = fixture.topics(model)
    topic_id = topics.create(
        TopicTask(objective="章节续读并保存中断前研究", source_ids=[fixture.source_id])
    )
    topics.access(topic_id).reading.budget = 1100
    try:
        result = await topics.run(topic_id)
        assert result.status in {"partial", "failed"} and result.note_ref, result
        assert len(read_pages) > 1
        returned = [p["passage_id"] for page in read_pages for p in page["passages"]]
        expected = [
            p.passage_id
            for p in fixture.store.retained(fixture.source_id).passages
            if p.section == "Methods"
        ]
        assert returned == expected, (returned, expected)
        assert result.note and result.readings
        saved_ref = result.note_ref
        write_json(root / "interrupted-delivery.json", result)
        await engine.close()

        def submit_saved(messages):
            require_content(messages, draft_text, METHOD)
            return reply(tool_call("submit_research", {"note": None}))

        engine, restored = fixture.topics(ScriptModel([submit_saved]))
        final = await restored.run(topic_id, followup="按已读范围交付当前稿。")
        assert final.status == "submitted" and final.note_ref == saved_ref
        assert set(plain(final.reading_records)[fixture.source_id]["passage_ids"]) == set(expected)
        write_json(root / "resumed-delivery.json", final)
        assert_private_not_saved(root)
        return {
            "topic_id": topic_id,
            "method_pages": len(read_pages),
            "method_passages": len(returned),
            "interruption_preserved_note": True,
            "resume_submitted_same_note": True,
        }
    finally:
        await engine.close()
        await fixture.close()


async def completed_version_and_long_application(root):
    fixture = await Fixture(root).setup()

    def submit_first(messages):
        return reply(
            tool_call(
                "submit_research",
                {"note": note("第一版只解释原来源 A 的观察。", aliases(messages, RESULT))},
            )
        )

    def interrupt_after_new_source(messages):
        require_content(messages, FOLLOWUP_SOURCE)
        raise ModelError("fixed_followup_timeout", category="timeout", request_sent=True)

    model = ScriptModel(
        [
            reply(
                tool_call(
                    "acquire_source",
                    {"source_id": fixture.source_id, "reading_purpose": "Huh7 TAG Results"},
                )
            ),
            submit_first,
            reply(
                tool_call(
                    "acquire_source",
                    {"url": BASE_URL + "followup.xml", "reading_purpose": "different assay"},
                )
            ),
            interrupt_after_new_source,
        ]
    )
    engine, topics = fixture.topics(model)
    topic_id = topics.create(
        TopicTask(objective="提交版本与续问阅读范围", source_ids=[fixture.source_id])
    )
    try:
        first = await topics.run(topic_id)
        assert first.status == "submitted"
        interrupted = await topics.run(topic_id, followup="阅读新增来源 B，研究不同测量方法。")
        assert interrupted.status in {"failed", "partial"}
        second_source = next(
            item["source_id"]
            for item in fixture.store.catalogue()
            if item["url"] == BASE_URL + "followup.xml"
        )
        assert second_source in plain(topics.access(topic_id).reading_records())
        completed = topics.completed()
        assert len(completed) == 1
        assert completed[0]["note_ref"] == first.note_ref
        assert completed[0]["reading_records"] == first.reading_records
        assert second_source not in completed[0]["source_ids"]
        assert second_source not in {row["source_id"] for row in completed[0]["materials"]}
        assert all(
            reading["reference"]["source_id"] != second_source
            for reading in completed[0]["readings"].values()
        )
        write_json(root / "completed-after-interruption.json", completed)
        write_json(root / "interrupted-current-delivery.json", interrupted)
        await engine.close()

        engine, restored = fixture.topics(ScriptModel([]))
        assert restored.completed()[0]["reading_records"] == first.reading_records
        assert second_source in plain(restored.access(topic_id).reading_records())

        # A long application starts from its directory. Merely retaining it is not a read.
        long_path = root / "long-application.md"
        long_path.write_text(APPLICATION * 40, encoding="utf-8")
        long_id = await fixture.store.add_local(long_path)
        engine.settings = SessionSettings(context_tokens=1200)
        restored.application_ids = [long_id]
        long_topic = restored.create(TopicTask(objective="长计划书入口"))
        state = engine.load(long_topic)
        assert state.task.application["full_text_in_context"] is False
        assert all("passages" not in source for source in state.task.application["sources"])
        assert long_id not in plain(restored.access(long_topic).reading_records())
        assert not state.evidence
        assert not engine.models["local"].requests
        assert_private_not_saved(root)
        return {
            "topic_id": topic_id,
            "immutable_note_ref": first.note_ref,
            "new_source_currently_read": second_source,
            "completed_version_excludes_followup_reading": True,
            "same_after_restart": True,
            "long_application_directory_only_not_marked_read": True,
        }
    finally:
        await engine.close()
        await fixture.close()


async def invalid_public_submission_resume(root):
    fixture = await Fixture(root).setup()
    draft_text = "尚未提交的公开稿：固定研究比较 Huh7 与 HepG2，培养条件限制其解释范围。"
    rejected_arguments = {}

    def invalid_drafts(messages):
        draft = note(draft_text, aliases(messages, RESULT))
        calls = []
        for name, arguments in (
            ("update_research", draft),
            ("submit_research", {"note": draft}),
        ):
            # A real malformed JSON envelope: all public science is present, last } is missing.
            raw = json.dumps(arguments, ensure_ascii=False)[:-1]
            rejected_arguments[name] = raw
            call = tool_call(name, {})
            call["function"]["arguments"] = raw
            calls.append(call)
        return reply(*calls)

    def interrupt(messages):
        failures = [
            row
            for payload in payloads(messages[-2:])
            for row in objects(payload)
            if row.get("type") == "json_invalid"
        ]
        assert len(failures) == 2
        assert all("line" in row["detail"] and "column" in row["detail"] for row in failures)
        raise ModelError(
            "fixed_timeout_after_rejected_draft", category="timeout", request_sent=True
        )

    model = ScriptModel(
        [
            reply(
                tool_call(
                    "acquire_source",
                    {"source_id": fixture.source_id, "reading_purpose": "Huh7 TAG Results"},
                )
            ),
            invalid_drafts,
            interrupt,
        ]
    )
    engine, topics = fixture.topics(model)
    topic_id = topics.create(
        TopicTask(objective="公开提交失败后续接", source_ids=[fixture.source_id])
    )
    try:
        interrupted = await topics.run(topic_id)
        assert interrupted.status in {"partial", "failed"}
        assert interrupted.note_ref is None and interrupted.note is None
        state = engine.load(topic_id)
        assert state.submissions == [] and not topics.completed()
        attempts = {
            row["name"]: row["attempted_arguments"]
            for row in state.observations
            if "attempted_arguments" in row
        }
        assert attempts == rejected_arguments
        assert all(draft_text in raw for raw in attempts.values())
        write_json(root / "interrupted-public-state.json", state)
        await engine.close()

        def original_author_continues(messages):
            require_content(messages, draft_text, RESULT, "attempted_arguments", "json_invalid")
            return reply(
                tool_call(
                    "submit_research",
                    {"note": note(draft_text, aliases(messages, RESULT))},
                )
            )

        resumed_model = ScriptModel([original_author_continues])
        engine, restored = fixture.topics(resumed_model)
        final = await restored.run(topic_id, followup="继续完成原研究说明。")
        assert final.status == "submitted" and final.topic_id == topic_id
        assert final.note_ref and final.readings
        assert engine.load(topic_id).revision == 1
        assert engine.load(topic_id).submissions == [final.note_ref]
        assert len(resumed_model.requests) == 1 and not resumed_model.steps
        write_json(root / "resumed-delivery.json", final)
        assert_private_not_saved(root)
        return {
            "topic_id": topic_id,
            "rejected_public_drafts_preserved": sorted(attempts),
            "json_error_has_syntax_position": True,
            "no_note_created_from_invalid_arguments": True,
            "original_author_resumed_requests": 1,
            "valid_saved_revisions": 1,
        }
    finally:
        await engine.close()
        await fixture.close()


CASES = (
    direct_sources_and_delivery,
    project_reuse_and_followup,
    continuation_and_interruption,
    completed_version_and_long_application,
    invalid_public_submission_resume,
)


async def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = {}
    for case in CASES:
        if not args.case or case.__name__ in args.case:
            results[case.__name__] = await case(output / case.__name__)
    summary = {
        "change": "change10",
        "mode": "offline",
        "network_requests": 0,
        "real_model_requests": 0,
        "full_review_runs": 0,
        "scientific_quality_validated": False,
        "cases": results,
    }
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "runs/v5-local/change10/offline-topic"
    )
    parser.add_argument("--case", action="append", choices=[case.__name__ for case in CASES])
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
