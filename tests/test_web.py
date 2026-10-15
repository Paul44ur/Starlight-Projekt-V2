"""Step 6 'done when': a research task fetches 20 pages, the logs show no more than 5
requests in any second, and a test login works."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from starlight.core import Kernel, Paths
from starlight.core.config import HttpConfig
from starlight.core.http import HttpError, PoliteHttp, RobotsDisallowed, SlidingWindowLimiter, Slot
from starlight.core.vault import MemoryBackend

CHROMIUM = "/opt/pw-browsers/chromium"


def page(host: str, path: str) -> str:
    return f"<html><head><title>{host}{path}</title></head><body><main>" + (
        f"<h1>Article on {host}</h1><p>{'Useful research text. ' * 20}</p>"
        f"<a href='/next'>next</a></main></body></html>"
    )


def mock_web(
    robots: dict[str, str] | None = None, status: Callable[[httpx.Request], int] | None = None
) -> tuple[httpx.MockTransport, list[tuple[float, str]]]:
    hits: list[tuple[float, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append((time.monotonic(), str(request.url)))
        host, path = request.url.host, request.url.path
        if path == "/robots.txt":
            text = (robots or {}).get(host)
            return httpx.Response(200, text=text) if text else httpx.Response(404)
        if status is not None:
            code = status(request)
            if code != 200:
                return httpx.Response(code, headers={"Retry-After": "1"})
        return httpx.Response(200, html=page(host, path))

    return httpx.MockTransport(handler), hits


def log_requests(paths: Paths) -> list[float]:
    times = []
    for line in (paths.logs / "starlight.log").read_text().splitlines():
        entry = json.loads(line)
        if entry.get("logger") == "starlight.http" and entry["event"] == "request":
            times.append(dt.datetime.fromisoformat(entry["timestamp"]).timestamp())
    return sorted(times)


def max_in_any_second(times: list[float]) -> int:
    best, start = 0, 0
    for end in range(len(times)):
        while times[end] - times[start] >= 1.0:
            start += 1
        best = max(best, end - start + 1)
    return best


async def test_research_20_pages_never_more_than_5_per_second(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("web")
    transport, hits = mock_web()
    urls = [f"https://site{i}.example/article" for i in range(20)]
    async with Kernel(paths, http_transport=transport) as kernel:
        started = time.monotonic()
        result = await kernel.dispatcher.call("web.fetch_many", {"urls": urls})
        elapsed = time.monotonic() - started
    assert result.ok
    assert all("Useful research text" in r["text"] for r in result.output)
    times = log_requests(paths)
    assert len(times) == 40  # 20 pages + 20 robots.txt
    assert max_in_any_second(times) <= 5
    assert elapsed >= (40 - 5) / 5 - 0.2  # the cap, not luck, decided the pace
    assert max_in_any_second(sorted(t for t, _ in hits)) <= 5


async def test_a_stall_after_the_permit_does_not_bunch_requests_up() -> None:
    """Found on CI: requests allowed just before the program stalled went out late, the
    next ones on time, and the server saw 6 in one second. A request's slot now counts
    from when it really goes out."""
    transport, hits = mock_web()
    http = PoliteHttp(HttpConfig(respect_robots=False), transport=transport)
    original = http.limiter.acquire
    allowed = 0

    async def then_stall(until_sent: bool = False) -> Slot:
        nonlocal allowed
        slot = await original(until_sent)
        allowed += 1
        if allowed <= 5:
            await asyncio.sleep(0.3)  # allowed, but the program stalls before sending
        return slot

    http.limiter.acquire = then_stall  # type: ignore[method-assign]
    urls = [f"https://site{i}.example/page" for i in range(10)]
    await asyncio.gather(*(http.get(url, use_cache=False) for url in urls))
    await http.aclose()
    assert len(hits) == 10
    assert max_in_any_second(sorted(t for t, _ in hits)) <= 5


async def test_sliding_window_never_bursts_past_the_cap() -> None:
    limiter = SlidingWindowLimiter(5, 0.5)
    stamps: list[float] = []

    async def go() -> None:
        await limiter.acquire()
        stamps.append(time.monotonic())

    await asyncio.gather(*(go() for _ in range(17)))
    stamps.sort()
    for i in range(5, len(stamps)):
        # stamps are taken after acquire() returns, so allow for scheduling delay
        assert stamps[i] - stamps[i - 5] >= 0.5 - 0.05


async def test_a_huge_response_is_capped_not_read_whole() -> None:
    """A server that returns gigabytes must not fill memory: the body is capped."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"A" * 5_000_000)  # 5 MB

    http = PoliteHttp(
        HttpConfig(respect_robots=False, max_response_bytes=10_000),
        transport=httpx.MockTransport(handler),
    )
    response = await http.get("https://big.example/file")
    again = await http.get("https://big.example/file")
    await http.aclose()
    assert len(response.text) == 10_000 and response.truncated  # only the cap was read
    # a cut-off body is never cached as if it were the whole page
    assert again.truncated and not again.from_cache


