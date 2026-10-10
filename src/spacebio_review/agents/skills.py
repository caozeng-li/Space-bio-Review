from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    references: dict[str, str]


class SkillLibrary:
    """An in-memory snapshot for one run; never executes files in a skill package."""

    def __init__(self, root: Path):
        self._skills: dict[str, Skill] = {}
        for path in sorted(root.glob("*/SKILL.md")):
            text = path.read_text(encoding="utf-8")
            parts = text.split("---", 2)
            if len(parts) != 3 or parts[0].strip():
                raise ValueError(f"Invalid skill frontmatter: {path.name}")
            meta = yaml.safe_load(parts[1])
            name = meta.get("name") if isinstance(meta, dict) else None
            description = meta.get("description") if isinstance(meta, dict) else None
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9-]+", name):
                raise ValueError("Invalid skill name")
            if not isinstance(description, str) or not description.strip():
                raise ValueError(f"Missing description: {name}")
            if name in self._skills:
                raise ValueError(f"Duplicate skill: {name}")
            refs = {}
            for ref in (path.parent / "references").glob("**/*.md"):
                if not ref.resolve().is_relative_to(path.parent.resolve()):
                    raise ValueError("Skill reference outside package")
                refs[ref.relative_to(path.parent).as_posix()] = ref.read_text(encoding="utf-8")
            self._skills[name] = Skill(name, description, parts[2].strip(), refs)

    def get(self, name: str) -> Skill:
        return self._skills[name]

    def catalog(self, names: list[str]) -> list[dict[str, str]]:
        return [{"name": n, "description": self.get(n).description} for n in dict.fromkeys(names)]
