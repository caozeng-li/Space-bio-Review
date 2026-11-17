"""Research perspectives and the full rules retained for the chief's final decision."""

from copy import deepcopy

from spacebio_review.review.evaluation import get_evaluation_system
from spacebio_review.review.instructions import GENERAL_REVIEW

DECISION_GUIDANCE = "references/decision-guidance.md"


def build_research_context(review_context):
    """Give research the assessment's questions, leaving scoring in the saved snapshot."""
    context = deepcopy(review_context)
    system = context["evaluation"]
    context["evaluation"] = {key: system[key] for key in ("id", "label", "scope")}
    context["evaluation"]["criteria"] = [
        {key: criterion[key] for key in ("id", "label", "description")}
        for criterion in system["criteria"]
    ]
    return context


def review_skill_context(skill, *, decision=False):
    """Load stage-specific skill material without exposing final delivery tasks to research."""
    return {
        "body": skill.body,
        "references": {
            name: body
            for name, body in skill.references.items()
            if decision or name != DECISION_GUIDANCE
        },
    }


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
        **({"scientific_review_guidance": GENERAL_REVIEW} if system.id == "general" else {}),
        "report_language": system.language,
        "capabilities": capabilities,
        "admission": deepcopy(admission),
        "admission_interpretation": (
            "The admission record is an input to this review. skipped means no admission review "
            "was performed; for completed, consult decision. limited or failed means a technical "
            "interruption. Base scientific evaluation on the complete proposal "
            "and research evidence."
        ),
    }
