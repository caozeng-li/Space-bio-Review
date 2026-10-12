from copy import deepcopy
from types import SimpleNamespace

import pytest

from spacebio_review.agents.types import TaskResult
from spacebio_review.review.project import REVIEW_ASPECTS
from spacebio_review.v4.delivery import Delivery


def evidence(key, source=None):
    return {
        "reference": {"source_id": source or key, "passage_id": key},
        "material_version": key,
        "title": key,
        "kind": "external",
        "quote": "Source " + key,
        "locator": key,
        "reading_scope": "text",
    }


class MemoryRecords:
    def __init__(self):
        self.rows = {}

    def value(self, key):
        return deepcopy(self.rows[key])

    def block(self, key):
        ref, block_id = key.split("::")
        return next(b for b in self.value(ref)["blocks"] if b["block_id"] == block_id)

    def put(self, kind, value, dependencies=()):
        key = f"{kind}-{len(self.rows)}"
        self.rows[key] = deepcopy(value)
        return key


def fixture(tmp_path, *, extra=True, explicit=True):
    records = MemoryRecords()
    ev = {key: evidence(key) for key in ("exact", "neighbor", "background")}
    records.rows["prior"] = {
        "title": "Prior argument",
        "scope_note": "Common scope[@background]" if extra else "Common scope",
        "scope_links": ["background"] if extra else [],
        "evidence": ev,
        "blocks": [
            {
                "block_id": "b1",
                "text": "First fact[@exact]. Neighbor[@neighbor]." if explicit else "Two facts.",
                "evidence_refs": ["exact", "neighbor"] if extra else ["exact"],
                "result_refs": [],
            }
        ],
    }
    records.rows["assessment"] = {
        "title": "Assessment",
        "scope_note": "",
        "aspect_refs": {},
        "professional_refs": ["prior"],
        "coverage": {a: {"status": "unassessed"} for a in REVIEW_ASPECTS},
        "recommendation": "revise",
        "evidence": {"exact": ev["exact"]},
        "blocks": [
            {
                "block_id": "b1",
                "text": "Current fact[@exact][@prior::b1]",
                "evidence_refs": ["exact"],
                "result_refs": ["prior::b1"],
            }
        ],
    }
    delivery = Delivery(SimpleNamespace(records=records), tmp_path)
    return delivery, records


def test_argument_dependencies_never_become_local_evidence(tmp_path):
    delivery, records = fixture(tmp_path)
    manifest = records.value(delivery.assemble("assessment"))
    overall = next(s for s in manifest["main_sections"] if s["section_id"] == "overall")
    assert [manifest["sources"][s]["title"] for s in overall["sources"]] == ["exact"]
    assert "arguments.md#" in overall["text"]
    assert manifest["argument_sections"]
    target = overall["text"].split("arguments.md#")[1].split(")")[0]
    assert any(s["section_id"] == target for s in manifest["argument_sections"])


def test_legacy_unpositioned_sources_are_not_appended_to_a_sentence(tmp_path):
    delivery, records = fixture(tmp_path, explicit=False)
    manifest = records.value(delivery.assemble("assessment"))
    text = manifest["appendix_sections"][0]["text"]
    assert "Two facts.[" not in text
    assert "该论述所列来源" in text


def test_work_reuse_is_separate_from_requested_action(tmp_path):
    delivery, records = fixture(tmp_path)
    records.rows["snapshot"] = {
        "assessment": "assessment",
        "coverage": {},
        "requests": [
            {
                "status": "partial",
                "request": {
                    "question": "Provide calibration parameters.",
                    "decision_impact": "Comparison",
                },
                "reason": "Partially analyzed",
                "outcome": {"result_refs": ["prior::b1"]},
            }
        ],
    }
    manifest = records.value(delivery.assemble("assessment", delivery_input="snapshot"))
    gaps = next(s for s in manifest["main_sections"] if s["section_id"] == "gaps")
    assert "复用分析：" in gaps["text"]
    assert gaps["sources"] == []


def test_document_numbering_keeps_exact_evidence_identity(tmp_path):
    delivery, records = fixture(tmp_path)
    records.rows["prior"]["evidence"]["neighbor"]["reference"]["source_id"] = "exact"
    manifest = records.value(delivery.assemble("assessment"))
    identities = [
        k for k, v in manifest["sources"].items() if v["reference"]["source_id"] == "exact"
    ]
    assert len(identities) == 2
    assert len({manifest["numbering"][k] for k in identities}) == 1
    row = next(s for s in manifest["main_sections"] if s["section_id"] == "overall")
    evidence_refs = [r for u in row["units"] for r in u["references"] if r["kind"] == "evidence"]
    assert len(evidence_refs) == 1
    assert evidence_refs[0]["evidence_id"] == "exact"


@pytest.mark.asyncio
async def test_translation_only_authors_text_and_export_uses_bound_units(tmp_path):
    delivery, records = fixture(tmp_path)
    records.rows["assessment"]["blocks"][0]["text"] += "\nScope preserved."
    manifest = records.value(delivery.assemble("assessment"))
    row = next(s for s in manifest["main_sections"] if s["section_id"] == "overall")
    calls = []

    async def call(key, kind, objective, contract, **kwargs):
        texts = kwargs["materials"][0].data["units"]
        assert "references" not in texts
        calls.append(key)
        parsed = contract.parse(
            {"title": "Translation", "units": {k: "English " + v.strip() for k, v in texts.items()}}
        )
        return TaskResult(
            task_id=key,
            agent_id="writer",
            status="completed",
            reason="fixture",
            product=contract.product(parsed).model_dump(),
        )

    delivery.ex.call = call
    delivery.ex.reporting = SimpleNamespace(material_batch_chars=10000)
    translated = await delivery.translate(row)
    assert len(calls) == 1
    assert [u["references"] for u in translated.product["units"]] == [
        u["references"] for u in row["units"]
    ]
    state = {
        "zh": {"sections": [row]},
        "en": {"sections": [{**row, **translated.product}]},
        "appendix": {"sections": []},
        "technical_limits": [],
    }
    state["zh"]["sections"][0]["text"] = "STALE_DERIVED_TEXT"
    delivery.export(state, manifest)
    assert "STALE_DERIVED_TEXT" not in (tmp_path / "report.md").read_text("utf-8")
    assert "English " in (tmp_path / "report.en.md").read_text("utf-8")
    assert "\nEnglish Scope preserved." in translated.product["text"]
    assert (tmp_path / "arguments.md").exists()


def test_lineage_in_argument_sidecar_is_not_a_premise_or_direct_evidence():
    from spacebio_review.v4.report_content import ReportReferences

    statements = {
        "statement-old": {"kind": "external", "parts": [{"text": "Old", "cites": ["exact"]}]},
        "statement-new": {
            "kind": "interpretation",
            "parts": [{"text": "New", "cites": []}],
            "based_on": ["statement-old"],
            "supersedes": ["statement-old"],
        },
    }
    refs = ReportReferences(MemoryRecords(), {"exact": evidence("exact")}, statements)
    sections = [{"text": "Inference[@statement-new]"}]
    arguments = refs.collect(sections)
    new = next(a for a in arguments if a["section_id"] == refs.arguments["statement-new"])
    assert sections[0]["sources"] == []
    assert new["sources"] == []
    assert {r["kind"] for u in new["units"] for r in u["references"]} == {"based_on", "supersedes"}
