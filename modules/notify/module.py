"""Show a notification on this PC's desktop, so Starlight can get your attention.

A normal `notify` pops a toast; `alert` is for something more urgent. On a machine with no
desktop (a headless server) it says so instead of failing oddly.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel

from starlight.core import Event, Module, ModuleContext, ToolError, tool

from .backends import Backend, NotifyUnavailable, detect

MAX_LEN = 1000


class Config(BaseModel):
    app_name: str = "Starlight"  # the sender shown on a Windows toast / by notify-send
    default_title: str = "Starlight"
    on_deliver: bool = True  # also pop up reminders, alerts and briefings Starlight sends you


class NotifyModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.backend: Backend | None = detect(self.config.app_name)
        if self.config.on_deliver and self.backend is not None:
            ctx.bus.subscribe("deliver", self._on_deliver, owner=ctx.name, background=True)
        ctx.logger.info("ready", backend=self.backend.name if self.backend else None)

    async def _on_deliver(self, event: Event) -> None:
        if self.backend is None:
            return
        title = str(event.data.get("title") or self.config.default_title)[:MAX_LEN]
        text = str(event.data.get("text", ""))[:MAX_LEN]
        try:
            await asyncio.to_thread(self.backend.send, title, text, False)
        except NotifyUnavailable as exc:
            self.ctx.logger.warning("desktop notification failed", error=str(exc))

    def _show(self, title: str, message: str, urgent: bool) -> dict[str, Any]:
        message = (message or "").strip()
        if not message:
            raise ToolError("give something to say in the notification")
        if len(message) > MAX_LEN or len(title) > MAX_LEN:
            raise ToolError(f"keep the text under {MAX_LEN} characters")
        if self.backend is None:
            raise ToolError(
                "this computer has no desktop notifications available "
                "(on Linux, install 'notify-send' from the libnotify package)"
            )
        try:
            self.backend.send(title.strip() or self.config.default_title, message, urgent)
        except NotifyUnavailable as exc:
            raise ToolError(f"could not show the notification: {exc}") from exc
        self.ctx.logger.info("shown", urgent=urgent)
        return {
            "shown": True,
            "title": title.strip() or self.config.default_title,
            "via": self.backend.name,
        }

    @tool
    def notify(self, message: str, title: str = "") -> dict[str, Any]:
        """Show a desktop notification. Use this to tell the user something when they may
        not be looking at the chat."""
        return self._show(title, message, urgent=False)

    @tool
    def alert(self, message: str, title: str = "") -> dict[str, Any]:
        """Show an urgent desktop notification (for something that needs attention now)."""
        return self._show(title or "Alert", message, urgent=True)
