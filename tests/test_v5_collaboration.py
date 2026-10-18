"""Directed author collaboration through the real runtime and local scripted models."""

import asyncio
import inspect
import json
from collections import deque
from copy import deepcopy

from spacebio_review.evidence.store import SourceStore
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument
from spacebio_review.models.config import ModelConfig
from spacebio_review.models.provider import ChatReply
from spacebio_review.prose import cited_ids
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance
from spacebio_review.v5.chief import ChiefReview
from spacebio_review.v5.collaboration import RequestInquiry
from spacebio_review.v5.records import ContributionSelection, ReportSelection, ResearchNote
from spacebio_review.v5.session import ResearchRuntime
from spacebio_review.v5.topic import ApplicationRef, TopicResearch, TopicTask

APPLICATION = "在轨进行活细胞连续成像，返回样本用于测序；研究目标包括类核相分离。"
METHOD = "类核动态、融合与可逆性观测共同帮助区分结构变化和相分离解释。"


def action(name, **arguments):
    return True, {
        "role": "assistant", "content": None, "tool_calls": [{
            "id": "local-call", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        }],
    }


def manuscript(text):
    return False, {"role": "assistant", "content": text}


def finish(reason="现有科学稿足以回答当前问题。", **kwargs):
    return action("finish_research", reason=reason, **kwargs)


class ScriptedModel:
    def __init__(self, budget, name):
        self.budget, self.script = budget, deque()
        self.config = ModelConfig(
            model=name, base_url="https://unused.invalid", max_tokens=512, retries=0
        )
        self.requests = []

    async def complete(self, messages, tools=None, *, response_schema=None):
        assert response_schema is None
        assert self.script, f"Unexpected request to {self.config.model}."
        step = self.script.popleft()
        if callable(step):
            step = step(messages)
            if inspect.isawaitable(step):
                step = await step
        expected_tools, message = step
        assert bool(tools) == expected_tools, "Unexpected research/authoring transition."
        ticket = self.budget.reserve(self.config, messages=messages, tools=tools)
        self.requests.append({"messages": deepcopy(messages), "tools": deepcopy(tools)})
        reply = ChatReply(
            message=message, prompt_tokens=100, completion_tokens=20,
            finish_reason="tool_calls" if expected_tools else "stop",
        )
        self.budget.settle(ticket, reply)
        return reply

    @property
    def authors(self):
        return [request for request in self.requests if not request["tools"]]


def local_team(root):
    names = ("path-author", "mechanism-author", "chief-author")
    budget = Budget(
        root, BudgetLimits(max_seconds=10000),
        token_counters={name: lambda _: 92 for name in names},
    )
    models = {name: ScriptedModel(budget, name) for name in names}
    allowance = SessionAllowance(
        max_input_tokens=100000, completion_input_tokens=1000,
        completion_output_tokens=1024, completion_calls=1,
    )
    runtime = ResearchRuntime(
        root, models, {"researcher": names[0], "chief": names[2]}, budget=budget,
        allowances={"researcher": allowance, "chief": allowance},
    )
    sources = SourceStore(root / "sources", client=None, env_file=None)
    ids = []
    for title, kind, text in (
        ("Application", "application", APPLICATION), ("Methods paper", "literature", METHOD),
    ):
        source_id = sources.register(SourceCandidate(title=title, kind=kind))
        sources._save(SourceDocument(
            source_id=source_id, candidate=sources.candidate(source_id),
            availability="full_text",
            passages=[Passage(passage_id="p1", locator="Methods / p1", text=text)],
        ))
        ids.append(source_id)
    topics = TopicResearch(runtime, sources, None, application_ids=[ids[0]], parallel_topics=1)
    return topics, models, ids[0], ids[1]


def author(topics, model, application, *, objective):
    topics.runtime.routes["researcher"] = model
    return topics.create(TopicTask(
        objective=objective, impact="将原文、实际实验路径与可获得的认识连接起来。",
        application_refs=[ApplicationRef(source_id=application, passage_id="p1")],
    ))


def read_local_paper(topics, inquiry_id, paper, quote=METHOD):
    access = topics.access(inquiry_id)
    access.record_context_read({
        "source_id": paper, "title": "Methods paper",
        "passages": [{"passage_id": "p1", "text": quote}],
    })
    state = topics.state(inquiry_id)
    topics.runtime._capture(state, access.references.snapshot_choices(selected=False))
    topics.runtime.store.save(state)
    return evidence_alias(topics, inquiry_id, paper)


