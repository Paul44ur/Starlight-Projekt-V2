"""What the model should know before every request: the date and time (a local model
has no clock), where files it saves end up, and who it is talking to (the profile)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def local_now(timezone: str = "") -> dt.datetime:
    """Now, in the profile's time zone when it names a valid one, else the PC's."""
    if timezone:
        try:
            return dt.datetime.now(ZoneInfo(timezone.strip()))
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return dt.datetime.now().astimezone()


def describe(now: dt.datetime, output_dir: Path | None, profile: dict[str, str]) -> str:
    offset = now.strftime("%z")
    zone = f"UTC{offset[:3]}:{offset[3:]}" if offset else ""
    name = getattr(now.tzinfo, "key", None) or now.tzname() or ""  # the zone actually used
    when = f"{now:%A}, {now.day} {now:%B %Y}, {now:%H:%M}"
    lines = [f"Now: {when} ({', '.join(p for p in (name, zone) if p)})."]
    if output_dir is not None:
        lines.append(f"Files you save with files.save go to: {output_dir}")
    about = [f"{key}: {value}" for key, value in profile.items() if value]
    if about:
        lines.append("About the user (their profile): " + "; ".join(about) + ".")
    return "\n".join(lines)


class Situation:
    """Agent enricher: one short system message with the above."""

    def __init__(
        self,
        output_dir: Callable[[], Path | None],
        profile: Callable[[], dict[str, str]],
        clock: Callable[[str], dt.datetime] = local_now,
    ) -> None:
        self.output_dir = output_dir
        self.profile = profile
        self.clock = clock

    async def enrich(self, request: str) -> str | None:
        try:
            profile = self.profile()
        except Exception:  # an unreadable profile must not cost the date and time
            profile = {}
        return describe(self.clock(profile.get("timezone", "")), self.output_dir(), profile)
