"""Existing assessment rules and capabilities for a continuing chief reviewer."""

from copy import deepcopy

from spacebio_review.review.evaluation import get_evaluation_system


def build_review_context(evaluation_system, catalogue, admission):
    """Snapshot supplied inputs without turning admission status into a new decision."""
    system = get_evaluation_system(evaluation_system)
    capabilities = (
        {
            "expertise": [entry.model_dump() for entry in catalogue.entries.values()],
            "methods": catalogue.skills.catalog(catalogue.methods),
        }
        if catalogue is not None
        else {"expertise": [], "methods": []}
    )
    return {
        "evaluation": system.snapshot(),
        "capabilities": capabilities,
        "admission": deepcopy(admission),
        "admission_interpretation": (
            "准入记录为本轮实际输入。skipped 表示未执行审查；completed 的结论见 decision；"
            "limited 或 failed 表示审查在技术上未完成。科学评价依据完整计划书与研究材料形成。"
        ),
    }
