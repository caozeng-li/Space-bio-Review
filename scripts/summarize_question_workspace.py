"""Execution and input-exposure measurements, not an automatic scientific grader."""

import json
from collections import Counter
from pathlib import Path

from probe_expert_trajectory_live import hashes, load, production_hashes

from spacebio_review.persistence import write_json

ROOT = Path("runs/question_workspace_sample2")
TARGETS = {
    "application_medium_exchange": "培养皿适配，培养基换液及成像条件确定等",
    "confocal_existing": "发展到激光扫描共聚焦显微镜",
    "future_platform": "共焦拉曼光谱成像",
    "evt_method": "An experiment validation test (EVT) was performed prior to each launch",
    "evt_rin_table_title": "RNA yields and quality (RIN scores) from EVT",
    "flight_rnaseq_table": "Tubules available for RNAseq",
}


def metrics(run):
    calls = [load(p) for p in sorted((run / "trace").glob("call-*-output.json"))]
    events = [json.loads(line) for line in (run / "trace/events.jsonl").read_text("utf-8")
              .split("\n") if line]
    observations = [event["observation"] for event in events
                    if event["kind"] == "observation"
                    and event["observation"].get("kind") == "tool"]
    record_path = run / "question-records.json"
    records = load(record_path) if record_path.exists() else []
    scientific = [row for row in calls if row["alias"] == "scientific"]
    composition = [row for row in scientific if row["stage"] == "question_synthesis" or (
        row["stage"] == "research" and any(
            c["function"]["name"] == "publish_manuscript"
            for c in row["message"].get("tool_calls") or []
        ) and not records
    )]
    exposure = {}
    for row in scientific:
        actual = load(run / "trace" / f"{row['call_id']}-input.json")
        text = "\n".join(m.get("content") or "" for m in actual["messages"]
                         if isinstance(m.get("content"), str))
        exposure[row["call_id"]] = {
            "stage": row["stage"], "turn": row["turn"],
            **{key: needle.lower() in text.lower() for key, needle in TARGETS.items()},
        }
    scheduler = load(run / "scheduler.json") if (run / "scheduler.json").exists() else []
    snapshots = [load(p) for p in (run / "trace").glob("session*-turn-*.json")]
    return {
        "run": str(run), "verification": load(run / "verification.json"),
        "calls": len(calls),
        "calls_by_alias": dict(Counter(row["alias"] for row in calls)),
        "scientific_calls_by_stage": dict(Counter(row["stage"] for row in scientific)),
        "model_input_tokens": sum(row["input_tokens"] for row in calls),
        "model_output_tokens": sum(row["output_tokens"] for row in calls),
        "maximum_model_input": max(row["input_tokens"] for row in scientific),
        "question_count": len(records),
        "question_source_selection": [{
            "question": r["question"], "original_units": len(r["originals"]),
            "positions": [(v["reference"]["source_id"], v["reference"].get("passage_id"))
                          for v in r["originals"].values()],
        } for r in records],
        "tools": dict(Counter(o["name"] for o in observations)),
        "tool_failures": [{"tool": o["name"], "error": o["output"]["result"].get("error")
                           or (o["output"]["result"].get("content") or {}).get("error")}
                          for o in observations if o["output"]["result"].get("ok") is False],
        "model_errors": [event for event in events if event["kind"] == "model_error"],
        "scheduler_retries": sum(r.get("retries", 0) for r in scheduler),
        "maximum_compactions": max(s["context_compactions"] for s in snapshots),
        "composition_calls": [row["call_id"] for row in composition],
        "composition_exposure": {row["call_id"]: exposure[row["call_id"]] for row in composition},
        "input_exposure": exposure,
        "all_thinking_off": all(not r["effective_request"].get("enable_thinking")
                                for r in scientific),
    }


def main():
    design = load(ROOT / "design.json")
    rows = [metrics(ROOT / group / f"run-{n}")
            for group, repeats in design["comparison_runs"].items() for n in repeats
            if (ROOT / group / f"run-{n}" / "verification.json").exists()]
    write_json(ROOT / "execution_metrics.json", rows)
    for row in rows:
        print(json.dumps({k: row[k] for k in [
            "run", "calls", "scientific_calls_by_stage", "model_input_tokens",
            "model_output_tokens", "question_count", "tool_failures", "composition_exposure",
        ]}, ensure_ascii=False))
    plan = load(ROOT / "baseline/plan.json")
    downstream = {}
    for group in ["originals_only", "chief"]:
        group_rows = []
        for path in sorted((ROOT / group).glob("*/verification.json")):
            folder = path.parent
            calls = [load(p) for p in sorted((folder / "trace").glob("call-*-output.json"))]
            decision = load(folder / "decision.json") if (folder / "decision.json").exists() else {}
            group_rows.append({
                "run": str(folder), "verification": load(path), "calls": len(calls),
                "input_tokens": sum(c["input_tokens"] for c in calls),
                "output_tokens": sum(c["output_tokens"] for c in calls),
                "finish_reasons": [c["finish_reason"] for c in calls],
                "engineering": decision.get("dimensions", {}).get("experiment_feasibility"),
            })
        downstream[group] = group_rows
    write_json(ROOT / "handoff_metrics.json", downstream)
    write_json(ROOT / "final_verification.json", {
        "production_unchanged": production_hashes() == design["production_hashes"],
        "original_sources_unchanged": hashes([Path(p) for p in plan["original_source_hashes"]])
        == plan["original_source_hashes"],
        "full_review_runs": 0,
        "completed_experts": sum(r["verification"]["status"] == "submitted" for r in rows),
        "expert_sample_count": len(rows),
        "paired_synthesis": {Path(r["run"]).name: r["verification"]["status"]
                             for r in downstream["originals_only"]},
        "completed_comparison_model_calls": sum(r["calls"] for r in rows)
        + sum(r["calls"] for group in downstream.values() for r in group),
        "chiefs": {str(p.parent.name): load(p)["status"]
                   for p in (ROOT / "chief").glob("*/verification.json")},
    })


if __name__ == "__main__":
    main()