async def test_a_server_that_never_answers_is_given_up_on() -> None:
    """The time ceiling covers waiting for the response headers, not only the body."""
    import asyncio

    class Silent(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(30)  # connected, but no response ever comes
            return httpx.Response(200)

    http = PoliteHttp(HttpConfig(respect_robots=False, max_total_seconds=0.3), transport=Silent())
    with pytest.raises(HttpError, match="took longer"):
        await asyncio.wait_for(http.get("https://slow.example/"), 5)
    await http.aclose()


async def test_a_redirect_loop_is_stopped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"Location": "https://loop.example/go"})

    http = PoliteHttp(
        HttpConfig(respect_robots=False, max_redirects=3),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(HttpError):
        await http.get("https://loop.example/go")
    await http.aclose()


async def test_robots_crawl_delay_cache_and_per_domain() -> None:
    transport, hits = mock_web({"a.example": "User-agent: *\nDisallow: /private\nCrawl-delay: 1"})
    http = PoliteHttp(HttpConfig(per_domain_interval=0.2), transport=transport)
    with pytest.raises(RobotsDisallowed):
        await http.get("https://a.example/private/x")
    first = await http.get("https://a.example/one")
    second = await http.get("https://a.example/two")
    cached = await http.get("https://a.example/one")
    await http.aclose()
    assert first.ok and second.ok and cached.from_cache
    page_hits = [t for t, url in hits if "robots" not in url]
    assert page_hits[1] - page_hits[0] >= 1.0 - 0.05  # crawl-delay honoured
    assert sum("robots.txt" in url for _, url in hits) == 1  # fetched once


async def test_backs_off_on_429_with_retry_after() -> None:
    calls = {"n": 0}

    def status(request: httpx.Request) -> int:
        calls["n"] += 1
        return 429 if calls["n"] == 1 else 200

    transport, _ = mock_web(status=status)
    http = PoliteHttp(HttpConfig(per_domain_interval=0), transport=transport)
    started = time.monotonic()
    response = await http.get("https://b.example/")
    await http.aclose()
    assert response.ok and calls["n"] == 2
    assert time.monotonic() - started >= 1.0 - 0.01


async def test_searxng_search(paths: Paths, use_module: Callable[[str], Path]) -> None:
    use_module("web")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search" and request.url.params["format"] == "json"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Starlight",
                        "url": "https://x.example",
                        "content": "A local assistant",
                    }
                ]
            },
        )

    async with Kernel(paths, http_transport=httpx.MockTransport(handler)) as kernel:
        result = await kernel.dispatcher.call("web.search", {"query": "starlight"})
    assert result.output == [
        {"title": "Starlight", "url": "https://x.example", "snippet": "A local assistant"}
    ]


