from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from spacebio_review.admission import (
    FORMAL_CONTENT,
    FORMAL_REVIEW_SKILL,
    AdmissionConfig,
    AdmissionRun,
    FormalReviewDecision,
    ProposalAdmission,
    admission_contract,
    admission_report,
)
from spacebio_review.agents.skills import SkillLibrary
from spacebio_review.agents.types import TaskResult
from spacebio_review.application import ApplicationBundle, ApplicationSource
from spacebio_review.evidence.types import Passage, SourceCandidate, SourceDocument


def application(*, text="计划书原文", reading_gaps=(), load_error=None):
    doc = SourceDocument(
        source_id="plan",
        candidate=SourceCandidate(title="实验计划书", kind="application"),
        availability="full_text",
        passages=[Passage(passage_id="p1", locator="正文", text=text)],
    )
    bundle = ApplicationBundle(
        sources=[
            ApplicationSource(
                source_id="plan",
                title="实验计划书",
                role="main",
                availability="full_text",
                sections=["正文"],
                reading_gaps=list(reading_gaps),
                load_error=load_error,
                empty_text=not text,
            )
        ]
    )
    return bundle, doc, SimpleNamespace(retained=lambda _: doc)


def formal_draft(*, missing=(), anchor="plan/passage/p1"):
    return {
        "checks": {
            key: {
                "covered": key not in missing,
                "rationale": "未找到该项内容。" if key in missing else "原文提供了相关说明。",
                "anchors": [] if key in missing else [anchor],
            }
            for key in FORMAL_CONTENT
        },
        "reading_limits": [],
    }


@pytest.mark.parametrize("missing,expected", [((), "A"), (("budget",), "C")])
def test_formal_contract_assembles_conclusion_and_source_quotes(missing, expected):
    _, doc, _ = application()
    contract = admission_contract({"plan": doc}, {"plan": {"p1"}}, {}, formal=True)
    product = contract.product(contract.parse(formal_draft(missing=missing)))
    assert product.conclusion == expected
    assert product.checks.significance.anchors[0].quote == "计划书原文"
    assert "conclusion" not in contract.schema()["properties"]
    assert "next_steps" not in product.model_dump()
    restored = AdmissionRun.model_validate(
        AdmissionRun(kind="proposal_content", status="completed", decision=product).model_dump()
    )
    assert isinstance(restored.decision, FormalReviewDecision)
    assert restored.can_proceed is (expected == "A")
    report = admission_report(restored)
    assert "整改" not in report
    if missing:
        assert "经费预算：缺失" in report


def test_formal_contract_only_allows_absence_after_complete_reading():
    _, doc, _ = application()
    doc.passages.append(Passage(passage_id="p2", locator="附录", text="经费安排"))
    read = {"plan": {"p1"}}
    contract = admission_contract(
        {"plan": doc}, read, {}, formal=True, complete=lambda: len(read["plan"]) == 2
    )
    with pytest.raises(ValidationError):
        contract.parse(formal_draft(missing=("budget",)))
    partial = contract.product(
        contract.parse({"checks": None, "reading_limits": ["尚有未读附录。"]})
    )
    assert partial.conclusion is None
    read["plan"].add("p2")
    contract.refresh(contract)
    product = contract.product(contract.parse(formal_draft(missing=("budget",))))
    assert product.conclusion == "C"


async def test_disabled_formal_review_skips_model_and_material_access():
    def unexpected(*args, **kwargs):
        pytest.fail("Disabled formal review must not read materials or call a model")

    run = await ProposalAdmission(
        SimpleNamespace(run_task=unexpected),
        SimpleNamespace(retained=unexpected),
        AdmissionConfig(formal_review_enabled=False),
        evaluation_system="gbt42048_2022",
    ).check(ApplicationBundle(sources=[]))
    assert run.status == "skipped"
    assert run.decision is None
    assert run.can_proceed
    assert AdmissionRun.model_validate_json(run.model_dump_json()).can_proceed
    assert "已跳过" in admission_report(run)
    assert "通过" not in admission_report(run)


