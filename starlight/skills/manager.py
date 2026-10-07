"""Learning, approving and using skills.

* drafting: when the same kind of task (same sequence of tools) succeeds again, the model
  writes a draft skill from the examples. The AI can also draft one with ``skills.draft``
* approval: drafts are never used automatically; ``starlight skills approve <name>``
* use: approved skills that match a request are added to the prompt
* history: every change is a git commit; ``starlight skills rollback <name>``
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from starlight.core.config import SkillsConfig
from starlight.core.logging import get_logger
from starlight.core.model import Message, ModelProvider
from starlight.core.paths import Paths
from starlight.core.stats import Stats
from starlight.core.tools import Tool, make_tool
from starlight.memory.embed import Embedder, HashEmbedder
from starlight.memory.store import slugify
from starlight.skills.git import GitError, SkillHistory
from starlight.skills.store import Skill, SkillStore

MODULE = "skills"
IGNORED_TOOLS = ("memory.", "skills.")
STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "in",
    "on",
    "for",
    "my",
    "me",
    "is",
    "it",
    "what",
    "how",
    "please",
    "can",
    "you",
    "with",
    "this",
    "that",
    "der",
    "die",
    "das",
    "und",
    "ist",
    "mein",
    "meine",
    "bitte",
    "wie",
    "was",
}
DRAFT_PROMPT = """\
These requests were all solved the same way, with this sequence of tools: {tools}

Requests:
{requests}

Last answer:
{answer}

