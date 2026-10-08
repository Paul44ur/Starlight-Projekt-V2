"""Watch web pages for you.

    "Tell me when this page changes"            when="changes"
    "Tell me when it costs less than 50 euros"  when="price below 50"
    "... when it costs more than 200"           when="price above 200"
    "Tell me when 'in stock' appears"           when="contains in stock"
    "Tell me when 'sold out' is gone"           when="gone sold out"

Each watch is checked on its own interval (at least every 15 minutes), only by the
primary Starlight process, through the shared polite web client (robots.txt, rate limits,
response size cap). ``part`` (a CSS selector such as ``.price`` or ``#availability``)
limits a watch to one part of the page, which makes price and change watches reliable on
busy pages. Pages built entirely by JavaScript may show nothing to a plain fetch.

A hit is delivered on every open channel and published as ``watch.changed`` or
``watch.price``, so automations can act on it. Watches live in
``data/modules/watch/watches.json``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, Permission, ToolError, tool
from starlight.core.http import HttpError
from starlight.core.schedule import UNITS
from starlight.text import html_to_text

HISTORY = 50

CURRENCY = r"(?:€|\$|£|¥|EUR|USD|GBP|CHF|CAD|AUD|JPY|SEK|NOK|DKK|PLN|CZK|Fr\.)"
# With thousands separators (1.234,56 / 1,234.56), or plain digits (1299, 49,99). The first
# needs at least one group, or "1299" would stop after "129".
NUMBER = (
    r"\d{1,3}(?:[.,'\u00a0\u202f]\d{3})+(?:[.,]\d{1,2})?(?!\d)"
    r"|\d+(?:[.,]\d{1,2})?(?!\d)"
)
PRICE = re.compile(
    rf"(?:{CURRENCY}\s?(?P<a>{NUMBER}))|(?:(?P<b>{NUMBER})\s?(?:,-|\.-)?\s?{CURRENCY})"
)


def parse_number(raw: str) -> float | None:
    """'1.234,56' -> 1234.56, '1,234.56' -> 1234.56, '49,99' -> 49.99, '1.234' -> 1234."""
    text = re.sub(r"[\s'\u00a0\u202f]", "", raw)
    if not text:
        return None
    last_dot, last_comma = text.rfind("."), text.rfind(",")
    if last_dot >= 0 and last_comma >= 0:  # both: the later one is the decimal point
        decimal = "." if last_dot > last_comma else ","
        thousands = "," if decimal == "." else "."
        text = text.replace(thousands, "").replace(decimal, ".")
    elif last_dot >= 0 or last_comma >= 0:
        sep = "." if last_dot >= 0 else ","
        head, _, tail = text.rpartition(sep)
        if len(tail) == 3 and text.count(sep) >= 1 and head:  # 1.234 / 12,500: thousands
            text = text.replace(sep, "")
        else:
            text = head.replace(sep, "") + "." + tail
    try:
        return float(text)
    except ValueError:
        return None


def find_price(text: str) -> float | None:
    """The first price with a currency in the text."""
    for match in PRICE.finditer(text):
        value = parse_number(match.group("a") or match.group("b") or "")
        if value is not None:
            return value
    return None


def parse_every(text: str) -> int:
    """'1h', '30 min', 'every 2 hours', 'daily' -> seconds."""
    raw = text.strip().lower().removeprefix("every").strip()
    if raw in ("hourly", "hour", "an hour"):
        return 3600
    if raw in ("daily", "day", "a day"):
        return 86400
    match = re.fullmatch(r"(\d+(?:\.\d+)?)?\s*([a-z]+)", raw)
    if not match or match.group(2) not in UNITS:
        raise ToolError(f"how often? e.g. '30m', '2h' or 'daily', not '{text}'")
    return int(float(match.group(1) or 1) * UNITS[match.group(2)])


def parse_condition(when: str) -> tuple[str, Any]:
    """'changes' | 'price below 50' | 'price above 200' | 'contains X' | 'gone X'."""
    raw = " ".join(when.strip().split())
    low = raw.lower()
    if low in ("", "change", "changes", "changed", "any change"):
        return "changes", None
    price = re.fullmatch(
        r"(?:price\s+|costs?\s+)?(below|under|less than|<|above|over|more than|>)\s*(.+)", low
    )
    if price:
        limit = parse_number(re.sub(r"[^\d.,' ]", "", price.group(2)))
        if limit is None:
            raise ToolError(f"which price? '{when}' has no number")
        below = price.group(1) in ("below", "under", "less than", "<")
        return ("below" if below else "above"), limit
    for prefix, kind in (
        ("contains ", "contains"),
        ("appears ", "contains"),
        ("shows ", "contains"),
        ("gone ", "gone"),
        ("disappears ", "gone"),
        ("no longer contains ", "gone"),
        ("not contains ", "gone"),
    ):
        if low.startswith(prefix):
            needle = raw[len(prefix) :].strip().strip("'\"")
            if needle:
                return kind, needle
    raise ToolError(
        "when to tell you? 'changes', 'price below 50', 'price above 200', "
        "'contains in stock' or 'gone sold out'"
    )


class Config(BaseModel):
    check_interval: float = Field(default=60, ge=0)  # seconds; 0 turns the background check off
    min_every_minutes: int = Field(default=15, ge=1)
    max_watches: int = Field(default=50, ge=1)


class WatchModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.file = ctx.data_dir / "watches.json"
        if self.config.check_interval > 0:
            ctx.every(self.config.check_interval, self.check_due, "watch")

    # -- storage -------------------------------------------------------------------------

    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.file.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def _save(self, items: list[dict[str, Any]]) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.file)

    def _find(self, items: list[dict[str, Any]], name: str) -> dict[str, Any]:
        key = name.strip().lower()
        for item in items:
            if key in (item["name"].lower(), item["id"]):
                return item
        names = ", ".join(i["name"] for i in items) or "none yet"
        raise ToolError(f"no watch called '{name}' (watches: {names})")

    # -- fetching ------------------------------------------------------------------------

    async def _read(self, url: str, part: str) -> tuple[str, str]:
        """(title, text) of the page, or of the part the selector picks."""
        try:
            response = await self.ctx.http.get(url, use_cache=False)
        except HttpError as exc:
            raise ToolError(f"could not read {url}: {exc}") from exc
        if not response.ok:
            raise ToolError(f"{url} answered with HTTP {response.status}")
        page = html_to_text(response.text, url)
        if not part:
            return page.title, page.text
        try:
            found = BeautifulSoup(response.text, "html.parser").select(part)
        except Exception as exc:  # a broken selector
            raise ToolError(f"'{part}' is not a CSS selector bs4 understands: {exc}") from exc
        if not found:
            raise ToolError(f"nothing on the page matches '{part}'")
        return page.title, "\n".join(e.get_text(" ", strip=True) for e in found)

    def _now(self) -> float:
        return time.time()

    async def _check(self, item: dict[str, Any], text: str | None = None) -> dict[str, Any]:
        """Check one watch (``text``: the page, already read), update it in place and
        return what was seen."""
        now = self._now()
        item["last_checked"] = datetime.fromtimestamp(now).isoformat(timespec="seconds")
        item["due"] = now + item["every_s"]
        if text is None:
            try:
                _, text = await self._read(item["url"], item.get("part", ""))
            except ToolError as exc:
                item["last_error"] = str(exc)
                return {"name": item["name"], "error": str(exc), "hit": False}
        item["last_error"] = None
        kind, value = item["kind"], item["value"]
        digest = hashlib.sha256(text.encode()).hexdigest()[:16]
        seen: dict[str, Any] = {"name": item["name"], "hit": False}
        message = ""
        if kind == "changes":
            before = item.get("hash")
            if before is not None and before != digest:
                seen["hit"] = True
                message = f"{item['name']} changed: {item['url']}"
            seen["excerpt"] = text[:300]
        elif kind in ("below", "above"):
            price = find_price(text)
            seen["price"] = price
            if price is None:
                item["last_error"] = "no price found on the page (try a 'part' selector)"
            else:
                met = price < value if kind == "below" else price > value
                if met and not item.get("met"):
                    seen["hit"] = True
                    word = "below" if kind == "below" else "above"
                    message = f"{item['name']} is now {price:g} ({word} {value:g}): {item['url']}"
                item["met"] = met
                if price != item.get("price"):
                    self._remember(item, now, price)
                item["price"] = price
        else:  # contains / gone
            present = value.lower() in text.lower()
            met = present if kind == "contains" else not present
            if met and item.get("met") is False:  # only a change counts, not the first look
                seen["hit"] = True
                verb = "now shows" if kind == "contains" else "no longer shows"
                message = f"{item['name']} {verb} '{value}': {item['url']}"
            item["met"] = met
            seen["present"] = present
        if kind == "changes" and digest != item.get("hash"):
            self._remember(item, now, text[:120])
        item["hash"] = digest
        if seen["hit"]:
            item["hits"] = item.get("hits", 0) + 1
            seen["message"] = message
        return seen

    @staticmethod
    def _remember(item: dict[str, Any], now: float, value: Any) -> None:
        history = item.setdefault("history", [])
        at = datetime.fromtimestamp(now).isoformat(timespec="minutes")
        history.append({"at": at, "value": value})
        del history[:-HISTORY]

    async def _announce(self, item: dict[str, Any], seen: dict[str, Any]) -> None:
        await self.ctx.deliver(seen["message"], title="Watch")
        event = "watch.price" if item["kind"] in ("below", "above") else "watch.changed"
        await self.ctx.publish(
            event,
            {
                "id": item["id"],
                "name": item["name"],
                "url": item["url"],
                "when": item["when"],
                "price": seen.get("price"),
                "message": seen["message"],
            },
        )

    async def check_due(self) -> list[dict[str, Any]]:
        """Check every watch that is due (the background loop)."""
        items = self._load()
        now = self._now()
        results: list[dict[str, Any]] = []
        due = [i for i in items if i.get("due", 0) <= now]
        for item in due:
            self.ctx.kill_switch.check()
            seen = await self._check(item)
            results.append(seen)
        if due:
            self._merge_save(due)
        for item, seen in zip(due, results, strict=True):
            if seen["hit"]:
                await self._announce(item, seen)
        return results

    def _merge_save(self, checked: list[dict[str, Any]]) -> None:
        """Save checked watches without losing ones added or removed meanwhile."""
        by_id = {i["id"]: i for i in checked}
        current = self._load()
        self._save([by_id.get(i["id"], i) for i in current])

    # -- tools ---------------------------------------------------------------------------

    @tool(permission=Permission.CONFIRM)
    async def add(
        self, url: str, when: str = "changes", every: str = "1h", name: str = "", part: str = ""
    ) -> dict[str, Any]:
        """Watch a web page and tell the user when something happens. `when`: "changes",
        "price below 50", "price above 200", "contains in stock" or "gone sold out".
        `every`: how often to look, e.g. "30m", "2h", "daily" (at least 15 minutes).
        `part`: optional CSS selector for the part to watch, e.g. ".price"."""
        url = url.strip()
        if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
            raise ToolError("give the full address of the page, starting with https://")
        kind, value = parse_condition(when)
        seconds = parse_every(every)
        minimum = self.config.min_every_minutes * 60
        if seconds < minimum:
            raise ToolError(
                f"that is too often for someone else's site; at least every "
                f"{self.config.min_every_minutes} minutes"
            )
        items = self._load()
        if len(items) >= self.config.max_watches:
            raise ToolError(f"too many watches (limit {self.config.max_watches}); remove some")
        title, text = await self._read(url, part)  # fails now, not silently later
        label = " ".join((name or title or urlsplit(url).netloc).split())[:60]
        taken = {i["name"].lower() for i in items}
        base, n = label, 2
        while label.lower() in taken:
            label, n = f"{base} ({n})", n + 1
        item: dict[str, Any] = {
            "id": uuid.uuid4().hex[:8],
            "name": label,
            "url": url,
            "when": when.strip() or "changes",
            "kind": kind,
            "value": value,
            "every_s": seconds,
            "part": part.strip(),
            "created": datetime.fromtimestamp(self._now()).isoformat(timespec="seconds"),
            "hits": 0,
        }
        first = await self._check(item, text)  # the starting point to compare against
        items.append(item)
        self._save(items)
        result = {"watching": item["name"], "id": item["id"], "every_minutes": seconds // 60}
        result.update({k: v for k, v in first.items() if k in ("price", "present", "error")})
        if first["hit"]:  # e.g. already below the price: say so now
            result["already"] = first["message"]
        return result

    @tool(name="list")
    def list_watches(self) -> dict[str, Any]:
        """The pages being watched, with what each waits for and what was last seen."""
        items = self._load()
        return {
            "count": len(items),
            "watches": [
                {
                    "name": i["name"],
                    "url": i["url"],
                    "when": i["when"],
                    "every_minutes": i["every_s"] // 60,
                    "last_checked": i.get("last_checked"),
                    "price": i.get("price"),
                    "hits": i.get("hits", 0),
                    "problem": i.get("last_error"),
                }
                for i in items
            ],
        }

    @tool
    async def check_now(self, name: str) -> dict[str, Any]:
        """Check one watched page right now."""
        items = self._load()
        item = self._find(items, name)
        seen = await self._check(item)
        self._merge_save([item])
        if seen["hit"]:
            await self._announce(item, seen)
        return seen

    @tool
    def history(self, name: str) -> dict[str, Any]:
        """What a watch has seen over time (prices, or the start of the page after each
        change), oldest first."""
        item = self._find(self._load(), name)
        return {"name": item["name"], "when": item["when"], "history": item.get("history", [])}

    @tool
    def remove(self, name: str) -> dict[str, Any]:
        """Stop watching a page (by its name or id)."""
        items = self._load()
        item = self._find(items, name)
        self._save([i for i in items if i["id"] != item["id"]])
        return {"removed": item["name"]}
