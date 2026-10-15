"""Event bus. Modules never call each other directly; they publish and subscribe to events
such as ``task.requested``, ``web.fetched`` or ``message.received``."""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from starlight.core.logging import get_logger

type Handler = Callable[[Event], Awaitable[None] | None]


@dataclass(frozen=True)
class Event:
    name: str
    data: dict[str, Any] = field(default_factory=dict)
    source: str = "core"
    timestamp: float = field(default_factory=time.time)


@dataclass
class Subscription:
    pattern: str
    handler: Handler
    owner: str | None
    background: bool = False  # run in its own task: a slow handler never holds up publish


class EventBus:
    def __init__(self) -> None:
        self._subscriptions: list[Subscription] = []
        self._pending: set[asyncio.Task[None]] = set()
        self._log = get_logger("events")

    def subscribe(
        self,
        pattern: str,
        handler: Handler,
        owner: str | None = None,
        background: bool = False,
    ) -> Subscription:
        """Subscribe to events whose name matches ``pattern`` (glob, e.g. ``web.*``).
        ``background`` handlers (sending a message, running an automation) get their own
        task, so ``publish`` returns without waiting for them."""
        sub = Subscription(pattern, handler, owner, background)
        self._subscriptions.append(sub)
        return sub

    def unsubscribe_owner(self, owner: str) -> None:
        self._subscriptions = [s for s in self._subscriptions if s.owner != owner]

    async def publish(
        self, name: str, data: dict[str, Any] | None = None, source: str = "core"
    ) -> Event:
        """Deliver an event to every matching handler. A failing handler is logged and
        skipped; it never affects the publisher or the other handlers."""
        event = Event(name, data or {}, source)
        for sub in list(self._subscriptions):
            if not fnmatch.fnmatchcase(name, sub.pattern):
                continue
            if sub.background:
                task = asyncio.create_task(self._call(sub, event), name=f"event-{name}")
                self._pending.add(task)
                task.add_done_callback(self._pending.discard)
            else:
                await self._call(sub, event)
        return event

    async def _call(self, sub: Subscription, event: Event) -> None:
        try:
            result = sub.handler(event)
            if inspect.isawaitable(result):
                await result
        except Exception:
            self._log.exception("event handler failed", event_name=event.name, owner=sub.owner)

    async def settle(self, timeout: float = 30.0) -> None:
        """Wait for background handlers still running (used at shutdown and in tests)."""
        if self._pending:
            await asyncio.wait(list(self._pending), timeout=timeout)
