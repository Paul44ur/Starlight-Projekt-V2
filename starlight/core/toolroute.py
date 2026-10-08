"""Send the model only the tools a request needs.

Every tool's description and parameters go into the prompt. With 200+ tools that is about
18,000 tokens before the conversation starts: a local model reads them on every request
(slow to start answering) and picks the wrong tool more often. The router picks the few
modules that fit a request and sends their tools, plus:

* tools named in the request (``!weather.now``, "use calendar.agenda")
* the modules of tools used a moment ago in the same conversation (follow-up questions)
* a few tools that are always there (memory, and ``tools.find``)

``tools.find(need)`` lets the model ask for more when nothing it sees fits; what it finds
is added for the rest of the request. Any registered tool can still be called.

Scoring is lexical (words of the request against module and tool names, descriptions,
parameter names and the ``keywords`` in module.yaml) plus, when it answers, the embedding
model memory uses.
"""

from __future__ import annotations

import contextvars
import math
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from starlight.core.config import AgentConfig
from starlight.core.dispatcher import Dispatcher
from starlight.core.logging import get_logger
from starlight.core.model import Message
from starlight.core.tools import Tool, make_tool

FIND_TOOL = "tools.find"
EMBED_RETRY = 300.0
ALWAYS = ("memory.recall", "memory.remember", "files.save", FIND_TOOL)

# Tools of the running request; tools.find adds to it (one set per request).
active_tools: contextvars.ContextVar[set[str] | None] = contextvars.ContextVar(
    "active_tools", default=None
)

_STOP = """
    a an and are as at be but by can could do does for from get give go had has have he her
    him his how i if in into is it its just let me my no not now of on or our please she so
    some than that the their them then there these they this to too up us was we what when
    where which who why will with would you your yours tell show want need make like know
    about any all also one ok okay hey hi hello thanks thank
    der die das ein eine einen einem einer und oder aber ist sind bin bist war mit für von
    zu zum zur im in am an auf aus bei nach über unter vor wie was wer wo wann warum ich du
    er sie es wir ihr mir mich dir dich mein meine dein deine bitte kannst kann könntest
    mal noch schon auch nicht kein keine den dem des ja nein gib zeig sag
"""
STOP_WORDS = frozenset(_STOP.split())
WORD = re.compile(r"[^\W_]+", re.UNICODE)
SUFFIXES = ("ings", "ing", "ers", "er", "ed", "es", "en", "s", "e")


def stem(word: str) -> str:
    """A rough stem, enough to match "reminders" with "remind" or "scanning" with "scan"."""
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "aeiou":
        word = word[:-1]
    return word


def words(text: str) -> list[str]:
    return [stem(w) for w in WORD.findall(text.lower()) if w not in STOP_WORDS and len(w) > 1]


@dataclass
class _Entry:
    tool: Tool
    words: dict[str, float]  # word -> weight in this tool's text
    vector: Any = None


@dataclass
class Selection:
    names: list[str]
    modules: list[str] = field(default_factory=list)  # chosen by score, best first


