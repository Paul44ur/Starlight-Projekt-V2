"""Memory service: what the agent remembers across restarts.

* recall: before each answer the most relevant memories are added to the prompt
* writing: after each task the model picks what is worth keeping; duplicates are merged
  into the existing file (same topic, or nearly the same meaning) instead of piling up
* summaries of long conversations (Step 4) land in ``memory/conversations/``
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

from starlight.core.config import MemoryConfig
from starlight.core.logging import get_logger
from starlight.core.model import Message, ModelProvider
from starlight.core.paths import Paths
from starlight.core.permissions import Permission
from starlight.core.stats import Stats
from starlight.core.tools import Tool, ToolError, make_tool
from starlight.memory.embed import Embedder, create_embedder
from starlight.memory.index import Hit, MemoryIndex
from starlight.memory.profile import PROFILE_ID, Profile, field_key
from starlight.memory.store import MemoryEntry, MemoryStore, slugify

MODULE = "memory"
CONVERSATIONS = "conversations"
EXTRACT_PROMPT = """\
Below is one exchange between the user and you. List the facts worth remembering for \
future conversations: things about the user, their preferences, people, projects, \
systems and decisions, or lessons learned that will help later. Skip small talk, \
one-off details and anything already obvious. Never include passwords or other secrets.
If the user says their name, the language they want answers in, where they live or their \
time zone, add it as a profile item instead, e.g. {"profile": "home", "value": "Vienna"} \
(profile fields: name, language, home, timezone, preferences).

Answer with JSON only, a list like
[{"topic": "short topic", "fact": "one or two sentences", "confidence": 0.9}]
or [] if nothing is worth keeping.

