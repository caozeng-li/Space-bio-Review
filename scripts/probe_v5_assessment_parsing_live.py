"""Opt-in two historical assessment parsings; no scientific author or review run.

Hard cap: two actual parser requests, no retries, no embeddings, fifteen minutes.
The fixed plain-text fixtures are explicitly documented historical derivatives.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_chief_live import record_public_replies  # noqa: E402

from spacebio_review.models.capabilities import formal_capability  # noqa: E402
from spacebio_review.models.provider import model_class  # noqa: E402
from spacebio_review.models.scheduling import RequestScheduler, ScheduledModel  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.review.evaluation import get_evaluation_system  # noqa: E402
from spacebio_review.review.scoring import finalize_assessment  # noqa: E402
from spacebio_review.v5.assessment import assessment_type, bind_citations  # noqa: E402
from spacebio_review.v5.assessment_authoring import (  # noqa: E402
    assessment_mapping_type,
    assessment_values,
    parsing_instruction,
)
from spacebio_review.v5.authoring import DeliveryParser, numbered_text  # noqa: E402
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance  # noqa: E402
from spacebio_review.v5.config import load_config  # noqa: E402

FIXTURES = ROOT / "scripts/fixtures/v5_assessment_parsing"


def read_fixture(name):
    return json.loads((FIXTURES / (name + ".json")).read_text("utf-8"))


def parse_input(fixture):
    return {
        "operation": fixture["operation"],
        "content": fixture["content"],
        "paragraphs": numbered_text(fixture["content"]),
        "aliases": fixture["aliases"],
    }


def parse_context(system, *, partial):
    return {
        "evaluation_system": system.id,
        "criteria": [
            {"id": item.id, "label": item.label, "max_score": item.max_score}
            for item in system.criteria
        ],
        "selected_topic_options": [],
        "complete_submission": not partial,
    }


async def parse_case(parser, budget, fixture, output):
    name = fixture["name"]
    folder = output / name
    budget.register(name, SessionAllowance(completion_calls=0))
    raw = parse_input(fixture)
    system = get_evaluation_system(fixture["evaluation_system"])
    partial = fixture["operation"] == "save_assessment"
    write_json(folder / "fixture.json", fixture)
    write_json(folder / "raw.json", raw)
    try:
        with budget.scope(name, purpose="auxiliary"):
            async with asyncio.timeout(budget.remaining_seconds()):
                mapping = await parser.parse(
                    raw,
                    assessment_mapping_type(system),
                    parsing_instruction(raw["operation"]),
                    parse_context(system, partial=partial),
                )
        write_json(folder / "mapping.json", mapping)
        if mapping.missing_fields:
            return {
                "case": name,
                "status": "incomplete",
                "missing_fields": mapping.missing_fields,
                "usage": budget.usage(name),
            }
        values = assessment_values(raw, mapping)
        write_json(folder / "values.json", values)
        # This is the existing production contract, not a second scoring judgment.
        assessment_type(system, raw["aliases"], partial=partial).model_validate(values)
        equal = values == fixture["expected_values"]
        comparison = {
            "exact_values_equal": equal,
            "expected_fields": list(fixture["expected_values"]),
            "actual_fields": list(values),
            "unequal_fields": [
                key
                for key in dict.fromkeys([*fixture["expected_values"], *values])
                if values.get(key) != fixture["expected_values"].get(key)
            ],
        }
        bound = bind_citations(values, raw["aliases"])
        write_json(folder / "bound-values.json", bound)
        if not partial:
            write_json(folder / "assessment.json", finalize_assessment(bound, system))
        return {
            "case": name,
            "status": "completed" if equal else "text_comparison_failed",
            "operation": fixture["operation"],
            "comparison": comparison,
            "scores": {key: value["score"] for key, value in values.get("dimensions", {}).items()},
            "safety_veto": values.get("safety_veto", {}).get("applies"),
            "usage": budget.usage(name),
        }
    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "reason": str(exc),
            "category": getattr(exc, "category", None),
            "request_sent": getattr(exc, "request_sent", None),
        }
        if public_reply := getattr(exc, "public_reply", None):
            write_json(folder / "failed-public-reply.json", public_reply)
        write_json(folder / "error.json", error)
        return {"case": name, "status": "failed", "error": error, "usage": budget.usage(name)}
    finally:
        budget.finish(name)


async def probe(args):
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("probe_requires_empty_output_directory")
    output.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config)
    limits = BudgetLimits(
        max_model_calls=1 if args.case else 2, max_embedding_calls=0, max_seconds=900
    )
    budget = Budget(output, limits)
    alias = config.auxiliary["delivery_parser"]
    settings = config.models[alias].model_copy(update={"retries": 0}, deep=True)
    model = model_class(settings)(settings, env_file=args.env_file, budget=budget)
    scheduler = RequestScheduler(config.concurrency)
    scheduler.request_budget = budget
    parser = DeliveryParser(ScheduledModel(model, scheduler, alias))
    record_public_replies(SimpleNamespace(models={alias: model}, budget=budget), output)
    fixtures = [
        read_fixture(name)
        for name in ([args.case] if args.case else ("failed-six-criteria", "failed-partial"))
    ]
    write_json(
        output / "input.json",
        {
            "limits": limits,
            "model": settings,
            "capability": formal_capability(settings),
            "fixtures": [fixture["provenance"] for fixture in fixtures],
            "process_no_proxy": {key: os.environ.get(key) for key in ("NO_PROXY", "no_proxy")},
            "environment_modified_by_probe": False,
            "scientific_author_requests": 0,
            "retrieval_requests": 0,
            "embedding_requests": 0,
            "full_review_runs": 0,
            "scope": "Two historical derivatives; fresh author/report acceptance is separate.",
        },
    )
    results = []
    try:
        for fixture in fixtures:
            result = await parse_case(parser, budget, fixture, output)
            results.append(result)
            write_json(output / fixture["name"] / "result.json", result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        budget.save()
        summary = {
            "status": "completed"
            if len(results) == len(fixtures)
            and all(row["status"] == "completed" for row in results)
            else "incomplete",
            "results": results,
            "usage": budget.usage(),
            "elapsed_seconds": 900 - budget.remaining_seconds(),
            "requests": budget.data["requests"],
            "full_review_runs": 0,
            "scientific_quality": "Structure and exact text only; judgments are not certified.",
        }
        write_json(output / "summary.json", summary)
        await model.close()
    return 0 if summary["status"] == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=("failed-six-criteria", "failed-partial"))
    parser.add_argument("--config", type=Path, default=ROOT / "config/runtime.yaml")
    parser.add_argument("--env-file", default=".env")
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
