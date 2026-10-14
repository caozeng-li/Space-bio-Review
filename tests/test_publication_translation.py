import asyncio
import json

import pytest
from markdown_it import MarkdownIt
from test_assessed_publication import draft_fixture, frozen_fixture, prose

from spacebio_review.agents.contracts import example_for
from spacebio_review.models.provider import ChatReply
from spacebio_review.v4.assessed_delivery import run_downstream
from spacebio_review.v4.manuscript_call import ManuscriptCall, ManuscriptConfig
from spacebio_review.v4.publication_compile import compile_publication
from spacebio_review.v4.publication_document import Section, parse_text, walk_nodes
from spacebio_review.v4.publication_render import Renderer
from spacebio_review.v4.publication_translation import (
    text_batches,
    translate_section,
    translation_fragment,
)
from spacebio_review.v4.records import Records


@pytest.mark.parametrize(
    "text,expected",
    [
        ("。条件仍不确定。", ("条件仍不确定", ". ", ".")),
        (".5 mM", (".5 mM", "", "")),
        ("-2 °C。", ("-2 °C", "", ".")),
        ("0万元 ", ("0万元", "", " ")),
    ],
)
def test_translation_edges_keep_numbers_and_units(text, expected):
    assert translation_fragment(text) == expected


def test_batches_keep_whole_paragraphs():
    section = Section(section_id="s", title="Test", blocks=parse_text("Alpha.\n\nBeta.\n\nGamma."))
    assert len(text_batches(section, 8)) == 3
    assert all(
        len(batch) == 1 and batch[0].kind == "paragraph" for batch in text_batches(section, 8)
    )


@pytest.mark.asyncio
async def test_translation_sees_only_public_words_and_reuses_unchanged_chunks(tmp_path):
    captured = []

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, **kwargs):
            data = json.loads(messages[1]["content"])
            captured.append(data)
            assert "E1" not in str(data) and "evidence" not in data
            return ChatReply({"content": json.dumps(data)})

        async def close(self):
            pass

    caller = ManuscriptCall(Records(tmp_path), ManuscriptConfig.load(), model_factory=Model)
    section = Section(
        section_id="test",
        title="Test",
        title_en="Test",
        blocks=parse_text("First 0万元[@E1].\n\nSecond .5 mM."),
    )
    first = await translate_section(caller, section, batch_chars=15)
    assert [n.target for n in walk_nodes(first.blocks) if n.kind == "citation"] == ["E1"]
    assert len(captured) == 2
    next(n for n in walk_nodes(section.blocks) if n.kind == "text").text += " changed"
    await translate_section(caller, section, batch_chars=15)
    assert len(captured) == 3


