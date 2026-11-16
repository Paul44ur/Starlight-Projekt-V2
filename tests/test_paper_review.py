# ruff: noqa: E501  (the pages' scripts are written out as long strings)
"""Block P7 of the UI 3 plan (docs/plan-ui-3.md): the review of the Paper look. The shared rules
the pages repeated locally now live in style.css: an approval button is an outlined rectangle,
a state label (``.pill``) has no fill, the chosen one of a group of choices has one look, tags
and callouts in answers have no tint. And two fixes in the chat: the code block's "Copy" in
German, and the search row of the sidebar on one line in German."""

from __future__ import annotations

import copy
import json
import os
import re
from typing import Any

import pytest

from tests.conftest import REPO
from tests.test_chat_app_look import open_app
from tests.test_chat_page import SEND, static_site  # noqa: F401  (a fixture)
from tests.test_pages_look import DATA
from tests.test_pages_paper import open_at
from tests.test_pages_review import full_app as full_app
from tests.test_pages_review import notebook as notebook

STATIC = REPO / "starlight" / "web" / "static"
OWN_STYLES = [
    *(STATIC / f"{n}.html" for n in ("memory", "activity", "automations", "settings", "today")),
    REPO / "starlight" / "viewer" / "static" / "index.html",
    STATIC / "notes.css",
    STATIC / "chat.css",
]

APPROVALS = {
    "items": [
        {"id": f"a{i}", "title": f"Send report {i}", "created": "", "runs": "mail.send"}
        for i in range(3)
    ]
}
SKILLS = {
    "skills": [
        {"name": f"draft-{i}", "status": "draft", "description": "A draft", "instructions": "1. Do",
         "version": 1, "uses": 0}
        for i in range(2)
    ]
    + [
        {"name": "weekly-report", "status": "approved", "description": "Report",
         "instructions": "1. Read", "version": 3, "uses": 1}
    ]
}  # fmt: skip

# a button is "filled" when its ground is a colour of its own (a page ground is not a fill)
FILLED = """() => {
  const colour = (name) => { const p = document.createElement('i');
    p.style.background = 'var(' + name + ')'; document.body.appendChild(p);
    const c = getComputedStyle(p).backgroundColor; p.remove(); return c; };
  const grounds = ['--bg', '--panel', '--panel2'].map(colour);
  grounds.push('rgba(0, 0, 0, 0)', 'transparent');
  return [...document.querySelectorAll('button')].filter(b => {
    const r = b.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && !grounds.includes(getComputedStyle(b).backgroundColor);
  }).map(b => b.id || b.textContent.trim());
}"""


async def with_data(
    playwright: Any, site: str, name: str, scheme: str, extra: dict[str, Any]
) -> Any:
    """``open_at`` of the Paper test with more data for the page (it copies DATA first)."""
    saved = copy.deepcopy(DATA)
    DATA.update(extra)
    try:
        return await open_at(playwright, site, name, scheme=scheme)
    finally:
        DATA.clear()
        DATA.update(saved)


