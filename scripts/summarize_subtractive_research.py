"""Offline execution/exposure audit; scientific conclusions are manually assessed."""

import json
import re
from collections import defaultdict
from pathlib import Path

from probe_expert_trajectory_live import hashes, load, production_hashes
from summarize_question_workspace import metrics

from spacebio_review.persistence import write_json
from spacebio_review.prose import prose_inputs

ROOT = Path("runs/subtractive_research_sample2")
GROUPS = ["baseline", "lean", "consult_full", "consult_narrow", "handoff_full", "handoff_narrow"]


def main():
    rows = []
    for group in GROUPS:
        for run in sorted((ROOT / group).glob("run-*")):
            if not (run / "topic-result.json").exists():
                continue
            row = metrics(run)
            result = load(run / "topic-result.json")
            row["group"] = group
            body = prose_inputs(result["note"])["body"] if result.get("note") else ""
            row["expert_characters"] = len(body)
            row["expert_chinese_characters"] = len(re.findall(r"[\u4e00-\u9fff]", body))
            row["recipient_composition_calls"] = []
            row["automatic_manuscript_omissions"] = []
            row["main_model_input_characters"] = []
            peer_path = run / "initial-peer.json"
            peer = load(peer_path) if peer_path.exists() else None
            peer_prefix = prose_inputs(peer["note"])["body"][:70] if peer else None
            row["peer_manuscript_prefix"] = peer_prefix
            row["peer_manuscript_visible_calls"] = []
            row["explicit_peer_manuscript_reads"] = []
            for path in sorted((run / "trace").glob("call-*-output.json")):
                call = load(path)
                if call.get("session_id") != row["verification"]["session_id"]:
                    continue
                if call["call_id"] in row["composition_calls"]:
                    row["recipient_composition_calls"].append(call["call_id"])
                actual = load(run / "trace" / f"{call['call_id']}-input.json")
                if peer_prefix and any(
                    peer_prefix in m.get("content", "")
                    for m in actual["messages"]
                    if isinstance(m.get("content"), str)
                ):
                    row["peer_manuscript_visible_calls"].append(call["call_id"])
                for tool in call["message"].get("tool_calls") or []:
                    if (
                        peer
                        and tool["function"]["name"] == "read_references"
                        and peer["note_ref"] in tool["function"]["arguments"]
                    ):
                        row["explicit_peer_manuscript_reads"].append(call["call_id"])
                row["main_model_input_characters"].append(
                    {
                        "call_id": call["call_id"],
                        "turn": call["turn"],
                        "characters": sum(
                            len(m.get("content") or "")
                            for m in actual["messages"]
                            if isinstance(m.get("content"), str)
                        ),
                    }
                )
            events = [
                json.loads(line)
                for line in (run / "trace/events.jsonl").read_text("utf-8").splitlines()
            ]
            row["automatic_manuscript_omissions"] = [
                e for e in events if e["kind"] == "automatic_manuscript_omitted"
            ]
            if (run / "consultations.json").exists():
                requests = load(run / "consultations.json")
                row["consultation_statuses"] = [
                    {k: q[k] for k in ["request_id", "status", "target_inquiry"]}
                    for q in requests
                    if q["kind"] == "consultation"
                ]
            rows.append(row)
    write_json(ROOT / "execution_metrics.json", rows)
    totals = defaultdict(lambda: defaultdict(int))
    for row in rows:
        for field in [
            "calls",
            "model_input_tokens",
            "model_output_tokens",
            "expert_characters",
            "expert_chinese_characters",
        ]:
            totals[row["group"]][field] += row[field]
        totals[row["group"]]["samples"] += 1
    write_json(ROOT / "group_totals.json", dict(totals))
    chiefs = []
    for path in sorted((ROOT / "chief").glob("*/verification.json")):
        run = path.parent
        calls = [load(p) for p in (run / "trace").glob("call-*-output.json")]
        decision = load(run / "decision.json") if (run / "decision.json").exists() else {}
        chiefs.append(
            {
                "run": str(run),
                "verification": load(path),
                "calls": len(calls),
                "input_tokens": sum(c["input_tokens"] for c in calls),
                "output_tokens": sum(c["output_tokens"] for c in calls),
                "engineering": decision.get("dimensions", {}).get("experiment_feasibility"),
            }
        )
    write_json(ROOT / "chief_metrics.json", chiefs)
    design, plan = load(ROOT / "design.json"), load(ROOT / "baseline/plan.json")
    write_json(
        ROOT / "final_verification.json",
        {
            "production_unchanged": production_hashes() == design["production_hashes"],
            "original_sources_unchanged": hashes([Path(p) for p in plan["original_source_hashes"]])
            == plan["original_source_hashes"],
            "full_review_runs": 0,
            "topic_runs": len(rows),
            "submitted": sum(r["verification"]["status"] == "submitted" for r in rows),
            "chiefs": {Path(r["run"]).name: r["verification"]["status"] for r in chiefs},
            "completed_api_responses": sum(r["calls"] for r in rows)
            + sum(r["calls"] for r in chiefs),
            "accepted_chief_decisions": sum(
                r["verification"]["status"] == "complete" for r in chiefs
            ),
        },
    )
    print(json.dumps(dict(totals), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
