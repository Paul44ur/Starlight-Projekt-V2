"""Block G1 of the notes plan (docs/plan-notes-app.md): a note as one HTML file that works on
its own (its pictures inside, links between notes as plain text) or as a PDF, from the
Notes page and for the assistant ("send me Project X as PDF")."""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from tests.test_chat_page import CHROMIUM
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_home_devices import call, fails
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_notes_page import notes_page, opened
from tests.test_remote import LOCAL

GARDEN = """---
tags: [garden]
status: open
aliases: [Garten]
---
# Garden

A plan for the **garden** with *roses*, `code` and a [[Tools|link]]. Größe: 12 m².

- [ ] Buy seeds due: 2026-11-05
- [x] Dig the bed

> [!tip] Water early
> The soil stays wet longer.

| Plant | Water |
|---|---|
| Roses | daily |

![[photo.png|200]]
![[Notes/Attachments/other.jpg]]
![[../../secret.png]]
![[fake.png]]

</script><script>window.hacked = 1</script> and <b>bold?</b>
"""


def png(width: int = 40, height: int = 20) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), "#4c8bf5").save(out, "PNG")
    return out.getvalue()


def garden(root: Path) -> Path:
    attachments = root / "Notes" / "Attachments"
    attachments.mkdir(parents=True, exist_ok=True)
    (attachments / "photo.png").write_bytes(png())
    (attachments / "other.jpg").write_bytes(png(30, 30))  # (named .jpg, a PNG: its bytes count)
    (attachments / "fake.png").write_text("not a picture", "utf-8")
    (root.parent / "secret.png").write_bytes(png())
    note = root / "Notes" / "Garden.md"
    note.write_text(GARDEN, encoding="utf-8")
    return note


def test_the_pictures_a_note_shows_and_nothing_else(tmp_path: Path) -> None:
    from starlight.notebook import export

    root = tmp_path / "Starlight"
    garden(root)
    found = export.pictures(root, GARDEN)
    assert sorted(found) == ["Notes/Attachments/other.jpg", "Notes/Attachments/photo.png"]
    assert all(v.startswith("data:image/png;base64,") for v in found.values())
    assert export.picture_path("photo.png") == "Notes/Attachments/photo.png"
    for bad in ("../x.png", "/etc/x.png", "C:/x.png", "https://x.example/a.png", ".hidden/a.png"):
        assert export.picture_path(bad) is None, bad
    svg = root / "Notes" / "Attachments" / "drawing.svg"
    svg.write_text("<svg onload='alert(1)'/>", "utf-8")
    assert export.read_picture(root, "Notes/Attachments/drawing.svg") is None  # (SVG can run code)


def test_markdown_in_the_pdf_is_escaped_markup() -> None:
    from starlight.notebook.export import inline

    assert inline("a **b** *c* `<d>` ~~e~~ [[Note|shown]] [[Plain#Part]] [site](https://x)") == (
        'a <b>b</b> <i>c</i> <font face="Courier">&lt;d&gt;</font> <strike>e</strike> '
        "shown Plain site"
    )
    assert inline("<b>not markup</b> & more") == "&lt;b&gt;not markup&lt;/b&gt; &amp; more"


def test_the_pdf(tmp_path: Path) -> None:
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("reportlab")
    from starlight.notebook import export, markdown

    root = tmp_path / "Starlight"
    garden(root)
    data = export.to_pdf("Garden", markdown.split(GARDEN).body, root, when="Notes · 2026-11-02")
    assert data.startswith(b"%PDF")
    reader = pypdf.PdfReader(io.BytesIO(data))
    text = " ".join(page.extract_text() for page in reader.pages)
    assert text.count("Garden") == 1  # (the title once: the first heading is the same)
    for words in ("A plan for the garden with roses", "Größe: 12 m²", "Buy seeds",
                  "Water early", "Roses", "daily", "link"):  # fmt: skip
        assert words in text, words
    assert "[[" not in text and "**" not in text
    assert sum(len(page.images) for page in reader.pages) == 2  # (photo and other, not the rest)


