"""Opt-in delivery parsing only: at most six actual requests and fifteen minutes.

One short route probe, two fixed historical derivatives, then one scientific
author response plus its parser. No search, embeddings, assessment or full review.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_chief_live import record_public_replies  # noqa: E402

from spacebio_review.models.capabilities import formal_capability  # noqa: E402
from spacebio_review.models.provider import ModelError, model_class  # noqa: E402
from spacebio_review.models.scheduling import RequestScheduler  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.prose import bind_prose  # noqa: E402
from spacebio_review.v5.authoring import (  # noqa: E402
    RESEARCH_INSTRUCTION,
    ResearchMapping,
    numbered_text,
    research_note,
)
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance  # noqa: E402
from spacebio_review.v5.config import load_config  # noqa: E402
from spacebio_review.v5.records import SessionSettings, SessionTask  # noqa: E402
from spacebio_review.v5.session import ResearchRuntime  # noqa: E402

FIXTURES = ROOT / "scripts/fixtures/v5_author_parsing"
OBJECTIVE = (
    "只基于已提供的历史研究说明及其原始证据快照，写成一份可直接交付的测量研究说明。"
    "本次验证普通正文交付，不重新检索，不评分，不写综合报告。说明 DCF、Fluo-8、"
    "PicoGreen/TMRM/MitoTracker Green 读出与推断范围，保留现有证据的适用条件与未知。"
    "历史研究说明是固定输入材料，不是已验证正确的结论；具体科学认识以给出的证据为依据。"
    "本轮只有一次科学作者调用，请直接在普通 assistant 正文中完成完整新稿，"
    "首行严格写「交付操作：submit_research」，不调用工具。标题、正文各自然段、"
    "尚未确定栏目及每条未知事项之间用空行分隔。引用原位写 [@E编号]；不写 JSON。"
)


def read_fixture(name):
    return json.loads((FIXTURES / name).read_text("utf-8"))


def raw_text(content, aliases):
    return {
        "operation": "submit_research",
        "content": content,
        "paragraphs": numbered_text(content),
        "aliases": aliases,
    }


def compare_fixture(note, fixture):
    """Compare fixed text and explicit anchors, without judging scientific claims."""
    expected = fixture["expected_note"]
    actual = note["body"]["paragraphs"]
    omitted = [
        index
        for index, paragraph in enumerate(expected["body"]["paragraphs"])
        if paragraph not in actual
    ]
    optional = set(fixture["optional_section_heading_body_indexes"])
    missing_body = [index for index in omitted if index not in optional]
    return {
        "title_equal": note["title"] == expected["title"],
        "unknowns_equal": note["unknowns"] == expected["unknowns"],
        "body_exact_equal": note["body"] == expected["body"],
        "missing_required_body_indexes": missing_body,
        "omitted_section_heading_indexes": [i for i in omitted if i in optional],
        "passed": not missing_body
        and note["title"] == expected["title"]
        and note["unknowns"] == expected["unknowns"],
    }


async def fixed_parse(engine, owner, raw, output, *, fixture=None):
    budget = engine.budget
    budget.register(owner, SessionAllowance(completion_calls=0))
    write_json(output / owner / "raw.json", raw)
    if fixture:
        write_json(output / owner / "fixture.json", fixture)
    try:
        with budget.scope(owner, purpose="auxiliary"):
            async with asyncio.timeout(budget.remaining_seconds()):
                mapping = await engine.delivery_parser.parse(
                    raw, ResearchMapping, RESEARCH_INSTRUCTION
                )
        write_json(output / owner / "mapping.json", mapping)
        note = research_note(raw, mapping).model_dump(mode="json")
        note["body"] = bind_prose(note["body"], raw["aliases"])
        write_json(output / owner / "note.json", note)
        result = {"case": owner, "status": "completed", "usage": budget.usage(owner)}
        if fixture:
            result["comparison"] = compare_fixture(note, fixture)
            if not result["comparison"]["passed"]:
                result["status"] = "text_comparison_failed"
        else:
            result["capability"] = formal_capability(engine.delivery_parser.config)
            result["capability"]["verified_by_this_probe"] = True
        write_json(output / owner / "result.json", result)
        return result
    finally:
        budget.finish(owner)


def only_one_author_call(model):
    original = model.complete
    calls = 0

    async def complete(messages, tools=None, *, response_schema=None):
        nonlocal calls
        if calls:
            raise ModelError(
                "local_probe_single_author_call",
                category="budget",
                request_sent=False,
            )
        calls += 1
        try:
            return await original(messages, tools, response_schema=response_schema)
        except ModelError as exc:
            if exc.request_sent is False:
                calls -= 1
            raise

    model.complete = complete


async def real_author(engine, fixture, output):
    # Readings iterate in the same order as the fixture's E1... aliases.
    readings = {key: fixture["evidence"][key] for key in fixture["aliases"].values()}
    session_id = engine.create(
        SessionTask(
            role="researcher",
            objective=OBJECTIVE,
            application={
                "fixed_research_note": fixture["content"],
                "provenance": fixture["provenance"],
                "scope": "Existing note and previously read evidence only; no new retrieval.",
            },
        ),
        readings=readings,
        allowance=SessionAllowance(
            max_input_tokens=250000,
            max_output_tokens=32768,
            completion_input_tokens=96000,
            completion_output_tokens=12288,
            completion_calls=2,
            completion_seconds=300,
        ),
    )
    result = await engine.run(session_id)
    write_json(output / "real-author" / "outcome.json", result)
    if result.note_ref:
        write_json(output / "real-author" / "note.json", engine.store.note(result.note_ref))
    row = {
        "case": "real-author",
        "session_id": session_id,
        "status": result.status,
        "reason": result.reason,
        "note_ref": result.note_ref,
        "scientific_turns": result.turns_used,
        "usage": engine.budget.usage(session_id),
        "scientific_quality": "Requires manual reading; fixed prior research was supplied.",
    }
    write_json(output / "real-author" / "result.json", row)
    return row


async def probe(args):
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("probe_requires_empty_output_directory")
    output.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config)
    limits = BudgetLimits(max_model_calls=6, max_embedding_calls=0, max_seconds=900)
    budget = Budget(output, limits)
    author_alias = config.roles["researcher"]
    parser_alias = config.auxiliary["delivery_parser"]
    models, stages, active_stage = {}, [], "initialization"
    engine, error = None, None
    try:
        for alias in (author_alias, parser_alias):
            settings = config.models[alias].model_copy(
                update={
                    "retries": 0,
                    "timeout_seconds": min(config.models[alias].timeout_seconds, 240),
                    "max_tokens": min(config.models[alias].max_tokens, 8192),
                },
                deep=True,
            )
            models[alias] = model_class(settings)(settings, env_file=args.env_file, budget=budget)
        only_one_author_call(models[author_alias])
        engine = ResearchRuntime(
            output,
            models,
            {"chief": author_alias, "researcher": author_alias},
            budget=budget,
            scheduler=RequestScheduler(config.concurrency),
            settings=SessionSettings(max_turns=2, completion_turns=1, context_tokens=64000),
            delivery_parser=models[parser_alias],
        )
        engine._owned_models = list(models.values())
        engine.scheduler.request_budget = budget
        record_public_replies(engine, output)
        saved = read_fixture("saved-note.json")
        failed = read_fixture("failed-quote-excerpt.json")
        write_json(
            output / "input.json",
            {
                "limits": limits,
                "expected_actual_requests": 5,
                "maximum_scientific_author_requests": 1,
                "models": {alias: model.config for alias, model in models.items()},
                "parser_mode": formal_capability(models[parser_alias].config),
                "fixtures": [failed["provenance"], saved["provenance"]],
                "process_no_proxy": {key: os.environ.get(key) for key in ("NO_PROXY", "no_proxy")},
                "environment_modified_by_probe": False,
                "full_review_runs": 0,
                "retrieval_requests": 0,
                "embedding_requests": 0,
            },
        )
        async with asyncio.timeout(budget.remaining_seconds()):
            active_stage = "capability"
            stages.append(
                await fixed_parse(
                    engine,
                    active_stage,
                    raw_text(
                        "交付操作：submit_research\n\n# 结构能力探针\n\n"
                        '测试文本原样包含 "DCF"、H₂O₂、Ca²⁺ 和反斜杠 \\。\n\n'
                        "尚未确定：无",
                        {},
                    ),
                    output,
                )
            )
            for fixture in (failed, saved):
                active_stage = fixture["name"]
                stages.append(
                    await fixed_parse(
                        engine,
                        active_stage,
                        raw_text(fixture["content"], fixture["aliases"]),
                        output,
                        fixture=fixture,
                    )
                )
            active_stage = "real-author"
            stages.append(await real_author(engine, saved, output))
    except Exception as exc:
        error = {
            "stage": active_stage,
            "type": type(exc).__name__,
            "reason": str(exc),
            "category": getattr(exc, "category", None),
            "request_sent": getattr(exc, "request_sent", None),
        }
        if public_reply := getattr(exc, "public_reply", None):
            write_json(output / active_stage / "failed-public-reply.json", public_reply)
        write_json(output / active_stage / "error.json", error)
    finally:
        budget.save()
        summary = {
            "status": "completed"
            if error is None
            and len(stages) == 4
            and all(row["status"] in {"completed", "submitted"} for row in stages)
            else "incomplete",
            "stages": stages,
            "error": error,
            "usage": budget.usage(),
            "elapsed_seconds": 900 - budget.remaining_seconds(),
            "requests": budget.data["requests"],
            "full_review_runs": 0,
            "scientific_quality": "Delivery protocol only; scientific validity is not certified.",
        }
        write_json(output / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if engine:
            await engine.close()
        else:
            for model in models.values():
                await model.close()
    return 0 if summary["status"] == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/runtime.yaml")
    parser.add_argument("--env-file", default=".env")
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