def evidence_alias(topics, inquiry_id, source_id):
    state = topics.state(inquiry_id)
    return next(alias for alias, handle in state.aliases.items()
                if state.evidence[handle]["reference"]["source_id"] == source_id)


def outgoing(team, sender, target):
    return next(request for request in reversed(list(team.requests.values()))
                if request.sender == sender and request.target == target
                and request.kind == "consultation")


def wait_for(team, sender, target):
    return lambda _: finish(
        "等待这一具体解释后继续自己的科学问题。",
        wait_for=[outgoing(team, sender, target).request_id],
    )


def text_of(value):
    return json.dumps(value, ensure_ascii=False)


async def seed_author(topics, model, inquiry_id, focus, text):
    model.script.extend([
        action("contribute", mode="write", focus=focus), manuscript(text), finish(),
    ])
    result = await asyncio.wait_for(topics.run(inquiry_id), timeout=10)
    assert result.status == "submitted"
    return result.note_ref


async def test_single_permit_wait_delivers_existing_science_and_publishes_before_finish(tmp_path):
    topics, models, application, paper = local_team(tmp_path)
    team, runtime = topics.collaboration, topics.runtime
    path_model, mechanism_model = models["path-author"], models["mechanism-author"]
    path = author(topics, "path-author", application, objective="理解实际观测路径")
    mechanism = author(topics, "mechanism-author", application, objective="理解类核相分离")
    paper_alias = read_local_paper(topics, mechanism, paper)
    chief = ChiefReview(topics, admission={"status": "skipped"})
    chief_id = chief.create("理解科学贡献并安排研究")
    chief_inquiry = runtime.load(chief_id).inquiry_id
    early = []
    try:
        mechanism_ref = await seed_author(
            topics, mechanism_model, mechanism, "哪些观测能支持类核相分离解释",
            f"仅有亮点变化不足以区分解释，融合与可逆性提供额外信息。[@{paper_alias}]",
        )
        team._receive(chief_inquiry, runtime.load(chief_id))
        control_text = "时间匹配对照可帮助区分成像漂移与真实生物学变化。"
        control = topics.store.register(SourceCandidate(title="Temporal-control paper"))
        topics.store._save(SourceDocument(
            source_id=control, candidate=topics.store.candidate(control), availability="full_text",
            passages=[Passage(passage_id="p1", locator="Methods / p1", text=control_text)],
        ))
        control_alias = read_local_paper(topics, mechanism, control, control_text)
        control_handle = topics.state(mechanism).aliases[control_alias]
        assert control_handle not in runtime.store.note(mechanism_ref)["evidence"]

        def request_explanation(_):
            state = topics.state(path)
            delivered = [content for content, _ in team.actors[chief_inquiry].deliveries.values()]
            early.append(next(row for row in delivered if row.get("inquiry_id") == path))
            assert state.submitted_ref is None
            assert early[-1]["contribution_ref"] == state.note_ref
            return action(
                "request_inquiry", question="连续在轨成像怎样推进类核相分离认识？",
                scientific_use="将实际实验路径连接到项目的相分离目标。",
                input_refs=[state.note_ref], target=mechanism,
            )

        def answer_existing(messages):
            assert topics.state(path).status == "waiting"
            assert "连续在轨成像怎样推进类核相分离认识" in text_of(messages)
            return finish(
                "现稿指出融合与可逆性可增加区分力；请把它们连到在轨时间序列。"
                f"时间匹配对照还可识别成像漂移。[@{control_alias}]"
            )

        def integrated_manuscript(messages):
            text = text_of(messages)
            assert mechanism_ref in text and METHOD in text and control_text in text
            alias = evidence_alias(topics, path, paper)
            return manuscript(
                f"在轨时间序列能连接类核动态与相分离假说；融合、可逆性等观测增加区分力。[@{alias}]"
            )

        def read_cited_original(messages):
            reply = json.loads(next(
                row["content"] for row in reversed(messages) if row["role"] == "tool"
            ))["result"]
            assert reply["ok"] and reply["reading_scope"] == "reread"
            contribution = reply["content"][mechanism_ref]
            assert contribution["contribution_ref"] == mechanism_ref
            assert contribution["inquiry_id"] == mechanism
            assert "融合与可逆性提供额外信息" in text_of(contribution["note"])
            assert METHOD in text_of(contribution["originals"])
            alias = evidence_alias(topics, path, paper)
            assert alias in list(cited_ids(contribution["note"]))
            return action("read_references", references=[alias])

        def compose_after_reading(messages):
            reply = json.loads(next(
                row["content"] for row in reversed(messages) if row["role"] == "tool"
            ))["result"]
            assert reply["ok"] and reply["reading_scope"] == "reread"
            alias = evidence_alias(topics, path, paper)
            assert reply["content"][alias]["reference"]["source_id"] == paper
            assert reply["content"][alias]["quote"] == METHOD
            handle = topics.state(path).aliases[alias]
            received = topics.access(path).references.snapshot_choices(selected=False)
            assert received[handle]["quote"] == METHOD
            return action("contribute", mode="write")

        def finish_after_rereading(messages):
            reply = json.loads(next(
                row["content"] for row in reversed(messages) if row["role"] == "tool"
            ))["result"]
            assert reply["ok"] and reply["reading_scope"] == "reread"
            assert reply["content"][mechanism_ref]["contribution_ref"] == mechanism_ref
            assert runtime.store.note(topics.state(path).note_ref)["revision"] == 2
            return finish("已有综合稿已吸收该解释，回读没有新增材料，无需修订。")

        mechanism_model.script.append(answer_existing)
        path_model.script.extend([
            action("contribute", mode="write", focus="在轨路径能产生什么动态认识"),
            manuscript("申请安排在轨活细胞连续成像，返回后测序；固定后探针保持不是此路线前提。[@E1]"),
            request_explanation, wait_for(team, path, mechanism),
            action("read_references", references=[mechanism_ref]),
            read_cited_original, compose_after_reading, integrated_manuscript,
            action("read_references", references=[mechanism_ref]), finish_after_rereading,
        ])
        result = await asyncio.wait_for(topics.run(path), timeout=10)

        assert result.status == "submitted"
        assert outgoing(team, path, mechanism).status == "answered"
        assert outgoing(team, path, mechanism).answer["contribution_ref"] == mechanism_ref
        assert list(cited_ids(outgoing(team, path, mechanism).answer["reason"])) == [control_handle]
        assert topics.state(path).evidence[control_handle]["quote"] == control_text
        assert early and "在轨活细胞连续成像" in text_of(early[0]["note"])
        assert len(path_model.authors) == 2 and len(mechanism_model.authors) == 1
        assert runtime.store.note(result.note_ref)["revision"] == 2
        assert topics.inquiry(mechanism).current_contribution_ref == mechanism_ref
        assert "相分离" in text_of(result.note) and METHOD in text_of(result.readings)
        assert not path_model.script and not mechanism_model.script
        assert runtime.budget.data["sessions"][topics.state(path).session_id]["reserve_start"] == 0

        # A chief consumes drafts as they arrive; final choice is frozen when dispatch ends.
        chief_state = runtime.load(chief_id)
        assert team._receive(chief_inquiry, chief_state)
        assert result.note_ref in text_of(runtime._context(chief_state))
        outcome = await team._finish(chief_inquiry, chief_state, "现有认识已足以评价。", [])
        assert outcome == "finish"
        chosen = dict(team.selected_contributions)
        calls_before = {name: len(model.requests) for name, model in models.items()}
        late_state = topics.state(mechanism)
        late_ref = runtime.store.save_note(late_state, ResearchNote.model_validate({
            "title": "Late explanation",
            "body": {"paragraphs": [{"parts": [{"text": "晚到的补充解释。", "cites": []}]}]},
        }), absorbed_through=late_state.observation_index)
        team.publish(mechanism, late_ref)
        assert late_ref != mechanism_ref
        assert team.selected_contributions == chosen
        assert chosen[mechanism] == mechanism_ref
        assert not team.actors[chief_inquiry].deliveries
        assert all(actor.task is None for actor in team.actors.values())
        assert {name: len(model.requests) for name, model in models.items()} == calls_before
    finally:
        await team.close()


