"""Offline regressions for waiting, handoffs and token admission; no review run."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx
from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fixtures.session import ScriptModel, reply, runtime, tool_call  # noqa: E402

from spacebio_review.models.provider import DashScopeModel, ModelError  # noqa: E402
from spacebio_review.models.scheduling import RequestScheduler  # noqa: E402
from spacebio_review.prose import compile_authored_body  # noqa: E402
from spacebio_review.v5.collaboration import (  # noqa: E402
    ScientificCollaboration,
    _AuthorHook,
    _Request,
    _Work,
)
from spacebio_review.v5.config import load_config  # noqa: E402
from spacebio_review.v5.records import SessionTask  # noqa: E402
from spacebio_review.v5.source_reading import SourceReading  # noqa: E402


class FlowChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="spacebio-request-flow-")
        self.addCleanup(self.folder.cleanup)
        self.model = ScriptModel([])
        self.runtime = runtime(Path(self.folder.name), self.model)
        self.chief_id = self.runtime.create(SessionTask(
            role="chief", application="局部协作验证材料。", objective="综合两位专家的实际交付。",
        ))
        self.state = self.runtime.load(self.chief_id)
        self.inquiry = self.state.inquiry_id
        access = SimpleNamespace(receive=lambda readings: None)
        topics = SimpleNamespace(runtime=self.runtime, _topic_ids=lambda: [])
        self.team = ScientificCollaboration(topics)
        self.hook = _AuthorHook(self.team, self.inquiry, consulting=False)

        async def run_segment(chief_id, **kwargs):
            return await self.runtime.run(chief_id, collaboration=kwargs["collaboration"])

        self.team.bind_chief(SimpleNamespace(
            access=lambda _: access, _run_segment=run_segment,
        ), self.chief_id)
        self.actor = self.team._actor(self.inquiry)
        self.actor.work.append(_Work())
        # Drive segments explicitly so assertions can inspect the suspended interval.
        self.actor.task = asyncio.current_task()
        for number in (1, 2):
            key = f"request-{number}"
            self.team.requests[key] = _Request(
                key, self.inquiry, f"expert-{number}", "研究问题", "用于综合判断",
                kind="research", status="assigned",
            )

    async def segment(self):
        return await self.runtime.run(self.chief_id, collaboration=self.hook)

    def delivery(self, event):
        self.team._deliver(self.inquiry, "contribution", "expert-1", {"event": event}, {})

    async def test_focus_notifications_do_not_turn_waiting_into_model_calls(self):
        def wait_with_pending_focus(messages):
            self.delivery("focus_changed")
            return reply(tool_call("finish_research", {
                "reason": "等待实际评审意见。", "wait_for": ["request-1", "request-2"],
            }))

        self.model.steps = [wait_with_pending_focus]
        outcome = await self.segment()
        self.assertEqual(outcome.status, "waiting")
        self.assertEqual(len(self.model.requests), 1)
        self.assertEqual(self.actor.work[-1].wait_for, {"request-1", "request-2"})
        self.assertFalse(self.team._ready(self.inquiry))
        self.delivery("focus_changed")
        self.assertFalse(self.team._ready(self.inquiry))

    async def test_real_progress_wakes_chief_then_plain_reply_yields(self):
        self.actor.work[-1].wait_for = {"request-1", "request-2"}
        self.delivery("contribution")
        self.assertTrue(self.team._ready(self.inquiry))
        self.model.steps = [reply(content="已理解第一份意见，继续等待其他专家。")]
        outcome = await self.segment()
        self.assertEqual(outcome.status, "waiting")
        self.assertEqual(len(self.model.requests), 1)
        self.assertEqual(self.actor.work[-1].wait_for, {"request-1", "request-2"})
        self.assertFalse(self.team._ready(self.inquiry))
        self.team.requests["request-1"].status = "answered"
        self.team._deliver(self.inquiry, "answer", "request-1", {"status": "answered"}, {})
        self.assertTrue(self.team._ready(self.inquiry))
        self.model.steps = [reply(tool_call("finish_research", {
            "reason": "等待剩余专题。", "wait_for": [],
        }))]
        self.assertEqual((await self.segment()).status, "waiting")
        self.assertEqual(self.actor.work[-1].wait_for, {"request-2"})
        self.team.requests["request-2"].status = "unavailable"
        self.assertTrue(self.team._ready(self.inquiry))

    async def test_unassigned_consultation_wakes_waiting_chief(self):
        self.actor.work[-1].wait_for = {"request-1", "request-2"}
        self.team._deliver(self.inquiry, "assignment", "question", {
            "question": "需要安排方法专家。",
        }, {})
        self.assertTrue(self.team._ready(self.inquiry))
        self.assertEqual(self.actor.work[-1].wait_for, {"request-1", "request-2"})

    async def test_final_delivery_reserve_does_not_spin_on_queued_progress(self):
        def finish_with_new_opinion(messages):
            self.delivery("contribution")
            return reply(tool_call("finish_research", {
                "reason": "等待剩余交付后综合。", "wait_for": [],
            }))

        self.model.steps = [finish_with_new_opinion]
        self.assertEqual((await self.segment()).status, "waiting")
        self.assertFalse(
            self.team._ready(self.inquiry),
            "Pending progress would repeatedly restart a chief that has yielded to final delivery",
        )
        self.assertTrue(any(
            row["kind"] == "author_delivery"
            for row in self.runtime.load(self.chief_id).observations
        ))
        state = self.runtime.load(self.chief_id)
        state.turns_used = state.settings.max_turns - 1
        self.runtime.store.save(state)
        self.delivery("contribution")
        self.assertEqual((await self.segment()).status, "waiting")
        self.assertFalse(self.team._ready(self.inquiry))
        self.assertEqual(len(self.model.requests), 1)

    async def test_reader_body_enters_actual_context_once_across_three_handoffs(self):
        original = {
            "reading_id": "reader-1", "note_ref": "note-reader-1",
            "note": {"title": "来源解释", "body": compile_authored_body("唯一的来源解释正文。")},
            "readings": {}, "reading_records": {}, "author_session_id": "reader-1",
        }
        conversation = self.runtime._conversation(self.state, None, None)
        for event in ("contribution", "answer", "read_references"):
            # Exercise the actual delivery projection, then the actual receiver.
            view = SourceReading.view(original)
            payload = self.runtime._live_payload(self.state, {
                "ok": True, "content": {"event": event, "source_readings": [view]},
            }, {})
            conversation.append({
                "role": "user", "content": json.dumps(payload, ensure_ascii=False),
            })
        rendered = json.dumps(conversation.messages, ensure_ascii=False)
        self.assertEqual(rendered.count("唯一的来源解释正文。"), 1)
        self.assertIn("note-reader-1", rendered)
        revised = {**original, "note_ref": "note-reader-2", "note": {
            "title": "修订解释", "body": compile_authored_body("新证据改变了解释。"),
        }}
        payload = self.runtime._live_payload(self.state, {
            "ok": True, "content": SourceReading.view(revised),
        }, {})
        self.assertIn("新证据改变了解释。", json.dumps(payload, ensure_ascii=False))

    async def test_chief_failure_stops_outstanding_expert(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def expert():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        task = asyncio.create_task(expert())
        self.addAsyncCleanup(self.team.close)
        self.team._actor("expert-1").task = task
        await started.wait()
        self.model.steps = [ModelError(
            "model_http_429:insufficient_quota", category="rate_limit", http_status=429,
        )]
        await self.team._drive(self.inquiry)
        self.assertTrue(cancelled.is_set(), "Chief failed but expert work kept running")
        self.assertTrue(self.team.closed)
        self.assertEqual(self.runtime.load(self.chief_id).status, "failed")


class AdmissionChecks(unittest.IsolatedAsyncioTestCase):
    async def test_scientific_and_translation_share_token_pacing(self):
        config = load_config(ROOT / "config/runtime.yaml")
        scientific, translation = config.models["scientific"], config.models["translation"]
        self.assertIsNotNone(scientific.quota_group)
        self.assertEqual(scientific.quota_group, translation.quota_group)
        value, starts = [0.0], []

        async def sleep(delay):
            value[0] += delay
            await asyncio.sleep(0)

        scheduler = RequestScheduler(config.concurrency, clock=lambda: value[0], sleep=sleep)
        quota = config.concurrency.quotas[scientific.quota_group]

        async def request():
            starts.append(value[0])

        await asyncio.gather(*[
            scheduler.call(
                request, model_id=route.model, quota_group=route.quota_group,
                estimated_tokens=weight,
            )
            for route, weight in [(scientific, 36000), (scientific, 88000), (translation, 40000)]
        ])
        self.assertAlmostEqual(starts[1] - starts[0], 60 * 36000 / quota.tokens_per_minute)
        self.assertAlmostEqual(starts[2] - starts[1], 60 * 88000 / quota.tokens_per_minute)
        self.assertGreater(scheduler.metrics()["rate_wait_seconds_sum"], 0)

    async def test_http_429_retries_through_real_provider_and_scheduler(self):
        config = load_config(ROOT / "config/runtime.yaml")
        route = config.models["scientific"]
        self.assertEqual(route.retries, 2)
        calls = []

        def transport(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(429, json={"error": {"code": "insufficient_quota"}})
            return httpx.Response(200, json={
                "id": "offline", "object": "chat.completion", "created": 0, "model": route.model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "完成"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 2, "total_tokens": 102},
            })

        client = AsyncOpenAI(
            api_key="offline", base_url="https://offline.invalid/v1", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
        )
        scheduler = RequestScheduler(config.concurrency)
        model = DashScopeModel(route, client=client, scheduler=scheduler)
        self.addAsyncCleanup(model.close)
        result = await model.complete([{"role": "user", "content": "局部测试"}])
        self.assertEqual(result.message["content"], "完成")
        self.assertEqual(len(calls), 2)
        self.assertEqual([r["status"] for r in scheduler.records], ["failed", "completed"])
        self.assertEqual(scheduler.metrics()["retries"], 1)
        self.assertGreaterEqual(scheduler.metrics()["retry_wait_seconds_sum"], 5)


if __name__ == "__main__":
    unittest.main()
