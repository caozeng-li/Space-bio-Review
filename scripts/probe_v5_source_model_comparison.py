"""One alternate source author, sharing the selected local experiment's ledger."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from spacebio_review.models.provider import model_class
from spacebio_review.models.scheduling import RequestScheduler, ScheduledModel
from spacebio_review.persistence import write_json
from spacebio_review.prose import bind_prose
from spacebio_review.v5.authoring import (
    numbered_text,
    research_note,
)
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance
from spacebio_review.v5.config import load_config
from spacebio_review.v5.session import ResearchRuntime

ROOT = Path(__file__).resolve().parents[1]


def prepare(root, config_path):
    manifest = json.loads((root / "probe.json").read_text("utf-8"))
    reading_id = manifest["cases"]["siew"]["reading_id"]
    # Use the production writing entry, with the saved task and actually-read originals.
    # The submitted answer and post-submission observation are deliberately absent.
    engine = ResearchRuntime(root, {}, {})
    state = engine.load(reading_id)
    original_model = dict(state.model_snapshot)
    state.note_ref = state.submitted_ref = None
    state.observations = []
    state.turns_used = max(0, state.turns_used - 1)
    state.revision = 0
    state.submissions = []
    engine.load = lambda session_id: state
    source = json.loads(state.task.evaluation)["source"]
    messages = engine._writing_messages(
        state, "final_manuscript", "submit_research", lambda: {"source": source}, None
    )
    config = load_config(config_path)
    alternative = config.models[config.auxiliary["visual"]].model_copy(
        update={"tools": True, "max_tokens": original_model["max_tokens"], "retries": 0}
    )
    parser_config = config.models[config.auxiliary["delivery_parser"]].model_copy(
        update={"retries": 0}
    )
    return state, messages, alternative, parser_config, original_model


async def run(args):
    root = args.root.resolve()
    output = root / "model-comparison" / "siew-qwen"
    output.mkdir(parents=True, exist_ok=True)
    state, messages, alternative, parser_config, original_model = prepare(root, args.config)
    write_json(
        output / "input.json",
        {
            "reading_id": state.session_id,
            "messages": messages,
            "alternative_config": alternative,
            "baseline_config": original_model,
            "parser_config": parser_config,
            "scope": "Same saved task, skills, actually read evidence and writing system prompt; "
            "no previous answer or corrective instruction. Original tool observations "
            "were not retained after submission, so this is reconstructed context, "
            "not byte-for-byte replay of the earlier request.",
        },
    )
    if not args.execute:
        return {"status": "prepared", "real_requests": 0, "input": str(output / "input.json")}
    if (output / "author.json").exists():
        raise ValueError("comparison_already_has_author_no_automatic_repeat")
    budget = Budget(root, BudgetLimits(max_model_calls=24, max_seconds=1800, max_embedding_calls=0))
    before = budget.usage()
    owner = "comparison-siew-qwen"
    budget.register(owner, SessionAllowance(completion_calls=1))
    budget.activate(owner)
    scheduler = RequestScheduler()
    author = model_class(alternative)(alternative, env_file=args.env_file, budget=budget)
    try:
        with budget.scope(owner, purpose="completion"):
            async with asyncio.timeout(budget.remaining_seconds()):
                with scheduler.task(owner, "source_reader", "source_model_comparison"):
                    reply = await ScheduledModel(author, scheduler, "alternate_author").complete(
                        messages, []
                    )
                content = reply.message.get("content") or ""
                raw = {
                    "operation": "submit_research",
                    "content": content,
                    "paragraphs": numbered_text(content),
                    "aliases": state.aliases,
                    "model": alternative.model_dump(mode="json"),
                    "finish_reason": reply.finish_reason,
                }
                write_json(output / "author.json", raw)
                (output / "author.md").write_text(content, encoding="utf-8")
                if reply.finish_reason != "stop" or reply.message.get("tool_calls"):
                    raise ValueError("incomplete_alternative_author")
                note = research_note(raw, title=state.task.objective).model_dump(mode="json")
                note["body"] = bind_prose(note["body"], state.aliases)
                write_json(output / "note.json", note)
        result = {
            "status": "completed_for_manual_reading",
            "before": before,
            "after": budget.usage(),
            "real_requests": budget.usage()["model_calls"] - before["model_calls"],
            "search_calls": 0,
            "full_reviews": 0,
            "interpretation": "One alternate available scientific author route; "
            "not a general model ranking or exact-request A/B trial.",
        }
        write_json(output / "result.json", result)
        return result
    except Exception as exc:
        write_json(
            output / "failure.json",
            {
                "error": str(exc),
                "type": type(exc).__name__,
                "before": before,
                "after": budget.usage(),
            },
        )
        raise
    finally:
        budget.finish(owner)
        await author.close()
        write_json(output / "metrics.json", scheduler.metrics())


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--root", type=Path, required=True)
    cli.add_argument("--config", type=Path, default=ROOT / "config/v5-runtime.yaml")
    cli.add_argument("--env-file", type=Path, default=ROOT / ".env")
    cli.add_argument("--execute", action="store_true")
    print(json.dumps(asyncio.run(run(cli.parse_args())), ensure_ascii=False, indent=2))