async def test_first_read_of_unreceived_scientific_note_is_new_explanation(tmp_path):
    topics, models, application, _ = local_team(tmp_path)
    team, runtime = topics.collaboration, topics.runtime
    path = author(topics, "path-author", application, objective="理解实际路径")
    mechanism = author(topics, "mechanism-author", application, objective="解释动态信息")
    path_model, mechanism_model = models["path-author"], models["mechanism-author"]
    try:
        mechanism_ref = await seed_author(
            topics, mechanism_model, mechanism, "动态变化怎样增加认识",
            "连续观察可比较恢复过程，为区分机制解释提供信息。[@E1]",
        )
        # The same original is already known; the newly obtained explanation still matters.
        evidence = runtime.store.note(mechanism_ref)["evidence"]
        assert set(evidence) <= set(topics.state(path).evidence)

        def finish_after_first_read(messages):
            reply = json.loads(next(
                row["content"] for row in reversed(messages) if row["role"] == "tool"
            ))["result"]
            assert reply["ok"] and reply["reading_scope"] == "research_note"
            assert reply["content"][mechanism_ref]["contribution_ref"] == mechanism_ref
            return finish("吸收刚读到的恢复过程解释，形成更新后的综合。")

        path_model.script.extend([
            action("contribute", mode="write", focus="理解在轨观测的价值"),
            manuscript("在轨成像保留时间序列。[@E1]"),
            action("read_references", references=[mechanism_ref]), finish_after_first_read,
            manuscript("在轨时间序列可比较恢复过程，增加对不同机制解释的区分。[@E1]"),
        ])
        result = await asyncio.wait_for(topics.run(path), timeout=10)
        assert result.status == "submitted"
        assert len(path_model.authors) == 2 and not path_model.script
        assert runtime.store.note(result.note_ref)["revision"] == 2
        assert runtime.budget.data["requests"][-1]["purpose"] == "completion"
    finally:
        await team.close()