@pytest.mark.asyncio
async def test_structured_condition_paragraphs_keep_groups_and_local_citations(tmp_path):
    frozen = frozen_fixture()
    assessment = draft_fixture(frozen)
    assessment["conditions"] = [
        {
            "timing": "before_support",
            "text": {
                "paragraphs": [
                    {
                        "parts": [
                            {"text": "确认测量能力", "cites": ["E1"]},
                            {"text": "，再确认重复性。", "cites": ["E2"]},
                        ]
                    },
                    {"parts": [{"text": "若能力未达到，缩小观测范围。", "cites": ["E2"]}]},
                ]
            },
        },
        {
            "timing": "implementation_milestone",
            "text": prose("实施前完成接口确认。", ["E1"]),
        },
    ]
    document, _ = compile_publication(
        assessment, {"summary": prose("摘要。"), "topic_1": prose("附录。")}, frozen
    )
    section = next(row for row in document.main if row.section_id == "conditions")
    before = section.model_dump()
    translations = {
        "确认测量能力": "Confirm measurement capability",
        "再确认重复性": "then confirm repeatability",
        "若能力未达到，缩小观测范围": "If capability is insufficient, narrow the observation scope",
        "实施前完成接口确认": "Complete interface verification before implementation",
    }
    captured = []

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, **kwargs):
            assert not tools
            data = json.loads(messages[1]["content"])
            captured.append(data)
            assert set(data) == {"title", "units"}
            assert set(data["units"].values()) <= translations.keys()
            return ChatReply(
                {
                    "content": json.dumps(
                        {
                            "title": "Unused model title",
                            "units": {
                                key: translations[text] for key, text in data["units"].items()
                            },
                        }
                    )
                }
            )

        async def close(self):
            pass

    caller = ManuscriptCall(Records(tmp_path), ManuscriptConfig.load(), model_factory=Model)
    translated = await translate_section(caller, section, batch_chars=12)
    assert len(captured) == 3
    assert list(captured[0]["units"].values()) == ["确认测量能力", "再确认重复性"]
    assert section.model_dump() == before
    assert translated.title == "Support conditions"
    assert [
        (n.kind, n.target, n.attrs, len(n.children)) for n in walk_nodes(translated.blocks)
    ] == [(n.kind, n.target, n.attrs, len(n.children)) for n in walk_nodes(section.blocks)]
    assert [n.target for n in walk_nodes(translated.blocks) if n.kind == "evidence"] == [
        "E1",
        "E2",
        "E2",
        "E1",
    ]
    assert [
        len(item.children)
        for node in translated.blocks
        if node.kind == "ordered_list"
        for item in node.children
    ] == [2, 1]
    markdown = Renderer(document, "en").render("en", [translated])
    assert "### Before a support decision" in markdown
    assert "### Implementation milestones" in markdown
    assert "### Recommendations" not in markdown
    assert "\n   \n   If capability is insufficient" in markdown
    html = MarkdownIt().render(markdown)
    assert html.count("<ol>") == 2
    assert 'href="#ref-1">[1]</a>' in html
    assert 'href="#ref-2">[2]</a>' in html
    assert await translate_section(caller, section, batch_chars=12) == translated
    assert len(captured) == 3


@pytest.mark.asyncio
async def test_translation_interruption_resumes_completed_sections(tmp_path, monkeypatch):
    import spacebio_review.v4.assessed_delivery as module

    frozen, calls = frozen_fixture(), []

    class Model:
        def __init__(self, config):
            self.config = config

        async def complete(self, messages, tools=None, *, response_schema=None, **kwargs):
            calls.append(messages)
            value = example_for(response_schema)
            if "dimensions" in value:
                value = draft_fixture(frozen)
            else:
                for unit in value.values():
                    if isinstance(unit, dict) and "parts" in unit:
                        unit["parts"][0]["cites"] = ["E1"]
            return ChatReply({"content": json.dumps(value)})

        async def close(self):
            pass

    caller = ManuscriptCall(Records(tmp_path), ManuscriptConfig.load(), model_factory=Model)
    ref = caller.records.put("frozen-review", frozen)
    original = module.translate_section

    async def interrupted(caller, section, **kwargs):
        if section.section_id == "project":
            raise asyncio.CancelledError()
        return await original(caller, section, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(module, "translate_section", interrupted)
        with pytest.raises(asyncio.CancelledError):
            await run_downstream(caller, ref, tmp_path)
    checkpoint = json.loads((tmp_path / "delivery.json").read_text("utf-8"))
    assert set(checkpoint["translations"]) == {"summary", "grades"}
    assert checkpoint["status"] == "partial"
    partial = (tmp_path / "report.en.md").read_text("utf-8")
    assert "Incomplete delivery" not in partial and "missing_sections" not in partial
    first = len(calls)
    await run_downstream(caller, ref, tmp_path)
    assert len(calls) > first
    complete = len(calls)
    await run_downstream(caller, ref, tmp_path)
    assert len(calls) == complete
    html = MarkdownIt().render((tmp_path / "report.md").read_text("utf-8"))
    assert 'href="#ref-1">[1]</a>' in html
