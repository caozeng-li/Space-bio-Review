"""One-off closure of four saved runs, using existing prose and source identities only."""

import argparse
import asyncio
import copy
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

GROUP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GROUP.parent / "scripts"))
from batch import batch_lock, write_json  # noqa: E402

from spacebio_review.agents.diagnostics import digest  # noqa: E402
from spacebio_review.prose import cited_ids  # noqa: E402
from spacebio_review.review.evaluation import get_evaluation_system  # noqa: E402
from spacebio_review.v5.delivery import deliver_assessment  # noqa: E402
from spacebio_review.v5.records import SessionStore  # noqa: E402
from spacebio_review.v5.report_writing import ReportWriting  # noqa: E402

PROJECTS = (
    "nci_hdrp_r01_braithwaite",
    "niaid_r01_faubion",
    "nhgri_r21_korngiebel",
    "nasa_evs3_farrar_smode_2018",
)
MARKER = re.compile(r"\\?\[(证据\s*\d+(?:\s*[,，]\s*证据\s*\d+)*)\\?\]")


def read(path):
    return json.loads(path.read_text("utf-8"))


def without_citations(text):
    text = MARKER.sub("", text)
    return re.sub(r"\[evidence-[a-f0-9]{16}\]", "", text)


def source_pair(store, author_ref, labels):
    author = store.author_text(author_ref)
    owner = store.load(author["session_id"])
    refs = {label: author["citation_refs"][label] for label in labels}
    return refs, {ref: owner.evidence[ref] for ref in refs.values()}


def report_body(pid, store, state, assessment):
    evidence = dict(state.evidence)
    audit = {"model_calls": 0, "changes": []}
    if pid == "niaid_r01_faubion":
        # The decision is the last adopted scientific judgment. Copy its reasons, not older
        # opinions whose strength may differ, and do not ask a model to extend the arguments.
        outline = ReportWriting(get_evaluation_system("general"), store).outline()
        bodies = [
            assessment["overall_judgment"],
            *[assessment["dimensions"][item["section_id"]]["reason"] for item in outline[1:]],
        ]
        original = "\n\n".join(
            "## " + item["title"] + "\n\n" + body for item, body in zip(outline, bodies)
        )
        text = original.replace("LGthe explicit", "the explicit").replace("would adds", "would add")
        audit["changes"].append(
            {
                "kind": "layout_existing_decision",
                "sources": ["overall_judgment", "dimensions.*.reason"],
                "typographical_changes": [
                    "LGthe explicit -> the explicit",
                    "would adds -> would add",
                ],
            }
        )
        # Resolve only author/year mentions already in that decision to the retained papers.
        bibliography = {
            r"Arvey 2014": "evidence-d6e924e466f2f0fa",
            r"DuPage 2015": "evidence-e3840ed3d7c59fd9",
            r"Bamidele 2018": "evidence-3739f2ca4f11c4d1",
            r"Sahasrabuddhe 2014": "evidence-651dbd4f713351ff",
            r"Yap et al\., Blood 2011": "evidence-ac97fa68822fe5e8",
        }
        before = text
        for pattern, ref in bibliography.items():
            assert ref in evidence
            text, count = re.subn(pattern, lambda m: m[0] + f"[{ref}]", text)
            assert count
            audit["changes"].append(
                {
                    "kind": "bind_named_source",
                    "mention": pattern,
                    "target": ref,
                    "occurrences": count,
                }
            )
        assert without_citations(text) == before
        audit["prose_source"] = "saved assessment; no section-writing call existed"
        return text, evidence, audit

    reports = [
        read(p) | {"author_text_ref": p.stem}
        for p in store.root.joinpath("author-texts").glob("*.json")
        if read(p).get("operation") == "synthesize_report"
        and read(p).get("origin") != "fixed_by_me"
    ]
    assert len(reports) == 1
    raw = reports[0]
    original = raw["content"]
    refs = {k: [v] for k, v in raw["citation_refs"].items()}
    if pid == "nci_hdrp_r01_braithwaite":
        owner = "author-b235e5cae9f2895f7d6e2d3fe17dad459d4dfc54dfcda255bcfa29b6640d9309"
        corrected, extra = source_pair(store, owner, ["证据87", "证据115"])
        evidence.update(extra)
        refs.update({k: [v] for k, v in corrected.items()})
        audit["changes"].append(
            {"kind": "restore_expert_local_citations", "owner": owner, "mapping": corrected}
        )
    elif pid == "nasa_evs3_farrar_smode_2018":
        owner = "author-d1faaa7f8eb6128e51a8503afe19295ef357114834007a237b737d2818f1502a"
        corrected, extra = source_pair(store, owner, ["证据119", "证据122"])
        assert original.count("[证据119][证据122]") == 1
        assert original.count("[证据119]") == 1
        evidence.update(extra)
        refs.update({k: [v] for k, v in corrected.items()})
        audit["changes"].append(
            {"kind": "restore_expert_local_citations", "owner": owner, "mapping": corrected}
        )
    else:
        # Exact source paragraphs already retained in this run: recruitment, demographics,
        # May-August 2019 interviews, and limitations. Do not use the chief's unrelated 76-79.
        corrected = {
            "证据76": ["evidence-518486725cf3cd76", "evidence-ff0fe142f129cc4a"],
            "证据77": ["evidence-3283bb1f60fcab34"],
            "证据78": ["evidence-3283bb1f60fcab34", "evidence-ff0fe142f129cc4a"],
            "证据79": ["evidence-78e38af2c19c802d"],
        }
        for targets in corrected.values():
            for ref in targets:
                assert evidence[ref]["title"].startswith("Patient Recommendations for the Content")
        refs.update(corrected)
        audit["changes"].append(
            {
                "kind": "bind_existing_named_paper_passages",
                "mapping": corrected,
                "passages": {
                    r: evidence[r]["quote"] for targets in corrected.values() for r in targets
                },
            }
        )

    def replace(match):
        labels = [re.sub(r"\s+", "", x) for x in re.split(r"[,，]", match[1])]
        return "".join(f"[{ref}]" for label in labels for ref in refs[label])

    text = MARKER.sub(replace, original)
    assert without_citations(text) == without_citations(original), "Scientific prose changed"
    assert "证据" not in text
    audit.update(
        prose_source=raw["author_text_ref"],
        original_prose_sha256=hashlib.sha256(original.encode()).hexdigest(),
        prose_unchanged_except_citation_markers=True,
    )
    return text, evidence, audit


