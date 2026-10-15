"""Polite HTTP for every module that touches the web.

* a global limit shared by every module: at most N requests (default 5) in any one-second
  window. A sliding window, not a token bucket, so bursts can never exceed the cap
* at most one request per second per domain, or the site's robots.txt crawl-delay
* robots.txt is respected
* HTTP 429 and 503 back off and honour Retry-After
* GET responses are cached so the same URL is not fetched twice
"""

from __future__ import annotations

import asyncio
import codecs
import email.utils
import json
import math
import re
import sqlite3
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from starlight.core.config import HttpConfig
from starlight.core.killswitch import KillSwitch
from starlight.core.logging import get_logger
from starlight.core.stats import Stats

RETRY_STATUSES = {429, 503}


class HttpError(Exception):
    pass


class RobotsDisallowed(HttpError):
    pass


@dataclass
class HttpResponse:
    url: str
    status: int
    headers: dict[str, str]
    text: str
    from_cache: bool = False
    elapsed_ms: float = 0.0
    truncated: bool = False  # the body was longer than max_response_bytes and was cut off

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400

    def json(self) -> Any:
        return json.loads(self.text)


# The encoding a page or feed names itself, for servers that don't send a charset.
_DECLARED = re.compile(
    rb"""<\?xml[^>]*?encoding\s*=\s*["']([\w.:-]+)|<meta[^>]+?charset\s*=\s*["']?([\w.:-]+)""",
    re.IGNORECASE,
)


def body_encoding(charset: str | None, body: bytes) -> str:
    """How to decode a response: the charset the server sent, else what the document itself
    declares (an XML prolog such as encoding="ISO-8859-1", or an HTML <meta charset>), else
    UTF-8. Unknown names fall back to UTF-8."""
    if body.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if not charset:
        found = _DECLARED.search(body[:2048])
        charset = (found.group(1) or found.group(2)).decode("ascii") if found else None
    try:
        return codecs.lookup(charset).name if charset else "utf-8"
    except LookupError:
        return "utf-8"


@dataclass(eq=False)
class Slot:
    """One request's place in the window."""

    at: float  # when the request went out (until then: when it was allowed)
    pending: bool = False  # allowed, not sent yet: counts as going out right now

    def sent(self) -> None:
        """The request is leaving now. Only the first call counts."""
        if self.pending:
            self.at = time.monotonic()
            self.pending = False


class SlidingWindowLimiter:
    """At most ``limit`` requests in any ``window`` seconds.

    With ``until_sent=True`` a request's slot counts from when it really went out
    (``Slot.sent``), not from when it was allowed: if the program stalls in between, the
    requests after it still keep their distance where the server sees them."""

    def __init__(self, limit: int, window: float = 1.0) -> None:
        self.limit = limit
        self.window = window
        self._slots: list[Slot] = []
        self._lock = asyncio.Lock()

    async def acquire(self, until_sent: bool = False) -> Slot:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._slots = [s for s in self._slots if s.pending or now - s.at < self.window]
                if len(self._slots) < self.limit:
                    slot = Slot(now, pending=until_sent)
                    self._slots.append(slot)
                    return slot
                done = [s.at for s in self._slots if not s.pending]
                wait = self.window - (now - min(done)) + 0.001 if done else 0.01
                await asyncio.sleep(max(wait, 0.001))


# The slot of the request this task is sending; the client's request hook marks it sent.
_sending: ContextVar[Slot | None] = ContextVar("starlight_http_sending", default=None)


@dataclass
class _Host:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    robots_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_at: float = 0.0
    robots: RobotFileParser | None = None
    robots_until: float = 0.0


MAX_HOSTS = 500  # sites whose robots.txt and pacing are remembered at once

_FINAL_URL = "x-starlight-final-url"  # stored with a cached page that was redirected