class ToolRouter:
    def __init__(
        self,
        dispatcher: Dispatcher,
        config: AgentConfig,
        describe: Callable[[str], tuple[str, list[str]]] | None = None,
        embedder: Any = None,
    ) -> None:
        """``describe(module)`` gives a module's description and keywords."""
        self.dispatcher = dispatcher
        self.config = config
        self.describe = describe or (lambda module: ("", []))
        self.embedder = embedder
        self._signature: tuple[str, ...] = ()
        self._entries: dict[str, _Entry] = {}
        self._idf: dict[str, float] = {}
        self._modules: dict[str, dict[str, float]] = {}  # module -> word weights
        self._module_idf: dict[str, float] = {}
        self._vectors: dict[str, Any] = {}  # tool text -> vector, kept across rebuilds
        self._embed_failed_at: float | None = None  # retried after EMBED_RETRY seconds
        self._log = get_logger("toolroute")

    # -- index ---------------------------------------------------------------------------

    def _text(self, tool: Tool) -> str:
        description, keywords = self.describe(tool.module)
        params = " ".join(tool.args_model.model_fields)
        parts = [tool.module, tool.name.replace("_", " "), description, *keywords]
        return " ".join([*parts, tool.description, params])

    def _weights(self, tool: Tool) -> dict[str, float]:
        """Names and keywords count more than the words of a long description."""
        description, keywords = self.describe(tool.module)
        weights: dict[str, float] = {}
        parts: list[tuple[str, float]] = [
            (tool.module, 3.0),
            (tool.name.replace("_", " "), 3.0),
            (" ".join(keywords), 2.0),
            (description, 1.0),
            (tool.description.split("\n\n")[0], 1.5),
            (tool.description, 0.5),
            (" ".join(tool.args_model.model_fields).replace("_", " "), 0.5),
        ]
        for text, weight in parts:
            for word in words(text):
                weights[word] = max(weights.get(word, 0.0), weight)
        return weights

    def refresh(self) -> None:
        """Re-index if tools were added or removed (cheap when nothing changed)."""
        self._rebuild()

    def _rebuild(self) -> None:
        tools = self.dispatcher.tools()
        signature = tuple(t.qualified_name for t in tools)
        if signature == self._signature:
            return
        self._signature = signature
        self._entries = {t.qualified_name: _Entry(t, self._weights(t)) for t in tools}
        counts: dict[str, int] = {}
        for entry in self._entries.values():
            for word in entry.words:
                counts[word] = counts.get(word, 0) + 1
        total = max(1, len(self._entries))
        self._idf = {w: math.log(1 + total / c) for w, c in counts.items()}
        self._modules = {}
        for module in sorted({t.module for t in tools}):
            description, keywords = self.describe(module)
            weights: dict[str, float] = {}
            for text, weight in ((module, 3.0), (" ".join(keywords), 3.0), (description, 1.0)):
                for word in words(text):
                    weights[word] = max(weights.get(word, 0.0), weight)
            self._modules[module] = weights
        module_counts: dict[str, int] = {}
        for weights in self._modules.values():
            for word in weights:
                module_counts[word] = module_counts.get(word, 0) + 1
        total = max(1, len(self._modules))
        self._module_idf = {w: math.log(1 + total / c) for w, c in module_counts.items()}
        find = self.dispatcher.get(FIND_TOOL)
        if find is not None:
            find.description = self._find_description()

    def _find_description(self) -> str:
        modules = sorted({e.tool.module for e in self._entries.values()} - {"tools"})
        return (
            "Find more tools when none of the tools you see fits what the user wants. "
            "Describe the need in a few words; the tools found can be used right after. "
            "Areas: " + ", ".join(modules) + "."
        )

    @property
    def _embedding(self) -> bool:
        """Whether to use the embedder now (not while it is failing; retried later)."""
        if self.embedder is None:
            return False
        failed = self._embed_failed_at
        return failed is None or time.monotonic() - failed > EMBED_RETRY

    def _embed_failure(self, exc: Exception) -> None:
        self._embed_failed_at = time.monotonic()
        self._log.warning("tool routing without embeddings for now", error=str(exc))

    async def warm(self) -> None:
        """Index and embed the tools now, so the first request does not wait for it."""
        try:
            self._rebuild()
            await self._embed_all()
        except Exception:  # the first request tries again
            self._log.exception("tool index not prepared")

    async def _embed_all(self) -> None:
        if not self._embedding:
            return
        missing = [e for e in self._entries.values() if self._text(e.tool) not in self._vectors]
        if missing:
            texts = [self._text(e.tool) for e in missing]
            try:
                vectors = await self.embedder.embed(texts)
            except Exception as exc:  # the embedding model is down: words only
                self._embed_failure(exc)
                return
            self._embed_failed_at = None
            self._vectors.update(zip(texts, vectors, strict=True))
        for entry in self._entries.values():
            entry.vector = self._vectors.get(self._text(entry.tool))

    # -- scoring -------------------------------------------------------------------------

    async def scores(self, request: str) -> dict[str, float]:
        """A score per tool: matching words (rarer words count more), plus meaning."""
        self._rebuild()
        query = set(words(request))
        result: dict[str, float] = {}
        for name, entry in self._entries.items():
            result[name] = sum(
                entry.words[w] * self._idf.get(w, 0.0) for w in query if w in entry.words
            )
        top = max(result.values(), default=0.0)
        if top > 0:
            result = {n: s / top for n, s in result.items()}
        await self._embed_all()
        if self._embedding and query:
            try:
                [vector] = await self.embedder.embed([request])
            except Exception as exc:
                self._embed_failure(exc)
                return result
            for name, entry in self._entries.items():
                if entry.vector is not None:
                    similarity = float(entry.vector @ vector)
                    result[name] += max(0.0, similarity - 0.1)
        return result

    def module_scores(self, request: str) -> dict[str, float]:
        """How well each module as a whole (name, keywords, description) fits, 0 to 1."""
        self._rebuild()
        query = set(words(request))
        result = {
            module: sum(weights[w] * self._module_idf.get(w, 0.0) for w in query if w in weights)
            for module, weights in self._modules.items()
        }
        top = max(result.values(), default=0.0)
        return {m: s / top for m, s in result.items()} if top > 0 else result

    async def select(self, request: str, history: Iterable[Message] = ()) -> Selection:
        """The tools to send for this request (names, sorted)."""
        self._rebuild()
        if not self.config.tool_routing:
            return Selection(sorted(self._entries))
        chosen: list[str] = []

        def add(names: Iterable[str]) -> None:
            for name in names:
                if name in self._entries and name not in chosen:
                    chosen.append(name)

        def module_tools(module: str, order: dict[str, float] | None = None) -> list[str]:
            names = [n for n, e in self._entries.items() if e.tool.module == module]
            return sorted(names, key=lambda n: -(order or {}).get(n, 0.0))

        lowered = request.lower()
        add(n for n in self._entries if n.lower() in lowered)  # named outright
        add(ALWAYS)
        modules = {e.tool.module for e in self._entries.values()}
        named = [m for m in sorted(modules) if re.search(rf"\b{re.escape(m)}\b", lowered)]
        recent = self._recent_modules(history)
        scores = await self.scores(request)
        best = self.module_scores(request)
        for name, score in scores.items():
            module = self._entries[name].tool.module
            best[module] = best.get(module, 0.0) + score / 2
        top = max(best.values(), default=0.0)
        floor = max(0.25, top * 0.4)  # only modules close to the best fit
        ranked = [m for m in sorted(best, key=lambda m: -best[m]) if best[m] >= floor]
        picked = ranked[: self.config.max_tool_modules]
        for module in [*named, *recent, *picked]:
            remaining = self.config.max_tools - len(chosen)
            if remaining <= 0:
                break
            add(module_tools(module, scores)[:remaining])
        return Selection(sorted(chosen), picked)

    @staticmethod
    def _recent_modules(history: Iterable[Message], turns: int = 6) -> list[str]:
        modules: list[str] = []
        for message in reversed(list(history)[-turns * 3 :]):
            for call in message.tool_calls:
                module = call.name.split(".", 1)[0]
                if module not in modules:
                    modules.append(module)
        return modules[:3]

    # -- the find tool -------------------------------------------------------------------

    def find_tool(self) -> Tool:
        async def find(need: str, limit: int = 12) -> list[dict[str, str]]:
            """Find more tools for a need (filled in from the loaded modules)."""
            scores = await self.scores(need)
            ranked = sorted((s, n) for n, s in scores.items() if s > 0 and n != FIND_TOOL)
            found = [n for _, n in reversed(ranked)][: max(1, min(limit, 30))]
            active = active_tools.get()
            if active is not None:
                active.update(found)
            return [
                {"tool": n, "does": self._entries[n].tool.description.split("\n")[0][:160]}
                for n in found
            ]

        return make_tool("tools", find, name="find")
