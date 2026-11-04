"""Block G5 of the notes plan (docs/plan-notes-app.md): Mermaid diagrams. A ```mermaid block in
a note is drawn as a diagram on the Notes page: the library (vendor/mermaid.js, built in
bundles/mermaid without the ELK layout) loads only for a note that has one, Mermaid runs in
its strict mode, and its drawing is shown as a picture, where nothing can run."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO
from tests.test_chat_page import open_chat
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_notes_page import notes_page, opened
from tests.test_remote import LOCAL

VENDOR = REPO / "starlight" / "web" / "static" / "vendor"
BUILD = REPO / "bundles" / "mermaid"
FREE = {"MIT", "ISC", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "(MPL-2.0 OR Apache-2.0)",
        "Unlicense"}  # fmt: skip
DIAGRAMS = """# Diagrams

```mermaid
graph LR
  Idea[Ein Einfall] --> Plan{Plan?}
  Plan -->|ja| Done((Erledigt))
```

```mermaid
sequenceDiagram
  Paul->>Starlight: Was steht heute an?
  Starlight-->>Paul: Drei Termine
```

```mermaid
graph TD
  A["<img src=x onerror=window.hacked=1>"] --> B["<script>window.hacked=3</script>"]
  click A call alert(1)
  click B href "javascript:window.hacked=2"
```

```mermaid
this is not a diagram
```

```mermaid
---
config:
  layout: elk
---
graph LR
  X --> Y
```
"""


def test_the_shipped_mermaid_bundle_is_the_one_the_build_made() -> None:
    data = (VENDOR / "mermaid.js").read_bytes()
    assert (VENDOR / "mermaid.sha256").read_text("utf-8").split() == [
        hashlib.sha256(data).hexdigest(), "mermaid.js",
    ]  # fmt: skip
    assert b"\r\n" not in data and 2_000_000 < len(data) < 4_500_000
    assert b"elk.bundled" not in data and b"Starlight's Mermaid has no ELK layout" in data
    package = json.loads((BUILD / "package.json").read_text("utf-8"))
    pinned = {**package["dependencies"], **package["devDependencies"]}
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in pinned.values()), pinned
    lock = json.loads((BUILD / "package-lock.json").read_text("utf-8"))["packages"]
    licences = (VENDOR / "mermaid-LICENSES.txt").read_text("utf-8")
    listed = re.findall(r"^(\S+) (\d+\.\d+\.\d+) \((.+)\)$", licences, re.MULTILINE)
    names = {name for name, _, _ in listed}
    assert "mermaid" in names and "dompurify" in names and "elkjs" not in names
    for name, version, kind in listed:
        assert lock[f"node_modules/{name}"]["version"] == version, name
        assert kind in FREE, (name, kind)
    assert pinned["mermaid"] == lock["node_modules/mermaid"]["version"]
    assert licences.count("=" * 78) == len(listed) >= 40  # (each with its licence text)


def test_the_web_app_serves_the_mermaid_bundle(app: Any) -> None:
    served = app.get("/vendor/mermaid.js", headers=LOCAL)
    assert served.status_code == 200 and served.headers["content-type"].startswith(
        "text/javascript"
    )
    assert hashlib.sha256(served.content).hexdigest() in (VENDOR / "mermaid.sha256").read_text()
    assert "\ndompurify 3." in app.get("/vendor/mermaid-LICENSES.txt", headers=LOCAL).text
    assert app.get("/vendor/mermaid.sha256", headers=LOCAL).status_code == 404  # (not listed)


@pytest.mark.parametrize(("scheme", "locale"), [("light", "en-US"), ("dark", "de-DE")])
async def test_diagrams_on_the_notes_page(
    static_site: str,
    app: Any,
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    scheme: str,
    locale: str,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    german = locale.startswith("de")
    (notebook / "Notes" / "Diagrams.md").write_text(DIAGRAMS, encoding="utf-8")
    async with notes_page(static_site, app, locale=locale, scheme=scheme) as page:
        requests: list[str] = []
        page.on("request", lambda r: requests.append(r.url))
        await opened(page, f"{static_site}/notes.html?path=Notes%2FPlan.md")  # no diagram:
        await page.wait_for_selector("#column .note-body p")
        assert not [u for u in requests if "mermaid" in u]  # the library is not loaded
        await opened(page, f"{static_site}/notes.html?path=Notes%2FDiagrams.md")
        await page.wait_for_function(
            "() => document.querySelectorAll('.diagram-picture, .diagram-error').length === 5",
            timeout=60_000,
        )
        pictures = await page.locator(".diagram-picture").evaluate_all(
            "is => is.map(i => [i.alt, i.naturalWidth > 50, i.getAttribute('src').slice(0, 28)])"
        )
        word = "Diagramm" if german else "Diagram"
        assert pictures == [
            [f"{word}: graph LR", True, "data:image/svg+xml;charset=u"],
            [f"{word}: sequenceDiagram", True, "data:image/svg+xml;charset=u"],
            [f"{word}: graph TD", True, "data:image/svg+xml;charset=u"],
        ]
        sources = await page.locator(".diagram-picture").evaluate_all(
            "is => is.map(i => decodeURIComponent(i.getAttribute('src')))"
        )
        assert "Einfall" in sources[0] and "Termine" in sources[1]
        assert all("<script" not in s and "onerror" not in s for s in sources)
        light = "#ECECFF" in sources[0]  # (Mermaid's light colours; the dark theme has others)
        assert light == (scheme == "light")
        assert await page.evaluate("() => window.hacked") is None  # nothing ran
        # (pictures; the svg.ico are the note's toolbar icons)
        assert await page.locator("#column svg:not(.ico), #column script").count() == 0
        errors = await page.locator(".diagram-error").all_text_contents()
        cannot = (
            "Dieses Diagramm ließ sich nicht zeichnen:"
            if german
            else ("This diagram could not be drawn:")
        )
        assert errors[0].startswith(cannot) and "No diagram type detected" in errors[0]
        assert "ELK layout" in errors[1]  # (the one layout left out says so)
        assert await page.locator(".diagram-code[open]").count() == 2  # (their code shows)
        summary = page.locator(".diagram-code summary").first
        assert await summary.text_content() == ("Code zeigen" if german else "Show the code")
        await summary.click()
        code = await page.locator(".diagram-code").first.locator("pre").text_content()
        assert code.startswith("graph LR\n  Idea[Ein Einfall]")
        loaded = [u for u in requests if "mermaid" in u]
        assert len(loaded) == 1 and loaded[0].endswith("/vendor/mermaid.js")  # once
        outside = [u for u in requests if not u.startswith((static_site, "data:"))]
        assert not [u for u in outside if "127.0.0.1" not in u], outside  # nothing from outside
        assert page.errors == []  # type: ignore[attr-defined]


async def test_elsewhere_a_mermaid_block_stays_code(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_chat(playwright, static_site)  # (a chat answer)
        try:
            shown = await page.evaluate(
                """() => { const box = document.createElement('div');
                  renderMarkdown(box, '```mermaid\\ngraph LR\\n  A --> B\\n```');
                  return [box.querySelectorAll('.md-code').length,
                          box.querySelectorAll('.diagram').length, box.textContent]; }"""
            )
            assert shown[:2] == [1, 0] and "A --> B" in shown[2]
        finally:
            await browser.close()
