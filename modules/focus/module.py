"""Focus mode from the chat: "focus for 90 minutes", "don't disturb me until 15:00",
"I'm done, what did I miss?".

While focus is on, Starlight holds back what it would otherwise tell you (reminders,
watch hits, new mail, automation messages). Urgent things still come through: a calendar
event starting within five minutes, a server going down, a new device on your network.
When focus ends, you get one summary of everything that was held. The core does the
holding (``starlight/core/focus.py``); this module only switches it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.focus import finish
from starlight.core.schedule import parse_when


class FocusModule(Module):
    async def setup(self, ctx: ModuleContext) -> None:
        self.focus = ctx.focus

    def _now(self) -> datetime:
        return datetime.now()

    @tool
    async def start(self, minutes: int = 0, until: str = "") -> dict[str, Any]:
        """Turn on focus mode: hold back messages until the user is done. Give `minutes`
        (e.g. 90) or `until` (e.g. "15:00", "in 2h"); with neither it lasts until
        focus.stop."""
        now = self._now()
        end: datetime | None = None
        if until.strip():
            end = parse_when(until, now)
        elif minutes > 0:
            end = datetime.fromtimestamp(now.timestamp() + min(minutes, 24 * 60) * 60)
        if end is not None and end <= now:
            raise ToolError("that time has already passed")
        self.focus.start(end.timestamp() if end else None)
        await self.ctx.publish("focus.started", {"until": end.isoformat() if end else None})
        return {
            "focus": True,
            "until": end.strftime("%H:%M") if end else "until you stop it",
            "note": "urgent things (an event about to start, a server down) still come through",
        }

    @tool
    async def stop(self) -> dict[str, Any]:
        """Turn focus mode off and deliver what was held back."""
        was_on = self.focus.read().on
        text = await finish(self.focus, self.ctx.services.deliver or _quiet)
        await self.ctx.publish("focus.ended", {})
        return {"focus": False, "was_on": was_on, "held": text or "nothing was held"}

    @tool
    def status(self) -> dict[str, Any]:
        """Whether focus mode is on, until when, and how many messages are held."""
        state = self.focus.read()
        on = self.focus.active()
        until = datetime.fromtimestamp(state.until).strftime("%H:%M") if state.until else None
        return {"focus": on, "until": until if on else None, "held": len(state.held)}


async def _quiet(*args: Any) -> None:
    return None
