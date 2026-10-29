"""The web chat page in a real browser, with a stand-in for the server's WebSocket: what the
page shows while an answer is on its way."""

from __future__ import annotations

import functools
import http.server
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO

CHROMIUM = "/opt/pw-browsers/chromium"
# The page's WebSocket and fetch, replaced: the test plays the server's messages itself.
FAKE_SERVER = """
window.sockets = [];
window.WebSocket = class {
  constructor() { window.sockets.push(this); setTimeout(() => this.onopen && this.onopen(), 20); }
  send() {}
  close() {}
};
window.WebSocket.OPEN = 1;
window.fetch = async () => new Response("{}", {headers: {"content-type": "application/json"}});
"""
SEND = "m => window.sockets[window.sockets.length - 1].onmessage({data: JSON.stringify(m)})"
WAITING = """() => {
  const body = document.querySelector('.msg.bot .body.waiting');
  return body ? getComputedStyle(body, '::before').content : null;
}"""


@pytest.fixture
def static_site() -> Iterator[str]:
    handler: Any = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(REPO / "starlight/web/static")
    )
    handler.log_message = lambda *args: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


async def test_an_answer_on_its_way_shows_that_starlight_is_working(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=executable)
        try:
            page = await browser.new_page()
            await page.add_init_script(FAKE_SERVER)
            await page.goto(f"{static_site}/chat.html")
            await page.wait_for_function("() => document.querySelector('#log, body')")
            await page.evaluate("() => { if (!window.sockets.length) connect(); }")
            await page.wait_for_function("() => window.sockets.length > 0")

            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            assert await page.evaluate(WAITING) == '"…"'
            await page.wait_for_function(
                "() => (document.querySelector('.body.waiting') || {}).dataset?.wait"
                "?.startsWith('Working on it')",
                timeout=6000,
            )  # a model that thinks first: after a few seconds, for how long
            await page.evaluate(SEND, {"type": "token", "text": "Hello"})
            assert await page.evaluate(WAITING) is None  # the first word ends the waiting
            assert await page.locator(".msg.bot .body").last.text_content() == "Hello"

            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            await page.evaluate(SEND, {"type": "error", "text": "HTTP 500"})
            assert await page.locator(".body.waiting").count() == 0  # never left waiting
        finally:
            await browser.close()
