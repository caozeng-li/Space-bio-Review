"""Evidence identities and consultation replies cross the real collaboration path."""

import json
from copy import deepcopy

import httpx
import pytest

from spacebio_review.evidence.store import SourceStore
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument
from spacebio_review.models.config import ModelConfig
from spacebio_review.models.provider import ChatReply
from spacebio_review.persistence import write_json
from spacebio_review.prose import cited_ids, compile_authored_body, prose_inputs
from spacebio_review.publication.compile import compile_publication
from spacebio_review.publication.document import walk_nodes
from spacebio_review.review.evaluation import GENERAL
from spacebio_review.v5.chief import ChiefReview
from spacebio_review.v5.collaboration import RequestInquiry
from spacebio_review.v5.records import ReportSelection
from spacebio_review.v5.report_composition import ReportComposition
from spacebio_review.v5.session import ResearchRuntime
from spacebio_review.v5.topic import TopicResearch, TopicTask


def tool_reply(name, **arguments):
    return ChatReply(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-local",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
            ],
        },
        "tool_calls",
        prompt_tokens=100,
        completion_tokens=40,
    )


class ScriptedAuthor:
    """Only the model is substituted; reading, publication and delivery stay real."""

    def __init__(self, name, steps=()):
        self.config = ModelConfig(
            model=name,
            base_url="https://local.invalid/v1",
            tools=True,
            max_tokens=512,
            retries=0,
        )
        self.steps = list(steps)
        self.requests = []

    async def complete(self, messages, tools=None, *, response_schema=None, **kwargs):
        assert response_schema is None
        assert isinstance(messages[0]["content"], str)
        self.requests.append(deepcopy(messages))
        assert self.steps, "Unexpected extra model request"
        step = self.steps.pop(0)
        return step(messages) if callable(step) else step


class AssemblyModel:
    config = ModelConfig(model="assembly", base_url="https://local.invalid/v1", tools=False)

    async def complete(self, messages, tools=None, *, response_schema=None):
        # These handoff fixtures already carry canonical links. No new annotation is needed.
        assert response_schema is not None
        values = {key: {} for key in response_schema["properties"]}
        return ChatReply({"role": "assistant", "content": json.dumps(values)}, "stop")


def publication(body, focus):
    return [
        tool_reply("set_research_focus", focus=focus),
        tool_reply("publish_manuscript", content=body),
        tool_reply("finish_research", reason="科学稿已完整表达本题认识。"),
    ]


def retained_source(store, title, text):
    candidate = SourceCandidate(title=title, kind="literature")
    sid = store.register(candidate)
    store._save(
        SourceDocument(
            source_id=sid,
            candidate=candidate,
            availability="full_text",
            passages=[Passage(passage_id="p1", locator="第1段", text=text)],
        )
    )
    return sid


def read_original(runtime, store, access, session_id, source_id):
    document = store.retained(source_id)
    access.record_context_read(
        {
            "source_id": source_id,
            "title": document.candidate.title,
            "passages": [p.model_dump(mode="json") for p in document.passages],
        }
    )
    state = runtime.load(session_id)
    runtime._capture(state, access.references.snapshot_choices(selected=False))
    runtime.store.save(state)
    return next(iter(state.evidence))


