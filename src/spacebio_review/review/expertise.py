"""File-based specialist metadata; no fixed panel, person database, or stance."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Expertise(BaseModel):
    id: str
    group: str
    expertise: str
    applicable_objects: list[str] = Field(min_length=1)
    cognitive_contribution: str
    skill: str
    method_skills: list[str] = Field(default_factory=list)


class ExpertiseCatalogue:
    def __init__(self, path, skills):
        self.skills = skills
        raw = yaml.safe_load(Path(path).read_text("utf-8"))
        self.methods = list(raw["methods"])
        self.entries = {}
        for row in raw["experts"]:
            entry = Expertise.model_validate(row)
            if entry.id in self.entries:
                raise ValueError("duplicate_expertise:" + entry.id)
            self.entries[entry.id] = entry
            for name in [entry.skill, *entry.method_skills]:
                skills.get(name)
            if set(entry.method_skills) - set(self.methods):
                raise ValueError("unknown_default_method:" + entry.id)
        for name in self.methods:
            skills.get(name)

    def brief(self):
        return {
            "expertise": [e.model_dump(exclude={"skill"}) for e in self.entries.values()],
            "methods": self.skills.catalog(self.methods),
        }
