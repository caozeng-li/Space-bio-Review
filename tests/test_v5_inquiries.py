"""Persistent question and manuscript identities, without a model or review run."""

from copy import deepcopy

import pytest

from spacebio_review.v5.authoring import research_note
from spacebio_review.v5.records import SessionSettings, SessionStore, SessionTask


def session(store):
    return store.create(
        task=SessionTask(
            role="researcher", objective="最初的知识职责", application="申请原文保持不变"
        ),
        settings=SessionSettings(),
        model_alias="author",
        model_snapshot={"model": "local-test"},
    )


def inquiry(store, state):
    return store.create_inquiry(
        {"objective": "最初的问题", "impact": "需要重新理解的前提"},
        owner_session_id=state.session_id,
        budget_account_id=state.session_id,
        application_anchors=[{"source_id": "application", "passage_id": "p1"}],
    )


def manuscript(store, state, text, *, focus=None, reason=None):
    raw_ref = store.save_author_text(
        state,
        text,
        "update_research",
        absorbed_through=state.observation_index,
        focus=focus,
        reason=reason,
    )
    raw = store.author_text(raw_ref)
    note_ref = store.save_note(
        state,
        research_note(raw, title="当前科学认识"),
        absorbed_through=raw["absorbed_through"],
        author_text_ref=raw_ref,
    )
    return raw_ref, note_ref


def test_focus_only_keeps_previous_contribution_and_original_assignment(tmp_path):
    store = SessionStore(tmp_path)
    author = session(store)
    question = inquiry(store, author)
    assert store.load(author.session_id).inquiry_id == question.inquiry_id
    author = store.load(author.session_id)
    store.set_focus(question.inquiry_id, "返回样品成像", reason="初次理解")
    _, first = manuscript(store, author, "已有方法认识。")
    first_artifact = store.contribution(question.inquiry_id, first)

    current = store.set_focus(
        question.inquiry_id, "在轨活细胞成像与返回测序的互补性", reason="申请实际采用在轨成像"
    )
    assert current.assignment == question.assignment
    assert current.application_anchors == question.application_anchors
    assert current.current_contribution_ref == first
    assert current.focus_reason == "申请实际采用在轨成像"
    assert store.contribution(question.inquiry_id, first) == first_artifact
    assert first_artifact["focus"] == "返回样品成像"
    assert len(list((store.root / "notes").glob("*.json"))) == 1


def test_contribution_chain_freezes_focus_and_preserves_scientific_text(tmp_path):
    store = SessionStore(tmp_path)
    author = session(store)
    question = inquiry(store, author)
    author = store.load(author.session_id)
    author.aliases = {"E1": "evidence-alpha"}
    author.evidence = {"evidence-alpha": {"quote": "10 μg mL⁻¹", "locator": "方法 §2"}}
    store.save(author)
    store.set_focus(question.inquiry_id, "对象与读出")
    first_raw, first = manuscript(store, author, "剂量为“10 μg mL⁻¹”，ΔF/F₀ = 0.2。[@E1]")
    first_artifact = deepcopy(store.contribution(question.inquiry_id, first))

    text = "在轨得到时序形态，返回后测序提供分子状态。[@E1]\n\n二者共同限定解释，不能互相替代。"
    second_raw = store.save_author_text(
        author, text, "update_research", absorbed_through=0,
        focus="时序形态与分子状态的互补性", reason="依据申请实际路线重新定题",
    )
    frozen = store.author_text(second_raw)
    assert frozen["previous_ref"] == first
    # A later focus change must not relabel a manuscript already authored.
    store.set_focus(question.inquiry_id, "尚未写入本稿的问题")
    second = store.save_note(
        author, research_note(frozen, title="新认识"), absorbed_through=0,
        author_text_ref=second_raw,
    )
    current = store.inquiry(question.inquiry_id)
    artifact = store.contribution(question.inquiry_id, second)
    assert current.current_contribution_ref == second
    assert current.current_focus == artifact["focus"] == frozen["focus"]
    assert artifact["previous_ref"] == first and artifact["revision"] == 2
    assert artifact["focus_reason"] == "依据申请实际路线重新定题"
    assert artifact["author_session_id"] == author.session_id
    assert store.author_text(second_raw)["content"] == text
    assert store.author_text(first_raw)["content"] == "剂量为“10 μg mL⁻¹”，ΔF/F₀ = 0.2。[@E1]"
    assert artifact["note"]["body"]["paragraphs"][0]["parts"] == [
        {"text": "在轨得到时序形态，返回后测序提供分子状态。", "cites": ["evidence-alpha"]}
    ]
    assert artifact["evidence"] == author.evidence
    assert store.contribution(question.inquiry_id, first) == first_artifact
    with pytest.raises(ValueError, match="contribution_inquiry_mismatch"):
        store.contribution("inquiry-another", second)


def test_new_owner_continues_same_chain_with_own_local_aliases(tmp_path):
    store = SessionStore(tmp_path)
    first_author = session(store)
    question = inquiry(store, first_author)
    first_author = store.load(first_author.session_id)
    store.set_focus(question.inquiry_id, "研究问题")
    first_author.aliases = {"E1": "evidence-first"}
    first_author.evidence = {"evidence-first": {"quote": "前人方法"}}
    _, previous = manuscript(store, first_author, "前人的方法适用于该条件。[@E1]")

    second_author = session(store)
    second_author.aliases = {"E1": "evidence-second"}
    second_author.evidence = {"evidence-second": {"quote": "新方法"}}
    store.save(second_author)
    assigned = store.assign_inquiry(question.inquiry_id, second_author.session_id)
    second_author = store.load(second_author.session_id)
    assert assigned.current_owner_session_id == second_author.session_id
    assert assigned.budget_account_id == first_author.session_id
    assert assigned.current_contribution_ref == previous
    assert second_author.aliases == {"E1": "evidence-second"}
    # Runtime capture imports evidence by stable identity and gives it a local alias.
    second_author.evidence.update(store.contribution(question.inquiry_id, previous)["evidence"])
    second_author.aliases["E2"] = "evidence-first"
    _, current = manuscript(store, second_author, "新方法读出更丰富。[@E1]旧方法仍有用途。[@E2]")
    artifact = store.contribution(question.inquiry_id, current)
    assert artifact["revision"] == 2 and artifact["previous_ref"] == previous
    assert artifact["author_session_id"] == second_author.session_id
    assert [part["cites"] for part in artifact["note"]["body"]["paragraphs"][0]["parts"]] == [
        ["evidence-second"], ["evidence-first"],
    ]
    assert store.inquiry(question.inquiry_id).budget_account_id == first_author.session_id


def test_contribution_absorbs_only_observations_in_its_writing_input(tmp_path):
    store = SessionStore(tmp_path)
    author = session(store)
    question = inquiry(store, author)
    author = store.load(author.session_id)
    store.set_focus(question.inquiry_id, "完整科学问题")
    author.observation_index = 2
    author.observations = [{"index": 1, "content": "旧材料"}, {"index": 2, "content": "新材料"}]
    store.save(author)
    raw_ref = store.save_author_text(author, "连续解释。", "update_research", absorbed_through=1)
    raw = store.author_text(raw_ref)
    note = research_note(raw, title="科学稿")

    current = store.save_note(author, note, absorbed_through=1, author_text_ref=raw_ref)
    assert store.inquiry(question.inquiry_id).current_contribution_ref == current
    assert store.load(author.session_id).note_ref == current
    assert author.revision == 1
    assert author.observations == [{"index": 2, "content": "新材料"}]
    assert len(list((store.root / "notes").glob("*.json"))) == 1
