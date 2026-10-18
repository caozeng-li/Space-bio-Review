"""Inspect one retained topic's composition input; --live makes at most one model call.

Default: no API client, credentials, retrieval, or review runner. All outputs go to a
new directory with a fresh Session/Inquiry. Historical files supply scientific
material only. The baseline reconstructs pre-change-01 context assembly, not the
original provider request.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from spacebio_review.agents.context import TokenEstimator
from spacebio_review.models.config import ModelConfig
from spacebio_review.models.provider import ModelError, model_class
from spacebio_review.persistence import write_json
from spacebio_review.prose import bind_prose, cited_ids, prose_inputs
from spacebio_review.tools import ToolResult
from spacebio_review.v5.budget import Budget, BudgetLimits, SessionAllowance
from spacebio_review.v5.records import SessionTask
from spacebio_review.v5.session import ResearchRuntime, encoded
from spacebio_review.v5.source_reading import SourceReading, visible_note_refs

DEFAULT_SESSION = "session-e0730c635aad42e48bd6c9d89a4dd98f"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def legacy_payload(result, readings, aliases):
    """Frozen pre-change-01 presentation; do not use the new payload normalizer."""
    output = prose_inputs(result.model_dump(mode="json"))
    reverse = {handle: alias for alias, handle in aliases.items()}

    def link(value):
        if isinstance(value, dict):
            if isinstance(value.get("cites"), list):
                value["cites"] = [reverse.get(key, key) for key in value["cites"]]
            alias = reverse.get(value.get("evidence_handle"))
            if alias:
                value["evidence_handle"] = alias
                original = readings.get(alias)
                if original and value.get("text") == original.get("quote"):
                    value.pop("text")
                    value["text_reference"] = alias
            for child in value.values():
                link(child)
        elif isinstance(value, list):
            for child in value:
                link(child)

    link(output)
    return {"result": output, "readings": readings}


def legacy_context(runtime, state, context):
    """Reproduce the old _context with the same saved state and working context."""
    notes, active = [], set()
    working = context()
    active.update(state.aliases.get(ref, ref) for ref in cited_ids(working))
    observations = deepcopy(state.observations)
    for row in observations:
        if row.get("kind") in {"tool", "author_delivery"} and "output" in row:
            output = row["output"]
            row["output"] = legacy_payload(
                ToolResult.model_validate(output["result"]),
                output.get("readings", {}),
                state.aliases,
            )
            if row.get("author_text_ref") and not output["result"]["ok"]:
                row["author_text"] = runtime.store.author_text(row["author_text_ref"])["content"]
    if state.note_ref:
        note = deepcopy(runtime.store.note(state.note_ref)["note"])
        active.update(cited_ids(note))
        reverse = {handle: alias for alias, handle in state.aliases.items()}
        note["body"] = bind_prose(note["body"], reverse)
        notes.append({"note_ref": state.note_ref, "note": note})
    if not notes:
        active.update(state.evidence)
    present = {
        state.aliases[alias]
        for row in state.observations
        for alias in row.get("output", {}).get("readings", {})
        if alias in state.aliases
    }
    application = state.task.application
    if isinstance(application, dict):
        passages = {
            (source["source_id"], passage["passage_id"]): passage["text"]
            for source in application.get("sources", [])
            for passage in source.get("passages", [])
        }
        present.update(
            key
            for key, item in state.evidence.items()
            if passages.get((item["reference"]["source_id"], item["reference"].get("passage_id")))
            == item.get("quote")
        )
    return {
        "task": state.task.model_dump(mode="json"),
        "skills": state.skill_bodies,
        "current_research": prose_inputs(notes),
        "originals": {
            alias: state.evidence[key]
            for alias, key in state.aliases.items()
            if key in active - present
        },
        "available_readings": {
            alias: {
                key: state.evidence[handle].get(key, {} if key == "reference" else "")
                for key in ("title", "locator", "reading_scope", "reference")
            }
            for alias, handle in state.aliases.items()
        },
        "unabsorbed_observations": observations,
        "followup": state.followup,
        "working_context": legacy_payload(ToolResult(ok=True, content=working), {}, state.aliases)[
            "result"
        ]["content"],
        "resources": {
            "session_model_turns_left": max(0, state.settings["max_turns"] - state.turns_used),
            "session_usage": None,
            "run_usage": None,
            "run_model_calls_left": None,
            "run_seconds_left": None,
        },
    }


def copy_inputs(source, output, session_id):
    """Import immutable scientific products, never historical execution records."""
    if output == source or source in output.parents or output in source.parents:
        raise ValueError("The output must be separate from the source run")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty output directory")
    delivery = read_json(source / "v5" / "deliveries" / f"{session_id}.json")
    state = read_json(source / "v5" / "sessions" / f"{session_id}.json")
    paths = set()
    refs = {row["note_ref"] for row in delivery["source_readings"] if row.get("note_ref")}
    refs.update(
        ref for ref in (state.get("note_ref"), state.get("initial_understanding_ref")) if ref
    )
    for ref in refs:
        relative = Path("notes") / f"{ref}.json"
        paths.add(relative)
        note = read_json(source / "v5" / relative)
        if note.get("author_text_ref"):
            paths.add(Path("author-texts") / (note["author_text_ref"] + ".json"))
    paths.update(
        Path("author-texts") / (row["author_text_ref"] + ".json")
        for row in state.get("observations", []) if row.get("author_text_ref")
    )
    for relative in sorted(paths):
        target = output / "v5" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / "v5" / relative, target)
    return state, delivery, [str(Path("v5") / path) for path in sorted(paths)]


def import_scientific_state(runtime, source, historical):
    """Create a new inquiry while retaining the source material's E ordering."""
    topic_path = source / "v5" / "topics" / f"{historical.session_id}.json"
    assignment = (
        read_json(topic_path)["task"] if topic_path.exists() else historical.task.model_dump()
    )
    focus = assignment.get("objective") or historical.task.objective
    if historical.inquiry_id:
        inquiry = read_json(source / "v5" / "inquiries" / f"{historical.inquiry_id}.json")
        assignment = inquiry["assignment"]
        focus = inquiry["current_focus"] or focus
    # Fresh capture assigns E1... in the same order as the retained observations.
    readings = {handle: historical.evidence[handle] for handle in historical.aliases.values()}
    readings.update(historical.evidence)
    session_id = runtime.create(
        historical.task.model_copy(deep=True),
        readings=readings,
        inquiry_assignment=assignment,
    )
    state = runtime.load(session_id)
    state.skill_bodies = deepcopy(historical.skill_bodies)
    state.observations = deepcopy(historical.observations)
    state.observation_index = historical.observation_index
    state.research_finished = True
    runtime.store.set_focus(
        state.inquiry_id, focus, reason="独立成稿 probe：沿用历史专题问题，仅导入已有材料。"
    )
    runtime.store.save(state)
    return state


