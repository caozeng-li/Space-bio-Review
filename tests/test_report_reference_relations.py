from copy import deepcopy
from types import SimpleNamespace

from spacebio_review.review.project import REVIEW_ASPECTS
from spacebio_review.v4.delivery import Delivery
from spacebio_review.v4.publication_delivery import load_document
from spacebio_review.v4.publication_document import plain_text, walk_nodes


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
    return Delivery(SimpleNamespace(records=records), tmp_path), records


def test_argument_dependencies_never_become_local_evidence(tmp_path):
    delivery, records = fixture(tmp_path)
    manifest = records.value(delivery.assemble("assessment"))
    doc = load_document(records, manifest)
    overall = next(s for s in doc.main if s.section_id == "overall")
    local = [n for n in walk_nodes(overall.blocks) if n.kind == "evidence"]
    assert len(local) == 1
    assert manifest["audit"]["evidence"][local[0].target]["evidence_id"] == "exact"
    related = next(n for n in walk_nodes(overall.blocks) if n.kind == "crossref")
    assert doc.targets[related.target].document == "appendix"


def test_legacy_unpositioned_sources_are_not_appended_to_a_sentence(tmp_path):
    delivery, records = fixture(tmp_path, explicit=False)
    doc = load_document(records, records.value(delivery.assemble("assessment")))
    block = doc.appendix[0].sections[0].blocks[-1]
    assert plain_text([block.children[0]]).strip() == "Two facts."
    assert not any(n.kind == "evidence" for n in walk_nodes([block.children[0]]))
    assert any(n.kind == "evidence" for n in walk_nodes([block.children[1]]))


def test_work_reuse_is_separate_from_requested_action(tmp_path):
    delivery, records = fixture(tmp_path)
    records.rows["snapshot"] = {
        "assessment": "assessment",
        "coverage": {},
        "requests": [
            {
                "status": "partial",
                "reason": "Internal explanation",
                "request": {
                    "question": "Provide calibration parameters.",
                    "decision_impact": "Comparison",
                },
                "outcome": {"result_refs": ["prior::b1"]},
            }
        ],
    }
    doc = load_document(
        records, records.value(delivery.assemble("assessment", delivery_input="snapshot"))
    )
    gaps = next(s for s in doc.main if s.section_id == "gaps")
    assert "Internal explanation" not in plain_text(gaps.blocks)
    assert "相关分析" in plain_text(gaps.blocks)
    assert not any(n.kind == "evidence" for n in walk_nodes(gaps.blocks))


def test_document_numbering_keeps_exact_evidence_identity(tmp_path):
    delivery, records = fixture(tmp_path)
    records.rows["prior"]["evidence"]["neighbor"]["reference"]["source_id"] = "exact"
    manifest = records.value(delivery.assemble("assessment"))
    doc = load_document(records, manifest)
    source = next(k for k, v in manifest["audit"]["sources"].items() if v == "exact")
    assert len([loc for loc in doc.locations.values() if loc.source == source]) == 2
    assert doc.numbering[source] == 1


def test_lineage_is_not_a_premise_or_direct_evidence(tmp_path):
    delivery, records = fixture(tmp_path)
    statements = {
        "statement-old": {"kind": "external", "parts": [{"text": "Old", "cites": ["exact"]}]},
        "statement-premise": {"kind": "external", "parts": [{"text": "Premise", "cites": []}]},
        "statement-new": {
            "kind": "interpretation",
            "parts": [{"text": "New", "cites": []}],
            "premise_ids": ["statement-premise"],
            "based_on": ["statement-old"],
            "supersedes": ["statement-old"],
        },
    }
    records.rows["assessment"]["blocks"][0]["text"] = "Inference[@statement-new]"
    records.rows["assessment"]["statement_content"] = {"/registry": {"statements": statements}}
    manifest = records.value(delivery.assemble("assessment"))
    doc = load_document(records, manifest)
    supplements = doc.appendix[-1].sections
    assert len(supplements) == 2
    new, premise = supplements
    assert not any(n.kind == "evidence" for n in walk_nodes(new.blocks))
    reference = next(n for n in walk_nodes(new.blocks) if n.kind == "crossref")
    assert doc.targets[reference.target].section == premise.section_id
    assert plain_text(premise.blocks).strip() == "Premise"
    assert "statement-old" not in str(doc.model_dump())
    assert manifest["audit"]["lineage"]["statement-new"] == {
        "based_on": ["statement-old"],
        "supersedes": ["statement-old"],
    }