class ResponseCache:
    def __init__(self, path: Path | None) -> None:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            str(path) if path else ":memory:", check_same_thread=False, isolation_level=None
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, fetched REAL, "
            "status INTEGER, headers TEXT, body TEXT)"
        )
        self._lock = threading.Lock()
        self._puts = 0
        self.purge()

    #: pages older than this are never read again (the cache's ttl is an hour by default)
    MAX_AGE = 86400.0
    MAX_ROWS = 2000

    def purge(self) -> int:
        """Delete pages that are too old to be used, then the oldest beyond MAX_ROWS. Without
        this the cache file only ever grows (every researched page is kept whole)."""
        with self._lock:
            before = self._db.total_changes
            self._db.execute("DELETE FROM pages WHERE fetched < ?", (time.time() - self.MAX_AGE,))
            # (rowid breaks ties: on Windows the clock ticks every ~15 ms)
            self._db.execute(
                "DELETE FROM pages WHERE url IN (SELECT url FROM pages "
                "ORDER BY fetched DESC, rowid DESC LIMIT -1 OFFSET ?)",
                (self.MAX_ROWS,),
            )
            removed = self._db.total_changes - before
            if removed >= 50:
                self._db.execute("VACUUM")
        return removed

    def get(self, url: str, ttl: float) -> HttpResponse | None:
        with self._lock:
            row = self._db.execute(
                "SELECT fetched, status, headers, body FROM pages WHERE url = ?", (url,)
            ).fetchone()
        if row is None or time.time() - row[0] > ttl:
            return None
        headers = json.loads(row[2])
        final = headers.pop(_FINAL_URL, url)  # where redirects ended
        return HttpResponse(final, row[1], headers, row[3], from_cache=True)

    def put(self, response: HttpResponse, url: str | None = None) -> None:
        """Keep a response under the address that was asked for (``url``; a redirect may
        have ended somewhere else, which is remembered too)."""
        headers = dict(response.headers)
        if url and url != response.url:
            headers[_FINAL_URL] = response.url
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO pages VALUES (?, ?, ?, ?, ?)",
                (
                    url or response.url,
                    time.time(),
                    response.status,
                    json.dumps(headers),
                    response.text,
                ),
            )
            self._puts += 1
            due = self._puts % 200 == 0
        if due:
            self.purge()

    def close(self) -> None:
        with self._lock:
            self._db.close()


def parse_retry_after(value: str | None, default: float) -> float:
    """Seconds to wait from a Retry-After header (a number or an HTTP date); ``default`` when
    the header is missing or makes no sense."""
    if not value:
        return default
    try:
        seconds = float(value)
    except ValueError:
        pass
    else:
        return default if math.isnan(seconds) else max(0.0, seconds)
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):  # Python 3.10+ raises instead of returning None
        return default
    return max(0.0, parsed.timestamp() - time.time())