async def test_cross_consultations_answer_from_existing_drafts_then_continue_own_work(tmp_path):
    topics, models, application, paper = local_team(tmp_path)
    team = topics.collaboration
    path_model, mechanism_model = models["path-author"], models["mechanism-author"]
    path = author(topics, "path-author", application, objective="理解实际路径")
    mechanism = author(topics, "mechanism-author", application, objective="理解机制辨识")
    alias = read_local_paper(topics, mechanism, paper)
    try:
        path_ref = await seed_author(
            topics, path_model, path, "区分在轨与返回阶段", "在轨连续成像，返回样本用于测序。[@E1]",
        )
        mechanism_ref = await seed_author(
            topics, mechanism_model, mechanism, "类核相分离的观测辨识",
            f"动态融合与可逆性为机制解释提供信息。[@{alias}]",
        )

        def answer_path_question(messages):
            assert "成像是在轨还是返回后进行？" in text_of(messages)
            assert path_ref in text_of(messages)
            assert outgoing(team, path, mechanism).status == "assigned"
            return finish("现稿已有直接申请依据：成像在轨完成，返回后用于测序。")

        def answer_mechanism_question(messages):
            assert "成像在轨完成，返回后用于测序" in text_of(messages)
            assert outgoing(team, mechanism, path).status == "answered"
            return finish("沿用现稿的融合与可逆性解释，可用于在轨连续成像的科学目标。")

        path_model.script.extend([
            action("request_inquiry", question="哪些动态观测有助于区分机制？",
                   scientific_use="解释在轨成像的科学价值。",
                   input_refs=[path_ref], target=mechanism),
            wait_for(team, path, mechanism), answer_path_question,
            finish("已把路径和机制解释连接，可以形成当前综合。"),
            manuscript("在轨连续成像提供动态过程，融合与可逆性观测可推进机制区分。"),
        ])
        mechanism_model.script.extend([
            action("request_inquiry", question="成像是在轨还是返回后进行？",
                   scientific_use="判断动态机制解释适用于哪个阶段。",
                   input_refs=[mechanism_ref], target=path),
            wait_for(team, mechanism, path), answer_mechanism_question,
        ])
        result = await asyncio.wait_for(
            topics.run(path, followup="把现有实验路径与机制解释连接起来。"), timeout=10,
        )

        assert result.status == "submitted"
        assert outgoing(team, path, mechanism).status == "answered"
        assert outgoing(team, mechanism, path).status == "answered"
        assert outgoing(team, mechanism, path).answer["contribution_ref"] == path_ref
        assert topics.inquiry(mechanism).current_contribution_ref == mechanism_ref
        assert len(path_model.authors) == 2 and len(mechanism_model.authors) == 1
        assert not path_model.script and not mechanism_model.script
        assert "融合与可逆性" in text_of(result.note)
        assert all(not actor.work and not actor.incoming for actor in team.actors.values())
    finally:
        await team.close()


