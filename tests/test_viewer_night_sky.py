# ruff: noqa: E501  (the pages' scripts are written out as long strings)
"""Block R3 of the round 2 plan (docs/plan-round-2.md): the statistics page (starlight/viewer) in
the Night Sky look and in German. It takes its look from the chat's own files (served by the
viewer, and only those), has no colour of its own, shows night, day and "like your system",
keeps gold for the star, writes numbers in mono, stays inside 360 px, and has no English left
in German. The tests start the real viewer app on a free port, on a stats.db with test data."""

from __future__ import annotations

import json
import random
import re
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.core import Paths
from starlight.core.stats import SCHEMA
from tests.conftest import REPO
from tests.test_german_everywhere import COLLECT, english_left, executable

STATIC = REPO / "starlight" / "web" / "static"
PAGE = REPO / "starlight" / "viewer" / "static" / "index.html"
COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
GOLD = "rgb(242, 193, 78)"  # --primary, the same in night and day


def seed(db_path: Path) -> None:
    """A day and a bit of realistic data: speed, GPU, VRAM, tasks, web bursts, tool calls (some
    failed), two models, a few errors, skills, memory, a benchmark and a fresh heartbeat."""
    rng = random.Random(7)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA)
    now = time.time()
    rows: list[tuple[float, str, float, str]] = []

    def add(ts: float, name: str, value: float, **labels: Any) -> None:
        rows.append((ts, name, value, json.dumps(labels, sort_keys=True)))

    models = ["qwen3-8b-q4", "Memory"]  # (a model called like a German word stays as it is)
    for step in range(0, 26 * 60, 2):
        ts = now - step * 60
        model = models[(step // 40) % 2]
        add(ts, "model.tokens_per_second", 28 + 14 * rng.random(), model=model)
        add(ts, "model.duration_s", 1.2 + rng.random() * 2, model=model)
        add(ts, "gpu.utilization_percent", 35 + 55 * rng.random())
        add(ts, "gpu.vram_used_bytes", (5.2 + 1.8 * rng.random()) * 2**30)
        add(ts, "gpu.vram_total_bytes", 12 * 2**30)
        if rng.random() < 0.7:
            add(ts, "tasks.completed", 1, steps=2)
    for _ in range(500):
        ts = now - rng.random() * 26 * 3600
        for _ in range(rng.randint(1, 12)):
            add(ts + rng.random() * 0.9, "web.requests", 1, host="127.0.0.1")
    for module, tool, n in (
        ("web", "web.search", 40),
        ("memory", "memory.recall", 26),
        ("<i>mail</i>", "mail.send", 12),  # (a name with markup is shown as text)
        ("calendar", "calendar.add", 9),
        ("files", "files.read", 6),
    ):
        for _ in range(n):
            ok = rng.random() > 0.12
            add(now - rng.random() * 3600, "tool.calls", 1, module=module, tool=tool, ok=ok,
                model=rng.choice(models))  # fmt: skip
    add(now - 300, "errors", 1, source="kernel")
    add(now - 900, "errors", 1, source="model")
    add(now - 5, "core.heartbeat", 1)
    add(now - 5, "skills.count", 4, status="approved")
    add(now - 5, "skills.count", 2, status="draft")
    add(now - 5, "memory.entries", 37)
    add(now - 4000, "whatsapp.received", 3)
    add(now - 4000, "model.bench.tokens_per_second", 41.5)
    add(now - 4000, "model.bench.gpu_fraction", 0.92)
    add(now - 3000, "model.exam_score", 7, provider="qwen3-8b-q4", of=8)
    db.executemany("INSERT INTO metrics VALUES (?, ?, ?, ?)", rows)
    db.commit()
    db.close()


@pytest.fixture
def viewer(tmp_path: Path) -> Iterator[str]:
    """The real viewer app on a free port, over a home with seeded statistics."""
    import uvicorn

    from starlight.viewer.app import create_app

    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "starlight.yaml").write_text(
        "model:\n  provider: echo\nkill_switch:\n  hotkey: null\n", encoding="utf-8"
    )
    paths = Paths(home)
    seed(paths.stats_db)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(paths), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


# -- the files: one source for the look, and nothing else of the chat's folder ------------------


def test_the_viewer_serves_the_looks_files_and_nothing_else(viewer: str) -> None:
    with httpx.Client(base_url=viewer, trust_env=False) as client:
        for name, kind in (
            ("style.css", "text/css"),
            ("i18n.js", "javascript"),
            ("common.js", "javascript"),
            ("icon.svg", "image/svg+xml"),
            ("vendor/space-grotesk-latin.woff2", "font/woff2"),
            ("vendor/space-grotesk-latin-ext.woff2", "font/woff2"),
        ):
            reply = client.get("/" + name)
            assert reply.status_code == 200 and kind in reply.headers["content-type"], name
            assert reply.content == (STATIC / name).read_bytes()  # (the chat's own file)
        for name in (
            "chat.html",
            "settings.html",
            "notes.html",
            "chat-core.js",
            "sw.js",
            "quick.js",
            "markdown.js",
            "chat.css",
            "vendor/editor.js",
            "vendor/LICENSES.txt",
            "vendor/../chat.html",
            "static/chat.html",
        ):
            assert client.get("/" + name).status_code == 404, name


# -- the page has no colour of its own ----------------------------------------------------------


def test_the_page_has_no_colour_of_its_own() -> None:
    html = PAGE.read_text(encoding="utf-8")
    css = "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert not COLOUR.findall(css), COLOUR.findall(css)
    script = html[html.index("<script>") :]
    assert not COLOUR.findall(script)  # (the charts take their colours from classes)
    assert "--page" not in html and "--surface-1" not in html and "--series-1" not in html
    assert '<link rel="stylesheet" href="/style.css">' in html


# -- in the browser ------------------------------------------------------------------------------

READY = (
    "document.querySelectorAll('#tiles .tile').length === 8 && document.querySelector('#c-tps svg')"
)


async def opened(
    playwright: Any,
    viewer: str,
    *,
    scheme: str = "dark",
    width: int = 1280,
    query: str = "",
    locale: str = "en-US",
    theme: str | None = None,
) -> tuple[Any, Any, list[str]]:
    """The page open with its data drawn; also the console errors, which there must be none of."""
    browser = await playwright.chromium.launch(executable_path=executable())
    context = await browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme, locale=locale
    )
    page = await context.new_page()
    problems: list[str] = []
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(str(e)))
    if theme:
        await page.add_init_script(f"localStorage.setItem('starlight-theme', '{theme}')")
    await page.goto(f"{viewer}/{query}")
    await page.wait_for_function(READY)
    return browser, page, problems


