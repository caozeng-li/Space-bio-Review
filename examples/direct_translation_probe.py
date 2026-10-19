"""Translate two supplied scientific sections directly (dry by default).

Each section fits one batch and spends at most one request. The live mode uses
the configured translation route, with retries disabled and a two-request cap.
No review, retrieval, admission, research team or mapping model is invoked.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from time import monotonic

from spacebio_review.models.config import ModelConfig
from spacebio_review.models.provider import ChatReply, DashScopeModel
from spacebio_review.persistence import write_json
from spacebio_review.prose import Prose, compile_authored_body
from spacebio_review.publication.document import (
    Location,
    PublicationDocument,
    Section,
    Source,
    walk_nodes,
)
from spacebio_review.publication.render import Renderer
from spacebio_review.publication.translation import text_batches, translate_section
from spacebio_review.v5.budget import (
    Budget,
    BudgetLimits,
    CompletionReserve,
    SessionAllowance,
    exhausted,
)
from spacebio_review.v5.config import load_config

FIXTURES = {
    "stages": {
        "title": "空间阶段",
        "body": (
            "在轨第3、6、9天只观察形态与线粒体类核荧光，**不测定mtDNA序列**。"
            "[E1](evidence:E1)\n\n"
            "样本返回后才进行分选、克隆培养和扩增测序；测得的是培养后克隆的变异比例，"
            "不是未经培养细胞的即时频率。[E2](evidence:E2)"
        ),
        "observations": [
            "U1 短标题保留为独立标题。",
            "第 3、6、9 天对应在轨观察，分选、克隆培养和测序在返回后开展。",
            "保留不测定 mtDNA 序列的否定，以及培养后克隆和未经培养细胞的对象区别。",
            "引用目标、强调结构及中文作者来源不变。",
        ],
    },
    "thresholds": {
        "title": "测量阈值",
        "body": (
            "MitoSOX 工作浓度为 **0.5 μM**；变异比例低于 **0.1%** 仅表示低于本例报告阈值，"
            "**不表示变异不存在**。[E2](evidence:E2)\n\n"
            "[方法说明](https://example.org/probe-method)中的阈值适用于返回后克隆培养所得样本，"
            "不适用于在轨荧光信号。计划书未提供团队既往空间实验经验的证据，"
            "这不等于团队没有相关经验。[E1](evidence:E1)"
        ),
        "observations": [
            "U1 短标题保留；0.5 μM 为工作浓度，0.1% 为变异比例报告阈值。",
            "低于阈值不等于变异不存在，未提供证据不等于没有经验。",
            "阈值对应返回后培养样本，不能改成在轨荧光阈值。",
            "普通链接目标、引用目标、强调结构及中文作者来源不变。",
        ],
    },
}

TRANSLATIONS = {
    "空间阶段": "Spaceflight stages",
    "在轨第3、6、9天只观察形态与线粒体类核荧光": (
        "Only morphology and mitochondrial nucleoid fluorescence are observed "
        "on days 3, 6 and 9 in orbit"
    ),
    "不测定mtDNA序列": "mtDNA sequences are not measured",
    "样本返回后才进行分选、克隆培养和扩增测序；测得的是培养后克隆的变异比例，"
    "不是未经培养细胞的即时频率": (
        "Sorting, clonal culture and amplicon sequencing are performed only after sample return; "
        "the measured values are variant fractions in cultured clones, "
        "not the instantaneous frequencies in uncultured cells"
    ),
    "测量阈值": "Measurement thresholds",
    "MitoSOX 工作浓度为": "The working concentration of MitoSOX is",
    "0.5 μM": "0.5 μM",
    "变异比例低于": "a variant fraction below",
    "0.1%": "0.1%",
    "仅表示低于本例报告阈值": (
        "only indicates a value below the reporting threshold in this example"
    ),
    "不表示变异不存在": "it does not indicate that the variant is absent",
    "方法说明": "The methods description",
    "中的阈值适用于返回后克隆培养所得样本，不适用于在轨荧光信号。"
    "计划书未提供团队既往空间实验经验的证据，这不等于团队没有相关经验": (
        "specifies a threshold for samples obtained by clonal culture after return, "
        "not for in-orbit fluorescence signals. The proposal provides no evidence of the team's "
        "previous spaceflight experience; this does not mean that the team has none"
    ),
}


def fixture_section(case):
    """Compile supplied Markdown with the same field origins as scientific manuscripts."""
    fixture = FIXTURES[case]
    body = Prose.model_validate(compile_authored_body(
        fixture["body"], {"E1": "evidence-application", "E2": "evidence-method"},
        origin={
            "author_text_ref": f"supplied-{case}-manuscript",
            "contribution_ref": f"supplied-{case}-contribution",
            "field_path": "body",
        },
    ))
    for node in walk_nodes(body.blocks):
        if node.kind == "citation":
            node.kind = "evidence"
    section = Section(
        section_id=f"scientific-{case}", title=fixture["title"], kind="scientific",
        blocks=body.blocks,
    )
    assert len(text_batches(section, 18000)) == 1
    return section


def document(sections):
    return PublicationDocument(
        main=sections, appendix=[], recommendation="conditional_support",
        sources={
            "source-application": Source(title="Supplied probe proposal", kind="application"),
            "source-method": Source(title="Supplied probe method", kind="external"),
        },
        locations={
            "evidence-application": Location(source="source-application", locator="Supplied plan"),
            "evidence-method": Location(source="source-method", locator="Supplied method"),
        },
        numbering={"source-application": 1, "source-method": 2},
    )


class FixedTranslator:
    def __init__(self, budget):
        self.budget = budget
        self.config = ModelConfig(
            model="supplied-translator", base_url="https://unused.invalid", max_tokens=2048,
            retries=0, tools=False, structured_output="json_object",
        )

    async def complete(self, messages, tools=None, *, response_schema=None):
        assert response_schema and not tools
        data = json.loads(messages[-1]["content"])
        values = {key: TRANSLATIONS[source] for key, source in data["units"].items()}
        assert set(values) == set(response_schema["properties"])
        ticket = self.budget.reserve(self.config, messages, response_schema=response_schema)
        reply = ChatReply(
            message={"role": "assistant", "content": json.dumps(values, ensure_ascii=False)},
            prompt_tokens=200, completion_tokens=180,
        )
        self.budget.settle(ticket, reply)
        return reply


class RecordingTranslator:
    """Save the actual boundary input and reply, including failed provider attempts."""

    def __init__(self, model, output):
        self.model, self.output, self.config = model, Path(output), model.config
        self.calls = 0

    async def complete(self, messages, tools=None, *, response_schema=None):
        self.calls += 1
        prefix = self.output / f"request-{self.calls}"
        write_json(str(prefix) + "-input.json", {
            "model": self.config.model, "messages": messages, "response_schema": response_schema,
            "structured_output": self.config.structured_output, "retries": self.config.retries,
            "extra_body": self.config.extra_body,
        })
        started = monotonic()
        try:
            reply = await self.model.complete(messages, tools, response_schema=response_schema)
        except Exception as exc:
            write_json(str(prefix) + "-result.json", {
                "elapsed_seconds": monotonic() - started, "error": type(exc).__name__,
                "reason": str(exc), "usage_known": False,
            })
            raise
        write_json(str(prefix) + "-result.json", {
            "elapsed_seconds": monotonic() - started, "message": reply.message,
            "finish_reason": reply.finish_reason, "prompt_tokens": reply.prompt_tokens,
            "completion_tokens": reply.completion_tokens,
            "reasoning_tokens": reply.reasoning_tokens,
            "effective_request": reply.effective_request,
            "usage_known": reply.prompt_tokens is not None and reply.completion_tokens is not None,
        })
        return reply


def prepare_request(budget, owner, requests, output):
    costs = [budget.measure(
        row["config"], row["messages"], response_schema=row["response_schema"],
    ) for row in requests]
    write_json(output / "request-plan.json", {
        "steps": [row["name"] for row in requests], "costs": costs,
    })
    assert len(requests) == 1 and requests[0]["name"] == "translation:author"
    reason = budget.update_completion(owner, CompletionReserve(
        calls=1, steps=[requests[0]["name"]],
        input_tokens=costs[0]["input_tokens"], output_tokens=costs[0]["output_reserve"],
    ))
    if reason:
        raise exhausted(reason)


async def run_case(case, output, model, budget):
    output = Path(output)
    section = fixture_section(case)
    write_json(output / "source-section.json", section)
    source = Renderer(document([section])).render("main")
    (output / "source.zh.md").write_text(source, encoding="utf-8")
    author = RecordingTranslator(model, output)
    owner = f"translation-{case}"
    budget.register(owner, SessionAllowance(completion_calls=0))
    started = monotonic()
    result = {"case": case, "manual_observation_points": FIXTURES[case]["observations"]}
    try:
        with budget.scope(owner, purpose="completion"):
            translated = await translate_section(
                author, section, artifact_dir=output / "translations",
                prepare_requests=lambda requests: prepare_request(budget, owner, requests, output),
            )
        write_json(output / "translated-section.json", translated)
        rendered = Renderer(document([translated]), "en").render("main")
        (output / "translated.en.md").write_text(rendered, encoding="utf-8")
        result.update(status="completed", translated_title=translated.title_en or translated.title)
        if isinstance(model, FixedTranslator):
            assert translated.title == TRANSLATIONS[section.title]
            assert author.calls == 1
    except Exception as exc:
        result.update(status="failed", error=type(exc).__name__, reason=str(exc))
    finally:
        budget.finish(owner)
        result.update(
            author_invocations=author.calls, elapsed_seconds=monotonic() - started,
            usage=budget.usage(owner),
        )
        write_json(output / "case-report.json", result)
    return result


async def main(args):
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new empty output directory; this probe does not resume past runs")
    output.mkdir(parents=True, exist_ok=True)
    cases = list(FIXTURES) if args.case == "all" else [args.case]
    budget = Budget(output, BudgetLimits(
        max_model_calls=len(cases), max_embedding_calls=0, max_seconds=700,
        max_input_tokens=100000, max_output_tokens=16384,
    ))
    if args.live:
        config = load_config(args.config)
        settings = config.models[config.auxiliary["translation"]].model_copy(update={"retries": 0})
        model = DashScopeModel(settings, env_file=args.env_file, budget=budget)
    else:
        model = FixedTranslator(budget)
    results = []
    try:
        for case in cases:
            results.append(await run_case(case, output / case, model, budget))
    finally:
        if args.live:
            await model.close()
        budget.save()
    report = {
        "mode": "live" if args.live else "dry", "results": results,
        "real_request_limit": len(cases),
        "real_requests": budget.usage()["model_calls"] if args.live else 0,
        "usage": budget.usage(), "full_review_runs": 0, "mapper_calls": 0,
        "retrieval_calls": 0, "expert_execution_calls": 0,
        "fixture_note": "Constructed sections; inspect scientific meaning manually, no model judge",
    }
    write_json(output / "probe-report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--case", choices=["all", *FIXTURES], default="all")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--config", default="config/runtime.yaml")
    parser.add_argument("--env-file", default=".env")
    asyncio.run(main(parser.parse_args()))
