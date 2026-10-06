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
import contextlib
import email.utils
import json
import sqlite3
import threading
import time
from collections import deque
from collections.abc import AsyncIterator
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

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400

    def json(self) -> Any:
        return json.loads(self.text)


class SlidingWindowLimiter:
    """At most ``limit`` acquisitions in any ``window`` seconds."""

    def __init__(self, limit: int, window: float = 1.0) -> None:
        self.limit = limit
        self.window = window
        self._times: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._times and now - self._times[0] >= self.window:
                    self._times.popleft()
                if len(self._times) < self.limit:
                    self._times.append(now)
                    return
                await asyncio.sleep(self.window - (now - self._times[0]) + 0.001)


@dataclass
class _Host:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    robots_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_at: float = 0.0
    robots: RobotFileParser | None = None
    robots_until: float = 0.0


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

    def get(self, url: str, ttl: float) -> HttpResponse | None:
        with self._lock:
            row = self._db.execute(
                "SELECT fetched, status, headers, body FROM pages WHERE url = ?", (url,)
            ).fetchone()
        if row is None or time.time() - row[0] > ttl:
            return None
        return HttpResponse(url, row[1], json.loads(row[2]), row[3], from_cache=True)

    def put(self, response: HttpResponse) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO pages VALUES (?, ?, ?, ?, ?)",
                (
                    response.url,
                    time.time(),
                    response.status,
                    json.dumps(response.headers),
                    response.text,
                ),
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()


def parse_retry_after(value: str | None, default: float) -> float:
    if not value:
        return default
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    parsed = email.utils.parsedate_to_datetime(value) if value else None
    if parsed is None:
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
            headers={"User-Agent": self.config.user_agent},
        )
        self._log = get_logger("http")

    # -- permits -----------------------------------------------------------------------

    def _host(self, url: str) -> tuple[str, _Host]:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise HttpError(f"not an http(s) URL: {url}")
        origin = f"{parts.scheme}://{parts.netloc}"
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
            if response.status in (401, 403):
                parser.parse(["User-agent: *", "Disallow: /"])
            elif response.ok:
                parser.parse(response.text.splitlines())
            else:
                parser.parse([])  # no robots.txt: everything allowed
        except httpx.HTTPError:
            parser.parse([])
        host.robots = parser
        host.robots_until = time.monotonic() + 3600
        return parser

    async def _wait_turn(self, host: _Host, delay: float, count_global: bool = True) -> None:
        """Per-domain spacing, then the global window. Call with host.lock held."""
        now = time.monotonic()
        if host.next_at > now:
            await asyncio.sleep(host.next_at - now)
        if count_global:
            await self.limiter.acquire()
        host.next_at = time.monotonic() + delay

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

    @contextlib.asynccontextmanager
    async def permit(self, url: str, respect_robots: bool = True) -> AsyncIterator[None]:
        """Wait until a request to ``url`` is allowed. For clients that fetch on their own,
        so they stay under the same limits."""
        host, delay = await self._prepare(url, respect_robots)
        async with host.lock:
            await self._wait_turn(host, delay)
            self._note(url, via="permit")
        yield

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

    async def _send(
        self, method: str, url: str, host: _Host, delay: float, **kwargs: Any
    ) -> HttpResponse:
        async with host.lock:
            await self._wait_turn(host, delay)
            self._note(url, method)
            started = time.perf_counter()
            response = await self._client.request(method, url, **kwargs)
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        return HttpResponse(
            str(response.url),
            response.status_code,
            dict(response.headers),
            response.text,
            elapsed_ms=elapsed,
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
            if cacheable and response.status == 200:
                self.cache.put(response)
            return response

    async def get(self, url: str, **kwargs: Any) -> HttpResponse:
        return await self.request("GET", url, **kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()
        self.cache.close()
