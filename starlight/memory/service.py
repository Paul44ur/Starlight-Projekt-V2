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
[{"topic": "a specific topic", "fact": "one or two sentences", "confidence": 0.9}]
or [] if nothing is worth keeping. A fact on the same topic replaces the old one, so make \
topics specific ("Sister's name", "Preferred editor"), not broad ("Family", "Tools").
Add "kind": "person" for a fact about a person, "kind": "place" for one about a place. \
A fact that only holds for a while ("my sister visits until Friday", "the office is \
closed this week") gets "until": the last day it holds, as YYYY-MM-DD. Today is {today}.

User: {request}
Assistant: {answer}"""

KINDS = ("person", "place")


def _signature(files: list[Path]) -> int:
    """Changes when any memory file is added, removed, edited or restored (per file: name,
    modification time and size), also when the count and the total size stay the same."""
    parts = []
    for path in files:
        try:
            info = path.stat()
        except OSError:  # gone since the listing
            continue
        parts.append((path.name, path.parent.name, info.st_mtime_ns, info.st_size))
    return hash(tuple(parts))


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


def _date(value: Any) -> str:
    """A YYYY-MM-DD date from what the model or the user gave, or "" if it is not one."""
    text = str(value or "").strip()[:10]
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


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
        self._signature: int | None = None
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
        """The memories that fit the query best; ones that expired are left out."""
        await self.sync()
        k = self.config.top_k if top_k is None else top_k
        hits = await self.index.search(query, k + 5, self.config.min_score)
        return [h for h in hits if not h.entry.expired()][:k]

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
        expires: str = "",
    ) -> tuple[MemoryEntry, bool]:
        """Store a fact. Returns (entry, merged) where merged means an existing file was
        updated instead of creating a new one."""
        content = content.strip()
        topic = (topic or " ".join(content.split()[:6])).strip()
        await self.sync()
        existing = self.store.get(slugify(topic))
        if existing is not None and existing.topic.casefold() != topic.casefold():
            existing = None  # another topic with the same file name ("C#" and "C++")
        if existing is None:
            hits = await self.index.search(f"{topic}: {content}", 1, self.config.merge_threshold)
            existing = hits[0].entry if hits else None
        if (
            existing is not None
            and existing.id != PROFILE_ID  # the profile changes only field by field
            and not existing.id.startswith(CONVERSATIONS + "/")
        ):
            entry = self._merge(existing, content, source, confidence, tags)
            entry.expires = expires  # the newest statement says how long it holds
            merged = True
        else:
            entry = MemoryEntry(
                id=self.store.new_id(topic),
                topic=topic,
                content=content,
                source=source,
                confidence=confidence,
                tags=tags or [],
                expires=expires,
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
        await self.prune_summaries()
        await self.sync(force=True)
        return entry

    async def prune_summaries(self) -> int:
        """Delete the oldest conversation summaries beyond ``summary_keep``. They are
        written automatically and never tidied or merged, so they would pile up (and make
        every re-index slower). Returns how many were removed."""
        keep = self.config.summary_keep
        folder = self.store.root / CONVERSATIONS
        if keep <= 0 or not folder.is_dir():
            return 0
        found = sorted(
            (p for p in folder.glob("*.md") if not p.name.startswith(".")),
            key=lambda p: (p.stat().st_mtime_ns, p.name),
        )  # oldest first
        removed = 0
        for old in found[: max(0, len(found) - keep)]:
            try:
                old.unlink()
                removed += 1
            except OSError:
                self._log.warning("could not remove an old summary", file=old.name)
        if removed:
            self._log.info("old conversation summaries removed", removed=removed, kept=keep)
            await self.sync(force=True)
        return removed

    async def extract(
        self, request: str, answer: str, request_id: str = "", conversation: str = ""
    ) -> list[MemoryEntry]:
        """Ask the model what from this exchange is worth keeping, and keep it. Each fact
        names the conversation it came from (memory.forget_conversation uses that)."""
        if not self.config.auto_extract or self.provider is None or not answer.strip():
            return []
        model = getattr(self.provider, "summarizer", self.provider)
        # Not str.format(): the prompt contains a JSON example with braces.
        values = {
            "request": request[:4000],
            "answer": answer[:4000],
            "today": dt.date.today().isoformat(),
        }
        prompt = re.sub(r"\{(request|answer|today)\}", lambda m: values[m.group(1)], EXTRACT_PROMPT)
        response = await model.chat([Message("user", prompt)], [])
        saved = []
        for fact in parse_facts(response.content)[:6]:
            if fact.get("profile"):
                self._update_profile(str(fact["profile"]), str(fact.get("value", "")))
                continue
            try:
                confidence = float(fact.get("confidence", 0.8))
            except (TypeError, ValueError):
                confidence = 0.8
            kind = str(fact.get("kind") or "").lower()
            entry, _ = await self.remember(
                str(fact["fact"]),
                topic=str(fact.get("topic") or "") or None,
                source=f"conversation {conversation or request_id}".strip(),
                confidence=min(1.0, max(0.0, confidence)),
                tags=[kind] if kind in KINDS else None,
                expires=_date(fact.get("until")),
            )
            saved.append(entry)
        return saved

    async def prune_expired(self) -> int:
        """Delete the memories whose "expires" date has passed. Returns how many."""
        removed = 0
        for entry in self.store.all():
            if entry.expired() and self.store.delete(entry.id):
                removed += 1
        if removed:
            self._log.info("expired memories removed", removed=removed)
            await self.sync(force=True)
        return removed

    def learned_in(self, conversation: str) -> tuple[list[MemoryEntry], list[MemoryEntry]]:
        """The memories learned in a conversation: (only from it, also from elsewhere)."""
        mark = f"conversation {conversation}"
        only: list[MemoryEntry] = []
        shared: list[MemoryEntry] = []
        for entry in self.store.all():
            sources = [s.strip() for s in entry.source.split(";") if s.strip()]
            if mark in sources:
                (only if all(s == mark for s in sources) else shared).append(entry)
        return only, shared

    async def forget_conversation(self, conversation: str) -> dict[str, Any]:
        """Delete what was learned only in this conversation. A memory that other
        conversations (or you) also said is kept, and named."""
        only, shared = self.learned_in(conversation)
        for entry in only:
            self.store.delete(entry.id)
        if only:
            await self.sync(force=True)
        return {
            "forgotten": [e.topic for e in only],
            "kept_because_also_said_elsewhere": [e.topic for e in shared],
        }

    def people_and_places(self) -> dict[str, list[dict[str, str]]]:
        """The memories about people and about places (tagged person / place)."""
        found: dict[str, list[dict[str, str]]] = {"people": [], "places": []}
        for entry in self.store.all():
            if entry.expired():
                continue
            for kind, group in (("person", "people"), ("place", "places")):
                if kind in entry.tags:
                    found[group].append(
                        {"id": entry.id, "topic": entry.topic, "fact": entry.content[:300]}
                    )
        for listed in found.values():
            listed.sort(key=lambda e: e["topic"].casefold())
        return found

    # -- tidying -----------------------------------------------------------------------

    def _tidy_candidates(self, entry: MemoryEntry) -> bool:
        return entry.id != PROFILE_ID and not entry.id.startswith(CONVERSATIONS + "/")

    async def duplicates(self) -> list[tuple[MemoryEntry, MemoryEntry, float]]:
        """Near-duplicate memories (as alike as memory.merge_threshold), each entry in at
        most one pair, most alike first."""
        await self.sync()
        used: set[str] = set()
        pairs = []
        for a, b, score in self.index.similar_pairs(self.config.merge_threshold):
            if not (self._tidy_candidates(a) and self._tidy_candidates(b)):
                continue
            if a.id in used or b.id in used:
                continue
            used.update((a.id, b.id))
            pairs.append((a, b, score))
        return pairs

    @staticmethod
    def _combine(a: MemoryEntry, b: MemoryEntry) -> tuple[MemoryEntry, MemoryEntry]:
        """(the merged entry, the one to delete). The newer statement wins, unless the
        older one already says it and more."""
        newer, older = (a, b) if (a.date, len(a.content)) >= (b.date, len(b.content)) else (b, a)
        content = newer.content
        if newer.content.strip() in older.content:
            content = older.content
        sources = [s.strip() for s in f"{older.source};{newer.source}".split(";") if s.strip()]
        merged = MemoryEntry(
            id=newer.id,
            topic=newer.topic,
            content=content,
            source="; ".join(list(dict.fromkeys(sources))[-5:]),
            date=newer.date,
            confidence=max(a.confidence, b.confidence),
            tags=sorted({*a.tags, *b.tags}),
        )
        return merged, older

    async def tidy(self) -> list[dict[str, str]]:
        """Merge every near-duplicate pair. Returns what was merged into what."""
        merged = []
        for a, b, _ in await self.duplicates():
            keep, drop = self._combine(a, b)
            self.store.write(keep)
            self.store.delete(drop.id)
            merged.append({"kept": keep.id, "merged_in": drop.id})
        if merged:
            await self.sync(force=True)
            self._log.info("memory tidied", merged=len(merged))
        return merged

    def _update_profile(self, field: str, value: str) -> None:
        """A profile fact the model picked up. Never clears a field, and adds to the
        preferences instead of replacing them."""
        key, value = field_key(field), " ".join(value.split())
        if key is None or not value:
            return
        if key == "preferences":
            current = self.profile.read().get("preferences", "")
            if value.lower() in current.lower():
                return
            items = [p for p in current.split("; ") if p] + [value]
            while len(items) > 1 and len("; ".join(items)) > 1000:
                items.pop(0)  # the oldest go first, so a new one always fits
            value = "; ".join(items)
        self.profile.set(key, value)
        self._log.info("profile updated", field=key)

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
        async def remember(fact: str, topic: str = "", until: str = "", kind: str = "") -> str:
            """Save a fact to long-term memory (merged with a note on the same topic).
            until: last day it holds (YYYY-MM-DD), if temporary; kind: person or place."""
            expires = _date(until)
            if until.strip() and not expires:
                raise ToolError("until: a date as YYYY-MM-DD, e.g. 2026-11-03")
            tags = [kind.lower()] if kind.lower() in KINDS else None
            entry, merged = await self.remember(
                fact, topic or None, source="assistant", tags=tags, expires=expires
            )
            held = f" (until {expires})" if expires else ""
            return f"{'Updated' if merged else 'Saved'} memory '{entry.id}'{held}."

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

        async def about_me(limit: int = 40) -> dict[str, Any]:
            """Everything you know about the user: their profile and what you remember,
            newest first - for "what do you know about me?"."""
            await self.sync()
            entries = [e for e in self.store.all() if self._tidy_candidates(e)]
            entries.sort(key=lambda e: e.date, reverse=True)
            return {
                "profile": self.profile.read(),
                "count": len(entries),
                "memories": [
                    {"id": e.id, "topic": e.topic, "fact": e.content[:300], "date": e.date}
                    for e in entries[: max(1, min(limit, 100))]
                ],
            }

        async def duplicates() -> dict[str, Any]:
            """Memories that say nearly the same thing, in pairs. memory.tidy merges them."""
            pairs = await self.duplicates()
            return {
                "count": len(pairs),
                "pairs": [
                    {"a": a.text[:200], "b": b.text[:200], "similarity": round(score, 3)}
                    for a, b, score in pairs
                ],
            }

        async def tidy() -> dict[str, Any]:
            """Merge memories that say nearly the same thing (see memory.duplicates): the
            newer statement is kept, tags and sources are joined. Asks first."""
            merged = await self.tidy()
            return {"merged": len(merged), "details": merged}

        def profile() -> dict[str, str]:
            """The user's profile: name, language, home place, time zone, preferences."""
            return self.profile.read()

        def people_and_places() -> dict[str, list[dict[str, str]]]:
            """The people and places you remember."""
            return self.people_and_places()

        async def forget_conversation(conversation: str) -> dict[str, Any]:
            """Forget what was learned only in one conversation (its 12-character id)."""
            if not re.fullmatch(r"[a-f0-9]{12}", conversation.strip()):
                raise ToolError("give the conversation's id (12 letters and digits)")
            return await self.forget_conversation(conversation.strip())

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
            make_tool(MODULE, about_me),
            make_tool(MODULE, duplicates),
            make_tool(MODULE, tidy, permission=Permission.CONFIRM),
            make_tool(MODULE, people_and_places),
            make_tool(MODULE, forget_conversation, permission=Permission.CONFIRM),
        ]

    def close(self) -> None:
        self.index.close()