def pages_transport(short: str, long: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        body = short if request.url.path == "/short" else long
        return httpx.Response(200, html=f"<html><body><main>{body}</main></body></html>")

    return httpx.MockTransport(handler)


class BrokenBrowser:
    """Stands in for a Chromium that cannot start."""

    def __init__(self) -> None:
        self.launches = 0

    async def new_context(self) -> Any:
        self.launches += 1
        raise RuntimeError("chromium crashed")

    async def close(self) -> None:
        pass


async def test_a_browser_that_fails_keeps_the_page_the_plain_fetch_read(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("web")
    transport = pages_transport("<p>Short page</p>", "<p>" + "Plenty of text. " * 40 + "</p>")
    async with Kernel(paths, http_transport=transport) as kernel:
        web: Any = kernel.modules.records["web"].instance
        web.browser = BrokenBrowser()
        one = await kernel.dispatcher.call("web.fetch", {"url": "https://a.example/short"})
        many = await kernel.dispatcher.call(
            "web.fetch_many",
            {"urls": ["https://a.example/short", "https://b.example/long"]},
        )
    assert one.ok and one.output["text"] == "Short page"
    assert web.browser.launches == 2  # tried each time, never fatal
    assert [r["text"][:10] for r in many.output] == ["Short page", "Plenty of "]


async def test_a_small_character_limit_does_not_start_the_browser(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("web")
    transport = pages_transport("", "<p>" + "Plenty of text. " * 40 + "</p>")
    async with Kernel(paths, http_transport=transport) as kernel:
        web: Any = kernel.modules.records["web"].instance
        web.browser = BrokenBrowser()
        result = await kernel.dispatcher.call(
            "web.fetch", {"url": "https://b.example/long", "max_chars": 100}
        )
    assert result.ok and web.browser.launches == 0
    assert result.output["text"].startswith("Plenty of text.") and result.output["text"].endswith(
        "…"
    )
    assert len(result.output["text"]) <= 102


# -- a local site for the browser tests -------------------------------------------------


class DemoSite(BaseHTTPRequestHandler):
    login_posts = 0
    SESSION = "session=abc"

    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, body: str, status: int = 200, headers: dict[str, str] | None = None) -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to: str, cookie: str | None = None) -> None:
        headers = {"Location": to}
        if cookie:
            headers["Set-Cookie"] = f"{cookie}; Path=/"
        self._send("", 303, headers)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        cookies = self.headers.get("Cookie", "")
        if path == "/js":
            self._send(
                "<html><body><main id=m>Loading</main><script>"
                "document.getElementById('m').textContent="
                "'Rendered by JavaScript: ' + 'x'.repeat(300);</script></body></html>"
            )
        elif path == "/login":
            self._send(
                "<form method=post action=/login><input id=user name=user>"
                "<input id=pass name=pass type=password>"
                "<button id=go type=submit>Sign in</button></form>"
            )
        elif path == "/otp":
            self._send(
                "<form method=post action=/otp><input id=otp name=otp>"
                "<button id=otp-go type=submit>Verify</button></form>"
            )
        elif path == "/account":
            if self.SESSION in cookies:
                self._send("<main><div id=welcome>Welcome Paul</div><p>Balance: 42</p></main>")
            else:
                self._redirect("/login")
        else:
            self._send("not found", 404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode())
        path = urlsplit(self.path).path
        if path == "/login":
            type(self).login_posts += 1
            if form.get("user") == ["paul"] and form.get("pass") == ["s3cret-pw"]:
                self._redirect("/otp", "pending=1")
            else:
                self._redirect("/login")
        elif path == "/otp":
            ok = form.get("otp") == ["123456"] and "pending=1" in self.headers.get("Cookie", "")
            if ok:
                self._redirect("/account", self.SESSION)
            else:
                self._redirect("/login")


@pytest.fixture
def site() -> Iterator[str]:
    DemoSite.login_posts = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), DemoSite)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def chromium(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright")
    if os.environ.get("STARLIGHT_CHROMIUM") or os.environ.get("CI"):
        return  # CI: the browser `playwright install chromium` put in place
    if not Path(CHROMIUM).exists():
        pytest.skip("no Chromium for Playwright")
    monkeypatch.setenv("STARLIGHT_CHROMIUM", CHROMIUM)


class FakeUser:
    def __init__(self) -> None:
        self.confirmations: list[str] = []
        self.questions: list[str] = []

    async def confirm(self, question: str) -> bool:
        self.confirmations.append(question)
        return True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        self.questions.append(question)
        return "123456"

    async def notify(self, text: str) -> None:
        pass


async def test_render_js_page(
    paths: Paths, use_module: Callable[[str], Path], site: str, chromium: None
) -> None:
    use_module("web")
    paths.config_file.write_text(
        paths.config_file.read_text() + "http:\n  per_domain_interval: 0.1\n"
    )
    async with Kernel(paths) as kernel:
        result = await kernel.dispatcher.call("web.fetch", {"url": f"{site}/js"})
    assert result.ok, result.error
    assert "Rendered by JavaScript" in result.output["text"]
    assert result.output.get("rendered") is True


async def test_login_with_2fa_saves_session(
    paths: Paths, use_module: Callable[[str], Path], site: str, chromium: None
) -> None:
    use_module("login")
    paths.config_file.write_text(
        paths.config_file.read_text() + "http:\n  per_domain_interval: 0.1\n"
    )
    (paths.module_configs / "login.yaml").write_text(f"""
sites:
  testsite:
    url: {site}/login
    username_selector: "#user"
    password_selector: "#pass"
    submit_selector: "#go"
    success_selector: "#welcome"
    otp_selector: "#otp"
    otp_submit_selector: "#otp-go"
""")
    vault = MemoryBackend()
    user = FakeUser()
    async with Kernel(paths, vault_backend=vault, ui=user) as kernel:
        kernel.vault.set("login.testsite.username", "paul")
        kernel.vault.set("login.testsite.password", "s3cret-pw")

        first = await kernel.dispatcher.call("login.login", {"site": "testsite"})
        assert first.ok, first.error
        assert len(user.confirmations) == 1  # asked the first time
        assert user.questions == ["Enter the 2FA code for testsite"]  # 2FA passed to you

        page = await kernel.dispatcher.call(
            "login.open", {"site": "testsite", "url": f"{site}/account"}
        )
        assert page.ok and "Balance: 42" in page.output["text"]
        assert DemoSite.login_posts == 1  # the saved session skipped the login

        await kernel.dispatcher.call("login.login", {"site": "testsite"})
        assert len(user.confirmations) == 1  # not asked again

    assert (paths.data / "modules" / "login" / "sessions" / "testsite.json").is_file()
    logs = "".join(p.read_text() for p in paths.logs.rglob("*.log"))
    assert "s3cret-pw" not in logs  # credentials never logged
