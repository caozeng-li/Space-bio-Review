"""Offline complete-manuscript mapping and publication; no research or model requests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from probe_v5_assessment import assessment_text, manuscript_draft  # noqa: E402
from probe_v5_delivery import fixture as assessment_fixture  # noqa: E402
from probe_v5_delivery import selection  # noqa: E402
from probe_v5_session import DELIVERY_FIXTURES  # noqa: E402

from spacebio_review.persistence import write_json  # noqa: E402
from spacebio_review.publication import compile_publication  # noqa: E402
from spacebio_review.publication.document import walk_nodes  # noqa: E402
from spacebio_review.review.evaluation import GBT42048  # noqa: E402
from spacebio_review.v5.assessment import bind_citations  # noqa: E402
from spacebio_review.v5.assessment_authoring import (  # noqa: E402
    assessment_mapping_type,
    assessment_values,
)
from spacebio_review.v5.authoring import citation_parts, numbered_text  # noqa: E402


def run(output):
    if output.exists() and any(output.iterdir()):
        raise ValueError("probe_requires_empty_output_directory")
    draft = manuscript_draft(GBT42048)
    content = assessment_text(GBT42048, "submit_assessment", draft).message["content"]
    raw = {
        "content": content,
        "paragraphs": numbered_text(content),
        "aliases": {"E1": "evidence-application"},
    }
    mapping = assessment_mapping_type(GBT42048).model_validate(
        DELIVERY_FIXTURES[tuple(row["text"] for row in raw["paragraphs"])]
    )
    values = bind_citations(assessment_values(raw, mapping), raw["aliases"])
    assert values["summary"] == values["overall"]
    assert mapping.summary == mapping.overall
    assert values["overall"] == bind_citations(draft["overall"], raw["aliases"])
    assessment, evidence, contributions = assessment_fixture(GBT42048)
    assessment.update(values)
    document, audit = compile_publication(
        assessment, evidence=evidence, report_selection=selection(contributions),
        contributions=contributions,
    )
    overall = next(section for section in document.main if section.section_id == "overall")
    citations = [node.target for node in walk_nodes(overall.blocks) if node.kind == "evidence"]
    assert citations == ["evidence-application"]
    aliases = {"E1": "one", "E2": "two", "E3": "three"}
    assert citation_parts('限定"ROS"。[@E1, @E2][@E3]', aliases) == [
        {"text": '限定"ROS"。', "cites": ["E1", "E2", "E3"]}
    ]
    assert citation_parts(r"字面\[@E1]与论述。[@E2]", aliases) == [
        {"text": r"字面\[@E1]与论述。", "cites": ["E2"]}
    ]
    write_json(output / "author-text.json", raw)
    write_json(output / "mapping.json", mapping)
    write_json(output / "publication.json", document)
    write_json(output / "audit.json", audit)
    summary = {
        "status": "passed",
        "same_original_summary": True,
        "original_identity_and_positions": True,
        "grouped_adjacent_escaped_citations": True,
        "real_model_requests": 0,
        "full_review_runs": 0,
        "science": "Fixed manual manuscript; mechanical acceptance only.",
    }
    write_json(output / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().output), ensure_ascii=False, indent=2))
