"""Offline request, handoff and source-exposure audit; no automatic science grading."""

import json
import re
from collections import Counter
from pathlib import Path

from probe_expert_trajectory_live import hashes, load, production_hashes

from spacebio_review.persistence import write_json
from spacebio_review.prose import prose_inputs

ROOT = Path("runs/question_relay_sample2")


def without_citations(body):
    return re.sub(r"\[\]\(evidence:[^)]+\)|\[证据\d+\]", "", body)


def strings(value):
    if isinstance(value, str):
        yield value
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return
        if isinstance(parsed, (list, dict)):
            yield from strings(parsed)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)


def main():
    rows = []
    for path in sorted(ROOT.glob("*/*/chain.json")):
        chain = load(path)
        calls, events, rounds = [], [], []
        earlier_bodies = []
        for row in chain["rounds"]:
            run = Path(row["run"])
            result = load(run / "topic-result.json")
            summary = load(run / "trace-summary.json")
            output = [load(p) for p in sorted((run / "trace").glob("call-*-output.json"))]
            transcript = [
                json.loads(line)
                for line in (run / "trace/events.jsonl").read_text("utf-8").split("\n")
                if line
            ]
            scientific = [c for c in output if c.get("role") == "researcher"]
            initial = load(run / "trace" / f"{scientific[0]['call_id']}-input.json")
            visible = []
            for call in scientific:
                actual = load(run / "trace" / f"{call['call_id']}-input.json")
                text = "\n".join(strings(actual["messages"]))
                visible.extend(
                    {"call": call["call_id"], "previous_round": i + 1}
                    for i, body in enumerate(earlier_bodies)
                    if body[:120] in text
                )
            body = prose_inputs(result["note"])["body"] if result.get("note") else ""
            if body:
                earlier_bodies.append(body)
            rounds.append(
                {
                    "round": row["round"],
                    "run": str(run),
                    "status": result["status"],
                    "reason": result.get("reason"),
                    "turns": result["turns_used"],
                    "note_characters": len(body),
                    "initial_message_roles": [m["role"] for m in initial["messages"]],
                    "earlier_manuscript_prefix_matches": visible,
                    "exposure": summary["exposure"],
                    "tools": summary["tools"],
                    "tool_failures": summary["tool_failures"],
                    "context_compactions": summary["maximum_context_compactions"],
                }
            )
            calls.extend(output)
            events.extend(transcript)
        rows.append(
            {
                **{k: chain[k] for k in ["condition", "topic", "repeat", "status"]},
                "stop": chain.get("stop"),
                "error": chain.get("error"),
                "rounds": rounds,
                "calls": len(calls),
                "calls_by_alias": dict(Counter(c["alias"] for c in calls)),
                "input_tokens": sum(c["input_tokens"] for c in calls),
                "output_tokens": sum(c["output_tokens"] for c in calls),
                "model_errors": [e for e in events if e["kind"] == "model_error"],
                "handoffs": chain.get("handoffs", []),
            }
        )
    write_json(ROOT / "execution_metrics.json", rows)
    chiefs = []
    for path in sorted((ROOT / "chief").glob("*/verification.json")):
        calls = [load(p) for p in (path.parent / "trace").glob("call-*-output.json")]
        verification = load(path)
        delivered = []
        if verification["status"] == "complete":
            request = load(path.parent / "trace/call-001-input.json")
            view = json.loads(request["messages"][1]["content"])
            group = next(x for x in view["expert_opinions"] if "research_rounds" in x)
            condition, topic, repeat = verification["case"].split(":")
            for record in group["research_rounds"]:
                run = ROOT / condition / f"{topic}-{repeat}" / f"round-{record['round']}" / "run-1"
                result = load(run / "topic-result.json")
                expected = prose_inputs(result["note"])["body"] if result.get("note") else ""
                actual = record.get("opinion", {}).get("body", "")
                delivered.append(
                    {
                        "round": record["round"],
                        "whole_body_preserved_except_citation_labels": (
                            without_citations(expected) == without_citations(actual)
                        ),
                    }
                )
        chiefs.append(
            {
                "run": str(path.parent),
                "verification": verification,
                "round_body_audit": delivered,
                "calls": len(calls),
                "input_tokens": sum(c["input_tokens"] for c in calls),
                "output_tokens": sum(c["output_tokens"] for c in calls),
            }
        )
    write_json(ROOT / "chief_metrics.json", chiefs)
    design = load(ROOT / "design.json")
    verification = {
        "production_unchanged": production_hashes() == design["production_hashes"],
        "original_sources_unchanged": hashes(
            [Path(p) for p in design["plan"]["original_source_hashes"]]
        )
        == design["plan"]["original_source_hashes"],
        "chains": len(rows),
        "rounds": sum(len(r["rounds"]) for r in rows),
        "chiefs": len(chiefs),
        "full_review_runs": 0,
        "completed_api_responses": sum(r["calls"] for r in [*rows, *chiefs]),
    }
    write_json(ROOT / "verification.json", verification)
    print(json.dumps(verification, ensure_ascii=False))
    for row in rows:
        print(
            json.dumps(
                {
                    k: row[k]
                    for k in [
                        "condition",
                        "topic",
                        "repeat",
                        "stop",
                        "calls",
                        "input_tokens",
                        "output_tokens",
                    ]
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