@pytest.fixture
async def team(tmp_path):
    def unexpected_http(request):
        pytest.fail(f"Local regression unexpectedly requested HTTP: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_http)) as client:
        store = SourceStore(tmp_path / "sources", client)
        raw_a = "生命生态实验柜支持 17–28℃。[E1]\n\n这是该装置的温度范围。"
        raw_b = "细胞组织实验模块具备独立培养能力。[E1]"
        models = {
            "a": ScriptedAuthor("a"),
            "b": ScriptedAuthor("b"),
            "assembly": AssemblyModel(),
            "chief": ScriptedAuthor("chief"),
        }
        runtime = ResearchRuntime(
            tmp_path,
            models,
            {"chief": "chief", "researcher": "a"},
        )
        topics = TopicResearch(
            runtime,
            store,
            None,
            application_ids=[],
            external_research_enabled=False,
        )
        chief = ChiefReview(topics, admission={"status": "skipped"})
        chief_id = chief.create("综合比较两个装置的适用条件")
        source_c = retained_source(store, "第三份资料", "独立的一项背景事实。")
        handle_c = read_original(runtime, store, chief.access(chief_id), chief_id, source_c)
        topic_ids, handles, source_ids = [], [], []
        for alias, title, original in (
            ("a", "生命生态实验柜", "生命生态实验柜温度控制范围为 17–28℃。"),
            ("b", "细胞组织实验模块", "细胞组织实验模块提供独立细胞培养能力。"),
        ):
            runtime.routes["researcher"] = alias
            iid = topics.create(TopicTask(responsibility=f"研究{title}的科学适用条件"))
            sid = retained_source(store, title, original)
            handle = read_original(
                runtime,
                store,
                topics.access(iid),
                topics.state(iid).session_id,
                sid,
            )
            body = (raw_a if alias == "a" else raw_b).replace("[E1]", f"[来源](evidence:{handle})")
            models[alias].steps.extend(publication(body, title))
            if alias == "a":
                raw_a = body
            else:
                raw_b = body
            topic_ids.append(iid)
            handles.append(handle)
            source_ids.append(sid)
        # Record ownership for report selection; publication itself uses the coordinator.
        manifest = chief._manifest(chief_id)
        manifest["topic_ids"] = topic_ids
        write_json(chief._path(chief_id), manifest)
        chief_state = runtime.load(chief_id)
        chief_state.coordination_phase = "synthesis"
        runtime.store.save(chief_state)
        for iid in topic_ids:
            await topics.run(iid)
            assert topics.state(iid).status == "submitted"
        yield {
            "runtime": runtime,
            "topics": topics,
            "chief": chief,
            "chief_id": chief_id,
            "models": models,
            "ids": topic_ids,
            "handles": handles,
            "source_ids": source_ids,
            "handle_c": handle_c,
            "raw": [raw_a, raw_b],
        }
        await topics.collaboration.close()


async def test_citations_reach_chief_and_report_with_unchanged_identities(team):
    runtime, topics, chief = team["runtime"], team["topics"], team["chief"]
    artifacts = [runtime.store.note(topics.result(iid).note_ref) for iid in team["ids"]]
    for artifact, handle, raw in zip(artifacts, team["handles"], team["raw"], strict=True):
        assert set(cited_ids(artifact["note"])) == {handle}
        assert set(artifact["evidence"]) == {handle}
        authored = runtime.store.author_text(artifact["author_text_ref"])
        assert authored["content"] == raw
        assert authored["readings"] == [handle]

    chief_state = runtime.load(team["chief_id"])
    assert topics.collaboration._receive(chief_state.inquiry_id, chief_state)
    assert set(chief_state.evidence) == {team["handle_c"], *team["handles"]}
    text = json.dumps(prose_inputs(chief_state.observations), ensure_ascii=False)
    for handle in team["handles"]:
        assert f"(evidence:{handle})" in text
    chief_body = "两份资料分别描述不同装置。" + "".join(
        f"[来源](evidence:{handle})" for handle in team["handles"]
    )
    team["models"]["chief"].steps.extend(publication(chief_body, "不同装置的适用条件"))
    # Run the real chief's research mode, without the full assessment/model workflow.
    await chief.run(team["chief_id"], mode="research")
    chief_state = runtime.load(team["chief_id"])
    chief_note = runtime.store.note(chief_state.note_ref)
    assert set(cited_ids(chief_note["note"])) == set(team["handles"])
    assert runtime.store.author_text(chief_note["author_text_ref"])["content"] == chief_body
    sent = json.dumps(team["models"]["chief"].requests[0], ensure_ascii=False)
    assert all(f"(evidence:{handle})" in sent for handle in team["handles"])

    selection = ReportSelection.model_validate(
        {
            "main_sections": [
                {"inquiry_id": iid, "contribution_ref": artifact_ref}
                for iid, artifact_ref in zip(
                    team["ids"],
                    [topics.result(iid).note_ref for iid in team["ids"]],
                    strict=True,
                )
            ],
        }
    )
    composition = ReportComposition(chief, team["chief_id"])
    composition.prepare(chief_state, selection)
    body = chief_note["note"]["body"]
    assessment = {
        "evaluation_system": GENERAL.snapshot(),
        "recommendation": "支持",
        "summary": body,
        "project": body,
        "scope": compile_authored_body("局部检验。"),
        "overall": body,
        "unknowns": [],
        "dimensions": {
            key: {"grade": "B", "confidence": "high", "body": body} for key in GENERAL.aspects
        },
    }
    document, audit = compile_publication(
        assessment,
        evidence=chief_note["evidence"],
        report_selection=selection.model_dump(mode="json"),
        contributions=composition.selected(chief_state),
    )
    assert set(document.sources) == set(team["source_ids"])
    assert set(audit["evidence"]) == set(team["handles"])
    sections = [section for section in document.main if section.kind == "scientific"]
    for section, handle, sid in zip(sections, team["handles"], team["source_ids"], strict=True):
        assert {node.target for node in walk_nodes(section.blocks) if node.kind == "evidence"} == {
            handle,
        }
        assert document.locations[handle].source == sid


@pytest.mark.parametrize("tool_answer", [False, True])
async def test_consultation_reply_does_not_replace_manuscript(team, tool_answer):
    topics, runtime = team["topics"], team["runtime"]
    a, b = team["ids"]
    handle_a, handle_b = team["handles"]
    before = topics.result(b).note_ref
    text = f"我的资料描述另一装置的培养能力。[来源](evidence:{handle_b})"

    def answer(messages):
        request_id = topics.collaboration._actor(b).hook.current_request
        if tool_answer:
            return tool_reply("reply_consultation", request_id=request_id, content=text)
        return ChatReply({"role": "assistant", "content": text}, "stop")

    team["models"]["b"].steps.append(answer)
    request = topics.collaboration.request(
        a,
        RequestInquiry(
            target=b,
            question=f"温度范围[来源](evidence:{handle_a})是否适用？",
            scientific_use="区分装置及适用条件。",
        ),
    )
    pending = topics.collaboration.requests[request["request_id"]]
    assert set(cited_ids(pending.question)) == {handle_a}
    await topics.collaboration.actors[b].task
    assert pending.status == "answered"
    assert set(cited_ids(pending.answer["reply"])) == {handle_b}
    assert "note" not in pending.answer and "contribution_ref" not in pending.answer
    assert topics.result(b).note_ref == before
    reply = runtime.store.reply(pending.answer["reply_ref"])
    assert reply["request_id"] == request["request_id"]
    composition = ReportComposition(team["chief"], team["chief_id"])
    assert (
        next(
            row
            for row in composition.candidates(runtime.load(team["chief_id"]))
            if row["inquiry_id"] == b
        )["contribution_ref"]
        == before
    )
    state = topics.state(a)
    assert topics.collaboration._receive(a, state)
    assert handle_b in set(cited_ids(state.observations))


@pytest.mark.parametrize("system_name", ["general", "national"])
async def test_plain_decision_overall_and_summary_have_owned_destinations(team, system_name):
    from spacebio_review.review.evaluation import GBT42048
    from spacebio_review.v5.assessment import AssessmentSubmission
    from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance

    runtime = team["runtime"]
    state = runtime.load(team["chief_id"])
    submission = AssessmentSubmission(team["chief"], team["chief_id"])
    system = GBT42048 if system_name == "national" else GENERAL
    submission.system = system
    submission.prepare(state)
    paragraphs = ["## 项目理解\n\n探索性比较有价值。", "## 审议范围\n\n评价现有计划。"]
    values = {
        "project": "p1",
        "scope": "p3",
        "dimensions": {},
        "unknowns": [],
        "main_sections": ["S1", "S2"],
        "appendix_sections": [],
    }
    for criterion in system.criteria:
        paragraphs.append(f"## {criterion.id}\n\n{criterion.id}：实际贡献及主导限制。")
        value = {"body": f"p{len(paragraphs) * 2 - 1}"}
        if system.score_grades:
            value["score"] = min(10, criterion.max_score)
        else:
            value.update(grade="B", confidence="C")
        values["dimensions"][criterion.id] = value
    paragraphs.append("## 决定说明\n\n当前依据支持限定范围内的研究。")
    if system.score_grades:
        values["safety_veto"] = {"applies": False, "reason": f"p{len(paragraphs) * 2 - 1}"}
    else:
        values.update(recommendation="support", support_scope=f"p{len(paragraphs) * 2 - 1}")

    class DecisionAssembly(AssemblyModel):
        async def complete(self, messages, tools=None, *, response_schema=None):
            properties = response_schema["properties"]
            output = {key: {} for key in properties}
            if "project" in properties:
                output = values
            return ChatReply({"role": "assistant", "content": json.dumps(output)}, "stop")

    class Metered:
        def __init__(self, delegate):
            self.delegate, self.config = delegate, delegate.config

        async def complete(self, messages, tools=None, *, response_schema=None):
            ticket = runtime.budget.reserve(self.config, messages, tools, response_schema)
            response = await self.delegate.complete(
                messages, tools, response_schema=response_schema
            )
            runtime.budget.settle(ticket, response)
            return response

    runtime.budget = Budget(runtime.store.root.parent, BudgetLimits(max_model_calls=30))
    runtime.budget.register(runtime.budget_account(state), SessionAllowance(completion_calls=9))
    author = team["models"]["chief"]
    author.steps.extend(
        ChatReply(
            {"role": "assistant", "content": text}, "stop", prompt_tokens=100, completion_tokens=50
        )
        for text in [
            "\n\n".join(paragraphs),
            "综合：既有依据支持有限科学贡献。",
            "摘要：支持这一探索计划。",
        ]
    )
    runtime.models["assembly"] = Metered(DecisionAssembly())
    chief_model = Metered(author)
    state.research_finished = True
    for operation in ["submit_assessment", "write_overall", "write_summary"]:
        if operation == "write_overall":
            state.evidence["evidence-unused-archive"] = {
                "reference": {"source_id": "unused-archive", "passage_id": "p1"},
                "title": "没有进入评价依据的历史材料",
                "quote": "该历史材料不应重新进入综合或摘要的装配。",
            }
        assert submission.completion_operations(state)[0] == operation
        await runtime._formal_submission(
            state, chief_model, operation=operation, submission=submission
        )
    result = submission.result()
    assert result["scientific_status"] == "completed"
    assert prose_inputs(result["assessment"]["overall"]) == "综合：既有依据支持有限科学贡献。"
    assert prose_inputs(result["assessment"]["summary"]) == "摘要：支持这一探索计划。"
    assert (
        "evidence-unused-archive"
        not in runtime.store.author_text(result["last_author_text_ref"])["readings"]
    )
    for criterion in system.criteria:
        assert prose_inputs(result["assessment"]["dimensions"][criterion.id]["body"]) == (
            prose_inputs(compile_authored_body(f"{criterion.id}：实际贡献及主导限制。"))
        )
    if system.score_grades:
        assert result["assessment"]["total_score"] == sum(
            v["score"] for v in values["dimensions"].values()
        )
    assert "综合：既有依据支持有限科学贡献。" in json.dumps(author.requests[-1], ensure_ascii=False)
    assert all(row["purpose"] == "completion" for row in runtime.budget.data["requests"])
    assert any(row["model"] == "assembly" for row in runtime.budget.data["requests"])
    assert all(
        row["session_id"] == runtime.budget_account(state)
        for row in runtime.budget.data["requests"]
    )


async def test_reply_returns_to_pending_research_and_only_full_publication_advances_revision(team):
    topics, runtime = team["topics"], team["runtime"]
    a, b = team["ids"]
    before = topics.result(b).note_ref
    revision = runtime.store.note(before)["revision"]

    def reply_current(messages):
        return tool_reply(
            "reply_consultation",
            request_id=topics.collaboration._actor(b).hook.current_request,
            content="咨询答复：两个装置的对象不同。",
        )

    def publish_full(messages):
        assert "继续补充适配说明" in json.dumps(messages, ensure_ascii=False)
        return tool_reply("publish_manuscript", content=team["raw"][1] + "\n\n补充适配说明。")

    team["models"]["b"].steps.extend(
        [reply_current, publish_full, tool_reply("finish_research", reason="完整专题已修订。")]
    )
    topics.collaboration.schedule(b, followup="继续补充适配说明")
    request = topics.collaboration.request(
        a, RequestInquiry(target=b, question="请先说明对象区别", scientific_use="避免混用。")
    )
    await topics.collaboration.actors[b].task
    pending = topics.collaboration.requests[request["request_id"]]
    assert pending.status == "answered"
    assert not topics.collaboration.actors[b].error
    current = topics.result(b).note_ref
    assert current != before
    assert runtime.store.note(current)["revision"] == revision + 1
    assert runtime.store.note(current)["previous_ref"] == before
    assert "补充适配说明。" in prose_inputs(runtime.store.note(current)["note"])["body"]