async def test_queued_consultation_uses_new_owner_and_the_original_budget_account(tmp_path):
    topics, models, application, _ = local_team(tmp_path)
    team, runtime = topics.collaboration, topics.runtime
    path = author(topics, "path-author", application, objective="理解实际路径")
    mechanism = author(topics, "mechanism-author", application, objective="理解机制辨识")
    model, replacement = models["mechanism-author"], models["chief-author"]
    try:
        note_ref = await seed_author(
            topics, model, mechanism, "解释成像阶段", "成像在轨完成，返回后用于测序。[@E1]",
        )
        original = topics.state(mechanism).session_id
        usage = runtime.budget.usage(original)["model_calls"]
        accounts = set(runtime.budget.data["sessions"])
        reserve_start = runtime.budget.data["sessions"][original]["reserve_start"]
        request = team.request(path, RequestInquiry(
            question="实际成像阶段怎样影响动态解释？", scientific_use="连接观测和知识。",
            input_refs=[], target=mechanism,
        ))
        replacement.script.append(finish("当前稿已说明在轨成像，动态解释应对应这个阶段。"))
        new_owner = await team.transfer(
            mechanism, reason="由主审模型接手这一科学职责。", model_role="chief",
        )
        await asyncio.wait_for(team._wait(mechanism), timeout=10)

        inquiry = topics.inquiry(mechanism)
        assert new_owner != original
        assert inquiry.current_owner_session_id == new_owner
        assert inquiry.budget_account_id == original
        assert inquiry.current_contribution_ref == note_ref
        assert set(runtime.budget.data["sessions"]) == accounts
        assert runtime.budget.usage(original)["model_calls"] == usage + 1
        assert runtime.budget.data["sessions"][original]["reserve_start"] == reserve_start
        assert team.requests[request["request_id"]].answer["contribution_ref"] == note_ref
        assert len(model.requests) == 3 and len(replacement.requests) == 1
        assert not replacement.authors and not replacement.script
        assert runtime.budget.data["requests"][-1]["session_id"] == original
        assert runtime.budget.data["requests"][-1]["purpose"] == "research"
    finally:
        await team.close()


async def test_chief_waits_then_uses_an_early_contribution_and_closes_dispatch(
    tmp_path, monkeypatch
):
    topics, models, application, _ = local_team(tmp_path)
    team, runtime = topics.collaboration, topics.runtime
    chief = ChiefReview(topics, admission={"status": "skipped"})
    chief_id = chief.create("理解科学贡献并安排开放研究")
    chief_model, path_model = models["chief-author"], models["path-author"]
    chief_waiting, author_after_draft = asyncio.Event(), asyncio.Event()
    never_finish = asyncio.Event()
    early_refs = []
    run = runtime.run

    async def observe_waiting(session_id, **kwargs):
        result = await run(session_id, **kwargs)
        if session_id == chief_id and result.status == "waiting":
            chief_waiting.set()
        return result

    monkeypatch.setattr(runtime, "run", observe_waiting)

    def wait_for_arrangement(_):
        request = next(request for request in team.requests.values() if request.kind == "research")
        assert topics.state(request.target).note_ref is None
        return finish("先等待这一作者产生科学认识。", wait_for=[request.request_id])

    async def begin_author(_):
        await chief_waiting.wait()
        assert runtime.load(chief_id).status == "waiting"
        return action("contribute", mode="write", focus="实际成像路径可以获得什么认识")

    async def continue_author(_):
        author_after_draft.set()
        await never_finish.wait()
        raise AssertionError("Chief closure should stop further exploration.")

    async def use_early_contribution(messages):
        await author_after_draft.wait()
        path = chief._manifest(chief_id)["topic_ids"][0]
        state = topics.state(path)
        assert state.note_ref and state.submitted_ref is None
        assert state.status == "running"
        assert state.note_ref in text_of(messages)
        assert "在轨动态读出" in text_of(messages)
        early_refs.append(state.note_ref)
        return finish("已有科学解释足以形成当前判断，结束继续投入。")

    chief_model.script.extend([
        action("research_topics", requests=[{"task": {
            "objective": "从原申请识别动态读出能够回答的问题",
            "impact": "理解预期数据怎样形成有价值的认识",
            "application_refs": [{"source_id": application, "passage_id": "p1"}],
        }}]),
        wait_for_arrangement, use_early_contribution,
        manuscript("在轨动态读出具有纵向观察价值；应将时间变化与返回测序的不同用途连接。[@E1]"),
    ])
    path_model.script.extend([
        begin_author, manuscript("在轨动态读出提供纵向观察，返回测序另有用途。[@E1]"),
        continue_author,
    ])
    try:
        result = await asyncio.wait_for(chief.run(chief_id), timeout=10)
        path = chief._manifest(chief_id)["topic_ids"][0]
        assert result["status"] == "submitted"
        assert team.closed and team.selected_contributions[path] == early_refs[0]
        assert len(chief_model.authors) == 1 and len(path_model.authors) == 1
        assert len(chief_model.requests) == 4 and len(path_model.requests) == 2
        assert all(request["tools"] for request in chief_model.requests[:-1])
        assert not chief_model.script and not path_model.script
        assert "纵向观察价值" in text_of(result["note"])
        assert all(actor.task is None for actor in team.actors.values())
        before = len(chief_model.requests)
        team.publish(path, early_refs[0])
        assert not team.actors[team.chief_inquiry].deliveries
        assert len(chief_model.requests) == before
    finally:
        await team.close()