Write a reusable skill so the next request like this is handled quickly and reliably.
Answer with JSON only:
{{"name": "short-kebab-case-name", "description": "one sentence: when to use it",
  "instructions": "numbered steps in Markdown, naming the tools and their arguments",
  "triggers": ["a few", "keywords"]}}"""


def _signature(tools: list[str]) -> str:
    steps: list[str] = []
    for name in tools:
        if name.startswith(IGNORED_TOOLS):
            continue
        if not steps or steps[-1] != name:
            steps.append(name)
    return " > ".join(steps)


def _keywords(texts: list[str], limit: int = 6) -> list[str]:
    words = Counter(
        w for t in texts for w in re.findall(r"\w{3,}", t.lower()) if w not in STOPWORDS
    )
    return [w for w, _ in words.most_common(limit)]


def _parse_json(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


class SkillManager:
    def __init__(
        self,
        paths: Paths,
        config: SkillsConfig | None = None,
        provider: ModelProvider | None = None,
        embedder: Embedder | None = None,
        stats: Stats | None = None,
    ) -> None:
        self.config = config or SkillsConfig()
        self.provider = provider
        self.embedder = embedder or HashEmbedder()
        self.stats = stats
        self.store = SkillStore(paths.skills)
        self.history = SkillHistory(paths.skills, paths.data / "skills.git")
        self.task_log_file = paths.data / "skill_tasks.json"
        self._vectors: dict[str, tuple[str, Any]] = {}
        self._log = get_logger("skills")

    # -- managing ----------------------------------------------------------------------

    def all(self, status: str | None = None) -> list[Skill]:
        return [s for s in self.store.all() if status is None or s.status == status]

    def get(self, name: str) -> Skill | None:
        return self.store.get(name)

    def _commit(self, message: str, name: str) -> None:
        try:
            self.history.commit(message, slugify(name))
        except GitError as exc:
            self._log.warning("skill history not updated", error=str(exc))
        self._record_counts()

    def _record_counts(self) -> None:
        if self.stats is None:
            return
        for status, count in Counter(s.status for s in self.store.all()).items():
            self.stats.record("skills.count", count, status=status)

    def draft(
        self,
        name: str,
        description: str,
        instructions: str,
        triggers: list[str] | None = None,
        tools: list[str] | None = None,
        example: str = "",
        code: dict[str, str] | None = None,
        source: str = "assistant",
    ) -> Skill:
        """Write a draft. An existing skill of that name gets a new version, which must be
        approved again."""
        slug = slugify(name)
        existing = self.store.get(slug)
        skill = Skill(
            name=slug,
            description=description.strip(),
            instructions=instructions.strip(),
            status="draft",
            version=existing.version + 1 if existing else 1,
            created=existing.created if existing else Skill("", "", "").created,
            source=source,
            triggers=triggers or [],
            tools=tools or [],
            example=example,
            code=code or {},
        )
        self.store.write(skill)
        self._commit(f"draft {slug} v{skill.version}", slug)
        self._log.info("skill drafted", skill=slug, version=skill.version)
        return skill

    def _set_status(self, name: str, status: str) -> Skill:
        skill = self.store.get(name)
        if skill is None:
            raise KeyError(f"no skill named '{name}'")
        updated = replace(skill, status=status, updated=Skill("", "", "").updated)  # type: ignore[arg-type]
        self.store.write(updated)
        self._commit(f"{status} {skill.name} v{skill.version}", skill.name)
        return updated

    def approve(self, name: str) -> Skill:
        return self._set_status(name, "approved")

    def reject(self, name: str) -> Skill:
        return self._set_status(name, "rejected")

    def delete(self, name: str) -> None:
        """Remove a skill. Its earlier versions stay in the skill history (rollback)."""
        slug = slugify(name)
        folder = self.store.folder(slug)
        if not folder.is_dir():
            raise KeyError(f"no skill '{name}'")
        shutil.rmtree(folder)
        self._vectors.pop(slug, None)
        self._commit(f"delete {slug}", slug)

    def versions(self, name: str) -> list[tuple[str, str, str]]:
        return self.history.log(slugify(name))

    def rollback(self, name: str, revision: str | None = None) -> Skill:
        """Restore a skill as it was at ``revision`` (default: before the last change)."""
        slug = slugify(name)
        log = self.history.log(slug)
        if revision is None:
            if len(log) < 2:
                raise KeyError(f"'{slug}' has no earlier version")
            revision = log[1][0]
        folder = self.store.folder(slug)
        backup = folder.with_name(folder.name + ".rollback")
        if folder.exists():
            folder.rename(backup)
        try:
            self.history.restore(slug, revision)
        except GitError:
            if backup.exists():
                backup.rename(folder)
            raise
        shutil.rmtree(backup, ignore_errors=True)
        self._commit(f"rollback {slug} to {revision}", slug)
        skill = self.store.get(slug)
        assert skill is not None
        return skill

    # -- using -------------------------------------------------------------------------

    async def _vector(self, skill: Skill) -> Any:
        cached = self._vectors.get(skill.name)
        if cached and cached[0] == skill.search_text:
            return cached[1]
        [vector] = await self.embedder.embed([skill.search_text])
        self._vectors[skill.name] = (skill.search_text, vector)
        return vector

    async def relevant(self, request: str) -> list[tuple[Skill, float]]:
        approved = self.all("approved")
        if not approved or self.config.max_in_prompt <= 0:
            return []
        [query] = await self.embedder.embed([request])
        scored = [(s, float(np.dot(await self._vector(s), query))) for s in approved]
        scored.sort(key=lambda pair: -pair[1])
        return [p for p in scored[: self.config.max_in_prompt] if p[1] >= self.config.min_score]

    async def enrich(self, request: str) -> str | None:
        """Agent enricher: matching approved skills as an extra system message."""
        if not self.config.enabled:
            return None
        matches = await self.relevant(request)
        if not matches:
            return None
        parts = [f"### Skill: {s.name}\n{s.description}\n\n{s.instructions}" for s, _ in matches]
        return "Skills you learned and the user approved. Follow them when they fit:\n\n" + (
            "\n\n".join(parts)
        )

    # -- learning from tasks -----------------------------------------------------------

    def _task_log(self) -> dict[str, Any]:
        if not self.task_log_file.is_file():
            return {}
        try:
            data = json.loads(self.task_log_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    async def on_task(self, data: dict[str, Any]) -> Skill | None:
        """Called after each successful task. Drafts a skill when a task repeats."""
        if not (self.config.enabled and self.config.auto_draft):
            return None
        results = data.get("tool_results") or []
        if not results or not all(getattr(r, "ok", False) for r in results):
            return None
        signature = _signature([r.tool for r in results])
        if not signature:
            return None
        log = self._task_log()
        entry = log.setdefault(signature, {"count": 0, "requests": [], "skill": None})
        entry["count"] += 1
        entry["requests"] = [*entry["requests"], str(data.get("request", ""))][-3:]
        skill = None
        if entry["count"] >= self.config.repeat_threshold and not (
            entry["skill"] and self.store.get(entry["skill"])
        ):
            skill = await self._draft_from(signature, entry, data, results)
            entry["skill"] = skill.name
        self.task_log_file.parent.mkdir(parents=True, exist_ok=True)
        self.task_log_file.write_text(json.dumps(log, indent=2), encoding="utf-8")
        return skill

    async def _draft_from(
        self, signature: str, entry: dict[str, Any], data: dict[str, Any], results: list[Any]
    ) -> Skill:
        requests = entry["requests"]
        answer = str(data.get("answer", ""))[:2000]
        spec: dict[str, Any] | None = None
        if self.provider is not None:
            prompt = DRAFT_PROMPT.format(
                tools=signature,
                requests="\n".join(f"- {r}" for r in requests),
                answer=answer,
            )
            try:
                response = await self.provider.chat([Message("user", prompt)], [])
                spec = _parse_json(response.content)
            except Exception:
                self._log.exception("skill drafting by the model failed")
        tools = signature.split(" > ")
        if not spec or not spec.get("instructions"):
            spec = {  # the model gave nothing usable: a plain recipe from what worked
                "name": "-".join(_keywords(requests, 4)) or "repeated-task",
                "description": f"For requests like: {requests[-1]}",
                "instructions": "\n".join(f"{i}. Call `{tool}`." for i, tool in enumerate(tools, 1))
                + "\n\nThen answer with the result.",
                "triggers": _keywords(requests),
            }
        code: dict[str, str] = {}
        for result in results:  # keep code that was written and tested in the sandbox
            output = getattr(result, "output", None)
            if isinstance(output, dict) and output.get("success") and output.get("files"):
                code.update({str(k): str(v) for k, v in output["files"].items()})
        example = f"Request: {requests[-1]}\n\nAnswer: {answer}"
        skill = self.draft(
            str(spec.get("name") or "repeated-task"),
            str(spec.get("description") or ""),
            str(spec.get("instructions")),
            triggers=[str(t) for t in spec.get("triggers") or []][:10],
            tools=tools,
            example=example,
            code=code,
            source=f"repeated task ({entry['count']}x)",
        )
        return skill

    # -- turning a skill into a module -------------------------------------------------

    def promote(self, name: str, modules_dir: Path) -> Path:
        """Start a full module from an approved skill (from modules/_template)."""
        skill = self.store.get(name)
        if skill is None or skill.status != "approved":
            raise KeyError(f"'{name}' is not an approved skill")
        module_name = slugify(name).replace("-", "_")
        target = modules_dir / module_name
        if target.exists():
            raise FileExistsError(f"{target} already exists")
        shutil.copytree(
            modules_dir / "_template", target, ignore=shutil.ignore_patterns("__pycache__")
        )
        class_name = "".join(p.capitalize() for p in module_name.split("_")) + "Module"
        for file in target.rglob("*"):
            if file.is_file() and file.suffix in (".py", ".yaml", ".md"):
                text = file.read_text(encoding="utf-8")
                file.write_text(
                    text.replace("TemplateModule", class_name).replace("template", module_name),
                    encoding="utf-8",
                )
        (target / "module.yaml").write_text(
            (target / "module.yaml")
            .read_text(encoding="utf-8")
            .replace("enabled_by_default: true", "enabled_by_default: false"),
            encoding="utf-8",
        )
        (target / "README.md").write_text(skill.to_markdown(), encoding="utf-8")
        for rel, content in skill.code.items():
            path = target / "skill_code" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return target

    # -- tools for the AI --------------------------------------------------------------

    def tools(self) -> list[Tool]:
        def list_skills() -> list[dict[str, str]]:
            """Skills you learned, with their status (only approved ones are used)."""
            return [
                {"name": s.name, "status": s.status, "description": s.description}
                for s in self.all()
            ]

        def show(name: str) -> str:
            """The instructions of one skill."""
            skill = self.get(name)
            return skill.to_markdown() if skill else f"No skill named '{name}'."

        def draft(
            name: str, description: str, instructions: str, triggers: list[str] | None = None
        ) -> str:
            """Save a reusable way of doing something as a draft skill. The user approves
            it before it is used."""
            skill = self.draft(name, description, instructions, triggers, source="assistant")
            return (
                f"Draft skill '{skill.name}' v{skill.version} saved. "
                f"Approve with: starlight skills approve {skill.name}"
            )

        return [
            make_tool(MODULE, list_skills, name="list"),
            make_tool(MODULE, show),
            make_tool(MODULE, draft),
        ]
