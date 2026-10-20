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
from spacebio_review.prose import cited_ids, prose_inputs
from spacebio_review.publication.compile import compile_publication
from spacebio_review.publication.document import walk_nodes
from spacebio_review.review.evaluation import GENERAL
from spacebio_review.v5.chief import ChiefReview
from spacebio_review.v5.collaboration import RequestInquiry
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
        tool_reply("publish_manuscript", title=focus, content=body),
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

    body = chief_note["note"]["body"]
    assessment = {
        "evaluation_system": GENERAL.snapshot(),
        "recommendation": "support",
        "body": body,
        "dimensions": {
            key: {"grade": "B", "confidence": "B", "reason": "依据专家意见"}
            for key in GENERAL.aspects
        },
    }
    document, audit = compile_publication(assessment, evidence=chief_note["evidence"])
    assert set(document.sources) == set(team["source_ids"])
    assert set(audit["evidence"]) == set(team["handles"])
    for handle, sid in zip(team["handles"], team["source_ids"], strict=True):
        assert document.locations[handle].source == sid
    assert [s.section_id for s in document.main] == ["body", "grades"]


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
    assert topics.contribution_refs(b)[0] == before
    state = topics.state(a)
    assert topics.collaboration._receive(a, state)
    assert handle_b in set(cited_ids(state.observations))


@pytest.mark.parametrize("system_name", ["general", "national"])
@pytest.mark.parametrize("chief_only", [False, True])
async def test_chief_decides_then_writes_whole_report_without_assembly(
    team, system_name, chief_only
):
    from spacebio_review.review.evaluation import GBT42048
    from spacebio_review.v5.assessment import AssessmentSubmission
    from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance

    runtime, chief = team["runtime"], team["chief"]
    if chief_only:
        body = f"主审形成的评审意见。[原文](evidence:{team['handle_c']})"
        team["models"]["chief"].steps.extend(publication(body, "主审意见"))
        await chief.run(team["chief_id"], mode="research")
        manifest = chief._manifest(team["chief_id"])
        manifest["topic_ids"] = []
        write_json(chief._path(team["chief_id"]), manifest)
    state = runtime.load(team["chief_id"])
    submission = AssessmentSubmission(chief, team["chief_id"])
    system = GBT42048 if system_name == "national" else GENERAL
    submission.system = system
    submission.prepare(state)
    decisions = {"overall_judgment": "依据支持探索性比较。", "dimensions": {}}
    for criterion in system.criteria:
        value = {"reason": "实际贡献及影响本项判断的限制。"}
        if system.score_grades:
            value["score"] = min(10, criterion.max_score)
        else:
            value.update(grade="B", confidence="C")
        decisions["dimensions"][criterion.id] = value
    if system.score_grades:
        decisions["safety_veto"] = {"applies": False, "reason": "没有具体否决事实。"}
    else:
        decisions.update(recommendation="support", support_scope="当前申请范围。")
    handle = team["handle_c"] if chief_only else team["handles"][0]
    report = f"## 综合评价\n\n这是主审融合意见后撰写的报告。[来源](evidence:{handle})"
    report += "\n\n## 科学目标\n\n申请的描述性比较具有价值。"
    author = team["models"]["chief"]
    author.steps.extend(
        ChatReply(
            {"role": "assistant", "content": text}, "stop", prompt_tokens=100, completion_tokens=50
        )
        for text in [json.dumps(decisions, ensure_ascii=False), report]
    )

    class NoAssembly:
        config = runtime.models["assembly"].config

        async def complete(self, *args, **kwargs):
            pytest.fail("Final report must not call a layout or citation assembly model")

    runtime.models["assembly"] = NoAssembly()
    runtime.budget = Budget(runtime.store.root.parent, BudgetLimits(max_model_calls=30))
    runtime.budget.register(runtime.budget_account(state), SessionAllowance(completion_calls=2))
    state.research_finished = True
    for operation in ["submit_assessment", "write_report"]:
        assert submission.completion_operations(state)[0] == operation
        await runtime._formal_submission(state, author, operation=operation, submission=submission)
        plan = runtime.budget.data["sessions"][state.session_id]["completion_plan"]
        assert not any("assembly" in step for step in plan["steps"])
    result = submission.result()
    assert result["scientific_status"] == "completed"
    assert submission.completion_operations(state) == []
    assert set(cited_ids(result["assessment"]["body"])) == {handle}
    assert result["assessment"]["dimensions"] == decisions["dimensions"]
    if system.score_grades:
        assert result["assessment"]["total_score"] == sum(
            value["score"] for value in decisions["dimensions"].values()
        )
    from spacebio_review.publication.render import Renderer

    document, _ = compile_publication(result["assessment"], evidence=result["evidence"])
    rendered = Renderer(document).render("zh")
    assert rendered.count("## 综合评价") == 1
    assert "主审融合意见" in rendered and "safety_veto" not in rendered
    assert "实际贡献及影响本项判断的限制" not in rendered
    assert [s.section_id for s in document.main] == ["body", "grades"]
    assert (
        sum(node.kind == "grade_table" for s in document.main for node in walk_nodes(s.blocks)) == 1
    )
    inputs = json.loads(author.requests[-1][-1]["content"])
    assert inputs["decision"]["dimensions"] == decisions["dimensions"]
    assert "note_ref" not in json.dumps(inputs)
    assert "unabsorbed_observations" not in inputs
    assert len(inputs["expert_opinions"]) == (0 if chief_only else 2)
