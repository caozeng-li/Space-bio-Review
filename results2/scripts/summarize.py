"""Aggregate recorded judgments and render tables and figures.

Run from the repository root:
uv run --no-project --with matplotlib==3.11.2 --with numpy==2.4.6 --python .venv/Scripts/python.exe python -B -X utf8 results2/scripts/summarize.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm, ListedColormap
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results2"
FIG = OUT / "figures"
METHODS = ["SpaceBioReview", "SingleResearchAgent", "StaticResearchCommittee",
           "WithoutConsultation", "smolagents", "ThorneSectionWise"]
LABELS = ["SCOPE", "Single Research Agent", "Static Committee",
          "Without Consultation", "smolagents", "Thorne Section-wise"]
SHORT = ["SCOPE", "Single", "Static", "No consult.", "smolagents", "Section-wise"]
COLORS = ["#007C83", "#4477AA", "#EE7733", "#CC6677", "#228833", "#AA3377"]
KEYS = ["overall", "value", "grounding"]
SEED, REPEATS = 20261030, 10000


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def check(condition, message):
    if not condition:
        raise ValueError(message)


def estimate(values, indices):
    means = values[indices].mean(axis=1)
    return {"mean": float(np.mean(values)),
            "ci95": [float(x) for x in np.quantile(means, [0.025, 0.975])]}


def quartiles(values):
    return dict(zip(("q25", "median", "q75"), map(float, np.quantile(values, [.25, .5, .75])))) if values else None


def table(headers, rows):
    def line(items):
        return "| " + " | ".join(str(x).replace("|", "\\|").replace("\n", " ") for x in items) + " |"
    return "\n".join([line(headers), line(["---"] * len(headers)), *[line(row) for row in rows]])


def fmt(stat):
    return f"{stat['mean']:.2f} [{stat['ci95'][0]:.2f}, {stat['ci95'][1]:.2f}]"


def iq(stat, digits=1):
    return "NA" if stat is None else f"{stat['median']:,.{digits}f} [{stat['q25']:,.{digits}f}, {stat['q75']:,.{digits}f}]"


def save(fig, name):
    for extension in ("png", "pdf", "svg"):
        fig.savefig(FIG / f"{name}.{extension}", dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_axisbelow(True)
    ax.grid(axis="x", color="#E5E9ED", linewidth=.7)
    ax.tick_params(length=0)


def main():
    manifest = read(OUT / "input_manifest.json")
    projects = manifest["projects"]
    ids = [p["project_id"] for p in projects]
    check(len(set(ids)) == 20, "Expected 20 distinct proposals")
    check({p.stem for p in (OUT / "judgments").glob("*.json")} == set(ids), "Judgment set mismatch")
    check({p.stem for p in (OUT / "reference_cards").glob("*.json")} == set(ids), "Reference-card set mismatch")
    judgments, cards, flat, pair_flat = {}, {}, [], []
    values = np.zeros((20, 6, len(KEYS)), dtype=float)
    runtime_ids = {p["project_id"] for p in projects
                   if all(r["elapsed_seconds"] is not None for r in p["reports"].values())}
    for i, p in enumerate(projects):
        pid = p["project_id"]
        check(hashlib.sha256((ROOT / p["input"]).read_bytes()).hexdigest() == p["input_sha256"], f"Changed input: {pid}")
        j = judgments[pid] = read(OUT / "judgments" / f"{pid}.json")
        card = cards[pid] = read(OUT / "reference_cards" / f"{pid}.json")
        check(j["project_id"] == card["project_id"] == pid, f"Project id mismatch: {pid}")
        check([x["id"] for x in card["items"]] == ["V1", "V2", "V3", "L1", "L2", "L3"], f"Card items: {pid}")
        check(set(j["reports"]) == set(p["reports"]) == set("ABCDEF"), f"Report count: {pid}")
        check({r["method"] for r in p["reports"].values()} == set(METHODS), f"Methods: {pid}")
        for alias, info in p["reports"].items():
            check(hashlib.sha256((ROOT / info["path"]).read_bytes()).hexdigest() == info["sha256"], f"Changed report: {pid}/{alias}")
            r = j["reports"][alias]
            check(set(r["scores"]) == set(KEYS), f"Score keys: {pid}/{alias}")
            check(all(type(v) is int and 1 <= v <= 5 for v in r["scores"].values()), f"Scores: {pid}/{alias}")
            check(bool(r["rationale"]), f"Missing reasons: {pid}/{alias}")
            m = METHODS.index(info["method"])
            v = [r["scores"][k] for k in KEYS]
            values[i, m] = v
            flat.append({"project_id": pid, "method": info["method"], "alias": alias,
                         **dict(zip(KEYS, map(float, v))),
                         "status": info["status"], "report": info["path"], "attempt": info["attempt"],
                         "elapsed_seconds": info["elapsed_seconds"], "words": info["words"], "usage": info["usage"]})
        anchor = next(a for a, r in p["reports"].items() if r["method"] == METHODS[0])
        check(len(j["pairwise"]) == 5, f"Pair count: {pid}")
        seen = set()
        for pair in j["pairwise"]:
            check(anchor in (pair["left"], pair["right"]), f"Missing Full: {pid}")
            other = pair["right"] if pair["left"] == anchor else pair["left"]
            check(other != anchor and other not in seen, f"Duplicate pair: {pid}")
            seen.add(other)
            check(pair["winner"] in (pair["left"], pair["right"], "tie") and pair["reason"], f"Pair result: {pid}")
            outcome = "tie" if pair["winner"] == "tie" else "win" if pair["winner"] == anchor else "loss"
            pair_flat.append({"project_id": pid, "reference": METHODS[0], "comparator": p["reports"][other]["method"],
                              "outcome": outcome, **pair})
    rng = np.random.default_rng(SEED)
    boot = rng.integers(0, 20, size=(REPEATS, 20))
    summary = {"judge": manifest["judge_label_supplied_by_user"], "n_projects": 20, "n_reports": 120,
               "bootstrap": {"seed": SEED, "resamples": REPEATS, "unit": "proposal", "method": "paired percentile"},
               "methods": {}, "paired_comparisons": {}}
    for m, method in enumerate(METHODS):
        rows = [r for r in flat if r["method"] == method]
        common = [r for r in rows if r["project_id"] in runtime_ids]
        summary["methods"][method] = {
            "n": 20, "scores": {k: estimate(values[:, m, t], boot) for t, k in enumerate(KEYS)},
            "overall_distribution": {str(k): int(np.sum(values[:, m, 0] == k)) for k in range(1, 6)},
            "resources": {
                "elapsed_n": sum(r["elapsed_seconds"] is not None for r in rows),
                "elapsed_minutes": quartiles([r["elapsed_seconds"] / 60 for r in rows if r["elapsed_seconds"] is not None]),
                "common_n": len(common), "common_elapsed_minutes": quartiles([r["elapsed_seconds"] / 60 for r in common]),
                "words": quartiles([r["words"] for r in rows]),
            }}
        if m:
            pairs = [p for p in pair_flat if p["comparator"] == method]
            summary["paired_comparisons"][method] = {
                "counts": {o: sum(p["outcome"] == o for p in pairs) for o in ("win", "tie", "loss")},
                "delta_overall": estimate(values[:, 0, 0] - values[:, m, 0], boot)}
    dump(OUT / "scores.json", {"reports": flat, "pairwise": pair_flat})
    dump(OUT / "summary.json", summary)
    make_tables(summary, flat, pair_flat, projects, values)
    make_details(projects, judgments, cards)
    make_figures(summary, values, projects)
    dump(OUT / "validation.json", {"proposal_hashes_unchanged": 20, "report_hashes_unchanged": 120,
        "reference_cards": 20, "report_judgments": 120,
        "pairwise_comparisons": 100, "figures": 5,
        "matplotlib": matplotlib.__version__, "numpy": np.__version__})
    print(json.dumps({"means": {m: {k: round(v["mean"], 3) for k, v in s["scores"].items()} for m, s in summary["methods"].items()},
                      "pairwise": summary["paired_comparisons"]}, ensure_ascii=False, indent=2))


def make_tables(s, flat, pairs, projects, values):
    text = [
        "# Evaluation results", "",
        "Twenty shared proposals, six methods, and 120 reports. Scores range from 1 to 5. "
        "Intervals are 95% paired proposal-bootstrap intervals from 10,000 resamples.", "",
        "## 1. Review quality (n = 20)", "",
        table(["Method", "Overall quality ↑", "Value recognition ↑", "Judgment & grounding ↑"],
              [[LABELS[m], *[fmt(s["methods"][method]["scores"][k]) for k in KEYS]]
               for m, method in enumerate(METHODS)]), "",
        "## 2. Pairwise comparisons", "",
        table(["Comparator", "Full wins", "Ties", "Full losses", "Win / tie / loss %",
               "Overall-quality difference [95% CI]"],
              [[LABELS[m], *[s["paired_comparisons"][method]["counts"][o] for o in ("win", "tie", "loss")],
                " / ".join(f"{s['paired_comparisons'][method]['counts'][o] / 20 * 100:.0f}"
                           for o in ("win", "tie", "loss")),
                fmt(s["paired_comparisons"][method]["delta_overall"])]
               for m, method in enumerate(METHODS) if m]), "",
        "Pairs are ranked by overall quality, then judgment and grounding, then value recognition. "
        "Equal score triples are ties. Differences are SpaceBioReview minus the comparator.", "",
        "## 3. Overall-quality distribution", "",
        table(["Method", "1", "2", "3", "4", "5"],
              [[LABELS[m], *[s["methods"][method]["overall_distribution"][str(k)] for k in range(1, 6)]]
               for m, method in enumerate(METHODS)]), "",
        "## 4. Runtime and report length", "",
        "Runtime uses the 16 proposals with recorded durations for every method. "
        "Report length uses all 20 proposals and includes references. Values are medians [Q1, Q3].", "",
        table(["Method", "Runtime, minutes (n = 16)", "Report words (n = 20)"],
              [[LABELS[m], iq(s["methods"][method]["resources"]["common_elapsed_minutes"]),
                iq(s["methods"][method]["resources"]["words"], 0)] for m, method in enumerate(METHODS)]), "",
        "## 5. Report-level scores", "",
        table(["Project", "Method", "Overall", "Value", "Grounding", "Delivery status"],
              [[r["project_id"], r["method"], *[int(r[k]) for k in KEYS], r["status"]]
               for r in sorted(flat, key=lambda r: ([p["project_id"] for p in projects].index(r["project_id"]),
                                                     METHODS.index(r["method"])))]), "",
        "## 6. Proposal-level comparisons", "",
        table(["Project", "Comparator", "Full outcome", "Reason"],
              [[p["project_id"], p["comparator"], p["outcome"], p["reason"]] for p in pairs]), ""]
    (OUT / "table.md").write_text("\n".join(text), encoding="utf-8")


def make_details(projects, judgments, cards):
    lines = ["# Evaluation rationales", "",
             "Proposal-specific reference points, report assessments, and source checks. "
             "Line numbers refer to the original Markdown files recorded in input_manifest.json.", ""]
    for p in projects:
        pid = p["project_id"]
        j, c = judgments[pid], cards[pid]
        lines.extend([f"## {pid}", "", p["title"], "", f"Materials examined: {j.get('reading', '')}",
                      "", "### Reference points", ""])
        for item in c["items"]:
            lines.append(f"- **{item['id']}**: {item['text']} (proposal lines {item['proposal_lines']})")
        for alias, r in j["reports"].items():
            info = p["reports"][alias]
            score = r["scores"]
            lines.extend(["", f"### {info['method']} (alias {alias})", "",
                          f"Overall: {score['overall']}/5; Value: {score['value']}/5; "
                          f"Grounding: {score['grounding']}/5.", "", r["rationale"], ""])
        lines.extend(["", "### Source checks", ""])
        for check_item in j.get("external_checks", []):
            link = f"[Source]({check_item['source']})" if check_item.get("source") else "Proposal and reasoning check"
            body = "; ".join(str(check_item[k]) for k in ("finding", "basis") if check_item.get(k))
            lines.append(f"- {link}: {body}")
        lines.append("")
    (OUT / "judgments.md").write_text("\n".join(lines), encoding="utf-8")


def make_figures(s, values, projects):
    FIG.mkdir(exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.titlesize": 12,
                         "axes.labelsize": 10, "pdf.fonttype": 42, "ps.fonttype": 42,
                         "svg.fonttype": "none", "axes.titlepad": 12})
    y = np.arange(6)
    fig, axs = plt.subplots(1, 3, figsize=(13, 4.4), sharey=True)
    for t, ax in enumerate(axs):
        for m, method in enumerate(METHODS):
            stat = s["methods"][method]["scores"][KEYS[t]]
            mean, (lo, hi) = stat["mean"], stat["ci95"]
            ax.errorbar(mean, m, xerr=[[mean-lo], [hi-mean]], fmt="o", color=COLORS[m], capsize=3, ms=7)
            ax.text(5.35, m, f"{mean:.2f}", ha="right", va="center", fontsize=10)
        ax.set(xlim=(1, 5.5), xticks=[1, 2, 3, 4, 5], xlabel="Mean score (1–5)", title=["A  Overall quality", "B  Value recognition", "C  Judgment & grounding"][t])
        style(ax)
    axs[0].set_yticks(y, LABELS); axs[0].invert_yaxis()
    fig.suptitle("Review quality across 20 shared proposals", fontsize=15, y=1.01)
    fig.tight_layout(); save(fig, "01_quality")

    fig, ax = plt.subplots(figsize=(10, 4.5))
    left = np.zeros(5)
    for outcome, color, label in [("win", "#007C83", "SCOPE wins"), ("tie", "#CDD3D9", "Tie"), ("loss", "#D78264", "Comparator wins")]:
        counts = np.array([s["paired_comparisons"][method]["counts"][outcome] for method in METHODS[1:]])
        width = counts / 20 * 100
        ax.barh(np.arange(5), width, left=left, color=color, label=label, height=.62)
        for i, n in enumerate(counts):
            if n:
                label_text = f"{n}\n({width[i]:.0f}%)" if width[i] <= 5 else f"{n} ({width[i]:.0f}%)"
                ax.text(left[i]+width[i]/2, i, label_text, ha="center", va="center", color="white" if outcome == "win" else "#202D39", fontsize=8 if width[i] <= 5 else 10)
        left += width
    ax.set(yticks=np.arange(5), yticklabels=LABELS[1:], xlim=(0, 100), xlabel="Share of 20 proposal-level comparisons (%)", title="Pairwise comparisons: SCOPE versus each comparator")
    ax.invert_yaxis(); style(ax)
    ax.set_title("Pairwise comparisons: SCOPE versus each comparator", pad=44)
    ax.legend(loc="lower center", bbox_to_anchor=(.5, 1.01), ncol=3, frameon=False)
    fig.tight_layout(); save(fig, "03_pairwise")

    fig, ax = plt.subplots(figsize=(10, 10))
    palette = ListedColormap(["#F8E2D7", "#EDBAA2", "#E6E9EC", "#9ACBCD", "#278A92"])
    im = ax.imshow(values[:, :, 0], cmap=palette, norm=BoundaryNorm(np.arange(.5, 6.5), 5), aspect="auto")
    names = []
    for p in projects:
        pid = p["project_id"]
        part = pid.split("_", 3)[-1] if pid.startswith("nci_") else pid.split("_", 2)[-1]
        names.append(part.replace("_", " ").title())
    ax.set(xticks=np.arange(6), xticklabels=SHORT, yticks=np.arange(20), yticklabels=names, title="Overall quality for every proposal and method")
    ax.tick_params(length=0); ax.xaxis.tick_top(); ax.title.set_y(1.07)
    for i in range(20):
        for m in range(6):
            ax.text(m, i, str(int(values[i, m, 0])), ha="center", va="center", color="#17313E", fontsize=11)
    ax.set_xticks(np.arange(-.5, 6, 1), minor=True); ax.set_yticks(np.arange(-.5, 20, 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=2); ax.tick_params(which="minor", length=0)
    for spine in ax.spines.values(): spine.set_visible(False)
    fig.colorbar(im, ax=ax, ticks=range(1, 6), fraction=.035, pad=.04, label="Overall quality (1–5)")
    fig.tight_layout(); save(fig, "04_project_heatmap")

    fig, axs = plt.subplots(1, 2, figsize=(11, 4.9))
    for m, method in enumerate(METHODS):
        data = s["methods"][method]
        for ax, x, q in [(axs[0], data["resources"]["common_elapsed_minutes"]["median"], data["scores"]["overall"]["mean"]),
                          (axs[1], data["resources"]["words"]["median"], data["scores"]["overall"]["mean"])]:
            ax.scatter(x, q, s=90, c=COLORS[m], edgecolor="white", linewidth=.8, label=SHORT[m], zorder=3)
            offset = [(6, 9), (-8, -14), (6, 12), (6, -16), (6, 9), (6, -16)][m]
            align = "right" if m == 1 else "left"
            if ax is axs[1]:
                offset = [(6, -14), (-8, -16), (6, 12), (6, -16), (6, 9), (6, -16)][m]
            ax.annotate(SHORT[m], (x, q), xytext=offset, ha=align, textcoords="offset points", fontsize=9, color=COLORS[m])
    axs[0].set(title="A  Observed runtime (common n = 16)", xlabel="Median final-attempt wall time (minutes)", xlim=(0, 22))
    axs[1].set(title="B  Report length (n = 20)", xlabel="Median words, including references", xlim=(0, 7500))
    for ax in axs:
        ax.set(ylim=(2.4, 3.7), ylabel="Mean overall quality (n = 20)"); style(ax)
    fig.suptitle("Review quality, runtime, and report length", fontsize=14, y=1.01)
    fig.tight_layout(); save(fig, "05_resources")

    fig, ax = plt.subplots(figsize=(10, 4.5))
    for i, method in enumerate(METHODS[1:]):
        stat = s["paired_comparisons"][method]["delta_overall"]
        mean, (lo, hi) = stat["mean"], stat["ci95"]
        ax.errorbar(mean, i, xerr=[[mean-lo], [hi-mean]], fmt="o", color=COLORS[0], capsize=3, ms=7)
        ax.text(1.24, i, f"+{mean:.2f}", ha="right", va="center", fontsize=10)
    ax.axvline(0, color="#7E8C99", ls="--", lw=1)
    ax.set(yticks=np.arange(5), yticklabels=LABELS[1:], xlabel="Paired mean difference in overall quality (SCOPE − comparator)", title="SCOPE gains in overall quality (n = 20)", xlim=(-.1, 1.3))
    ax.invert_yaxis(); style(ax)
    fig.tight_layout(); save(fig, "06_paired_effects")


if __name__ == "__main__":
    main()