class PoliteHttp:
    def __init__(
        self,
        config: HttpConfig | None = None,
        *,
        stats: Stats | None = None,
        kill_switch: KillSwitch | None = None,
        cache_path: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config or HttpConfig()
        self.stats = stats
        self.kill_switch = kill_switch
        # A small margin on the window: requests leave a few ms after their permit, and the
        # cap must hold where the server sees them, not only where we count them.
        self.limiter = SlidingWindowLimiter(self.config.max_requests_per_second, 1.05)
        self.cache = ResponseCache(cache_path)
        self._hosts: dict[str, _Host] = {}
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=self.config.timeout,
            follow_redirects=True,
            max_redirects=self.config.max_redirects,
            limits=httpx.Limits(max_connections=self.config.max_connections),
            headers={"User-Agent": self.config.user_agent},
            event_hooks={"request": [self._leaving]},
        )
        self._log = get_logger("http")

    # -- permits -----------------------------------------------------------------------

    def _host(self, url: str) -> tuple[str, _Host]:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise HttpError(f"not an http(s) URL: {url}")
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._hosts and len(self._hosts) >= MAX_HOSTS:
            self._hosts.pop(next(iter(self._hosts)))  # the one contacted longest ago
        return origin, self._hosts.setdefault(origin, _Host())

    async def _robots(self, origin: str, host: _Host) -> RobotFileParser:
        async with host.robots_lock:  # concurrent requests fetch robots.txt only once
            if host.robots is not None and time.monotonic() < host.robots_until:
                return host.robots
            return await self._fetch_robots(origin, host)

    async def _fetch_robots(self, origin: str, host: _Host) -> RobotFileParser:
        parser = RobotFileParser()
        try:
            response = await self._send(
                "GET", f"{origin}/robots.txt", host, self.config.per_domain_interval
            )
            # RFC 9309: robots.txt that cannot be read for a 4xx reason (401 and 403
            # included) means there are no rules for crawlers.
            if response.ok:
                parser.parse(response.text.splitlines())
            else:
                parser.parse([])  # no robots.txt: everything allowed
        except httpx.HTTPError:
            parser.parse([])
        host.robots = parser
        host.robots_until = time.monotonic() + 3600
        return parser

    async def _wait_turn(
        self, host: _Host, delay: float, count_global: bool = True, until_sent: bool = False
    ) -> Slot | None:
        """Per-domain spacing, then the global window. Call with host.lock held."""
        now = time.monotonic()
        if host.next_at > now:
            await asyncio.sleep(host.next_at - now)
        slot = await self.limiter.acquire(until_sent) if count_global else None
        host.next_at = time.monotonic() + delay
        return slot

    @staticmethod
    async def _leaving(request: httpx.Request) -> None:
        """httpx calls this right before a request goes out."""
        slot = _sending.get()
        if slot is not None:
            slot.sent()

    async def _prepare(self, url: str, respect_robots: bool) -> tuple[_Host, float]:
        """Kill-switch and robots.txt checks; returns the host and its request spacing."""
        if self.kill_switch is not None:
            self.kill_switch.check()
        origin, host = self._host(url)
        delay = self.config.per_domain_interval
        if respect_robots and self.config.respect_robots:
            robots = await self._robots(origin, host)
            if not robots.can_fetch(self.config.user_agent, url):
                raise RobotsDisallowed(f"robots.txt disallows {url}")
            crawl_delay = robots.crawl_delay(self.config.user_agent)
            if crawl_delay:
                delay = max(delay, float(crawl_delay))
        return host, delay

    def _note(self, url: str, method: str = "GET", **extra: Any) -> None:
        self._log.info("request", url=url, method=method, **extra)
        if self.stats is not None:
            self.stats.record("web.requests", host=urlsplit(url).netloc)

    async def navigation_permit(self, url: str, respect_robots: bool = True) -> None:
        """Browser page loads: kill switch, robots.txt and per-domain spacing. The requests
        the page then makes are counted one by one with ``count_request``."""
        host, delay = await self._prepare(url, respect_robots)
        async with host.lock:
            await self._wait_turn(host, delay, count_global=False)

    async def count_request(self, url: str) -> None:
        """Count one request (e.g. made by a browser) against the global limit."""
        if self.kill_switch is not None:
            self.kill_switch.check()
        await self.limiter.acquire()
        self._note(url, via="browser")

    # -- requests ----------------------------------------------------------------------

    async def _read_capped(self, response: httpx.Response) -> tuple[bytes, bool]:
        """Read the body, but never more than max_response_bytes. Counts decompressed
        bytes, so a gzip bomb is stopped too."""
        cap = self.config.max_response_bytes
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
            total += len(chunk)
            if total >= cap:
                return b"".join(chunks)[:cap], True
        return b"".join(chunks), False

    async def _send(
        self, method: str, url: str, host: _Host, delay: float, **kwargs: Any
    ) -> HttpResponse:
        async with host.lock:
            slot = await self._wait_turn(host, delay, until_sent=True)
            self._note(url, method)
            started = time.perf_counter()
            sending = _sending.set(slot)
            try:
                # The ceiling covers the whole exchange - connecting, waiting for the headers
                # and reading the body - so a server that never answers can't hold a request.
                async with (
                    asyncio.timeout(self.config.max_total_seconds),
                    self._client.stream(method, url, **kwargs) as response,
                ):
                    if slot is not None:
                        slot.sent()
                    body, truncated = await self._read_capped(response)
                    status, headers = response.status_code, dict(response.headers)
                    final_url, charset = str(response.url), response.charset_encoding
            except TimeoutError as exc:
                raise HttpError(
                    f"{url}: took longer than {self.config.max_total_seconds:.0f}s"
                ) from exc
            finally:
                _sending.reset(sending)
                if slot is not None:
                    slot.sent()  # failed before it went out: free the slot from now
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        return HttpResponse(
            final_url,
            status,
            headers,
            body.decode(body_encoding(charset, body), errors="replace"),
            elapsed_ms=elapsed,
            truncated=truncated,
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        use_cache: bool = True,
        respect_robots: bool = True,
        **kwargs: Any,
    ) -> HttpResponse:
        """Make a request under every limit. Raises RobotsDisallowed or HttpError."""
        method = method.upper()
        cacheable = method == "GET" and use_cache and not kwargs.get("params")
        if cacheable:
            cached = self.cache.get(url, self.config.cache_ttl)
            if cached is not None:
                return cached

        attempt = 0
        while True:
            host, delay = await self._prepare(url, respect_robots)
            try:
                response = await self._send(method, url, host, delay, **kwargs)
            except httpx.HTTPError as exc:
                raise HttpError(f"{type(exc).__name__}: {exc}") from exc
            self._log.info("response", url=url, status=response.status, ms=response.elapsed_ms)

            if response.status in RETRY_STATUSES and attempt < self.config.max_retries:
                wait = parse_retry_after(
                    response.headers.get("retry-after"), self.config.backoff_base * 2**attempt
                )
                if wait > self.config.max_retry_after:
                    raise HttpError(f"{url}: server asked to wait {wait:.0f}s, giving up")
                self._log.warning("backing off", url=url, status=response.status, wait=wait)
                host.next_at = max(host.next_at, time.monotonic() + wait)
                attempt += 1
                continue
            if cacheable and response.status == 200 and not response.truncated:
                self.cache.put(response, url)
            return response

    async def get(self, url: str, **kwargs: Any) -> HttpResponse:
        return await self.request("GET", url, **kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()
        self.cache.close()
