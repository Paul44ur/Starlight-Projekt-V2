"""Your day at a glance: today's events, what's due, reminders, the weather, a few
headlines, new mail and anything that needs attention (expiring certificates), in one short
message.

It uses the other modules' tools through the dispatcher, with their normal permission
rules; only read-only tools are used. A part whose module is off or not set up is left out.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool

SECTIONS = ("calendar", "todo", "reminders", "weather", "news", "mail", "certs")


class Config(BaseModel):
    sections: list[str] = Field(default_factory=lambda: list(SECTIONS))
    place: str = ""
    news_topic: str = ""
    news_count: int = Field(default=3, ge=1, le=20)
    mail_count: int = Field(default=5, ge=1, le=50)


class BriefingModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self._parts: dict[str, Callable[[], Awaitable[list[str]]]] = {
            "calendar": self._calendar,
            "todo": self._todo,
            "reminders": self._reminders,
            "weather": self._weather,
            "news": self._news,
            "mail": self._mail,
            "certs": self._certs,
        }

    async def _use(self, tool_name: str, **args: Any) -> Any:
        """A tool's output, or None if its module is off, not set up or failing."""
        if not self.ctx.has_tool(tool_name):
            return None
        result = await self.ctx.call(tool_name, args)
        return result.output if result.ok else None

    # -- sections (each returns lines; empty means nothing to say) ------------------------

    async def _calendar(self) -> list[str]:
        out = await self._use("calendar.agenda", days=1)
        if not out:
            return []
        if not out["events"]:
            return ["Calendar: nothing planned today."]
        lines = ["Today:"]
        for event in out["events"]:
            when = "all day" if event.get("all_day") else event["start"][-5:]
            place = f" @ {event['location']}" if event.get("location") else ""
            lines.append(f"  • {when} {event['title']}{place}")
        return lines

    async def _todo(self) -> list[str]:
        out = await self._use("todo.due", days=0)
        if not out or not out["count"]:
            return []
        lines = [f"To do ({out['count']}):"]
        for task in out["overdue"]:
            lines.append(f"  • {task['text']} (overdue since {task['due'][:10]})")
        for task in out["due"]:
            lines.append(f"  • {task['text']}")
        return lines

    async def _reminders(self) -> list[str]:
        out = await self._use("reminders.list")
        if not out:
            return []
        today = datetime.now().strftime("%Y-%m-%d")
        todays = [r for r in out["reminders"] if (r.get("next") or "").startswith(today)]
        if not todays:
            return []
        return ["Reminders today:"] + [f"  • {r['next'][-5:]} {r['text']}" for r in todays]

    async def _weather(self) -> list[str]:
        out = await self._use("weather.now", place=self.config.place)
        if not out:
            return []
        line = f"Weather in {out['place']}: {out['summary']}"
        forecast = await self._use("weather.forecast", place=self.config.place, days=1)
        if forecast and forecast.get("days"):
            day = forecast["days"][0]
            line += f"; today {day['low']} to {day['high']}, {day['rain_chance']} chance of rain"
        return [line]

    async def _news(self) -> list[str]:
        out = await self._use(
            "news.headlines", topic=self.config.news_topic, limit=self.config.news_count
        )
        if not out or not out.get("headlines"):
            return []
        return ["Headlines:"] + [f"  • {h['title']}" for h in out["headlines"]]

    async def _mail(self) -> list[str]:
        out = await self._use("mail.inbox", limit=self.config.mail_count)
        if not out or not out.get("messages"):
            return []
        lines = [f"Latest mail ({len(out['messages'])}):"]
        lines += [f"  • {m['from']}: {m['subject']}" for m in out["messages"]]
        return lines

    async def _certs(self) -> list[str]:
        out = await self._use("certs.expiring")
        if not out:
            return []
        lines = [_cert_line(c) for c in out.get("expiring", [])]
        lines += [f"⚠ {c.get('host')} is unreachable" for c in out.get("unreachable", [])]
        return lines

    # -- tools ---------------------------------------------------------------------------

    @tool
    async def briefing(self, sections: str = "") -> dict[str, Any]:
        """A short overview of the day: calendar, to-dos, reminders, weather, headlines,
        new mail and alerts. `sections` limits it, e.g. "calendar, weather". Show the
        `text` to the user as it is."""
        wanted = [s.strip().lower() for s in sections.split(",") if s.strip()]
        names = wanted or self.config.sections
        unknown = [n for n in names if n not in self._parts]
        if unknown:
            raise ToolError(f"unknown section(s) {', '.join(unknown)}; use {', '.join(SECTIONS)}")
        results = await asyncio.gather(
            *(self._safe(name) for name in names), return_exceptions=False
        )
        now = datetime.now()
        header = f"Good {_daytime(now.hour)}! {now:%A, %d %B %Y}"
        blocks = [block for block in results if block]
        text = "\n\n".join(["\n".join(b) for b in [[header], *blocks]])
        return {"text": text, "sections": [n for n, b in zip(names, results, strict=True) if b]}

    async def _safe(self, name: str) -> list[str]:
        try:
            return await self._parts[name]()
        except Exception:
            self.ctx.logger.exception("briefing section failed", section=name)
            return []

    @tool
    async def send(self, sections: str = "") -> dict[str, Any]:
        """Make the briefing and deliver it on every channel (desktop, web chat, phone)."""
        made = await self.briefing(sections)
        await self.ctx.deliver(made["text"], title="Briefing")
        return {"sent": True, "sections": made["sections"]}


def _cert_line(cert: dict[str, Any]) -> str:
    host, days = cert.get("host"), cert["days_left"]
    if days < 0:  # certs.expiring also lists the ones that have already run out
        return f"⚠ Certificate for {host} expired {-days} day{'s' if days != -1 else ''} ago"
    when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
    return f"⚠ Certificate for {host} expires {when}"


def _daytime(hour: int) -> str:
    return "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