async def fix(pid, *, apply):
    native = GROUP / "runs" / pid / "attempt-001/native"
    review = read(native / "review.json")
    assert review["status"] in ("partial", "fixed_by_me")
    if review["status"] == "fixed_by_me":
        print(pid, "already fixed")
        return
    store = SessionStore(native)
    state = store.load(review["chief"]["chief_id"])
    original_artifact = read(store._path("assessments", state.assessment_ref))
    assessment = copy.deepcopy(original_artifact["assessment"])
    text, evidence, audit = report_body(pid, store, state, assessment)

    # Use the native parser and publisher; no special layout or repair note in the report.
    class TextStore:
        def author_text(self, ref):
            return {"content": text, "citation_refs": {}}

    writer = ReportWriting(get_evaluation_system("general"), TextStore())
    sections = writer.sections({"report": "manual-close"})
    used = set(cited_ids(sections))
    assert used <= evidence.keys()
    audit.update(
        status="fixed_by_me",
        fixed_at=datetime.now(timezone.utc).isoformat(),
        original_status=review["status"],
        original_technical_limits=review["technical_limits"],
        recommendation_unchanged=True,
        ratings_unchanged=True,
        word_count=len(text.split()),
        referenced_sources=len({evidence[r]["reference"]["source_id"] for r in used}),
    )
    print(pid, "ready", audit["word_count"], "words", audit["referenced_sources"], "sources")
    if not apply:
        return
    backup = native / "manual_fix"
    backup.mkdir(exist_ok=True)
    for path in [native / "review.json", store._path("sessions", state.session_id)]:
        target = backup / ("original-" + path.name)
        if not target.exists():
            shutil.copyfile(path, target)
    write_json(backup / "record.json", audit)
    (backup / "report-body.md").write_text(text, "utf-8")
    # Add normal content-addressed author and publication artifacts without replacing old drafts.
    raw = {
        "session_id": state.session_id,
        "operation": "synthesize_report",
        "content": text,
        "citation_refs": {},
        "readings": sorted(used),
        "finish_reason": None,
        "origin": "fixed_by_me",
    }
    raw_ref = "author-" + digest(raw)
    write_json(store._path("author-texts", raw_ref), raw)
    sections = ReportWriting(get_evaluation_system("general"), store).sections({"report": raw_ref})
    assessment["sections"] = sections
    artifact = {
        **original_artifact,
        "assessment": assessment,
        "assessment_ref": state.assessment_ref,
        "evidence": {r: evidence[r] for r in used},
        "author_sources": {
            **original_artifact["author_sources"],
            "report": {"author_text_ref": raw_ref},
        },
        "repair_status": "fixed_by_me",
    }
    ref = "report-" + digest(artifact)
    write_json(store._path("assessments", ref), artifact)
    delivery = await deliver_assessment(
        None,
        assessment,
        evidence=artifact["evidence"],
        author_sources=artifact["author_sources"],
        admission=review["admission"],
        root=native,
    )
    report = (native / "report.en.md").read_text("utf-8")
    assert all(x not in report for x in ("fixed_by_me", "manual-close", "证据"))
    assert not re.search(r"evidence-[a-f0-9]{16}", report)
    assert assessment["dimensions"] == review["assessment"]["dimensions"]
    assert assessment["recommendation"] == review["recommendation"]
    state.report_ref, state.status, state.reason = ref, "submitted", None
    state.authoring_phase = None
    store.save(state)
    review.update(
        status="fixed_by_me",
        stage="completed",
        scientific_status="completed",
        reporting_status="completed",
        research_status="submitted",
        assessment=assessment,
        publication=delivery["publication"],
        delivery=delivery,
        report_en_path="report.en.md",
        technical_limits=[],
        manual_fix={"status": "fixed_by_me", "record": "manual_fix/record.json"},
    )
    review["chief"].update(
        status="submitted",
        reason=None,
        scientific_status="completed",
        report_ref=ref,
        assessment=assessment,
        evidence=artifact["evidence"],
        author_sources=artifact["author_sources"],
        repair_status="fixed_by_me",
    )
    write_json(native / "review.json", review)
    destination = GROUP / f"{pid}_report.md"
    shutil.copyfile(native / "report.en.md", destination)
    status_path = GROUP / "status.json"
    status = read(status_path)
    history = status["projects"][pid]
    history.append(
        {
            "status": "fixed_by_me",
            "attempt": history[-1]["attempt"],
            "fixed_at": audit["fixed_at"],
            "input_sha256": history[-1]["input_sha256"],
            "report": destination.name,
            "model_calls": 0,
            "repair_record": str((backup / "record.json").relative_to(GROUP)),
            "original_attempt_preserved": True,
        }
    )
    write_json(status_path, status)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with batch_lock(GROUP):
        snapshot = GROUP / "status.before_manual_fix.json"
        if args.apply and not snapshot.exists():
            shutil.copyfile(GROUP / "status.json", snapshot)
        for pid in PROJECTS:
            await fix(pid, apply=args.apply)


if __name__ == "__main__":
    asyncio.run(main())
