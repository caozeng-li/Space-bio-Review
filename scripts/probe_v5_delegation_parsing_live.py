"""Parse the two saved malformed delegations once each; no new science or child research."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_grounding_live import NoDiscovery  # noqa: E402
from probe_v5_session import ScriptModel  # noqa: E402

from spacebio_review.agents.skills import SkillLibrary  # noqa: E402
from spacebio_review.evidence.store import SourceStore  # noqa: E402
from spacebio_review.models.provider import model_class  # noqa: E402
from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.review.expertise import ExpertiseCatalogue  # noqa: E402
from spacebio_review.v5.authoring import DeliveryParser  # noqa: E402
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance  # noqa: E402
from spacebio_review.v5.chief import TopicBatch  # noqa: E402
from spacebio_review.v5.config import load_config  # noqa: E402
from spacebio_review.v5.delegation import parse_delegation_arguments  # noqa: E402
from spacebio_review.v5.session import ResearchRuntime  # noqa: E402
from spacebio_review.v5.topic import TopicResearch  # noqa: E402


def fixed_arguments(source_run, name):
    result = json.loads((source_run / name / "result.json").read_text("utf-8"))
    replies = []
    for path in sorted(
        (source_run / "public-responses").glob(f"*-{result['chief_id']}-scientific.json")
    ):
        saved = json.loads(path.read_text("utf-8"))
        for call in saved["message"].get("tool_calls") or []:
            if call["function"]["name"] == "research_topics":
                replies.append((path, call["function"]["arguments"]))
    assert len(replies) == 1, "This bounded probe expects one fixed malformed delegation per case"
    path, arguments = replies[0]
    try:
        TopicBatch.model_validate_json(arguments)
    except ValidationError as exc:
        assert any(row["type"] == "json_invalid" for row in exc.errors())
    else:
        raise AssertionError("No structure parser is needed for valid arguments")
    return path, arguments


def exact_text_checks(batch, arguments):
    """Inspect preservation of existing strings, without interpreting or editing them."""
    rows = []
    for index, request in enumerate(batch.requests):
        for field in ("objective", "impact"):
            text = getattr(request.task, field)
            rows.append(
                {
                    "task_index": index,
                    "field": field,
                    "unchanged_literal_in_original": text in arguments
                    or json.dumps(text, ensure_ascii=False)[1:-1] in arguments,
                    "text": text,
                }
            )
    return rows


async def assemble_without_research(config, original_run, name, output, batch):
    prior = json.loads((original_run / name / "result.json").read_text("utf-8"))
    state = json.loads(
        (original_run / "v5/sessions" / (prior["chief_id"] + ".json")).read_text("utf-8")
    )

    def no_http(request):
        raise AssertionError("delegation_parse_probe_has_no_source_http")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_http)) as client:
        store = SourceStore(original_run / name / "sources", client, browser_fallback=False)
        store.restore()
        fixed = ScriptModel([])
        skills = SkillLibrary(config.skills_dir)
        engine = ResearchRuntime(
            output, {"fixed": fixed}, {"chief": "fixed", "researcher": "fixed"}, skills=skills
        )
        topics = TopicResearch(
            engine,
            store,
            NoDiscovery(),
            application_ids=[row["source_id"] for row in state["task"]["application"]["sources"]],
            catalogue=ExpertiseCatalogue(config.expertise_config, skills),
            external_research_enabled=False,
        )
        assembled = []
        try:
            for request in batch.requests:
                topic_id = topics.create(request.task)
                task_context = json.loads(engine.load(topic_id).task.evaluation)
                assembled.append(
                    {
                        "topic_id": topic_id,
                        "task": request.task.model_dump(mode="json"),
                        "application_basis": task_context["application_basis"],
                        "available_sources": task_context["available_sources"],
                        "status": engine.load(topic_id).status,
                    }
                )
            assert not fixed.requests, "Assemble input only; never run any child"
        finally:
            await engine.close()
        return assembled


async def probe(args):
    output, source_run = args.output.resolve(), args.source_run.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = load_config(args.config)
    prior = json.loads((source_run / "summary.json").read_text("utf-8"))
    prior_count = prior["cumulative_actual_requests"]
    assert prior_count == 10, "This final local probe uses the two remaining change16 requests"
    source_input = json.loads((source_run / "input.json").read_text("utf-8"))
    original_run = Path(source_input["framing_only_from"])
    budget = Budget(output, BudgetLimits(max_model_calls=2, max_embedding_calls=0, max_seconds=600))
    settings = config.models[config.auxiliary["delivery_parser"]].model_copy(
        update={"retries": 0, "timeout_seconds": 180}, deep=True
    )
    model = model_class(settings)(settings, env_file=args.env_file, budget=budget)
    parser, cases = DeliveryParser(model), []
    write_json(
        output / "input.json",
        {
            "source_run": source_run.as_posix(),
            "prior_actual_requests": prior_count,
            "limits": budget.limits,
            "model": settings,
            "scope": "Two existing malformed arguments only; no new author calls or research.",
        },
    )
    try:
        async with asyncio.timeout(budget.remaining_seconds()):
            for name, expected_count in (("sample2", 2), ("explicit-positive", 3)):
                folder = output / name
                folder.mkdir(parents=True, exist_ok=True)
                source_path, arguments = fixed_arguments(source_run, name)
                (folder / "raw-arguments.txt").write_text(arguments, "utf-8")
                budget.register(name, SessionAllowance(completion_calls=0))
                row = {"case": name, "source": source_path.as_posix()}
                try:
                    with budget.scope(name, purpose="auxiliary"):
                        batch = await parse_delegation_arguments(parser, arguments, TopicBatch)
                    write_json(folder / "parsed.json", batch)
                    checks = exact_text_checks(batch, arguments)
                    write_json(folder / "text-comparison.json", checks)
                    assert len(batch.requests) == expected_count
                    assert all(check["unchanged_literal_in_original"] for check in checks)
                    assembled = await assemble_without_research(
                        config, original_run, name, folder, batch
                    )
                    write_json(folder / "assembled-inputs.json", assembled)
                    row.update(
                        status="passed",
                        parsed_tasks=len(batch.requests),
                        unchanged_text_fields=len(checks),
                        assembled_topics=len(assembled),
                    )
                except Exception as exc:
                    row.update(status="failed", error=type(exc).__name__, reason=str(exc))
                    if public := getattr(exc, "public_reply", None):
                        write_json(folder / "failed-public-reply.json", public)
                finally:
                    budget.finish(name)
                    row["usage"] = budget.usage(name)
                    write_json(folder / "result.json", row)
                    cases.append(row)
    finally:
        budget.save()
        await model.close()
        summary = {
            "status": "passed"
            if len(cases) == 2 and all(r["status"] == "passed" for r in cases)
            else "incomplete",
            "cases": cases,
            "usage": budget.usage(),
            "prior_actual_requests": prior_count,
            "cumulative_actual_requests": prior_count + budget.usage()["model_calls"],
            "elapsed_seconds": 600 - budget.remaining_seconds(),
            "requests": budget.data["requests"],
            "new_scientific_author_requests": 0,
            "child_topics_executed": 0,
            "retrieval_requests": 0,
            "embedding_requests": 0,
            "scientific_quality": "Parser preserves prior author content; it does not certify it.",
        }
        write_json(output / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/runtime.yaml")
    parser.add_argument("--env-file", default=".env")
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
