"""The web API of the Today page (starlight/web/static/today.html). Read only.

    GET /api/today          {date, cards}: today's date and the cards that exist right now
                            (the module is on; "approvals" only while something waits)
    GET /api/today/{card}   one card's data; ?fresh=1 skips the kept answer.
                            approvals, agenda, todo, reminders, weather. 404: no such card,
                            or its module is off. 503: the card failed, with a short reason.

One route per card, so that a slow card (the weather) never holds back the others.

How a card gets its data:
- approvals, todo, reminders only read local files: the module's own method is called.
- agenda and weather may use the network (a subscribed calendar, the weather service): they go
  through the dispatcher like the daily briefing does, so the kill switch, the safety limits
  and the activity log apply. The answer is kept in memory (agenda 5 minutes, weather 30).
  With the kill switch on, no tool runs and the card says "Starlight is stopped".

The answers are data (times, titles, numbers); the page writes the words around them.
Nothing here changes anything.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import date
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from starlight.core.killswitch import KillSwitchEngaged
from starlight.core.modules import ModuleStatus

STOPPED = "Starlight is stopped"
# card -> the module it needs
CARDS: dict[str, str] = {
    "approvals": "approvals",
    "agenda": "calendar",
    "todo": "todo",
    "reminders": "reminders",
    "weather": "weather",
}
KEEP_SECONDS = {"agenda": 5 * 60, "weather": 30 * 60}  # the cards that may use the network


def _clock(stamp: str) -> str:
    """ "HH:MM" from "YYYY-MM-DD HH:MM" (what calendar.agenda and reminders.list give)."""
    return stamp[-5:]


def add_today_api(
    app: FastAPI, kernel: Callable[[], Any], viewer: Callable[[Request], Any]
) -> None:
    kept: dict[str, tuple[float, str, dict[str, Any]]] = {}  # card -> (when, key, answer)
    locks = {card: asyncio.Lock() for card in KEEP_SECONDS}

    def instance(module: str) -> Any:
        record = kernel().modules.records.get(module)
        if record is None or record.instance is None or record.status is not ModuleStatus.LOADED:
            return None
        return record.instance

    def place() -> str:
        """Where the weather is for: the briefing's place, else the weather module's own."""
        briefing = instance("briefing")
        return str(getattr(getattr(briefing, "config", None), "place", "") or "").strip()

    def weather_known() -> bool:
        """The weather module has a place to ask about, or it would only say "which place?"."""
        module = instance("weather")
        if module is None:
            return False
        if place() or str(getattr(getattr(module, "config", None), "default_place", "")).strip():
            return True
        try:
            return bool(str(module.ctx.profile().get("home", "")).strip())
        except Exception:
            return False

    def waiting() -> int:
        queue = instance("approvals")
        return int(queue.pending()["count"]) if queue is not None else 0

    def exists(card: str) -> bool:
        if instance(CARDS[card]) is None:
            return False
        if card == "weather":
            return weather_known()
        return waiting() > 0 if card == "approvals" else True

    async def tool(name: str, **args: Any) -> Any:
        """One tool's output through the dispatcher; a failure becomes a short reason."""
        k = kernel()
        if k.dispatcher.get(name) is None:
            raise HTTPException(status_code=404, detail="that module is off")
        try:
            result = await k.dispatcher.call(name, args)
        except KillSwitchEngaged as exc:
            raise HTTPException(status_code=503, detail=STOPPED) from exc
        if not result.ok:
            raise HTTPException(status_code=503, detail=str(result.error or "failed")[:200])
        return result.output

    # -- the cards -----------------------------------------------------------------------

    def approvals_card() -> dict[str, Any]:
        queue = instance("approvals")
        return {"count": int(queue.pending()["count"]) if queue is not None else 0}

    def todo_card() -> dict[str, Any]:
        out = instance("todo").due(days=0)

        def view(task: dict[str, Any]) -> dict[str, Any]:
            due = str(task.get("due") or "")
            return {
                "text": task["text"],
                "due": due[:10],
                "time": due[11:16] if len(due) > 10 else "",
            }

        return {"overdue": [view(t) for t in out["overdue"]], "due": [view(t) for t in out["due"]]}

    def reminders_card() -> dict[str, Any]:
        out = instance("reminders").list_reminders()
        today = date.today().isoformat()
        todays = [r for r in out["reminders"] if str(r.get("next") or "")[:10] == today]
        shown = [{"time": _clock(str(r["next"])), "text": r["text"]} for r in todays]
        return {"reminders": sorted(shown, key=lambda r: r["time"])}

    async def agenda_card() -> dict[str, Any]:
        out = await tool("calendar.agenda", days=1)
        events = [
            {
                "time": "" if e.get("all_day") else _clock(str(e.get("start") or "")),
                "all_day": bool(e.get("all_day")),
                "title": e["title"],
                "location": e.get("location") or "",
            }
            for e in out["events"]
        ]
        return {"events": events}

    async def weather_card() -> dict[str, Any]:
        spot = place()
        now = await tool("weather.now", place=spot)
        card: dict[str, Any] = {
            "place": now["place"],
            "conditions": now.get("conditions") or "",
            "temperature": now.get("temperature") or "",
            "feels_like": now.get("feels_like") or "",
            "low": None,
            "high": None,
            "rain_chance": None,
        }
        try:  # (without a forecast the weather is still worth showing, as in the briefing)
            forecast = await tool("weather.forecast", place=spot, days=1)
        except HTTPException:
            forecast = None
        if forecast and forecast.get("days"):
            day = forecast["days"][0]
            card |= {"low": day["low"], "high": day["high"], "rain_chance": day["rain_chance"]}
        return card

    async def network_card(card: str, fresh: bool) -> dict[str, Any]:
        if kernel().kill_switch.engaged:
            raise HTTPException(status_code=503, detail=STOPPED)  # no tool runs
        # (a new day or a new place is a new answer)
        key = f"{date.today().isoformat()}|{place()}"
        async with locks[card]:  # two requests at once make one call
            found = kept.get(card)
            if (
                not fresh
                and found
                and found[1] == key
                and time.monotonic() - found[0] < KEEP_SECONDS[card]
            ):
                return found[2]
            answer = await (agenda_card() if card == "agenda" else weather_card())
            kept[card] = (time.monotonic(), key, answer)
            return answer

    # -- routes --------------------------------------------------------------------------

    @app.get("/api/today")
    def today(request: Request) -> dict[str, Any]:
        viewer(request)
        return {
            "date": date.today().isoformat(),
            "cards": [card for card in CARDS if exists(card)],
        }

    @app.get("/api/today/{card}")
    async def today_card(card: str, request: Request, fresh: int = 0) -> dict[str, Any]:
        viewer(request)
        if card not in CARDS:
            raise HTTPException(status_code=404, detail="no such card")
        if instance(CARDS[card]) is None:
            raise HTTPException(status_code=404, detail=f"the {CARDS[card]} module is off")
        local: dict[str, Callable[[], dict[str, Any]]] = {
            "approvals": approvals_card,
            "todo": todo_card,
            "reminders": reminders_card,
        }
        try:
            if card in KEEP_SECONDS:
                return await network_card(card, bool(fresh))
            return await run_in_threadpool(local[card])
        except HTTPException:
            raise
        except Exception as exc:  # (a short reason for the card)
            reason = f"{type(exc).__name__}: {exc}"
            raise HTTPException(status_code=503, detail=reason[:200]) from exc
