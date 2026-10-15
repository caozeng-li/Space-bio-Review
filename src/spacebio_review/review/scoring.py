"""Derive the national outcome from the existing criteria; general remains qualitative."""


def finalize_assessment(value, system):
    result = {"schema_version": "v5-assessment-1", "evaluation_system": system.snapshot(), **value}
    if system.id == "gbt42048_2022":
        total = sum(value["dimensions"][key]["score"] for key in system.aspects)
        grade = system.grade_for(total)
        result.update(
            total_score=total,
            score_grade=grade.id,
            recommendation="not_support"
            if value["safety_veto"]["applies"]
            else grade.recommendation,
        )
    return result
