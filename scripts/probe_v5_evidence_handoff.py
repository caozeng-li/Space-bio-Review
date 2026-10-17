"""Offline Change 17 handoff checks and fixed historical evidence for local authors.

Only reads retained mytest32 snapshots. Historical author notes are input material,
not scientific ground truth. No network, model requests, or review pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.prose import cited_ids  # noqa: E402
from spacebio_review.v5.topic import TopicResult  # noqa: E402

HISTORICAL = ROOT / "runs/mytest32_sample1"
TOPICS = (
    "session-f51bfc3ae27542938e5bfc6ba2191318",
    "session-a0ba2737babe4e73a6457b4f56ae55ed",
    "session-518540f482b048b88fc462aa5d7710b6",
)
CELL = "src-df445b84b9a7"
MAROTTA = "src-5a3765870d93"
MAROTTA_PASSAGES = {
    "m-758eb459ed38-p6",  # static culture and parallel ground control
    "m-758eb459ed38-p7",  # Figure 1 design and post-return analysis
    "m-758eb459ed38-p24",  # culture result, including in-orbit visualization
    "m-758eb459ed38-p26",  # Figure 2: actual in-orbit images and timing
}


def read_json(path):
    return json.loads(path.read_text("utf-8"))


def historical_results(root=HISTORICAL):
    return [
        TopicResult.model_validate(read_json(root / "v5/deliveries" / f"{sid}.json"))
        for sid in TOPICS
    ]


def historical_evidence(root=HISTORICAL):
    evidence = {}
    for sid in TOPICS:
        evidence.update(read_json(root / "v5/sessions" / f"{sid}.json")["evidence"])
    return evidence


def comparison_evidence(root=HISTORICAL):
    selected = {}
    for key, value in historical_evidence(root).items():
        reference = value["reference"]
        if (
            value["kind"] == "application"
            or (reference["source_id"] == CELL and "abstract" in value["reading_scope"])
            or (reference["source_id"] == MAROTTA and reference["passage_id"] in MAROTTA_PASSAGES)
        ):
            selected[key] = value
    present = {row["reference"]["passage_id"] for row in selected.values()}
    assert MAROTTA_PASSAGES <= present
    assert any(row["reference"]["source_id"] == CELL for row in selected.values())
    assert any(row["kind"] == "application" for row in selected.values())
    return selected


def rebind(value, aliases):
    """Rebind only explicit citation identities to the current probe's E aliases."""
    if isinstance(value, dict):
        if set(value) == {"text", "cites"}:
            return {"text": value["text"], "cites": [aliases[key] for key in value["cites"]]}
        return {key: rebind(item, aliases) for key, item in value.items()}
    if isinstance(value, list):
        return [rebind(item, aliases) for item in value]
    return value


def author_input(case, root=HISTORICAL):
    """One copy of direct originals, plus complete notes and short source directories."""
    evidence = comparison_evidence(root)
    topics = []
    if case == "chief-continue":
        for result in historical_results(root):
            evidence.update(result.readings)
            topics.append(result.model_view())
    reverse = {key: f"E{index + 1}" for index, key in enumerate(evidence)}
    aliases = {alias: key for key, alias in reverse.items()}
    payload = {
        "application": (ROOT / "data/sample2.md").read_text("utf-8"),
        "completed_topics": rebind(topics, reverse),
        "originals": {reverse[key]: value for key, value in evidence.items()},
        "source_scope": {
            "Cell": "Historical attempt obtained the abstract only; full text unavailable.",
            "Marotta": "Retained original text and Figure 2 caption; no new web retrieval.",
        },
        "provenance": {
            "application": "data/sample2.md, exact current text",
            "historical_run": str(root.relative_to(ROOT)),
            "selection": "Application passages, Cell abstract, and specified Marotta passages.",
            "historical_notes": "Uncorrected prior author output, not an answer key.",
            "stage": "Fixed material local author and handoff only; no child research execution.",
        },
    }
    return payload, aliases, evidence


def check_handoff(output, root=HISTORICAL):
    rows = []
    for result in historical_results(root):
        before = result.model_dump(mode="json")
        view = result.model_view()
        assert result.model_dump(mode="json") == before, "projection_mutated_archive"
        assert view["note"] == result.note
        assert "reading_records" not in view and "readings" not in view
        assert {row["source_id"] for row in view["materials"]} == {
            row["source_id"] for row in result.materials
        }
        for material in view["materials"]:
            assert "source_id" in material and "read_with" in material
            assert "sections" not in material and "materials" not in material
        assert set(cited_ids(view["note"])) <= set(result.readings)
        # A failure before a note must still make acquired and discovered sources reusable.
        interrupted = result.model_copy(
            update={"note": None, "note_ref": None, "status": "partial"}
        )
        assert interrupted.model_view()["materials"] == view["materials"]
        roundtrip = TopicResult.model_validate(deepcopy(before))
        assert roundtrip.reading_records == result.reading_records
        write_json(output / "views" / f"{result.topic_id}.json", view)
        rows.append(
            {
                "topic_id": result.topic_id,
                "persistent_chars": len(json.dumps(before, ensure_ascii=False)),
                "model_view_chars": len(json.dumps(view, ensure_ascii=False)),
                "cited_originals": len(result.readings),
                "source_entries": len(view["materials"]),
                "note_and_archive_unchanged": True,
                "uncited_and_interrupted_sources_retained": True,
            }
        )
    payload, aliases, evidence = author_input("chief-continue", root)
    assert len(payload["originals"]) == len(evidence) == len(aliases)
    assert set(cited_ids(payload["completed_topics"])) <= set(aliases)
    write_json(output / "chief-input.json", payload)
    summary = {
        "status": "passed",
        "topics": rows,
        "combined_originals": len(evidence),
        "actual_requests": 0,
        "retrieval_requests": 0,
        "full_review_runs": 0,
        "scope": "Identity, unchanged content, compact view, retained source reuse only.",
    }
    write_json(output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("probe_requires_empty_output_directory")
    print(json.dumps(check_handoff(args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
