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


async def test_esc_stops_an_answer_and_up_brings_back_the_last_question(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_chat(playwright, static_site)
        try:
            await page.evaluate(
                "() => { const s = window.sockets[window.sockets.length - 1];"
                " s.readyState = 1; s.sent = []; s.send = d => s.sent.push(JSON.parse(d)); }"
            )
            sent = "() => window.sockets[window.sockets.length - 1].sent.map(m => m.type)"
            box = page.locator("#input")
            await box.fill("How warm is it in Berlin?")
            await box.press("Enter")
            assert await page.evaluate(sent) == ["ask"]
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            await page.keyboard.press("Escape")
            assert await page.evaluate(sent) == ["ask", "stop"]
            await page.evaluate(SEND, {"type": "stopped", "conversation": "c1"})
            await page.keyboard.press("Escape")  # nothing is being written: nothing to stop
            assert await page.evaluate(sent) == ["ask", "stop"]
            await box.press("ArrowUp")
            assert await box.input_value() == "How warm is it in Berlin?"
            await box.press("ArrowUp")  # (only from an empty box: the text stays)
            assert await box.input_value() == "How warm is it in Berlin?"
        finally:
            await browser.close()


async def test_slash_stars(static_site: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_chat(playwright, static_site)
        try:
            await page.evaluate(
                "() => { const s = window.sockets[window.sockets.length - 1];"
                " s.readyState = 1; s.sent = []; s.send = d => s.sent.push(d); }"
            )
            await page.locator("#input").fill("/stars")
            await page.locator("#input").press("Enter")
            assert await page.locator(".star").count() == 48
            assert await page.evaluate("() => window.sockets.at(-1).sent.length") == 0  # (local)
            assert await page.locator("#input").input_value() == ""
            await page.wait_for_function("() => !document.querySelector('.star')", timeout=9000)
        finally:
            await browser.close()


async def test_send_turns_into_stop_and_only_the_latest_answer_regenerates(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_chat(playwright, static_site)
        try:
            await page.evaluate(
                "() => { const s = window.sockets[window.sockets.length - 1];"
                " s.readyState = 1; s.sent = []; s.send = d => s.sent.push(JSON.parse(d).type); }"
            )
            sent = "() => window.sockets.at(-1).sent"
            send, box = page.locator("#send"), page.locator("#input")
            for n in (1, 2):
                await box.fill(f"question {n}")
                await box.press("Enter")
                assert await send.text_content() == "Stop"  # at once, before the answer begins
                await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
                await box.fill("my next question")
                await box.press("Enter")  # Enter does not stop the answer
                assert "stop" not in await page.evaluate(sent)
                await box.fill("")
                if n == 1:
                    await page.evaluate(
                        SEND, {"type": "answer", "text": "one", "conversation": "c1"}
                    )
                else:
                    await send.click()  # Stop
                    assert (await page.evaluate(sent))[-1] == "stop"
                    await page.evaluate(SEND, {"type": "stopped", "conversation": "c1"})
                assert await send.text_content() == "Send"
            regenerate = page.locator(".msg.bot .regen")
            visible = await regenerate.evaluate_all(
                "bs => bs.map(b => getComputedStyle(b).display !== 'none')"
            )
            assert visible == [False, True]  # only the latest answer
        finally:
            await browser.close()


async def test_the_phone_header_is_one_row(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_chat(playwright, static_site)
        try:
            await page.set_viewport_size({"width": 360, "height": 740})
            header = await page.locator("#main header.top").bounding_box()
            assert header is not None and header["height"] < 60  # no second row
            assert await page.locator("#kill").is_visible()
            await page.locator("#menu").click()  # the links are in the drawer
            assert await page.locator(".drawer-nav a[href='/settings']").is_visible()
        finally:
            await browser.close()


SETTINGS_API = """
window.posted = [];
const TOOLS = [
  {name: "desktop.click", description: "Click at a screen position.", tag: "safe", now: "safe"},
  {name: "desktop.type_text", description: "Type text.", tag: "confirm", now: "safe"},
  {name: "mail.send", description: "Send an email.", tag: "confirm", now: "confirm"},
  {name: "mail.inbox", description: "The newest messages.", tag: "safe", now: "blocked"},
];
const MODULES = [
  {name: "desktop", status: "loaded", description: "Read the screen and use mouse and keyboard"},
  {name: "mail", status: "loaded", description: "Read and send email from your own mailbox"},
  {name: "weather", status: "disabled", description: "Current weather and a forecast"},
];
window.fetch = async (url, init) => {
  const json = (data) => new Response(JSON.stringify(data),
                                      {headers: {"content-type": "application/json"}});
  if (init && init.method === "POST") {
    window.posted.push([url, JSON.parse(init.body)]);
    return json({});
  }
  if (url === "/api/permissions") return json({tools: TOOLS, rules: {}});
  if (url === "/api/modules") return json({modules: MODULES});
  if (url === "/api/devices") return json({remote: {enabled: false, hosts: []}, devices: []});
  return json({local: true});
};
window.confirm = () => true;
"""


async def test_settings_groups_tools_under_their_modules_and_fits_a_phone(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    async with async_api.async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=executable)
        try:
            page = await browser.new_page(viewport={"width": 360, "height": 740})
            await page.add_init_script(SETTINGS_API)
            await page.goto(f"{static_site}/settings.html")
            await page.wait_for_selector("details.mod")
            assert await page.locator("details.mod").count() == 3
            mail = page.locator("details.mod[data-name=mail]")
            assert "1 ask first" in await mail.locator(".mcount").inner_text()
            assert "1 blocked" in await mail.locator(".mcount").inner_text()
            await mail.locator("summary").click()
            row = mail.locator(".trow").filter(has_text="mail.inbox")
            assert await row.locator(".seg button.sel").inner_text() == "Blocked"
            assert await row.locator(".reset").is_enabled()  # it differs from its own setting
            # nothing sticks out sideways on a phone
            assert await page.evaluate("document.documentElement.scrollWidth") <= 360
            await row.locator(".seg button", has_text="Asks first").click()
            await page.wait_for_function("window.posted.length > 0")
            assert await page.evaluate("window.posted") == [
                ["/api/permissions", {"pattern": "mail.inbox", "setting": "confirm"}]
            ]
            # a search opens the module that has the tool, and hides the rest
            await page.locator("#filter").fill("type")
            assert await page.locator("details.mod").count() == 1
            assert await page.locator("details.mod[data-name=desktop]").get_attribute("open") == ""
            # the switch turns a module on, without opening its list
            await page.locator("#filter").fill("")
            await page.locator("details.mod[data-name=weather] .switch").click()
            await page.wait_for_function("window.posted.length > 1")
            assert (await page.evaluate("window.posted"))[-1] == [
                "/api/modules/weather",
                {"enabled": True},
            ]
        finally:
            await browser.close()