@pytest.fixture
def playwright_api(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    return pytest.importorskip("playwright.async_api")


TOKEN = """(name) => { const p = document.createElement('div');
  p.style.background = 'var(--' + name + ')'; document.body.appendChild(p);
  const c = getComputedStyle(p).backgroundColor; p.remove(); return c; }"""


@pytest.mark.parametrize("scheme", ["dark", "light"])
async def test_page_and_cards_use_the_tokens(viewer: str, playwright_api: Any, scheme: str) -> None:
    async with playwright_api.async_playwright() as playwright:
        browser, page, problems = await opened(playwright, viewer, scheme=scheme)
        try:
            bg = await page.evaluate(TOKEN, "bg")
            panel = await page.evaluate(TOKEN, "panel")
            assert bg != panel
            assert await page.evaluate("getComputedStyle(document.body).backgroundColor") == bg
            cards = await page.evaluate(
                "[...document.querySelectorAll('.card')].map(c => [getComputedStyle(c).backgroundColor,"
                " getComputedStyle(c).borderRadius, getComputedStyle(c).paddingLeft])"
            )
            assert len(cards) == 16  # 8 tiles and 8 cards
            assert all(c == [panel, "16px", "20px"] or c[0] == panel for c in cards), cards
            assert {c[1] for c in cards} == {"16px"}
            assert problems == []  # (common.js asks for nothing that is not on this port)
        finally:
            await browser.close()


async def test_night_by_default_day_as_a_twin_and_the_theme_button(
    viewer: str, playwright_api: Any
) -> None:
    async with playwright_api.async_playwright() as playwright:
        for scheme, bg in (("dark", "rgb(11, 14, 26)"), ("light", "rgb(245, 246, 251)")):
            browser, page, _ = await opened(playwright, viewer, scheme=scheme)  # like the system
            try:
                assert await page.evaluate("document.documentElement.dataset.theme") is None
                assert await page.evaluate("getComputedStyle(document.body).backgroundColor") == bg
                button = page.locator("#theme")
                assert await button.get_attribute("data-icon") == "system"
                await button.click()  # system -> light
                assert await page.evaluate("document.documentElement.dataset.theme") == "light"
                assert await page.evaluate("getComputedStyle(document.body).backgroundColor") == (
                    "rgb(245, 246, 251)"
                )
                await button.click()  # light -> dark
                assert await page.evaluate("getComputedStyle(document.body).backgroundColor") == (
                    "rgb(11, 14, 26)"
                )
                await button.click()  # dark -> like the system
                assert await page.evaluate("document.documentElement.dataset.theme") is None
            finally:
                await browser.close()


async def test_the_brand_row_the_title_and_the_headings(viewer: str, playwright_api: Any) -> None:
    async with playwright_api.async_playwright() as playwright:
        browser, page, _ = await opened(playwright, viewer)
        try:
            assert await page.locator(".brand svg.ico .star").count() == 1
            for selector in (".brand-name", "h1", ".card h2"):
                font = await page.locator(selector).first.evaluate(
                    "n => getComputedStyle(n).fontFamily"
                )
                assert "Space Grotesk" in font, (selector, font)
            assert await page.locator("h1").inner_text() == "Statistics"
            # the heading font is really loaded from the viewer itself
            await page.evaluate("document.fonts.ready")
            assert await page.evaluate("document.fonts.check('600 20px \"Space Grotesk\"')")
        finally:
            await browser.close()


@pytest.mark.parametrize("scheme", ["dark", "light"])
async def test_nothing_is_gold_but_the_star(viewer: str, playwright_api: Any, scheme: str) -> None:
    async with playwright_api.async_playwright() as playwright:
        browser, page, _ = await opened(playwright, viewer, scheme=scheme)
        try:
            await page.locator("[data-minutes='1440']").click()  # (a filter chosen: still not gold)
            gold = await page.evaluate(
                """(gold) => {
                  const found = [];
                  for (const n of document.querySelectorAll('body *')) {
                    if (n.closest('.brand')) continue;
                    const s = getComputedStyle(n);
                    for (const k of ['color', 'backgroundColor', 'borderTopColor', 'fill', 'stroke'])
                      if (s[k] === gold) found.push(n.tagName + '.' + n.className + ' ' + k);
                  }
                  return found;
                }""",
                GOLD,
            )
            assert gold == []
            assert await page.locator("button.primary").count() == 0  # no main action here
            chosen = page.locator(".filters button[aria-pressed=true]")
            accent = await page.evaluate(
                "getComputedStyle(document.documentElement).getPropertyValue('--accent')"
            )
            probe = await page.evaluate(TOKEN, "accent")
            await page.mouse.move(0, 0)  # (the colour glides for a moment after the click)
            await page.wait_for_function(
                "([s, c]) => getComputedStyle(document.querySelector(s)).backgroundColor === c",
                arg=[".filters button[aria-pressed=true]", probe],
            )
            assert accent.strip() and await chosen.count() == 1
        finally:
            await browser.close()


async def test_numbers_are_in_mono(viewer: str, playwright_api: Any) -> None:
    async with playwright_api.async_playwright() as playwright:
        browser, page, _ = await opened(playwright, viewer)
        try:
            mono = await page.evaluate(
                "(() => { const p = document.createElement('code'); p.style.fontFamily = 'var(--font-mono)';"
                " document.body.appendChild(p); const f = getComputedStyle(p).fontFamily; p.remove(); return f; })()"
            )
            await page.locator("details summary").first.click()
            for selector in (
                ".tile .value",
                "#c-tps .tick",
                "#c-web .ref-label",
                "#t-tps td",
                "#t-models td:nth-child(2)",
                "#errors time",
                ".sub .num",
            ):
                font = await page.locator(selector).first.evaluate(
                    "n => getComputedStyle(n).fontFamily"
                )
                assert font == mono, (selector, font)
            variant = await page.locator(".tile .value").first.evaluate(
                "n => getComputedStyle(n).fontVariantNumeric"
            )
            assert "tabular-nums" in variant
        finally:
            await browser.close()


CONTRAST = """() => {
  const rgb = (c) => { const m = c.match(/[\\d.]+/g).map(Number); return m.slice(0, 3).map(v => v / 255); };
  const lin = (v) => v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  const lum = (c) => { const [r, g, b] = rgb(c).map(lin); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const backdrop = (n) => { for (; n; n = n.parentElement) {
    const c = getComputedStyle(n).backgroundColor;
    if (!/rgba\\(.*, 0\\)$/.test(c) && c !== 'transparent') return c; } return getComputedStyle(document.body).backgroundColor; };
  const out = [];
  const sels = ['.brand-name', 'h1', '#status', '.filters button[aria-pressed=true]',
    '.filters button[aria-pressed=false]', '.tile .label', '.tile .value', '.tile .note', '.card h2',
    '.card .sub', 'details summary', '#t-models th', '#t-models td', '#errors li', '#errors time',
    '#c-tps .tick', '#c-web .ref-label', '#c-modules .tick'];
  for (const s of sels) {
    const n = document.querySelector(s);
    if (!n) { out.push([s, 0]); continue; }
    const style = getComputedStyle(n);
    const fg = n instanceof SVGElement ? style.fill : style.color;
    const a = lum(fg), b = lum(backdrop(n));
    out.push([s, (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)]);
  }
  return out;
}"""


@pytest.mark.parametrize("scheme", ["dark", "light"])
async def test_text_contrast_is_at_least_4_5_to_1(
    viewer: str, playwright_api: Any, scheme: str
) -> None:
    async with playwright_api.async_playwright() as playwright:
        browser, page, _ = await opened(playwright, viewer, scheme=scheme)
        try:
            for ratio in await page.evaluate(CONTRAST):
                assert ratio[1] >= 4.5, (scheme, ratio)
        finally:
            await browser.close()


@pytest.mark.parametrize("scheme", ["dark", "light"])
@pytest.mark.parametrize("minutes", [60, 10080])
@pytest.mark.parametrize("query", ["", "?lang=de"])
async def test_nothing_goes_sideways_at_360_px(
    viewer: str, playwright_api: Any, scheme: str, minutes: int, query: str
) -> None:
    async with playwright_api.async_playwright() as playwright:
        browser, page, _ = await opened(playwright, viewer, scheme=scheme, width=360, query=query)
        try:
            await page.locator(f"[data-minutes='{minutes}']").click()
            await page.wait_for_function(
                f"document.querySelector(\"[data-minutes='{minutes}']\").getAttribute('aria-pressed') === 'true'"
            )
            await page.locator("details summary").first.click()
            width = await page.evaluate("document.documentElement.scrollWidth")
            assert width <= 360, width
            # the cards sit inside the screen, and every chart is drawn at the card's width
            edges = await page.evaluate(
                "[...document.querySelectorAll('.card, .chart svg')].map(n => n.getBoundingClientRect().right)"
            )
            assert max(edges) <= 360
        finally:
            await browser.close()


# -- German --------------------------------------------------------------------------------------


async def texts(page: Any) -> list[tuple[str, str, str]]:
    return [tuple(t) for t in await page.evaluate(COLLECT)]  # type: ignore[misc]


async def test_in_german_no_english_is_left(viewer: str, playwright_api: Any) -> None:
    async with playwright_api.async_playwright() as playwright:
        browser, english, _ = await opened(playwright, viewer, query="?lang=en")
        english_texts = {t for _, t, _ in await texts(english)}
        await browser.close()
        browser, page, problems = await opened(playwright, viewer, query="?lang=de")
        try:
            await page.locator("details summary").first.click()
            await page.locator("[data-minutes='1440']").click()
            await page.wait_for_function("document.querySelector('#c-web svg')")
            assert await page.evaluate("document.documentElement.lang") == "de"
            assert await page.title() == "Starlight-Statistik"
            # (the error lines name a tool or a source: the name stays as it is)
            left = [
                t
                for t in await english_left(page)
                if not re.match(r"^(Werkzeug [\w.*-]+ fehlgeschlagen|Fehler in \S+)$", t[1])
            ]
            assert left == []
            # a text that is the same as in English must be a name, a unit or a number
            same = {
                t
                for _, t, _ in await texts(page)
                if t in english_texts and re.search(r"[A-Za-z]{3,}", t)
            }
            assert same <= {"Starlight", "Memory", "Tokens/s", "GPU %", "<i>mail</i>"}, same
            body = await page.inner_text("body")
            for text in ("Kern läuft", "Werkzeugaufrufe pro Modul", "Letzte Stunde"):
                assert text in body, text
            assert await page.get_attribute("#tiles", "aria-label") == "Kennzahlen"
            # the tooltip and the error lines are German too
            await page.locator("#c-web .hit").last.hover(force=True)
            await page.wait_for_function("document.querySelector('#tooltip').offsetWidth > 0")
            assert "geschäftigsten Sekunde" in await page.inner_text("#tooltip")
            assert (
                "Werkzeug mail.send fehlgeschlagen" in body
                or "Werkzeug web.search fehlgeschlagen" in body
            )
            assert "Fehler in kernel" in body
            assert problems == []
        finally:
            await browser.close()


async def test_numbers_follow_the_pages_language(viewer: str, playwright_api: Any) -> None:
    async with playwright_api.async_playwright() as playwright:
        browser, page, _ = await opened(playwright, viewer, query="?lang=de", locale="en-US")
        try:
            value = await page.locator("#tiles .tile").nth(4).locator(".value").inner_text()
            assert re.fullmatch(r"\d+,\d", value), value  # 33,4: a German comma
        finally:
            await browser.close()
        browser, page, _ = await opened(playwright, viewer, query="?lang=en", locale="de-DE")
        try:  # ?lang=en wins over a German browser
            value = await page.locator("#tiles .tile").nth(4).locator(".value").inner_text()
            assert re.fullmatch(r"\d+\.\d", value), value
            assert await page.evaluate("document.documentElement.lang") == "en"
        finally:
            await browser.close()
        browser, page, _ = await opened(playwright, viewer, locale="de-DE")
        try:  # no ?lang: the browser's language
            assert await page.evaluate("document.documentElement.lang") == "de"
        finally:
            await browser.close()


async def test_names_from_outside_are_text_and_are_not_translated(
    viewer: str, playwright_api: Any
) -> None:
    async with playwright_api.async_playwright() as playwright:
        browser, page, _ = await opened(playwright, viewer, query="?lang=de")
        try:
            names = await page.locator("#t-models td:first-child").all_inner_texts()
            assert "Memory" in names and "qwen3-8b-q4" in names  # (not "Gedächtnis")
            await page.locator("details summary").nth(5).click()
            assert "<i>mail</i>" in await page.locator("#t-modules").inner_text()
            assert await page.locator("#t-modules i, #c-modules i").count() == 0  # not markup
        finally:
            await browser.close()