def walk(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def canonical_note(value, aliases):
    value = prose_inputs(value)
    if isinstance(value, dict):
        return {
            key: [aliases.get(ref, ref) for ref in item]
            if key == "cites"
            else canonical_note(item, aliases)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [canonical_note(item, aliases) for item in value]
    return value


def preservation_report(payload, state, readings):
    """Development observations, not a production validator or a semantic verdict."""
    objects = [item for item in walk(payload) if isinstance(item, dict)]
    note_results = []
    for reading in readings:
        ref = reading["note_ref"]
        copies = [
            item["note"] for item in objects if item.get("note_ref") == ref and item.get("note")
        ]
        expected = canonical_note(reading["note"], state.aliases)
        note_results.append(
            {
                "note_ref": ref,
                "source_id": reading["source_id"],
                "complete_copies": sum(
                    canonical_note(item, state.aliases) == expected for item in copies
                ),
                "different_copies": sum(
                    canonical_note(item, state.aliases) != expected for item in copies
                ),
            }
        )
    application = payload.get("task", {}).get("application", {})
    application_passages = (
        {
            (source["source_id"], passage["passage_id"]): passage.get("text")
            for source in application.get("sources", [])
            for passage in source.get("passages", [])
        }
        if isinstance(application, dict)
        else {}
    )
    originals = []
    for handle, evidence in state.evidence.items():
        quote = evidence.get("quote")
        reference = evidence["reference"]
        matching = [item for item in objects if item.get("reference") == reference]
        position = (reference.get("source_id"), reference.get("passage_id"))
        in_application = position in application_passages
        originals.append(
            {
                "evidence_handle": handle,
                "reference": reference,
                "identity_present": bool(matching) or in_application,
                "full_application_position": in_application,
                "exact_quote_present": (
                    any(item.get("quote") == quote for item in matching)
                    or application_passages.get(position) == quote
                )
                if quote
                else None,
            }
        )
    return {
        "notes": note_results,
        "all_notes_present_once": all(row["complete_copies"] == 1 for row in note_results),
        "all_notes_unchanged": all(row["different_copies"] == 0 for row in note_results),
        "original_count": len(originals),
        "identities_present": sum(row["identity_present"] for row in originals),
        "exact_quotes_present": sum(row["exact_quote_present"] is True for row in originals),
        "originals": originals,
        "scope": "Exact retained text/identity comparison; does not assess scientific correctness.",
    }


def measurements(estimator, messages, payload, config):
    return {
        "request": estimator.measure(messages, [], output_reserve=config.max_tokens),
        "parts": {
            key: {
                "characters": len(encoded(value)),
                "utf8_bytes": len(encoded(value).encode("utf-8")),
                "input_tokens": estimator.measure([{"role": "user", "content": encoded(value)}])[
                    "input_tokens"
                ],
            }
            for key, value in payload.items()
        },
    }


async def run_live(runtime, state, context):
    budget = runtime.budget
    model = runtime.models[state.model_alias]
    result = {
        "requested": True,
        "executed": False,
        "scientific_calls_limit": 1,
        "retrieval_enabled": False,
        "session_id": state.session_id,
        "inquiry_id": state.inquiry_id,
    }
    try:
        await runtime._write_phase(
            state,
            model,
            phase="final_manuscript",
            operation="submit_research",
            context=context,
        )
        result.update(status=state.status, note_ref=state.note_ref)
        artifact = runtime.store.note(state.note_ref)
        raw_ref = artifact["author_text_ref"]
        (runtime.store.root.parent / "manuscript.md").write_text(
            runtime.store.author_text(raw_ref)["content"], encoding="utf-8"
        )
        result["author_text_ref"] = raw_ref
    except Exception as exc:
        result.update(status="failed", error_type=type(exc).__name__)
        if isinstance(exc, ModelError):
            result["reason"] = str(exc)
    finally:
        budget.save()
        result["usage"] = budget.usage()
        result["executed"] = result["usage"]["model_calls"] > 0
    write_json(runtime.store.root.parent / "live-result.json", result)
    return result


async def main(args):
    source, output = Path(args.source_run).resolve(), Path(args.output).resolve()
    snapshot, delivery, copied = copy_inputs(source, output, args.session_id)
    # Read only the fields used as materials or baseline statistics. Historical
    # execution records are not loaded into the current SessionState contract.
    historical = SimpleNamespace(
        **{
            key: snapshot.get(key)
            for key in (
                "session_id", "inquiry_id", "settings", "turns_used", "skill_bodies",
                "evidence", "aliases", "observations", "observation_index", "note_ref",
                "initial_understanding_ref", "followup",
            )
        },
        task=SessionTask.model_validate(snapshot["task"]),
    )
    config = ModelConfig.model_validate(snapshot["model_snapshot"])
    budget = None
    allowances = None
    model = SimpleNamespace(config=config)
    if args.live:
        config = config.model_copy(update={"retries": 0})
        budget = Budget(
            output,
            BudgetLimits(
                max_model_calls=1,
                max_embedding_calls=0,
                max_input_tokens=args.max_input_tokens,
                max_output_tokens=config.max_tokens,
                max_seconds=config.timeout_seconds + 30,
            ),
        )
        allowances = {
            "researcher": SessionAllowance(
                max_input_tokens=args.max_input_tokens,
                max_output_tokens=config.max_tokens,
                completion_input_tokens=args.max_input_tokens,
                completion_output_tokens=config.max_tokens,
                completion_calls=1,
                completion_seconds=config.timeout_seconds,
            )
        }
        model = model_class(config)(config, env_file=args.env_file, budget=budget)
    runtime = ResearchRuntime(
        output,
        {snapshot["model_alias"]: model},
        {"researcher": snapshot["model_alias"]},
        budget=budget,
        allowances=allowances,
    )
    runtime.scheduler.request_budget = budget
    if args.live:
        runtime._owned_models = [model]
    state = import_scientific_state(runtime, source, historical)

    def retained_context(observations):
        # This is TopicResearch._context's normal SourceReading.view contract.
        seen = visible_note_refs(observations)
        return {
            "source_readings": [
                SourceReading.view(row, seen=seen) for row in delivery["source_readings"]
            ]
        }

    def context():
        result = retained_context(state.observations)
        if historical.note_ref:
            # A historical manuscript is reference material, not this Inquiry's first version.
            result["historical_topic_contribution"] = {
                "note_ref": historical.note_ref,
                "note": runtime.store.note(historical.note_ref)["note"],
                "source_session_id": historical.session_id,
            }
        return result

    composition_messages = runtime._writing_messages(
        state,
        "final_manuscript",
        "submit_research",
        context,
        None,
    )
    payloads = {
        "baseline": legacy_context(
            runtime, historical, lambda: retained_context(historical.observations)
        ),
        "research": runtime._context(state, context, purpose="research"),
        "composition": runtime._context(state, context, purpose="composition"),
    }
    estimator = TokenEstimator(image_tokens=config.image_tokens)
    report = {
        "mode": "live" if args.live else "dry",
        "source_run": str(source),
        "source_session_id": args.session_id,
        "session_id": state.session_id,
        "inquiry_id": state.inquiry_id,
        "state_import": "New inquiry; task/evidence/observations imported as scientific material.",
        "copied_files": copied,
        "model": config.model,
        "model_alias": state.model_alias,
        "estimate": "Uncalibrated TokenEstimator; same scale=1 throughout, not historical usage.",
        "baseline": "Reconstructed pre-change-01 _context; same author prompt, no tool schema.",
        "part_estimates": "Each part includes its own message overhead; parts are not additive.",
        "source_state": {
            key: snapshot.get(key)
            for key in (
                "status",
                "reason",
                "turns_used",
                "note_ref",
            )
        },
        "measurements": {},
        "preservation": {},
    }
    for name, payload in payloads.items():
        messages = deepcopy(composition_messages)
        messages[-1]["content"] = encoded(payload)
        if name == "composition":
            report["composition_matches_actual_messages"] = messages == composition_messages
        write_json(output / f"{name}-context.json", payload)
        write_json(output / f"{name}-messages.json", messages)
        report["measurements"][name] = measurements(estimator, messages, payload, config)
        report["preservation"][name] = preservation_report(
            payload, state, delivery["source_readings"]
        )
    write_json(output / "probe-report.json", report)
    if args.live:
        report["live"] = await run_live(runtime, state, context)
        write_json(output / "probe-report.json", report)
    await runtime.close()
    print(
        json.dumps(
            {
                "output": str(output),
                "mode": report["mode"],
                "input_tokens": {
                    name: value["request"]["input_tokens"]
                    for name, value in report["measurements"].items()
                },
                "composition_matches_actual_messages": report[
                    "composition_matches_actual_messages"
                ],
                "preservation": {
                    name: {
                        key: value[key]
                        for key in (
                            "all_notes_present_once",
                            "all_notes_unchanged",
                            "original_count",
                            "identities_present",
                            "exact_quotes_present",
                        )
                    }
                    for name, value in report["preservation"].items()
                },
                "live": report.get("live", {"executed": False}),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", default="runs/mytest37_sample2")
    parser.add_argument("--session-id", default=DEFAULT_SESSION)
    parser.add_argument("--output", required=True, help="Empty directory outside the source run")
    parser.add_argument(
        "--live", action="store_true", help="Spend quota on one scientific manuscript"
    )
    parser.add_argument("--env-file", default=".env", help="Read only when --live is supplied")
    parser.add_argument("--max-input-tokens", type=int, default=160000, help="Live probe input cap")
    asyncio.run(main(parser.parse_args()))