@pytest.mark.parametrize("scheme", ["dark", "light"])
async def test_approvals_are_outlined_so_only_the_main_action_is_filled(
    scheme: str,
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for name, extra, approve in (
            ("activity", {"/api/approvals": APPROVALS}, ".waiting button.ok"),
            ("memory", {"/api/skills": SKILLS}, ".skill button.ok"),
        ):
            browser, page = await with_data(playwright, static_site, name, scheme, extra)
            try:
                await page.wait_for_selector(approve, state="attached")
                await page.evaluate(
                    "() => document.querySelectorAll('details').forEach(d => { d.open = true; })"
                )
                assert await page.locator(approve).count() == (3 if name == "activity" else 2)
                assert await page.evaluate(FILLED) == [], (name, scheme)
                look = await page.locator(approve).first.evaluate(
                    "b => { const s = getComputedStyle(b);"
                    " return [s.backgroundColor, s.color, s.borderTopColor, s.borderTopWidth]; }"
                )
                assert look[0] == "rgba(0, 0, 0, 0)" and look[1] == look[2], look
                assert look[3] == "1px", look
            finally:
                await browser.close()


PROBE = """(html) => { const box = document.createElement('div'); box.id = 'probe';
  box.innerHTML = html; document.body.prepend(box); }"""
LOOK = """(selector) => { const e = document.querySelector(selector); const s = getComputedStyle(e);
  const back = (n) => { for (; n; n = n.parentElement) { const c = getComputedStyle(n).backgroundColor;
    if (!/, 0\\)$/.test(c) && c !== 'transparent') return c; } return 'rgb(255, 255, 255)'; };
  return {bg: s.backgroundColor, color: s.color, border: s.borderTopColor, width: s.borderTopWidth,
          radius: s.borderTopLeftRadius, left: s.borderLeftWidth, pad: s.paddingLeft,
          ground: back(e.parentElement), weight: s.fontWeight}; }"""
CONTRAST = """(selector) => { const e = document.querySelector(selector);
  const rgb = (c) => c.match(/[\\d.]+/g).map(Number).slice(0, 3).map(v => v / 255);
  const lin = (v) => v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  const lum = (c) => { const [r, g, b] = rgb(c).map(lin); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  let n = e, ground = 'rgb(255, 255, 255)';
  for (; n; n = n.parentElement) { const c = getComputedStyle(n).backgroundColor;
    if (!/, 0\\)$/.test(c) && c !== 'transparent') { ground = c; break; } }
  const a = lum(getComputedStyle(e).color), b = lum(ground);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); }"""
TOKEN = """(name) => { const p = document.createElement('i'); p.style.color = 'var(' + name + ')';
  document.body.appendChild(p); const c = getComputedStyle(p).color; p.remove(); return c; }"""


@pytest.mark.parametrize("scheme", ["dark", "light"])
async def test_every_pill_is_a_thin_outlined_rectangle_readable_in_both_looks(
    scheme: str,
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # (on a page with no local rule for it: the shared rule in style.css decides)
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_at(playwright, static_site, "today", scheme=scheme)
        try:
            await page.evaluate(
                PROBE,
                '<span class="pill" id="p0">plain</span><span class="pill on" id="p1">on</span>'
                '<span class="pill warn" id="p2">warn</span><span class="pill off" id="p3">off</span>',
            )
            colours = {"p1": "--ok", "p2": "--warn", "p3": "--dim"}
            for pill in ("p0", "p1", "p2", "p3"):
                look = await page.evaluate(LOOK, "#" + pill)
                assert look["bg"] == "rgba(0, 0, 0, 0)", (pill, look)  # no fill
                assert look["width"] == "1px" and look["radius"] == "2px", (pill, look)
                if pill in colours:
                    token = await page.evaluate(TOKEN, colours[pill])
                    assert look["color"] == token, (pill, look)
                    if pill != "p3":
                        assert look["border"] == token, (pill, look)
                assert await page.evaluate(CONTRAST, "#" + pill) >= 4.5, (pill, scheme)
        finally:
            await browser.close()


def own_css(path: Any) -> str:
    text = path.read_text(encoding="utf-8")
    css = (
        "\n".join(re.findall(r"<style>(.*?)</style>", text, re.S))
        if path.suffix == ".html"
        else text
    )
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


@pytest.mark.parametrize("scheme", ["dark", "light"])
async def test_the_chosen_one_of_a_group_has_one_look_everywhere(
    scheme: str,
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for name in ("today", "memory", "activity", "settings"):
            browser, page = await open_at(playwright, static_site, name, scheme=scheme)
            try:
                await page.evaluate(
                    PROBE,
                    '<div class="filters"><button class="on" id="c1">a</button>'
                    '<button aria-pressed="true" id="c2">b</button><button id="c0">c</button></div>'
                    '<div class="views"><button class="on" id="c3">d</button></div>'
                    '<div class="seg display"><button aria-pressed="true" id="c4">e</button></div>',
                )
                panel = await page.evaluate(
                    "() => { const p = document.createElement('i');"
                    " p.style.background = 'var(--panel2)'; document.body.appendChild(p);"
                    " const c = getComputedStyle(p).backgroundColor; p.remove(); return c; }"
                )
                accent = await page.evaluate(TOKEN, "--accent")
                for chosen in ("c1", "c2", "c3", "c4"):
                    look = await page.evaluate(LOOK, "#" + chosen)
                    assert look["bg"] == panel, (name, chosen, look)
                    assert look["color"] == accent and look["border"] == accent, (name, chosen)
                    assert look["width"] == "1px", (name, chosen, look)
                rest = await page.evaluate(LOOK, "#c0")
                assert rest["bg"] != panel and rest["color"] != accent, (name, rest)
            finally:
                await browser.close()


@pytest.mark.parametrize("scheme", ["dark", "light"])
async def test_tags_and_callouts_in_answers_have_no_tint(
    scheme: str,
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_at(playwright, static_site, "today", scheme=scheme)
        try:
            await page.evaluate(
                PROBE,
                '<div class="md"><a class="tag" id="t">#garden</a>'
                '<div class="callout callout-warning" id="w"><div class="callout-title">Careful</div>'
                '<div class="callout-body">text</div></div>'
                '<div class="callout" id="n"><div class="callout-body">note</div></div></div>',
            )
            tag = await page.evaluate(LOOK, "#t")
            assert tag["bg"] == "rgba(0, 0, 0, 0)" and tag["pad"] == "0px", tag
            assert tag["color"] == await page.evaluate(TOKEN, "--accent")
            for callout, token in (("w", "--warn"), ("n", "--accent")):
                look = await page.evaluate(LOOK, "#" + callout)
                assert look["bg"] == "rgba(0, 0, 0, 0)" and look["radius"] == "0px", look
                assert look["left"] == "2px", look
                found = await page.locator("#" + callout).evaluate(
                    "e => getComputedStyle(e).borderLeftColor"
                )
                assert found == await page.evaluate(TOKEN, token), (callout, found)
        finally:
            await browser.close()


# -- the chat ---------------------------------------------------------------------------------

CODE = "Here:\n\n```python\nprint('sunday')\n```\n"


async def test_the_code_blocks_copy_button_is_german_and_the_code_is_not(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for lang, word in (("de", "Kopieren"), ("en", "Copy")):
            browser, page = await open_app(
                playwright, static_site, f"localStorage.setItem('starlight-lang', '{lang}');\n"
            )
            try:
                await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
                await page.evaluate(SEND, {"type": "answer", "text": CODE, "conversation": "c1"})
                await page.wait_for_selector(".md-code-bar button")
                assert await page.locator(".md-code-bar button").inner_text() == word
                assert await page.locator(".md-code pre code").inner_text() == "print('sunday')"
                assert (
                    await page.locator(".md-code-bar").evaluate("bar => bar.firstChild.textContent")
                    == "python"
                )
            finally:
                await browser.close()


@pytest.mark.parametrize("width", [1366, 1024])
async def test_the_search_row_is_one_line_in_german_with_its_key(
    width: int,
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(
            playwright,
            static_site,
            "localStorage.setItem('starlight-lang', 'de');\n",
            viewport={"width": width, "height": 768},
        )
        try:
            row = page.locator("#quick-open")
            assert await row.locator("span:not(.ico)").inner_text() == "Springen zu …"
            assert await row.locator("kbd").is_visible()
            assert await row.locator("kbd").inner_text() == "Strg K"
            one = await row.evaluate(
                "r => { const s = r.querySelector('span:not(.ico)'); const k = r.querySelector('kbd');"
                " const line = parseFloat(getComputedStyle(s).lineHeight) ||"
                " parseFloat(getComputedStyle(s).fontSize) * 1.3;"
                " return {high: s.getBoundingClientRect().height, line,"
                " cut: s.scrollWidth > s.clientWidth + 1, row: r.getBoundingClientRect().height,"
                " fits: k.getBoundingClientRect().right <= r.getBoundingClientRect().right + 1}; }"
            )
            assert one["high"] <= one["line"] * 1.3, one  # (one line, not two)
            assert not one["cut"] and one["fits"], one
        finally:
            await browser.close()


# -- high contrast and forced colours, on the pages the review added checks for -----------------

HC_PAGES = ("chat", "notes", "today", "settings")


async def test_activity_dates_follow_the_pages_language(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # (the browser here is en-US: a German page must not show "AM" or "11/12/26")
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    data = copy.deepcopy(DATA)
    data["/api/activity"] = {
        "entries": [{"time": "2026-11-12T15:00:00", "kind": "action", "text": "mail.send ok"}]
    }
    init = (
        "localStorage.setItem('starlight-lang', 'de');\n"
        f"const DATA = {json.dumps(data)};\n"
        "window.fetch = async (url) => { const u = new URL(String(url), location.href).pathname;"
        " return new Response(JSON.stringify(DATA[u] || {}), {headers: {'content-type':"
        " 'application/json'}}); };"
    )
    async with async_api.async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=os.environ.get("STARLIGHT_CHROMIUM") or None
        )
        try:
            page = await browser.new_page(locale="en-US")
            await page.add_init_script(init)
            await page.goto(f"{static_site}/activity.html")
            await page.wait_for_selector(".entry .when")
            assert await page.locator(".entry .when").first.inner_text() == "12.11.26, 15:00:00"
        finally:
            await browser.close()
