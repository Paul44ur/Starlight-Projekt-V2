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
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

    handler = functools.partial(Quiet, directory=str(REPO / "starlight/web/static"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


ANSWER = """## Your week

Here is **what I found** for *you*, with `code` and a [link](https://example.org/a).

1. First step
2. Second step
   - a detail
   - another

| Day | Rain |
| --- | ---: |
| Mon | 2 mm |
| Tue | 0 mm |

```python
print("hi")  # <b>not bold</b>
```

> A quote

<img src=x onerror="window.hacked=1"> [bad](javascript:window.hacked=2) snake_case_name
See https://example.org/b."""


async def open_chat(playwright: Any, site: str) -> Any:
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    browser = await playwright.chromium.launch(executable_path=executable)
    page = await browser.new_page()
    await page.add_init_script(FAKE_SERVER)
    await page.goto(f"{site}/chat.html")
    await page.evaluate("() => { if (!window.sockets.length) connect(); }")
    await page.wait_for_function("() => window.sockets.length > 0")
    return browser, page


async def test_answers_are_drawn_from_markdown_and_never_run_html(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_chat(playwright, static_site)
        try:
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            for piece in (ANSWER[:40], ANSWER[40:200], ANSWER[200:]):  # it streams in
                await page.evaluate(SEND, {"type": "token", "text": piece})
            await page.evaluate(SEND, {"type": "answer", "text": ANSWER, "conversation": "c1"})
            body = page.locator(".msg.bot .body").last
            assert await body.locator("h4").text_content() == "Your week"
            assert await body.locator("strong").first.text_content() == "what I found"
            assert await body.locator("em").first.text_content() == "you"
            assert await body.locator("ol > li").count() == 2
            assert await body.locator("ol li ul li").count() == 2  # nested
            assert await body.locator("td").all_text_contents() == ["Mon", "2 mm", "Tue", "0 mm"]
            aligned = await body.locator("td").evaluate_all("c => c.map(x => x.style.textAlign)")
            assert aligned == ["", "right", "", "right"]  # "---:" is right-aligned
            assert await body.locator("pre code").text_content() == (
                'print("hi")  # <b>not bold</b>'
            )
            assert await body.locator("blockquote").text_content() == "A quote"
            links = await body.locator("a").evaluate_all("as => as.map(a => a.href)")
            assert links == ["https://example.org/a", "https://example.org/b"]
            text = await body.text_content()
            assert "<img src=x" in text and "snake_case_name" in text  # shown, not run
            assert await body.locator("img, b, script").count() == 0
            assert await page.evaluate("() => window.hacked") is None
            # Copy takes the Markdown as written
            raw = await body.evaluate("b => b.raw")
            assert raw == ANSWER
            # read aloud: the words, not the symbols, and no code
            spoken = await page.evaluate("t => markdownToSpeech(t)", ANSWER)
            assert "**" not in spoken and "print(" not in spoken and "(code)" in spoken
            assert "what I found" in spoken and "link" in spoken and "https://" not in spoken
            assert "Mon, 2 mm" in spoken and ", Mon" not in spoken  # a table row
            # half-written or broken Markdown (as it streams in) never stops the page
            broken = [
                "```",
                "```py\nx = 1",
                "|",
                "| a |\n|",
                "- ",
                "1.",
                "**",
                "*a",
                "[a](",
                "[a](b",
                "> ",
                "|-|\n|-|",
                "- a\n  - b\n    - c\n      - d",
                "#",
                "#### ",
                "~~~\n~~",
                "_",
                "`",
                "a | b\n--- | ---\nc | d",
                "\n\n\n",
            ]
            errors = await page.evaluate(
                """texts => texts.flatMap(t => {
                     try { renderMarkdown(document.createElement('div'), t); return []; }
                     catch (e) { return [t + ': ' + e]; } })""",
                broken,
            )
            assert errors == []
        finally:
            await browser.close()


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
