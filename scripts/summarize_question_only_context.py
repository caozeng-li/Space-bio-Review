"""Offline execution/exposure audit, with no automated scientific grading."""

import argparse
import json
from collections import Counter
from pathlib import Path

from probe_expert_trajectory_live import load, production_hashes
from spacebio_review.persistence import write_json
from spacebio_review.v5.research_context import application_originals


def strings(value):
    if isinstance(value, dict):
        return [s for child in value.values() for s in strings(child)]
    if isinstance(value, list):
        return [s for child in value for s in strings(child)]
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return [value]
        return strings(decoded) if isinstance(decoded, (list, dict)) else [value]
    return []


def audit_run(run):
    verification = load(run / "verification.json")
    calls = [load(p) for p in sorted((run / "trace").glob("call-*-output.json"))]
    main = [r for r in calls if r.get("role") == "researcher"]
    steps = load(run / "research-steps.json") if (run / "research-steps.json").exists() else []
    state = load(run / "initial-state.json")
    result = load(run / "topic-result.json") if (run / "topic-result.json").exists() else {}
    application = application_originals(state["task"]["application"], state["evidence"])
    original_texts = [item["text"] for item in application.values() if item.get("text")]
    coverage = []
    for row in main:
        actual = load(run / "trace" / f"{row['call_id']}-input.json")
        text = "\n".join(strings(actual["messages"]))
        copied = [r["turn"] for r in steps if r["turn"] < row["turn"]
                  and len(r["findings"]) >= 60 and r["findings"] in text]
        coverage.append({
            "call": row["call_id"], "turn": row["turn"],
            "application_positions": sum(t in text for t in original_texts),
            "application_total": len(original_texts),
            "old_complete_findings_visible": copied,
            "roles": [m["role"] for m in actual["messages"]],
            "research_topic_present": load(run.parent / "plan.json")["assignment"]["responsibility"] in text,
        })
    snapshots = [load(p) for p in (run / "trace").glob("session*-turn-*.json")]
    events = [json.loads(line) for line in (run / "trace/events.jsonl").read_text("utf-8").split("\n") if line]
    tools = [r["observation"] for r in events
             if r["kind"] == "observation" and r["observation"].get("kind") == "tool"]
    explicit_stop = bool(steps and steps[-1]["stop_research"]) if steps else any(
        r["name"] == "finish_research" and r["output"]["result"]["ok"] for r in tools
    )
    return {
        "path": str(run), "verification": verification,
        "main_calls": len(main), "model_calls": len(calls),
        "main_session_ids": sorted({r["session_id"] for r in main}),
        "calls_by_role": dict(Counter(r.get("role", "auxiliary") for r in calls)),
        "captured_sources": dict(Counter(
            item["reference"]["source_id"] for item in result.get("readings", {}).values()
        )),
        "input_tokens": sum(r["input_tokens"] or 0 for r in calls),
        "output_tokens": sum(r["output_tokens"] or 0 for r in calls),
        "records": sum(bool(r.get("opinion")) for r in steps),
        "explicit_stop": explicit_stop,
        "findings_characters": sum(len(r["findings"]) for r in steps),
        "studied_questions": len(load(run / "question-state.json")["studied_questions"])
        if (run / "question-state.json").exists() else None,
        "maximum_next_questions": max((len(r["next_questions"]) for r in steps), default=0),
        "maximum_context_compactions": max((r["context_compactions"] for r in snapshots), default=0),
        "unbound_citations": [{"turn": r["turn"], "labels": r["unbound_citations"]}
                              for r in steps if r.get("unbound_citations")],
        "tools": dict(Counter(r["name"] for r in tools)),
        "tool_errors": [{"tool": r["name"], "result": r["output"]["result"]} for r in tools
                        if not r["output"]["result"]["ok"]],
        "exposure_audit": coverage,
    }


def chief_audit(root, condition, topic, repeat):
    folder = root / "chief" / f"{condition}-{topic}-{repeat}"
    if not (folder / "verification.json").exists():
        return {}
    outputs = [load(p) for p in (folder / "trace").glob("call-*-output.json")]
    return {
        **load(folder / "verification.json"),
        "known_text_input_tokens": sum(x["input_tokens"] or 0 for x in outputs),
        "known_text_output_tokens": sum(x["output_tokens"] or 0 for x in outputs),
    }


def main(root):
    design = load(root / "design.json")
    rows = []
    for path in sorted(root.glob("*/*/run-*/verification.json")):
        rows.append(audit_run(path.parent))
    primary = []
    for condition, topics in design["main_cases"].items():
        for topic, repeats in topics.items():
            for repeat in repeats:
                path = root / condition / topic / f"run-{repeat}"
                audit = next((r for r in rows if Path(r["path"]) == path), None)
                if audit is None:
                    continue
                row = {"condition": condition, "topic": topic, "repeat": repeat, **audit}
                row["chief"] = chief_audit(root, condition, topic, repeat)
                primary.append(row)
    supplemental = []
    for spec in design.get("supplemental_cases", []):
        condition, topic, repeat = spec.split(":")
        path = root / condition / topic / f"run-{repeat}"
        audit = next((r for r in rows if Path(r["path"]) == path), None)
        if audit is not None:
            supplemental.append({
                "condition": condition, "topic": topic, "repeat": int(repeat), **audit,
                "chief": chief_audit(root, condition, topic, repeat),
            })
    groups = {}
    for condition in design["main_cases"]:
        group = [r for r in primary if r["condition"] == condition]
        groups[condition] = {
            "cases": len(group),
            "submitted": sum(r["verification"]["status"] == "submitted" for r in group),
            "explicit_stops": sum(r["explicit_stop"] for r in group),
            "main_responses": sum(r["main_calls"] for r in group),
            "all_model_responses": sum(r["model_calls"] for r in group),
            "model_attempts_including_retries": sum(
                r["verification"].get("usage", {}).get("model_calls", 0) for r in group
            ),
            "known_text_input_tokens": sum(r["input_tokens"] for r in group),
            "known_text_output_tokens": sum(r["output_tokens"] for r in group),
            "records": sum(r["records"] for r in group),
            "tool_errors": sum(len(r["tool_errors"]) for r in group),
            "complete_chiefs": sum(r.get("chief", {}).get("status") == "complete" for r in group),
            "chief_known_text_input_tokens": sum(
                r.get("chief", {}).get("known_text_input_tokens", 0) for r in group
            ),
            "chief_known_text_output_tokens": sum(
                r.get("chief", {}).get("known_text_output_tokens", 0) for r in group
            ),
        }
    write_json(root / "execution-audit.json", {
        "production_unchanged": production_hashes() == design["production_hashes"],
        "runs": rows,
        "primary_runs": primary,
        "supplemental_runs": supplemental,
        "groups": groups,
        "cost_note": "Text tokens are provider-returned usage; unknown failed-attempt usage and embeddings are not included.",
    })
    print(json.dumps([{k: row[k] for k in ("path", "main_calls", "model_calls", "records", "explicit_stop")}
                      | {"status": row["verification"]["status"]} for row in rows], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="runs/question_only_context_sample2")
    main(Path(parser.parse_args().output).resolve())
