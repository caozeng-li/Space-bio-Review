"""Freeze inputs and expose numbered originals for the in-session LLM judge.

The ChatGPT judge's scores are recorded in results2/judgments/<project_id>.json.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results2"
METHODS = ["SpaceBioReview", "SingleResearchAgent", "StaticResearchCommittee",
           "WithoutConsultation", "smolagents", "ThorneSectionWise"]

def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def freeze():
    manifest = json.loads((ROOT / "experiments/manifest.json").read_text(encoding="utf-8-sig"))
    rows = []
    for item in manifest["projects"]:
        pid = item["project_id"]
        names = METHODS.copy()
        random.Random("20261030:" + pid).shuffle(names)
        reports = {}
        for alias, method in zip("ABCDEF", names):
            path = ROOT / "experiments" / method / f"{pid}_report.md"
            state = json.loads((path.parent / "status.json").read_text(encoding="utf-8-sig"))
            last = state["projects"][pid][-1]
            if last["status"] not in ("completed", "fixed_by_me"):
                raise ValueError((pid, method, last["status"]))
            reports[alias] = {"method": method, "path": path.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "status": last["status"], "attempt": last["attempt"],
                "elapsed_seconds": last.get("elapsed_seconds"), "usage": last.get("usage", {}),
                "words": len(path.read_text(encoding="utf-8").split())}
        path = ROOT / "experiments/data" / f"{pid}.md"
        rows.append({"project_id": pid, "title": item["title"],
            "input": path.relative_to(ROOT).as_posix(),
            "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "reports": reports})
    dump(OUT / "input_manifest.json", {"judge_date": "2026-10-30", "research_as_of": manifest["as_of"],
        "judge_label_supplied_by_user": "ChatGPT Work (GPT-6 Astra Extra High)",
        "execution": "Direct judgments in the ChatGPT session",
        "masking": "Per-project randomized aliases and shuffled reading order",
        "projects": rows})
    print(f"Frozen {len(rows)} proposals / {len(rows)*len(METHODS)} reports")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["freeze", "list", "proposal", "report", "pairs"])
    ap.add_argument("project", nargs="?")
    ap.add_argument("aliases", nargs="*")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=100000)
    args = ap.parse_args()
    if args.mode == "freeze":
        freeze(); return
    rows = json.loads((OUT / "input_manifest.json").read_text(encoding="utf-8"))["projects"]
    if args.mode == "list":
        for row in rows:
            print(row["project_id"], row["title"])
        return
    row = next(r for r in rows if r["project_id"] == args.project)
    if args.mode == "pairs":
        anchor = next(k for k,v in row["reports"].items() if v["method"] == "SpaceBioReview")
        rng = random.Random("pairs:" + args.project)
        for other in row["reports"]:
            if other != anchor:
                pair = [anchor, other]; rng.shuffle(pair); print(" / ".join(pair))
        return
    if args.mode == "proposal":
        print("PROPOSAL:", row["project_id"])
        paths = [("proposal", row["input"])]
    else:
        paths = [(a, row["reports"][a]["path"]) for a in args.aliases]
    for label, path in paths:
        print(f"\n=== {label} ===")
        for i, line in enumerate((ROOT / path).read_text(encoding="utf-8").splitlines(), 1):
            if args.start <= i <= args.end:
                print(f"{i:04d} {line}")

if __name__ == "__main__":
    main()
