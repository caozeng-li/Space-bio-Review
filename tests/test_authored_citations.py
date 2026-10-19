"""Frozen prose and its stable identities remain intact through assembly."""

from copy import deepcopy

import pytest

from spacebio_review.authored_markdown import Node, numbered_blocks, plain_text
from spacebio_review.prose import cited_ids, compile_authored_body, render_authored_body
from spacebio_review.v5.assembly import apply_annotations


@pytest.mark.parametrize(
    "text",
    [
        "蛋白 E1/E2 与原文参考文献[103]，页码241。（E112）",
        "`[E1]`\n\n```text\n[E2]\n```",
        "![图中 [E1] 是样本标签](figure.png)",
    ],
)
def test_source_numbers_and_biochemical_names_are_literal(text):
    assert not list(cited_ids(compile_authored_body(text)))


@pytest.mark.parametrize(
    "text,anchor",
    [
        ("细胞培养模块支持37℃。", "支持37℃。"),
        ("| 对象 | 温度 |\n| --- | --- |\n| 细胞 | 37℃ |", "37℃"),
        ("- **细胞培养模块**支持37℃。\n- 另一对象另论。", "支持37℃。"),
    ],
)
def test_assembly_inserts_only_citations_into_frozen_blocks(text, anchor):
    raw = {
        "content": text,
        "paragraphs": numbered_blocks(text),
        "session_id": "session-local",
        "author_text_ref": "author-local",
    }
    original = deepcopy(raw)
    result = apply_annotations(
        raw,
        [
            {
                "block": "p1",
                "after": "p1.4" if text.startswith("|") else "p1.1",
                "references": ["evidence-cell"],
            }
        ],
        {"evidence-cell": {}},
    )
    assert raw == original
    assert set(cited_ids(result)) == {"evidence-cell"}

    def visible(body):
        return plain_text([Node.model_validate(node) for node in body["blocks"]])

    assert visible(result) == visible(compile_authored_body(text))
    rendered = render_authored_body(result)
    assert set(cited_ids(compile_authored_body(rendered))) == {"evidence-cell"}


def test_canonical_links_are_copied_without_renumbering():
    body = compile_authored_body("条件[手册](evidence:evidence-cell)。")
    assert render_authored_body(body) == "条件[手册](evidence:evidence-cell)。"
    assert set(cited_ids(body)) == {"evidence-cell"}


async def test_unchanged_manuscript_blocks_reuse_their_original_citation_binding(tmp_path):
    from spacebio_review.v5.assembly import ManuscriptAssembly
    from spacebio_review.v5.records import ResearchNote, SessionSettings, SessionStore, SessionTask

    store = SessionStore(tmp_path)
    state = store.create(
        task=SessionTask(role="source_reader", objective="理解装置", application="资料"),
        model_alias="fixture",
        model_snapshot={},
        settings=SessionSettings(),
    )
    state.evidence = {
        "evidence-cell": {
            "reference": {"source_id": "cell", "passage_id": "p1"},
            "title": "细胞装置",
            "quote": "细胞培养模块支持37℃。",
        },
        "evidence-plant": {
            "reference": {"source_id": "plant", "passage_id": "p1"},
            "title": "植物装置",
            "quote": "植物培养模块支持17–28℃。",
        },
    }
    calls = []

    async def call(state, stage, instruction, value, schema, *, purpose):
        calls.append(value)
        source = "plant" if "新增" in value["blocks"][0]["text"] else "cell"
        return {
            "annotations": {
                value["blocks"][0]["citation_positions"][0]["position"]: {
                    "basis": "该装置的温度条件",
                    "sources": {source: {"p1": True}},
                }
            }
        }

    assembler = ManuscriptAssembly(store, call)
    first = "细胞培养模块支持37℃。"
    ref = store.save_author_text(state, first, "update_research", absorbed_through=0)
    body, _ = await assembler.assemble(state, store.author_text(ref))
    note = store.save_note(
        state, ResearchNote(title="装置", body=body), absorbed_through=0, author_text_ref=ref
    )
    second = first + "\n\n新增：植物培养模块支持17–28℃。"
    ref = store.save_author_text(state, second, "update_research", absorbed_through=0)
    body, _ = await assembler.assemble(state, store.author_text(ref), previous=store.note(note))
    assert len(calls) == 2
    assert [p["text"] for p in calls[-1]["blocks"]] == ["新增：植物培养模块支持17–28℃。"]
    assert list(cited_ids(body)) == ["evidence-cell", "evidence-plant"]
    assert "aliases" not in type(state).model_fields


def test_source_positions_are_scoped_inside_the_output_grammar():
    from spacebio_review.models.capabilities import check_schema
    from spacebio_review.v5.assembly import support_schema

    schema = support_schema(
        {
            "evidence-a": {"reference": {"source_id": "one", "passage_id": "p1"}},
            "evidence-b": {"reference": {"source_id": "two", "passage_id": "p2"}},
        }
    )
    check_schema(schema)
    scopes = {source: list(units["properties"]) for source, units in schema["properties"].items()}
    assert scopes == {"one": ["p1"], "two": ["p2"]}
