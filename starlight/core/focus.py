"""Focus mode: hold messages back while you concentrate.

While focus is on, messages Starlight would deliver (reminders, watch hits, new mail,
briefings, automation messages) are kept instead of shown. Urgent ones still come through:
a calendar event about to start, a server going down, a new device on your network or a
newly paired phone. When focus ends - at the time you set, or when you stop it - one
digest of what was held is delivered.

The state lives in ``data/focus.json``, so every Starlight process (web chat, terminal)
sees the same focus.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlight.core.fsutil import write_atomic

MAX_HELD = 200


@dataclass
class FocusState:
    on: bool = False
    until: float | None = None  # end time; None: until stopped
    started: float | None = None
    held: list[dict[str, Any]] = field(default_factory=list)


class Focus:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.clock = clock

    def read(self) -> FocusState:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return FocusState()
        if not isinstance(data, dict):
            return FocusState()
        held = data.get("held")
        return FocusState(
            on=bool(data.get("on")),
            until=data.get("until"),
            started=data.get("started"),
            held=held if isinstance(held, list) else [],
        )

    def _write(self, state: FocusState) -> None:
        write_atomic(self.path, json.dumps(state.__dict__, ensure_ascii=False))

    def active(self) -> bool:
        """On, and not past its end time."""
        state = self.read()
        return state.on and (state.until is None or self.clock() < state.until)

    def expired(self) -> bool:
        """On, but its end time has passed: time for the digest."""
        state = self.read()
        return state.on and state.until is not None and self.clock() >= state.until

    def start(self, until: float | None) -> FocusState:
        state = self.read()  # keep anything already held
        if not state.on:
            state.started = self.clock()
        state.on, state.until = True, until
        self._write(state)
        return state

    def hold(self, text: str, title: str, source: str) -> None:
        state = self.read()
        state.held.append({"text": text, "title": title, "source": source, "at": self.clock()})
        del state.held[:-MAX_HELD]
        self._write(state)

    def stop(self) -> list[dict[str, Any]]:
        """Turn focus off; returns the held messages (and forgets them)."""
        state = self.read()
        held = state.held
        self._write(FocusState())
        return held


def digest(held: list[dict[str, Any]]) -> str:
    """One message for everything held: a count by kind, then each one."""
    if not held:
        return ""
    kinds: dict[str, int] = {}
    for item in held:
        kind = item.get("title") or item.get("source") or "message"
        kinds[kind] = kinds.get(kind, 0) + 1
    counts = ", ".join(f"{n} {k.lower()}" for k, n in kinds.items())
    lines = [f"While you were focused: {counts}."]
    for item in held[:20]:
        lines.append(f"- {item['text']}")
    if len(held) > 20:
        lines.append(f"- ... and {len(held) - 20} more")
    return "\n".join(lines)


async def finish(focus: Focus, deliver: Callable[..., Awaitable[None]]) -> str:
    """End focus and deliver the digest (urgent, so it is never held itself)."""
    text = digest(focus.stop())
    if text:
        await deliver(text, "Focus", "focus", True)
    return text