async def test_consultation_queued_at_writing_boundary_reuses_the_authors_current_draft(tmp_path):
    topics, models, application, _ = local_team(tmp_path)
    team = topics.collaboration
    path = author(topics, "path-author", application, objective="理解实际路径")
    mechanism = author(topics, "mechanism-author", application, objective="理解机制解释")
    model = models["path-author"]
    requests = []

    def begin_writing(_):
        requests.append(team.request(mechanism, RequestInquiry(
            question="实际成像发生在哪个阶段？", scientific_use="确定动态解释的适用阶段。",
            target=path,
        )))
        return action("contribute", mode="write", focus="区分成像与测序的阶段")

    def write_original_focus(messages):
        context = json.loads(messages[1]["content"])
        assert context["working_context"]["collaboration"]["current_work"]["kind"] == "research"
        assert context["task"]["objective"] == "区分成像与测序的阶段"
        return manuscript("申请在轨连续成像，返回样本用于测序。[@E1]")

    def answer_after_writing(messages):
        context = json.loads(messages[1]["content"])
        current = context["working_context"]["collaboration"]["current_work"]
        assert current["kind"] == "consultation"
        assert current["request_id"] == requests[0]["request_id"]
        assert "申请在轨连续成像，返回样本用于测序。" in text_of(context["current_research"])
        return finish("刚发布的稿已给出直接依据：在轨成像，返回后测序。")

    def finish_own_work(messages):
        context = json.loads(messages[1]["content"])
        assert context["working_context"]["collaboration"]["current_work"]["kind"] == "research"
        assert team.requests[requests[0]["request_id"]].status == "answered"
        return finish("原稿也已完成自己的阶段分析，无需再次写作。")

    model.script.extend([
        begin_writing, write_original_focus, answer_after_writing, finish_own_work,
    ])
    try:
        result = await asyncio.wait_for(topics.run(path), timeout=10)
        request = team.requests[requests[0]["request_id"]]
        assert result.status == "submitted"
        assert request.answer["contribution_ref"] == result.note_ref
        assert len(model.authors) == 1 and len(model.requests) == 4
        assert not model.script
        assert topics.runtime.store.note(result.note_ref)["revision"] == 1
        assert topics.runtime.store.note(result.note_ref)["previous_ref"] is None
        assert not models["mechanism-author"].requests
    finally:
        await team.close()