@pytest.mark.parametrize("missing,conclusion", [((), "A"), (("resources",), "C")])
async def test_formal_executor_uses_scoped_contract_without_legacy_validation(missing, conclusion):
    text = Path("tests/fixtures/gbt-plan-complete.md").read_text("utf-8")
    bundle, _, store = application(text=text)
    observed = []

    async def run_task(task, profile, view, registry, contract, *, validate_references):
        observed.append(task)
        assert profile.required_skills == [FORMAL_REVIEW_SKILL]
        assert task.max_repairs == 0
        assert validate_references is None
        assert view.materials[0].data["preview_complete"]
        assert view.materials[0].data["preview"][0]["text"] == text
        product = contract.product(contract.parse(formal_draft(missing=missing)))
        return TaskResult(
            task_id=task.task_id,
            agent_id=profile.agent_id,
            status="completed",
            reason="Submitted",
            product=product.model_dump(),
        )

    run = await ProposalAdmission(
        SimpleNamespace(run_task=run_task), store, evaluation_system="gbt42048_2022"
    ).check(bundle)
    assert len(observed) == 1
    assert run.status == "completed"
    assert run.decision.conclusion == conclusion
    assert run.can_proceed is (conclusion == "A")


async def test_shared_material_reading_refreshes_formal_contract():
    bundle, doc, store = application(text="正文" * 3000)
    doc.passages.append(Passage(passage_id="p2", locator="后文", text="经费说明" * 2000))

    async def run_task(task, profile, view, registry, contract, **kwargs):
        assert not view.materials[0].data["preview_complete"]
        with pytest.raises(ValidationError):
            contract.parse(formal_draft(missing=("budget",)))
        material = await registry.material_providers[0]("plan")
        registry.record_read(
            {"source_id": "plan", "passages": [p.model_dump() for p in material["passages"]]}
        )
        contract.refresh(contract)
        product = contract.product(contract.parse(formal_draft(missing=("budget",))))
        return TaskResult(
            task_id=task.task_id,
            agent_id=profile.agent_id,
            status="completed",
            reason="Submitted",
            product=product.model_dump(),
        )

    run = await ProposalAdmission(
        SimpleNamespace(run_task=run_task), store, evaluation_system="gbt42048_2022"
    ).check(bundle)
    assert run.decision.conclusion == "C"
    assert run.coverage["plan"]["read_passage_ids"] == ["p1", "p2"]


async def test_technical_reading_limit_has_no_business_conclusion():
    bundle, _, store = application(reading_gaps=["unreadable_page"])

    async def run_task(task, profile, view, registry, contract, **kwargs):
        product = contract.product(
            contract.parse({"checks": None, "reading_limits": ["图片内容无法读取。"]})
        )
        return TaskResult(
            task_id=task.task_id,
            agent_id=profile.agent_id,
            status="completed",
            reason="Submitted",
            product=product.model_dump(),
        )

    run = await ProposalAdmission(
        SimpleNamespace(run_task=run_task), store, evaluation_system="gbt42048_2022"
    ).check(bundle)
    assert run.status == "limited"
    assert run.decision.conclusion is None
    assert not run.can_proceed
    report = admission_report(run)
    assert "技术性未完成" in report
    assert "图片内容无法读取" in report
    assert "C（" not in report


async def test_input_failure_is_not_content_absence():
    bundle, _, store = application(load_error="ParserError")
    run = await ProposalAdmission(None, store, evaluation_system="gbt42048_2022").check(bundle)
    assert run.status == "failed"
    assert run.decision.conclusion is None
    assert run.decision.checks is None


async def test_confirmed_empty_plan_is_content_absence():
    bundle, _, store = application(text="")
    run = await ProposalAdmission(None, store, evaluation_system="gbt42048_2022").check(bundle)
    assert run.status == "completed"
    assert run.decision.conclusion == "C"
    assert not run.can_proceed


async def test_general_admission_remains_minimum_reviewability():
    bundle, _, store = application()

    async def run_task(task, profile, view, registry, contract, *, validate_references):
        assert profile.required_skills == ["proposal-admission"]
        product = contract.product(
            contract.parse(
                {
                    "decision": "admit",
                    "reason_code": "reviewable",
                    "rationale": "目标及基本方案可辨认。",
                    "objective": "观察植物根系",
                    "approach": "连续成像",
                    "anchors": ["plan/passage/p1"],
                }
            )
        )
        assert await validate_references(product) == []
        return TaskResult(
            task_id=task.task_id,
            agent_id=profile.agent_id,
            status="completed",
            reason="Submitted",
            product=product.model_dump(),
        )

    run = await ProposalAdmission(
        SimpleNamespace(run_task=run_task), store, AdmissionConfig(formal_review_enabled=False)
    ).check(bundle)
    assert run.kind == "minimum_reviewability"
    assert run.decision.decision == "admit"
    assert run.can_proceed


def test_formal_review_skill_loads_in_project_library():
    skill = SkillLibrary(Path("skills")).get(FORMAL_REVIEW_SKILL)
    assert skill.body and skill.description
