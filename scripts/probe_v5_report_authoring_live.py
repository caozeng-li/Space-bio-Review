"""Opt-in single-manuscript author/parser acceptance; no search or full review.

The fixed parser case costs one request. The author case consumes supplied, manually
reviewed research-note artifacts and costs one author plus one parser normally.
All invocations share an eight-request, twenty-minute ledger; retries are disabled.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_assessment import assessment_text, manuscript_draft, service  # noqa: E402
from probe_v5_chief import MeteredScript  # noqa: E402
from probe_v5_chief_live import record_public_replies  # noqa: E402
from probe_v5_topic import Fixture  # noqa: E402

from spacebio_review.models.provider import model_class  # noqa: E402
from spacebio_review.models.scheduling import ScheduledModel  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.review.evaluation import GBT42048  # noqa: E402
from spacebio_review.v5.assessment import AssessmentSubmission, bind_citations  # noqa: E402
from spacebio_review.v5.authoring import DeliveryParser  # noqa: E402
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance  # noqa: E402
from spacebio_review.v5.config import load_config  # noqa: E402
from spacebio_review.v5.delivery import deliver_assessment  # noqa: E402
from spacebio_review.v5.records import ResearchNote  # noqa: E402


async def prepare(folder, args, budget, models):
    fixed = await Fixture(folder).setup()
    fixed.application_id = await fixed.store.add_local(args.application.resolve())
    engine, _, chief = service(
        folder,
        fixed,
        MeteredScript([], name="unused-author"),
        MeteredScript([], name="unused-researcher"),
        prepared_application=False,
    )
    chief_id = chief.create("固定已研究材料的完整国标评价局部写作；不再检索或派题。")
    engine.budget = budget
    engine.scheduler.request_budget = budget
    engine.delivery_parser = DeliveryParser(
        ScheduledModel(models["parser"], engine.scheduler, "delivery_parser")
    )
    budget.register(chief_id, SessionAllowance(completion_calls=2, completion_seconds=240))
    state = engine.load(chief_id)
    engine.models[state.model_alias] = models["author"]
    state.model_snapshot = models["author"].config.model_dump(mode="json")
    state.settings = state.settings.model_copy(update={"context_tokens": 256_000})
    engine.store.save(state)
    return fixed, engine, chief, chief_id


async def parse_fixed(folder, args, budget, models):
    fixed, engine, chief, chief_id = await prepare(folder, args, budget, models)
    state = engine.load(chief_id)
    values = manuscript_draft(GBT42048)
    content = assessment_text(GBT42048, "submit_assessment", values).message["content"]
    raw_ref = engine.store.save_author_text(
        state,
        content,
        "submit_assessment",
        absorbed_through=state.observation_index,
        phase="final_manuscript",
    )
    state.pending_delivery = {
        "raw_ref": raw_ref,
        "operation": "submit_assessment",
        "purpose": "completion",
        "parse_ref": None,
    }
    engine.store.save(state)
    expected = bind_citations(values, state.aliases)
    write_json(
        folder / "fixture.json",
        {
            "provenance": "Manual mechanical fixture; scores are not project evaluation.",
            "values": values,
            "original_text": content,
        },
    )
    try:
        result = await chief.assess(chief_id, allow_research=False)
        write_json(folder / "result.json", result)
        exact = all(
            result.get("assessment", {}).get(key) == value for key, value in expected.items()
        )
        if result["report_ref"]:
            await deliver_assessment(
                engine, result["assessment"], evidence=result["evidence"], english=False
            )
        return {
            "status": "passed" if exact and result["report_ref"] else "failed",
            "exact_text_and_citations": exact,
            "reason": result.get("reason"),
            "author_turns": engine.load(chief_id).turns_used,
            "usage": budget.usage(chief_id),
        }
    finally:
        await engine.close()
        await fixed.close()


async def author_manuscript(folder, args, budget, models):
    if not args.research_note:
        raise ValueError("author_case_requires_reviewed_research_note_artifacts")
    fixed, engine, chief, chief_id = await prepare(folder, args, budget, models)
    state = engine.load(chief_id)
    inputs = []
    for path in args.research_note:
        artifact = json.loads(path.read_text("utf-8"))
        engine._capture(state, artifact["evidence"])
        ref = engine.store.save_note(
            state,
            ResearchNote.model_validate(artifact["note"]),
            absorbed_through=state.observation_index,
            aliases={key: key for key in artifact["evidence"]},
        )
        inputs.append({"original_artifact": str(path.resolve()), "local_note_ref": ref})
    engine.store.save(state)
    submission = AssessmentSubmission(chief, chief_id)
    write_json(folder / "research-inputs.json", inputs)
    write_json(
        folder / "author-input.json",
        engine._writing_messages(
            state,
            "final_manuscript",
            "submit_assessment",
            lambda: chief._context(chief_id),
            submission,
        ),
    )
    try:
        result = await chief.assess(chief_id, allow_research=False)
        write_json(folder / "result.json", result)
        if result["report_ref"]:
            await deliver_assessment(
                engine,
                result["assessment"],
                evidence=result["evidence"],
                topics=result["selected_topics"],
                english=False,
            )
        return {
            "status": "mechanical_pass_science_pending_read" if result["report_ref"] else "failed",
            "reason": result.get("reason"),
            "author_turns": engine.load(chief_id).turns_used,
            "usage": budget.usage(chief_id),
        }
    finally:
        await engine.close()
        await fixed.close()


async def probe(args):
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("probe_requires_empty_output_directory")
    config = load_config(args.config)
    budget = Budget(
        args.budget_root,
        BudgetLimits(
            max_model_calls=8,
            max_embedding_calls=0,
            max_seconds=1200,
        ),
    )
    models = {}
    for role, alias in (
        ("author", config.roles["chief"]),
        ("parser", config.auxiliary["delivery_parser"]),
    ):
        settings = config.models[alias].model_copy(update={"retries": 0}, deep=True)
        models[role] = model_class(settings)(settings, env_file=args.env_file, budget=budget)
    record_public_replies(SimpleNamespace(models=models, budget=budget), output)
    write_json(
        output / "input.json",
        {
            "application": str(args.application.resolve()),
            "research_notes": [str(path.resolve()) for path in args.research_note],
            "models": {key: model.config for key, model in models.items()},
            "budget_root": str(args.budget_root.resolve()),
            "usage_before": budget.usage(),
            "full_review_runs": 0,
            "search_requests": 0,
            "embedding_requests": 0,
        },
    )
    results = {}
    try:
        async with asyncio.timeout(budget.remaining_seconds()):
            if args.case in {"all", "fixed-parser"}:
                results["fixed_parser"] = await parse_fixed(
                    output / "fixed-parser",
                    args,
                    budget,
                    models,
                )
                write_json(output / "progress.json", results)
            if args.case in {"all", "manuscript-author"}:
                results["manuscript_author"] = await author_manuscript(
                    output / "manuscript-author",
                    args,
                    budget,
                    models,
                )
    except Exception as exc:
        results["error"] = {"type": type(exc).__name__, "reason": str(exc)}
    finally:
        budget.save()
        write_json(
            output / "summary.json",
            {"results": results, "usage": budget.usage(), "full_review_runs": 0},
        )
        for model in models.values():
            await model.close()
    print(json.dumps({"results": results, "usage": budget.usage()}, ensure_ascii=False, indent=2))
    return (
        0
        if results
        and all(
            row.get("status", "").startswith(("passed", "mechanical_pass"))
            for row in results.values()
        )
        else 1
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/v5-runtime.yaml")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--application", type=Path, default=ROOT / "data/sample2.md")
    parser.add_argument("--research-note", type=Path, action="append", default=[])
    parser.add_argument(
        "--case", choices=["all", "fixed-parser", "manuscript-author"], default="all"
    )
    raise SystemExit(asyncio.run(probe(parser.parse_args())))