async def test_assign_new_specialist_authors_its_first_manuscript_with_completion_budget(tmp_path):
    topics, models, application, _ = local_team(tmp_path)
    team, runtime = topics.collaboration, topics.runtime
    path = author(topics, "path-author", application, objective="解释实际实验路径")
    request = team.request(path, RequestInquiry(
        question="连续观测如何连接到机制辨识？", scientific_use="说明此计划可以增加什么知识。",
        input_refs=["E1"], target="类核动态与机制辨识",
    ))
    assert request["status"] == "pending" and request["target_inquiry"] is None
    specialist = author(topics, "mechanism-author", application, objective="研究可用的机制解释")
    account = topics.inquiry(specialist).budget_account_id
    accounts = set(runtime.budget.data["sessions"])
    model = models["mechanism-author"]

    def choose_focus(messages):
        context = json.loads(messages[1]["content"])
        assert context["working_context"]["collaboration"]["current_work"]["question"] == (
            "连续观测如何连接到机制辨识？"
        )
        assert APPLICATION in text_of(messages)
        return action("contribute", mode="focus_only", focus="哪些动态观测可以区分机制解释")

    model.script.extend([
        choose_focus, finish("已明确科学解释，形成首次完整稿。"),
        manuscript("连续观测保留变化过程，配合对照可比较不同机制解释。[@E1]"),
    ])
    try:
        team.assign(request["request_id"], specialist)
        await asyncio.wait_for(team._wait(specialist), timeout=10)
        state = topics.state(specialist)
        answer = team.requests[request["request_id"]].answer
        assert state.status == "submitted" and state.submitted_ref == state.note_ref
        assert answer["contribution_ref"] == state.note_ref
        assert team.requests[request["request_id"]].status == "answered"
        assert runtime.store.note(state.note_ref)["revision"] == 1
        assert len(team.requests) == 1 and len(model.authors) == 1 and not model.script
        assert set(runtime.budget.data["sessions"]) == accounts
        assert runtime.budget.usage(account)["model_calls"] == 3
        assert runtime.budget.data["requests"][-1]["purpose"] == "completion"
        assert {row["session_id"] for row in runtime.budget.data["requests"]} == {account}
    finally:
        await team.close()


async def test_external_close_cancels_a_chief_still_researching(tmp_path):
    topics, models, _, _ = local_team(tmp_path)
    team = topics.collaboration
    chief = ChiefReview(topics, admission={"status": "skipped"})
    chief_id = chief.create("理解科学项目")
    started, blocked = asyncio.Event(), asyncio.Event()

    async def researching(_):
        started.set()
        await blocked.wait()
        raise AssertionError("External close should cancel the current chief execution.")

    models["chief-author"].script.append(researching)
    running = asyncio.create_task(chief.run(chief_id))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        await asyncio.wait_for(team.close(), timeout=10)
        result = await asyncio.wait_for(running, timeout=10)
        assert result["status"] == "partial" and result["reason"] == "session_cancelled"
        assert team.closed and result["note_ref"] is None
        assert all(actor.task is None for actor in team.actors.values())
        assert not models["chief-author"].authors
    finally:
        await team.close()
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)


async def test_explicit_chief_followup_reopens_dispatch_and_clears_previous_selection(tmp_path):
    topics, models, _, _ = local_team(tmp_path)
    team, runtime = topics.collaboration, topics.runtime
    chief = ChiefReview(topics, admission={"status": "skipped"})
    chief_id = chief.create("理解已有科学认识")
    state = runtime.load(chief_id)
    state.coordination_phase = "synthesis"
    runtime.store.save(state)
    model = models["chief-author"]
    model.script.extend([finish(), manuscript("当前认识支持此项研究。")])
    try:
        first = await chief.run(chief_id)
        assert first["status"] == "submitted" and team.closed
        old_note = runtime.store.note(first["note_ref"])
        state = runtime.load(chief_id)
        state.report_selection = ReportSelection(main_sections=[ContributionSelection(
            inquiry_id="inquiry-science", contribution_ref="note-old"
        )])
        state.report_candidates = [{"contribution_ref": "note-old"}]
        runtime.store.save(state)

        def continue_research(messages):
            assert not team.closed and team.selected_contributions == {}
            current = runtime.load(chief_id)
            assert current.report_selection is None and current.report_candidates == []
            assert "新增知识" in text_of(messages)
            return finish("已经将新增知识纳入判断。")

        model.script.extend([continue_research, manuscript("新增知识使科学价值更加具体。")])
        second = await chief.run(chief_id, followup="依据新增知识继续研究。")
        assert second["status"] == "submitted" and team.closed
        assert second["note_ref"] != first["note_ref"]
        assert runtime.store.note(first["note_ref"]) == old_note
        assert not model.script
    finally:
        await team.close()
