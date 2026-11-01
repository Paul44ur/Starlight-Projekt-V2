"""Reading and writing skill folders."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from starlight.core.logging import get_logger
from starlight.memory.store import read_text, slugify
from starlight.notebook.markdown import split

type Status = Literal["draft", "approved", "rejected"]
SKILL_FILE = "SKILL.md"


def today() -> str:
    """Today as YYYY-MM-DD: a skill's created and updated dates."""
    return dt.date.today().isoformat()


@dataclass
class Skill:
    name: str
    description: str
    instructions: str
    status: Status = "draft"
    version: int = 1
    created: str = field(default_factory=today)
    updated: str = field(default_factory=today)
    source: str = "assistant"
    triggers: list[str] = field(default_factory=list)  # words that suggest this skill
    tools: list[str] = field(default_factory=list)  # tool sequence it was learned from
    example: str = ""
    code: dict[str, str] = field(default_factory=dict)

    def to_markdown(self) -> str:
        header: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "version": self.version,
            "created": self.created,
            "updated": self.updated,
            "source": self.source,
            "triggers": self.triggers,
            "tools": self.tools,
        }
        front = yaml.safe_dump(header, sort_keys=False, allow_unicode=True).strip()
        return f"---\n{front}\n---\n{self.instructions.strip()}\n"

    @property
    def search_text(self) -> str:
        return f"{self.name} {self.description} {' '.join(self.triggers)}"


class SkillStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def folder(self, name: str) -> Path:
        path = (self.root / slugify(name)).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"invalid skill name: {name}")
        return path

    def names(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.parent.name for p in self.root.glob(f"*/{SKILL_FILE}"))

    def get(self, name: str, files: bool = True) -> Skill | None:
        """``files=False`` skips example.md and code/ (a lot to read, rarely needed)."""
        folder = self.folder(name)
        path = folder / SKILL_FILE
        if not path.is_file():
            return None
        front = split(read_text(path))
        if front.error:  # (all() leaves such a skill out and logs why)
            raise ValueError(f"{path}: {front.error}")
        header: dict[str, Any] = front.properties
        body = front.body
        example = folder / "example.md"
        code_dir = folder / "code"
        code = (
            {
                p.relative_to(code_dir).as_posix(): p.read_text(encoding="utf-8")
                for p in sorted(code_dir.rglob("*"))
                if p.is_file()
            }
            if files and code_dir.is_dir()
            else {}
        )
        status = str(header.get("status", "draft"))
        return Skill(
            name=folder.name,
            description=str(header.get("description", "")),
            instructions=body.strip(),
            status=status if status in ("draft", "approved", "rejected") else "draft",  # type: ignore[arg-type]
            version=int(header.get("version", 1)),
            created=str(header.get("created", "")),
            updated=str(header.get("updated", "")),
            source=str(header.get("source", "")),
            triggers=[str(t) for t in header.get("triggers") or []],
            tools=[str(t) for t in header.get("tools") or []],
            example=example.read_text(encoding="utf-8") if files and example.is_file() else "",
            code=code,
        )

    def all(self, files: bool = True) -> list[Skill]:
        """Every skill that can be read. One hand-edited SKILL.md with a mistake (bad YAML,
        a version that is not a number) is skipped and logged, not fatal for the rest."""
        found: list[Skill] = []
        for name in self.names():
            try:
                skill = self.get(name, files)
            except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
                get_logger("skills").warning(
                    "skill skipped: SKILL.md could not be read", skill=name, error=str(exc)
                )
                continue
            if skill is not None:
                found.append(skill)
        return found

    def write(self, skill: Skill) -> Path:
        folder = self.folder(skill.name)
        folder.mkdir(parents=True, exist_ok=True)
        code_dir = (folder / "code").resolve()
        targets: dict[Path, str] = {}
        for rel, content in skill.code.items():
            target = (code_dir / rel).resolve()
            if not target.is_relative_to(code_dir):
                raise ValueError(f"invalid code file name: {rel}")
            targets[target] = content
        (folder / SKILL_FILE).write_text(skill.to_markdown(), encoding="utf-8")
        example = folder / "example.md"
        if skill.example:
            example.write_text(skill.example.strip() + "\n", encoding="utf-8")
        else:
            example.unlink(missing_ok=True)  # a new version must not keep the old example
        if code_dir.is_dir():
            for old in code_dir.rglob("*"):
                if old.is_file() and old.resolve() not in targets:
                    old.unlink()  # ... nor code files it no longer has
        for target, content in targets.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return folder
