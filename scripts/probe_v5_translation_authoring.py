"""Bounded translation/paragraph-mapping probes, never a complete review."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_delivery import fixture, selection  # noqa: E402

from spacebio_review.models.config import ModelConfig  # noqa: E402
from spacebio_review.models.provider import ChatReply, model_class  # noqa: E402
from spacebio_review.models.scheduling import RequestScheduler, ScheduledModel  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.publication import Renderer  # noqa: E402
from spacebio_review.publication.document import (  # noqa: E402
    Location,
    Node,
    PublicationDocument,
    Section,
    Source,
    text_slots,
    walk_nodes,
)
from spacebio_review.publication.translation import (  # noqa: E402
    TranslationFailure,
    translate_section,
)
from spacebio_review.review.evaluation import GBT42048, GENERAL  # noqa: E402
from spacebio_review.v5.authoring import DeliveryParser  # noqa: E402
from spacebio_review.v5.budget import (  # noqa: E402
    Budget,
    BudgetLimits,
    CompletionReserve,
    SessionAllowance,
    exhausted,
)
from spacebio_review.v5.config import load_config  # noqa: E402
from spacebio_review.v5.delivery import deliver_assessment  # noqa: E402

SUMMARY = (
    "计划书未提供团队既往空间实验经验的证据；这不等于团队没有相关经验。"
    "计划中的“线粒体机制”仍需区分在轨荧光观测与返回后分子检测。"
)
TRANSLATIONS = {
    SUMMARY[:-1]: (
        "The proposal provides no evidence of the team's previous spaceflight "
        "experience; this does not mean that the team has none. The proposed "
        '"mitochondrial mechanism" still requires a distinction between in-orbit '
        "fluorescence observations and molecular measurements after sample return"
    ),
    "测量范围": "Measurement scope",
    "DCF 表征细胞内 ROS": "DCF indicates intracellular ROS",
    "MitoSOX 表征线粒体相关 ROS（0.5 μM）": (
        "MitoSOX indicates mitochondria-associated ROS (0.5 μM)"
    ),
    "不同探针的读出对象需要区分": (
        "The readout targets of the different probes must be distinguished"
    ),
    "检测计划在返回后实施": "The measurements are planned after sample return",
    "保持": "Keep",
    "对象": "the target",
    "与条件": "and conditions",
}


def text(value):
    return Node(kind="text", text=value)


def fragments():
    return [
        Section(
            section_id="summary",
            title="摘要",
            title_en="Summary",
            blocks=[
                Node(kind="paragraph", children=[text(SUMMARY)]),
            ],
        ),
        Section(
            section_id="inline",
            title="固定测量片段",
            title_en="Fixed measurement fragment",
            blocks=[
                Node(kind="heading", attrs={"level": 3}, children=[text("测量范围")]),
                Node(
                    kind="paragraph",
                    children=[
                        text("DCF 表征细胞内 ROS"),
                        Node(kind="evidence", target="eA"),
                        text("；MitoSOX 表征线粒体相关 ROS（0.5 μM）"),
                        Node(kind="evidence", target="eB"),
                        text("。不同探针的读出对象需要区分。"),
                    ],
                ),
                Node(
                    kind="bullet_list",
                    children=[
                        Node(
                            kind="list_item",
                            children=[
                                Node(kind="paragraph", children=[text("检测计划在返回后实施。")]),
                            ],
                        )
                    ],
                ),
                Node(
                    kind="paragraph",
                    children=[
                        text("保持"),
                        Node(kind="strong", children=[text("对象")]),
                        text("与条件。"),
                    ],
                ),
            ],
        ),
    ]


class FixedModel:
    def __init__(self, budget, *, parser=False):
        self.config = ModelConfig(
            model="fixed-parser" if parser else "fixed-translator",
            base_url="https://local.invalid/v1",
            max_tokens=2048,
            tools=False,
            structured_output="json_object",
            retries=0,
        )
        self.budget, self.parser = budget, parser
        self.requests = []

    async def complete(self, messages, tools=None, *, response_schema=None):
        ticket = self.budget.reserve(self.config, messages, tools, response_schema)
        data = json.loads(messages[-1]["content"])
        self.requests.append(data)
        if self.parser:
            assert "科学作者" not in messages[0]["content"]
            rows, active = [], None
            for row in data["paragraphs"]:
                if active is None or active["unit_id"] != row.get("unit_label"):
                    active = {"unit_id": row["unit_label"], "paragraph_ids": []}
                    rows.append(active)
                active["paragraph_ids"].append(row["id"])
            content = json.dumps({"units": rows, "missing_fields": []})
        else:
            assert response_schema is None
            assert "contexts" in data
            values = []
            for unit, source in data["units"].items():
                value = TRANSLATIONS.get(source, "EN " + source)
                if source == SUMMARY[:-1]:
                    # A single text node can consume two manuscript blocks.
                    value = value.replace(". The proposed", ".\n\nThe proposed")
                values.append(f"## {unit}\n\n{value}")
            content = "\n\n".join(values)
            if data["title"] == "固定测量片段":
                # Real writers may omit all blank lines between the given U headings.
                content = content.replace("\n\n", "\n")
        reply = ChatReply(
            message={"role": "assistant", "content": content},
            prompt_tokens=150,
            completion_tokens=120,
        )
        self.budget.settle(ticket, reply)
        return reply


def engine(output, calls=100):
    return SimpleNamespace(
        store=SimpleNamespace(root=output / "v5"),
        budget=Budget(output, BudgetLimits(max_model_calls=calls, max_seconds=900)),
        scheduler=RequestScheduler(),
    )


def plan(engine, owner, requests):
    costs = [
        engine.budget.measure(
            row["config"], row["messages"], response_schema=row.get("response_schema")
        )
        for row in requests
    ]
    reason = engine.budget.update_completion(
        owner,
        CompletionReserve(
            calls=len(costs),
            steps=[row["name"] for row in requests],
            input_tokens=sum(
                row["input_tokens"] + request.get("anticipated_input_tokens", 0)
                for request, row in zip(requests, costs)
            ),
            output_tokens=sum(row["output_reserve"] for row in costs),
        ),
    )
    if reason:
        raise exhausted(reason)


async def translate(engine, model, parser, section, output, owner="local-translation"):
    engine.budget.register(owner, SessionAllowance(completion_calls=0))
    engine.budget.activate(owner)
    try:
        with engine.budget.scope(owner, purpose="completion"):
            async with asyncio.timeout(engine.budget.remaining_seconds()):
                return await translate_section(
                    ScheduledModel(model, engine.scheduler, "translation"),
                    section,
                    parser=parser,
                    artifact_dir=output / "translations",
                    prepare_requests=lambda requests: plan(engine, owner, requests),
                    request_scope=lambda role: engine.scheduler.task(owner, role, "delivery"),
                )
    finally:
        engine.budget.finish(owner)


def document(sections):
    return PublicationDocument(
        main=sections,
        appendix=[],
        recommendation="support",
        sources={
            "a": Source(title="DCF fixture", kind="external"),
            "b": Source(title="MitoSOX fixture", kind="external"),
        },
        locations={"eA": Location(source="a"), "eB": Location(source="b")},
        numbering={"a": 1, "b": 2},
    )


async def offline(output):
    run = engine(output)
    author, mapper = FixedModel(run.budget), FixedModel(run.budget, parser=True)
    parser = DeliveryParser(ScheduledModel(mapper, run.scheduler, "delivery_parser"))
    summary, inline = fragments()
    translated_summary = await translate(run, author, parser, summary, output)
    assert len(author.requests) == 1 and len(mapper.requests) == 1
    assert len(text_slots(translated_summary)[0]) == 2
    assert (
        '. The proposed "mitochondrial mechanism"' in translated_summary.blocks[0].children[0].text
    )
    translated_inline = await translate(run, author, parser, inline, output)
    rendered = Renderer(document([translated_summary, translated_inline]), "en").render("en")
    assert "Measurement scope" in rendered
    assert "DCF indicates intracellular ROS [1]; MitoSOX" in rendered
    assert "(0.5 μM) [2]. The readout targets" in rendered
    assert "Keep **the target** and conditions." in rendered
    assert [n.target for n in walk_nodes(translated_inline.blocks) if n.kind == "evidence"] == [
        "eA",
        "eB",
    ]
    (output / "rendered.en.md").write_text(rendered, encoding="utf-8")
    roles = {row["role"] for row in run.scheduler.records}
    assert roles == {"translation", "delivery_parser"}
    assert run.budget.usage()["model_calls"] == 4
    assert {r["purpose"] for r in run.budget.data["requests"]} == {"completion"}
    # A new batch requires its author and parser budget before authorship starts.
    limited = engine(output / "one-request-budget", calls=1)
    limited_author, limited_mapper = (
        FixedModel(limited.budget),
        FixedModel(limited.budget, parser=True),
    )
    try:
        await translate(
            limited,
            limited_author,
            DeliveryParser(limited_mapper),
            summary,
            output / "one-request-budget",
        )
    except TranslationFailure as exc:
        assert exc.details["stage"] == "author"
        assert not limited_author.requests and not limited_mapper.requests
    else:
        raise AssertionError("one-request budget started a two-request batch")

    for system in (GENERAL, GBT42048):
        local = engine(output / system.id)
        writer, reader = FixedModel(local.budget), FixedModel(local.budget, parser=True)
        local.delivery_parser = DeliveryParser(
            ScheduledModel(reader, local.scheduler, "delivery_parser")
        )
        assessment, evidence, contributions = fixture(system)
        done = await deliver_assessment(
            local,
            assessment,
            evidence=evidence,
            report_selection=selection(contributions),
            contributions=contributions,
            english=True,
            translation_model=writer,
        )
        assert done["en"]["status"] == "completed"
        assert (output / system.id / "report.zh.md").read_text(encoding="utf-8")
    return {
        "status": "passed",
        "real_model_requests": 0,
        "full_review_runs": 0,
        "checks": [
            "single-pass author and paragraph mapping",
            "short headings",
            "inline spacing",
            "citation identities",
            "two-call reservation",
            "general and gbt delivery",
            "request role attribution",
        ],
    }


async def live(output, config_path, env_file):
    config = load_config(config_path)
    run = engine(output, calls=8)
    models = [
        model_class(config.models[config.auxiliary[kind]])(
            config.models[config.auxiliary[kind]].model_copy(update={"retries": 0}),
            env_file=env_file,
            budget=run.budget,
        )
        for kind in ("translation", "delivery_parser")
    ]
    parser = DeliveryParser(ScheduledModel(models[1], run.scheduler, "delivery_parser"))
    values = []
    try:
        for section in fragments():
            write_json(output / f"{section.section_id}.zh.json", section)
            translated = await translate(run, models[0], parser, section, output)
            write_json(output / f"{section.section_id}.en.json", translated)
            values.append(translated)
        rendered = Renderer(document(values), "en").render("en")
        (output / "rendered.en.md").write_text(rendered, encoding="utf-8")
        return {
            "status": "completed_for_manual_reading",
            "input": "explicit manual fixed snippets",
            "usage": run.budget.usage(),
            "full_review_runs": 0,
            "scientific_acceptance": "Read rendered English for objects, time, strength and units.",
        }
    finally:
        for model in models:
            await model.close()
        write_json(output / "metrics.json", run.scheduler.metrics())


async def main(args):
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("probe_requires_empty_output_directory")
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        result = (
            await live(args.output, args.config, args.env_file)
            if args.real
            else await offline(args.output)
        )
    except Exception as exc:
        write_json(
            args.output / "summary.json",
            {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "details": getattr(exc, "details", None),
                "full_review_runs": 0,
            },
        )
        raise
    write_json(args.output / "summary.json", result)
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--real", action="store_true")
    cli.add_argument("--config", type=Path, default=ROOT / "config/runtime.yaml")
    cli.add_argument("--env-file", type=Path, default=ROOT / ".env")
    print(json.dumps(asyncio.run(main(cli.parse_args())), ensure_ascii=False, indent=2))