def test_without_reportlab_the_pdf_says_so(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import builtins

    from starlight.notebook import export

    real = builtins.__import__

    def no_reportlab(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("reportlab"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_reportlab)
    with pytest.raises(export.ExportError, match="'pdf' extra"):
        export.to_pdf("x", "text", tmp_path)


def test_the_export_api(app: Any, notebook: Path) -> None:
    garden(notebook)
    query = {"path": "Notes/Garden.md"}
    page = app.get("/api/note/export", params=query, headers=LOCAL)
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert page.headers["content-disposition"].startswith('attachment; filename="Garden.html"')
    text = page.text
    assert "<title>Garden</title>" in text and "window.hacked = 1</script>" not in text
    data = json.loads(re.search(r'id="data">(.*?)</script>', text, re.S).group(1))  # type: ignore[union-attr]
    assert data["title"] == "Garden" and data["print"] is False
    assert data["properties"] == "tags: garden · status: open"  # (other names are left out)
    assert sorted(data["pictures"]) == [
        "Notes/Attachments/other.jpg",
        "Notes/Attachments/photo.png",
    ]
    assert data["text"].startswith("\nA plan for the **garden**")  # (no properties block, and
    # no first heading that only says the title again)
    shown = app.get(
        "/api/note/export", params={**query, "show": "true", "lang": "de"}, headers=LOCAL
    )
    assert shown.headers["content-disposition"].startswith("inline")
    assert "Drucken oder als PDF speichern" in shown.text and '"print": true' in shown.text
    pdf = app.get("/api/note/export", params={**query, "format": "pdf"}, headers=LOCAL)
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert 'filename="Garden.pdf"' in pdf.headers["content-disposition"]
    umlaut = notebook / "Notes" / "Größe.md"
    umlaut.write_text("Text\n", "utf-8")
    named = app.get("/api/note/export", params={"path": "Notes/Größe.md"}, headers=LOCAL)
    assert "filename*=UTF-8''Gr%C3%B6%C3%9Fe.html" in named.headers["content-disposition"]
    for bad in ("../secret.md", "Notes/Nothing.md", "Notes/Attachments/photo.png"):
        assert app.get("/api/note/export", params={"path": bad}, headers=LOCAL).status_code in (
            400, 404,
        ), bad  # fmt: skip


async def test_the_exported_page_works_on_its_own(
    app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    import os

    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None:
        pytest.skip("no Chromium")
    garden(notebook)
    text = app.get("/api/note/export", params={"path": "Notes/Garden.md"}, headers=LOCAL).text
    async with async_api.async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=executable)
        try:
            page = await browser.new_page()
            requests: list[str] = []
            page.on("request", lambda r: requests.append(r.url))
            await page.set_content(text)
            await page.wait_for_selector("#note p")
            assert (
                await page.locator("#note h1, #note h2, #note h3").count() == 0
            )  # (the title once)
            assert await page.locator("#title").text_content() == "Garden"
            assert await page.locator("#note strong").first.text_content() == "garden"
            assert await page.locator("#note a").count() == 0  # (links became text)
            assert await page.locator("#note .note-link").first.text_content() == "link"
            sizes = await page.locator("#note img").evaluate_all(
                "is => is.map(i => [i.naturalWidth, i.getAttribute('src').slice(0, 15)])"
            )
            assert sizes == [[40, "data:image/png;"], [30, "data:image/png;"]]
            assert await page.locator(".callout").count() == 1
            assert await page.evaluate("() => window.hacked") is None
            assert not [u for u in requests if not u.startswith(("data:", "about:"))]  # no network
        finally:
            await browser.close()


async def test_notes_export_for_the_assistant(
    paths: Paths, use_module: Callable[[str], Path], documents: Path
) -> None:
    use_module("notes")
    root = documents / "Starlight"
    garden(root)
    async with Kernel(paths) as k:
        first = await call(k, "notes.export", title="Garden")
        assert Path(first["saved"]) == root / "Exports" / "Garden.pdf"
        assert (root / "Exports" / "Garden.pdf").read_bytes().startswith(b"%PDF")
        again = await call(k, "notes.export", title="garden", format="PDF")
        assert Path(again["saved"]).name == "Garden 2.pdf"  # (never over an earlier one)
        page = await call(k, "notes.export", title="Garden", format="html")
        assert "photo.png" in (root / "Exports" / "Garden.html").read_text("utf-8")
        assert page["format"] == "html"
        assert "pdf or html" in await fails(k, "notes.export", title="Garden", format="docx")


async def test_the_export_menu_on_the_page(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    garden(notebook)
    async with notes_page(static_site, app, locale="de-DE") as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FGarden.md")
        await page.locator("#export-toggle").click()
        labels = await page.locator(".export-menu button").all_text_contents()
        assert labels == ["HTML-Datei", "PDF-Datei", "Drucken"]
        # (a download bypasses the test's routing to the app: the address is recorded and
        # asked of the app here instead)
        await page.evaluate(
            "() => { window.saved = []; HTMLAnchorElement.prototype.click = function () {"
            " window.saved.push([this.getAttribute('href'), this.hasAttribute('download')]); }; }"
        )
        await page.locator('.export-menu [data-format="pdf"]').click()
        await page.wait_for_function("() => window.saved.length === 1")
        [[address, download]] = await page.evaluate("() => window.saved")
        assert download and "format=pdf" in address and "path=Notes%2FGarden.md" in address
        pdf = app.get(address, headers=LOCAL)
        assert (
            pdf.content.startswith(b"%PDF")
            and 'filename="Garden.pdf"' in pdf.headers["content-disposition"]
        )
        await page.locator("#export-toggle").click()
        async with page.expect_popup() as popup:
            await page.locator('.export-menu [data-format="html-print"]').click()
        printed = await popup.value
        assert "show=true" in printed.url and "format=html" in printed.url
        assert page.errors == []  # type: ignore[attr-defined]