User: {request}
Assistant: {answer}"""


def _signature(files: list[Path]) -> tuple[int, float, int]:
    stats = [f.stat() for f in files]
    return len(stats), max((s.st_mtime for s in stats), default=0.0), sum(s.st_size for s in stats)


def parse_facts(text: str) -> list[dict[str, Any]]:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    facts = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("fact", "")).strip() or (
            item.get("profile") and str(item.get("value", "")).strip()
        ):
            facts.append(item)
    return facts


class Memory:
    def __init__(
        self,
        paths: Paths,
        config: MemoryConfig | None = None,
        provider: ModelProvider | None = None,
        stats: Stats | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.config = config or MemoryConfig()
        self.provider = provider
        self.stats = stats
        self.store = MemoryStore(paths.memory)
        self.index = MemoryIndex(
            self.store,
            embedder or create_embedder(self.config.embedder),
            paths.data / "memory_index.sqlite",
        )
        self.profile = Profile(self.store)
        self._signature: tuple[int, float, int] | None = None
        self._log = get_logger("memory")

    async def sync(self, force: bool = False) -> None:
        """Re-read the folder if anything in it changed (cheap when nothing did)."""
        signature = _signature(self.store.files())
        if not force and signature == self._signature:
            return
        embedded = await self.index.sync()
        self._signature = signature
        if embedded:
            self._log.info("memory indexed", embedded=embedded, total=len(self.index))
        if self.stats is not None:
            self.stats.record("memory.entries", len(self.index))

    # -- reading -----------------------------------------------------------------------

    async def recall(self, query: str, top_k: int | None = None) -> list[Hit]:
        await self.sync()
        k = self.config.top_k if top_k is None else top_k
        return await self.index.search(query, k, self.config.min_score)

    async def enrich(self, request: str) -> str | None:
        """Agent enricher: relevant memories as an extra system message."""
        if not self.config.enabled:
            return None
        hits = [h for h in await self.recall(request) if h.entry.id != PROFILE_ID]
        if not hits:
            return None
        lines = [f"- {h.entry.text} ({h.entry.date}, id {h.entry.id})" for h in hits]
        return (
            "Things you remember from earlier conversations (from your memory folder; "
            "they may be outdated):\n" + "\n".join(lines)
        )

    # -- writing -----------------------------------------------------------------------

    async def remember(
        self,
        content: str,
        topic: str | None = None,
        source: str = "user",
        confidence: float = 1.0,
        tags: list[str] | None = None,
    ) -> tuple[MemoryEntry, bool]:
        """Store a fact. Returns (entry, merged) where merged means an existing file was
        updated instead of creating a new one."""
        content = content.strip()
        topic = (topic or " ".join(content.split()[:6])).strip()
        await self.sync()
        existing = self.store.get(slugify(topic))
        if existing is None:
            hits = await self.index.search(f"{topic}: {content}", 1, self.config.merge_threshold)
            existing = hits[0].entry if hits else None
        if (
            existing is not None
            and existing.id != PROFILE_ID  # the profile changes only field by field
            and not existing.id.startswith(CONVERSATIONS + "/")
        ):
            entry = self._merge(existing, content, source, confidence, tags)
            merged = True
        else:
            entry = MemoryEntry(
                id=self.store.new_id(topic),
                topic=topic,
                content=content,
                source=source,
                confidence=confidence,
                tags=tags or [],
            )
            merged = False
        self.store.write(entry)
        await self.sync(force=True)
        self._log.info("remembered", id=entry.id, merged=merged)
        return entry, merged

    @staticmethod
    def _merge(
        old: MemoryEntry, content: str, source: str, confidence: float, tags: list[str] | None
    ) -> MemoryEntry:
        """Newest statement wins; sources and tags accumulate."""
        sources = [s.strip() for s in old.source.split(";") if s.strip()]
        if source not in sources:
            sources.append(source)
        return MemoryEntry(
            id=old.id,
            topic=old.topic,
            content=old.content if content in old.content else content,
            source="; ".join(sources[-5:]),
            date=dt.date.today().isoformat(),
            confidence=max(old.confidence, confidence),
            tags=sorted({*old.tags, *(tags or [])}),
        )

    async def forget(self, entry_id: str) -> bool:
        removed = self.store.delete(entry_id)
        if removed:
            await self.sync(force=True)
        return removed

    async def save_summary(self, summary: str, source: str = "conversation") -> MemoryEntry:
        today = dt.date.today().isoformat()
        entry = MemoryEntry(
            id=self.store.new_id(f"{today} summary", folder=CONVERSATIONS),
            topic=f"Conversation summary {today}",
            content=summary,
            source=source,
            confidence=0.7,
            tags=["summary"],
        )
        self.store.write(entry)
        await self.sync(force=True)
        return entry

    async def extract(self, request: str, answer: str, request_id: str = "") -> list[MemoryEntry]:
        """Ask the model what from this exchange is worth keeping, and keep it."""
        if not self.config.auto_extract or self.provider is None or not answer.strip():
            return []
        model = getattr(self.provider, "summarizer", self.provider)
        # Not str.format(): the prompt contains a JSON example with braces.
        values = {"request": request[:4000], "answer": answer[:4000]}
        prompt = re.sub(r"\{(request|answer)\}", lambda m: values[m.group(1)], EXTRACT_PROMPT)
        response = await model.chat([Message("user", prompt)], [])
        saved = []
        for fact in parse_facts(response.content)[:6]:
            if fact.get("profile"):
                key = field_key(str(fact["profile"]))
                if key is not None:
                    self.profile.set(key, str(fact.get("value", "")))
                    self._log.info("profile updated", field=key)
                continue
            try:
                confidence = float(fact.get("confidence", 0.8))
            except (TypeError, ValueError):
                confidence = 0.8
            entry, _ = await self.remember(
                str(fact["fact"]),
                topic=str(fact.get("topic") or "") or None,
                source=f"conversation {request_id}".strip(),
                confidence=min(1.0, max(0.0, confidence)),
            )
            saved.append(entry)
        return saved

    def export(self, target: Path) -> Path:
        """Everything in one file: JSON if the name ends in .json, otherwise Markdown."""
        entries = self.store.all()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix == ".json":
            target.write_text(
                json.dumps([e.__dict__ for e in entries], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            parts = [f"<!-- {e.id} -->\n{e.to_markdown()}" for e in entries]
            target.write_text("\n".join(parts), encoding="utf-8")
        return target

    # -- tools for the AI --------------------------------------------------------------

    def tools(self) -> list[Tool]:
        async def remember(fact: str, topic: str = "") -> str:
            """Save a fact to long-term memory (merged with an existing note on the same
            topic). Use for things the user wants you to remember."""
            entry, merged = await self.remember(fact, topic or None, source="assistant")
            return f"{'Updated' if merged else 'Saved'} memory '{entry.id}'."

        async def recall(query: str, limit: int = 5) -> list[dict[str, Any]]:
            """Search long-term memory."""
            hits = await self.recall(query, limit)
            return [
                {
                    "id": h.entry.id,
                    "text": h.entry.text,
                    "date": h.entry.date,
                    "score": round(h.score, 3),
                }
                for h in hits
            ]

        async def forget(memory_id: str) -> str:
            """Delete one memory file by id."""
            return "Deleted." if await self.forget(memory_id) else "No such memory."

        def profile() -> dict[str, str]:
            """The user's profile: name, language, home place, time zone, preferences."""
            return self.profile.read()

        async def set_profile(field: str, value: str) -> dict[str, str]:
            """Set one field of the user's profile (name, language, home, timezone or
            preferences) when they tell you; an empty value clears it."""
            try:
                values = self.profile.set(field, value)
            except ValueError as exc:
                raise ToolError(str(exc)) from None
            await self.sync(force=True)
            return values

        return [
            make_tool(MODULE, remember),
            make_tool(MODULE, recall),
            make_tool(MODULE, forget, permission=Permission.CONFIRM),
            make_tool(MODULE, profile),
            make_tool(MODULE, set_profile),
        ]

    def close(self) -> None:
        self.index.close()
