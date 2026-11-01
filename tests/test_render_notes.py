"""Block B4 of the notes plan (docs/plan-notes-app.md): chat answers (and later the Notes
page) show links between notes, pictures, tags, callouts and task boxes (render.js), safely."""

from __future__ import annotations

from typing import Any

import pytest

from tests.test_chat_page import SEND, open_chat
from tests.test_chat_page import static_site as static_site  # (the fixture)

ANSWER = """I put it in [[Shopping]] (see [[Plan#Week 2|the plan]] and [[Recipes/Pancakes]]).

![[holiday.png|300]]
![[Pictures/cat.jpg]]
![[Weekly review]]

Tags: #home and #work/projects, not #123 or C# or `#code`.

> [!tip] Buy early
> The shop opens at **8**.

> [!warning]- Folded
> hidden at first

> [!unknown]
> falls back to a note

> Just a quote with [[Link]].

- [ ] milk due: 2026-11-05
- [x] bread
- [-] cake
- not a task

Inert: [[<img src=x onerror="window.hacked=1">]] ![[../../secret.png]] ![[https://evil.example/x.png]]
[[javascript:window.hacked=2]] #<b>bold</b> `[[code]]`"""


async def test_notes_syntax_in_an_answer(static_site: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_chat(playwright, static_site)
        try:
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            await page.evaluate(SEND, {"type": "answer", "text": ANSWER, "conversation": "c1"})
            body = page.locator(".msg.bot .body").last
            links: Any = await body.locator("a.note-link").evaluate_all(
                "as => as.map(a => [a.textContent, a.getAttribute('href'), a.className])"
            )
            assert links[:4] == [
                ["Shopping", "/notes?open=Shopping", "note-link"],
                ["the plan", "/notes?open=Plan&heading=Week+2", "note-link"],
                ["Recipes/Pancakes", "/notes?open=Recipes%2FPancakes", "note-link"],
                ["Weekly review", "/notes?open=Weekly+review", "note-link note-embed"],
            ]
            pictures = await body.locator("img.note-picture").evaluate_all(
                "is => is.map(i => [i.getAttribute('src'),"
                " i.width ? i.getAttribute('width') : null])"
            )
            assert pictures == [
                ["/api/notes/file/Notes/Attachments/holiday.png", "300"],
                ["/api/notes/file/Pictures/cat.jpg", None],
            ]
            tags = await body.locator("a.tag").evaluate_all(
                "ts => ts.map(t => [t.textContent, t.getAttribute('href')])"
            )
            assert tags[:2] == [
                ["#home", "/notes?tag=home"],
                ["#work/projects", "/notes?tag=work%2Fprojects"],
            ]
            assert "#123" not in [t[0] for t in tags] and "#code" not in [t[0] for t in tags]

            callouts = body.locator(".callout")
            assert await callouts.count() == 3
            tip = callouts.nth(0)
            assert await tip.get_attribute("class") == "callout callout-tip"
            assert await tip.locator(".callout-text").text_content() == "Buy early"
            assert await tip.locator(".callout-body strong").text_content() == "8"
            folded = callouts.nth(1)
            assert await folded.evaluate("d => d.tagName === 'DETAILS' && !d.open")
            assert await callouts.nth(2).get_attribute("class") == "callout callout-note"
            assert await callouts.nth(2).locator(".callout-text").text_content() == "Unknown"
            assert await body.locator("blockquote a.note-link").text_content() == "Link"

            items = await body.locator("li.task").evaluate_all(
                "ls => ls.map(l => [l.dataset.status, l.querySelector('input').checked,"
                " l.querySelector('input').disabled, l.textContent.trim()])"
            )
            assert items == [
                ["open", False, True, "milk due: 2026-11-05"],
                ["done", True, True, "bread"],
                ["cancelled", True, True, "cake"],
            ]
            assert await body.locator("li:not(.task)").count() == 1

            # nothing runs, nothing loads from elsewhere, nothing leaves the notebook
            assert await page.evaluate("() => window.hacked") is None
            assert await body.locator("b, script, img:not(.note-picture)").count() == 0
            hrefs = await body.locator("a").evaluate_all(
                "as => as.map(a => a.getAttribute('href'))"
            )
            assert all(h.startswith(("/notes?", "http")) for h in hrefs), hrefs
            sources = await body.locator("img").evaluate_all(
                "is => is.map(i => i.getAttribute('src'))"
            )
            assert all(s.startswith("/api/notes/file/") and ".." not in s for s in sources)
            text = await body.text_content()
            assert '<img src=x onerror="window.hacked=1">' in text  # shown as text
            assert await body.locator("code").last.text_content() == "[[code]]"
            spoken = await page.evaluate("t => markdownToSpeech(t)", ANSWER)
            assert "[[" not in spoken and "the plan" in spoken and "[!tip]" not in spoken
        finally:
            await browser.close()


async def test_without_render_js_notes_syntax_stays_text(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_chat(playwright, static_site)
        try:
            shown = await page.evaluate(
                """() => { Object.keys(mdHooks).forEach(k => mdHooks[k] = null);
                  const box = document.createElement('div');
                  renderMarkdown(box, 'See [[Shopping]] #home\\n\\n> [!tip] x\\n\\n- [ ] milk');
                  return [box.textContent, box.querySelectorAll('a, input, .callout').length]; }"""
            )
            assert shown == ["See [[Shopping]] #home[!tip] x[ ] milk", 0]
        finally:
            await browser.close()


def test_the_chat_page_loads_render_js(tmp_path: Any) -> None:
    from tests.test_web_chat import LOCAL, client, web_home

    with client(web_home(tmp_path / "h")) as c:  # type: ignore[attr-defined]
        script = c.get("/render.js", headers=LOCAL)
        page = c.get("/", headers=LOCAL).text
        worker = c.get("/sw.js", headers=LOCAL).text
    assert script.status_code == 200 and "mdHooks.wikilink" in script.text
    assert script.headers["content-type"].startswith("text/javascript")
    assert page.index('src="/markdown.js"') < page.index('src="/render.js"')
    assert '"/render.js"' in worker  # (the phone keeps it, like markdown.js)
