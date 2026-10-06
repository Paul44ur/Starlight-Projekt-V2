"""Reading and writing skill folders."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from starlight.memory.store import FRONT, slugify

type Status = Literal["draft", "approved", "rejected"]
SKILL_FILE = "SKILL.md"


@dataclass
class Skill:
    name: str
    description: str
    instructions: str
    status: Status = "draft"
    version: int = 1
    created: str = field(default_factory=lambda: dt.date.today().isoformat())
    updated: str = field(default_factory=lambda: dt.date.today().isoformat())
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

    def get(self, name: str) -> Skill | None:
        folder = self.folder(name)
        path = folder / SKILL_FILE
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8")
        match = FRONT.match(raw)
        header: dict[str, Any] = {}
        body = raw
        if match:
            loaded = yaml.safe_load(match.group(1))
            header = loaded if isinstance(loaded, dict) else {}
            body = match.group(2)
        example = folder / "example.md"
        code_dir = folder / "code"
        code = (
            {
                p.relative_to(code_dir).as_posix(): p.read_text(encoding="utf-8")
                for p in sorted(code_dir.rglob("*"))
                if p.is_file()
            }
            if code_dir.is_dir()
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
            example=example.read_text(encoding="utf-8") if example.is_file() else "",
            code=code,
        )

    def all(self) -> list[Skill]:
        return [s for n in self.names() if (s := self.get(n)) is not None]

    def write(self, skill: Skill) -> Path:
        folder = self.folder(skill.name)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / SKILL_FILE).write_text(skill.to_markdown(), encoding="utf-8")
        if skill.example:
            (folder / "example.md").write_text(skill.example.strip() + "\n", encoding="utf-8")
        for rel, content in skill.code.items():
            target = (folder / "code" / rel).resolve()
            if not target.is_relative_to(folder.resolve()):
                raise ValueError(f"invalid code file name: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return folder
