"""The pages on a phone (UI block U7), with a touch screen emulated: nothing scrolls sideways,
every control is big enough for a finger (44 px; a few small ones on purpose), the chat's bar
stays one row, and on a narrow phone Send is an arrow."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_page import CHROMIUM, FAKE_SERVER, SEND, static_site  # noqa: F401
from tests.test_pages_look import FAKE as PAGES_FAKE
from tests.test_settings_look import FAKE as SETTINGS_FAKE

# Controls smaller on purpose (still well over the 24 px minimum): a code block's Copy, the
# Undo in a changed-file chip, and the four buttons in the message box (36 px wide, 44 tall).
SMALL_ON_PURPOSE = ".md-code-bar button, .change button, #form > .icon"
CONTROLS = "button, select, input:not([type=hidden], [type=checkbox], [type=radio], [type=file])"
SMALL = """([controls, except]) => {
  const small = [];
  for (const e of document.querySelectorAll(controls)) {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    const gone = !r.width || !r.height || s.visibility === 'hidden' || e.closest('[hidden]');
    if (gone || e.matches(except)) continue;
    if (r.height < 44 || (e.matches('button.icon') && r.width < 44))
      small.push((e.id || e.className || e.tagName) + ' '
                 + Math.round(r.width) + 'x' + Math.round(r.height));
  }
  return small;
}"""


async def phone(playwright: Any, device: str, fake: str, url: str) -> tuple[Any, Any]:
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    browser = await playwright.chromium.launch(executable_path=executable)
    context = await browser.new_context(**playwright.devices[device])
    page = await context.new_page()
    await page.add_init_script(fake)
    await page.goto(url)
    return browser, page


@pytest.mark.parametrize("device", ["Galaxy S8", "Pixel 7"])
async def test_every_page_fits_a_phone_and_a_finger(
    static_site: str,  # noqa: F811
    device: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    pages = [
        ("chat", FAKE_SERVER, "#log"),
        ("settings", SETTINGS_FAKE, "details.mod"),
        ("memory", PAGES_FAKE, ".mem"),
        ("activity", PAGES_FAKE, "#filters button"),
    ]
    async with async_api.async_playwright() as playwright:
        for name, fake, ready in pages:
            browser, page = await phone(playwright, device, fake, f"{static_site}/{name}.html")
            try:
                await page.wait_for_selector(ready)
                if name == "chat":  # an answer, with its tools under it
                    await page.evaluate("() => { if (!window.sockets.length) connect(); }")
                    await page.wait_for_function("() => window.sockets.length > 0")
                    await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
                    await page.evaluate(
                        SEND, {"type": "answer", "text": "Hi.", "conversation": "c1"}
                    )
                if name == "settings":
                    await page.click('details.mod[data-name="mail"] > summary .mname')
                width = await page.evaluate("() => innerWidth")
                assert await page.evaluate("() => document.documentElement.scrollWidth") <= width
                assert await page.evaluate(SMALL, [CONTROLS, SMALL_ON_PURPOSE]) == [], name
            finally:
                await browser.close()


async def test_on_a_narrow_phone_the_bar_is_one_row_and_send_is_an_arrow(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await phone(
            playwright, "Galaxy S8", FAKE_SERVER, f"{static_site}/chat.html"
        )
        try:
            await page.evaluate("() => { if (!window.sockets.length) connect(); }")
            await page.wait_for_function("() => window.sockets.length > 0")
            # the bar: one row, even with 44 px buttons (each item crosses its middle line)
            one_row = await page.locator("#main header.top").evaluate(
                "h => { const b = h.getBoundingClientRect(), mid = b.top + b.height / 2;"
                " return [...h.children].filter(c => c.offsetParent).every(c => {"
                " const r = c.getBoundingClientRect();"
                " return r.top <= mid + 2 && r.bottom >= mid - 2; }); }"
            )
            assert one_row
            send = page.locator("#send")
            arrow = await send.evaluate("b => getComputedStyle(b, '::before').content")
            assert arrow == '"➤"'
            assert await send.text_content() == "Send"  # (for a screen reader, and tests)
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            stop = await send.evaluate("b => getComputedStyle(b, '::before').content")
            assert stop == '"■"'
            # the message box keeps room for its hint on one line
            box = page.locator("#input")
            assert await box.evaluate("i => i.offsetHeight") == 44
        finally:
            await browser.close()


async def test_a_switch_takes_a_tap_just_above_it(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await phone(
            playwright, "Pixel 7", SETTINGS_FAKE, f"{static_site}/settings.html"
        )
        try:
            switch = page.locator("details.mod label.switch").first
            await switch.scroll_into_view_if_needed()  # (a point off the screen hits nothing)
            hit = await switch.evaluate(
                "s => { const r = s.getBoundingClientRect();"
                " return document.elementFromPoint(r.left + r.width / 2, r.top - 5) === s; }"
            )
            assert hit  # (the tap area reaches 44 px around a 30 px switch)
        finally:
            await browser.close()
