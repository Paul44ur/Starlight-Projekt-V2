"""Event bus. Modules never call each other directly; they publish and subscribe to events
such as ``task.requested``, ``web.fetched`` or ``message.received``."""

from __future__ import annotations

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


class EventBus:
    def __init__(self) -> None:
        self._subscriptions: list[Subscription] = []
        self._log = get_logger("events")

    def subscribe(self, pattern: str, handler: Handler, owner: str | None = None) -> Subscription:
        """Subscribe to events whose name matches ``pattern`` (glob, e.g. ``web.*``)."""
        sub = Subscription(pattern, handler, owner)
        self._subscriptions.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        if sub in self._subscriptions:
            self._subscriptions.remove(sub)

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
            try:
                result = sub.handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                self._log.exception("event handler failed", event_name=name, owner=sub.owner)
        return event
