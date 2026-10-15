from types import SimpleNamespace

from spacebio_review.review.evaluation import GBT42048
from spacebio_review.v4.assessment_input import freeze_review, review_materials
from spacebio_review.v4.coordinator import Coordinator
from spacebio_review.v4.records import Records


def test_unresearched_dimensions_remain_applicable_and_freeze_retains_full_application(tmp_path):
    records = Records(tmp_path)
    coordinator = Coordinator(SimpleNamespace(records=records, system=GBT42048))
    coordinator._state["question_batch"] = records.put(
        "questions",
        {
            "questions": [
                {
                    "question": "已有地面实验实际支持哪些研究基础？",
                    "expert_id": "expert-1",
                    "aspects": ["research_foundation"],
                }
            ],
        },
    )
    coordinator._state["questions"]["q1"] = {}
    ref = records.put(
        "research",
        {
            "title": "前期研究基础",
            "scope_note": "仅根据计划书判断",
            "blocks": [{"block_id": "b1", "text": "已完成少量地面培养，未开展空间实验。"}],
            "scope_outcomes": [{"member_id": "q1", "status": "analyzed", "block_ids": ["b1"]}],
        },
    )
    coordinator._state["heads"]["q1"] = [ref]
    coverage = coordinator.coverage()
    assert set(coverage) == set(GBT42048.aspects)
    assert all(row["applicability"] == "applicable" for row in coverage.values())
    assert coverage["safety"]["status"] == "unassessed"
    application = [
        {
            "source_id": "plan",
            "candidate": {"title": "Plan"},
            "passages": [{"passage_id": "p1", "locator": "全文", "text": "完整计划书材料"}],
        }
    ]
    frozen = freeze_review(
        records,
        [ref],
        application,
        {"system": GBT42048.snapshot()},
        coverage=coverage,
        admission={"kind": "proposal_content", "status": "skipped", "rule_version": "content-1"},
    )
    assert frozen["materials"]["research_coverage"]["research_foundation"]["opinion_ids"] == ["O1"]
    assert frozen["materials"]["research_coverage"]["safety"]["opinion_ids"] == []
    assert frozen["materials"]["evidence"]["E1"]["quote"] == "完整计划书材料"
    request = review_materials(frozen)
    assert request["admission"]["status"] == "skipped"
    assert request["admission"]["conclusion"] is None
    assert ref not in str(request["research_coverage"])
    assert frozen["evaluation"]["system"]["id"] == "gbt42048_2022"
