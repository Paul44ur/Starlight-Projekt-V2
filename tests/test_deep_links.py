"""Block G6 of the notes plan (docs/plan-notes-app.md): links that open a note from other
programs, a mail or an automation's message: starlight://note/Notes/Plan.md. Starlight.exe
registers them for your own account on Windows and opens them (in the running Starlight, or
it starts with that page); the chat and the notes show them as links to the note. A link only
ever opens a page: it never asks Starlight anything."""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_home_devices import call
from tests.test_launcher import L, QuietUi
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_notes_page import notes_page, opened


@pytest.mark.parametrize(
    ("link", "page"),
    [
        ("starlight://note/Notes/Plan.md", "/notes?path=Notes%2FPlan.md"),
        ("starlight://note/Notes/Old%20ideas.md", "/notes?path=Notes%2FOld%20ideas.md"),
        ("starlight://note/Shopping#Food", "/notes?open=Shopping&heading=Food"),
        ("STARLIGHT://Note/Größe/", "/notes?open=Gr%C3%B6%C3%9Fe"),  # (a slash at the end)
        ("starlight://note/Journal/2026-10-30.md", "/notes?path=Journal%2F2026-10-30.md"),
        ("starlight://notes", "/notes"),
        ("starlight://note/", "/notes"),
        ("starlight://chat", "/"),
        ("starlight://note/../secret.md", None),
        ("starlight://note/Notes/%2E%2E/x.md", None),
        ("starlight://note/C:/Windows/x.md", None),
        ("starlight://note/a\\b.md", None),
        ("starlight://note/x%00y", None),
        ("starlight://ask/do something", None),  # (a link never asks Starlight anything)
        ("starlight://chat/hello", None),
        ("https://example.org/note/Plan", None),
        ("starlight://note/" + "x" * 600, None),
    ],
)
def test_what_a_link_opens(link: str, page: str | None) -> None:
    assert L.link_target(link) == page


def test_a_note_s_link(paths: Paths) -> None:
    from starlight.notebook.links import deep_link

    assert deep_link("Notes/Old ideas.md") == "starlight://note/Notes/Old%20ideas.md"
    assert deep_link("Notes/Größe.md", "Food & more") == (
        "starlight://note/Notes/Gr%C3%B6%C3%9Fe.md#Food%20%26%20more"
    )
    for path in ("Notes/Old ideas.md", "Notes/Größe.md", "Journal/2026-10-30.md"):
        assert L.link_target(deep_link(path)) is not None  # (the launcher reads them back)
    page = L.link_target(deep_link("Notes/Größe.md", "Food & more"))
    assert page == "/notes?path=Notes%2FGr%C3%B6%C3%9Fe.md&heading=Food%20%26%20more"


async def test_notes_link_for_the_assistant(
    paths: Paths, use_module: Callable[[str], Path], documents: Path
) -> None:
    use_module("notes")
    notes = documents / "Starlight" / "Notes"
    notes.mkdir(parents=True)
    (notes / "Old ideas.md").write_text("# Old ideas\n", encoding="utf-8")
    async with Kernel(paths) as k:
        made = await call(k, "notes.link", title="old ideas", heading="Later")
        assert made == {"note": "Old ideas", "link": "starlight://note/Notes/Old%20ideas.md#Later"}


# -- the launcher -------------------------------------------------------------------------------


def fake_registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, str]]:
    keys: dict[str, dict[str, str]] = {}

    class Key:
        def __init__(self, path: str) -> None:
            self.path = path

        def __enter__(self) -> Key:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    def create(root: int, path: str) -> Key:
        assert root == 1  # HKEY_CURRENT_USER: your own account only
        keys.setdefault(path, {})
        return Key(path)

    def query(key: Key, name: str) -> Any:
        if name not in keys[key.path]:
            raise FileNotFoundError(name)
        return keys[key.path][name], 1

    def delete(root: int, path: str) -> None:
        if path not in keys or any(k.startswith(path + "\\") for k in keys):
            raise FileNotFoundError(path)  # (as Windows: only a key without sub-keys)
        del keys[path]

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=1, REG_SZ=1,
        CreateKey=create, QueryValueEx=query, DeleteKey=delete,
        SetValueEx=lambda key, name, reserved, kind, value: keys[key.path].__setitem__(name, value),
    )  # fmt: skip
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(L.sys, "platform", "win32")
    return keys


def test_registering_the_links(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    keys = fake_registry(monkeypatch)
    command = '"C:\\Starlight\\Starlight.exe" --open "%1"'
    assert L.register_links(True, command) is True
    assert keys == {
        "Software\\Classes\\starlight": {"": "URL:Starlight", "URL Protocol": ""},
        "Software\\Classes\\starlight\\shell\\open\\command": {"": command},
    }
    assert L.register_links(True, command) is False  # (as it was: nothing written)
    moved = '"D:\\Starlight\\Starlight.exe" --open "%1"'
    assert L.register_links(True, moved) is True  # (the folder moved: the link follows)
    assert keys["Software\\Classes\\starlight\\shell\\open\\command"] == {"": moved}
    assert L.register_links(False, moved) is True and not keys  # "links": false
    assert L.register_links(False, moved) is False
    monkeypatch.setattr(L.sys, "platform", "linux")
    assert L.register_links(True, command) is False  # (Windows only)


def test_starlight_exe_sets_them_up_and_a_script_does_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    keys = fake_registry(monkeypatch)
    settings = L.Settings(root=tmp_path)
    ui = QuietUi()
    L.ensure_links(settings, ui)
    assert keys == {}  # a script run (as these tests): never
    monkeypatch.setattr(L.sys, "frozen", True, raising=False)
    monkeypatch.setattr(L.sys, "executable", str(tmp_path / "Starlight.exe"))
    L.ensure_links(settings, ui)
    command = keys["Software\\Classes\\starlight\\shell\\open\\command"][""]
    assert command == f'"{(tmp_path / "Starlight.exe").resolve()}" --open "%1"'
    settings.links = False
    L.ensure_links(settings, ui)
    assert keys == {}

    def broken(on: bool, command: str) -> bool:
        raise PermissionError("access denied")

    monkeypatch.setattr(L, "register_links", broken)
    L.ensure_links(L.Settings(root=tmp_path), ui)  # (said, and Starlight goes on)
    assert ui.lines[-1] == "   starlight:// links could not be set up: access denied"


def test_links_from_launcher_json(tmp_path: Path) -> None:
    from tests.test_lmstudio import starlight_root

    root = starlight_root(tmp_path, {"links": False})
    settings = L.load_settings(L.build_parser().parse_args(["--root", str(root)]))
    assert settings.links is False and settings.first_page == ""


def test_opening_a_link(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tests.test_lmstudio import starlight_root

    opened_pages: list[str] = []
    monkeypatch.setattr(L, "_open_browser", opened_pages.append)
    monkeypatch.setattr(L, "fetch_json", lambda url, **kw: {"kill": {"engaged": False}})
    monkeypatch.setattr(L, "port_in_use", lambda port: port == L.WEB_PORT)
    assert L.main(["--open", "starlight://note/Notes/Plan.md"]) == 0  # Starlight is running
    assert opened_pages == ["http://127.0.0.1:8700/notes?path=Notes%2FPlan.md"]
    assert L.main(["--open", "starlight://note/../x.md"]) == 2  # not one: nothing happens
    assert len(opened_pages) == 1

    started: list[Any] = []  # not running: it starts, with that page first
    monkeypatch.setattr(L, "port_in_use", lambda port: False)
    monkeypatch.setattr(L, "gui_wanted", lambda args: False)
    monkeypatch.setattr(L, "setup", lambda settings, ui: started.append(settings) or "installed")
    monkeypatch.setattr(L, "start", lambda settings, ui, installed, ask: 0)
    root = starlight_root(tmp_path)
    code = L.main(["--root", str(root), "--open", "starlight://note/Shopping#Food"])
    assert code == 0 and started[0].first_page == "/notes?open=Shopping&heading=Food"


def test_the_window_opens_the_link_s_page(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tests.test_cleanup_launcher import load_gui
    from tests.test_launcher_window import has_display
    from tests.test_lmstudio import starlight_root

    if not has_display():
        pytest.skip("needs tkinter and a display")
    gui = load_gui()
    monkeypatch.setattr(gui.App, "_start_background_lookups", lambda self: None)  # no network
    opened_pages: list[str] = []
    monkeypatch.setattr(gui.webbrowser, "open", opened_pages.append)
    args = L.build_parser().parse_args(["--root", str(starlight_root(tmp_path))])
    args.page = "/notes?path=Notes%2FPlan.md"
    app = gui.App(L, args)
    try:
        app.show("running")
        app.on_status(L.Status(model="ready", chat=True, viewer=None, kill=False))
        app.on_status(L.Status(model="ready", chat=True, viewer=None, kill=False))  # (once)
        assert opened_pages == ["http://127.0.0.1:8700/notes?path=Notes%2FPlan.md"]
        app.open_chat()  # the button: the chat itself
        assert opened_pages[-1] == "http://127.0.0.1:8700"
    finally:
        app.root.destroy()


# -- in the pages ---------------------------------------------------------------------------------


async def test_links_in_notes_and_copying_one(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    (notebook / "Notes" / "Links.md").write_text(
        "See [the plan](starlight://note/Notes/Plan.md), starlight://note/Shopping#Food and "
        "[bad](starlight://note/../x.md).\n",
        encoding="utf-8",
    )
    async with notes_page(static_site, app, locale="de-DE") as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FLinks.md")
        await page.wait_for_selector("#column .note-body a")
        links = await page.locator("#column .note-body a").evaluate_all(
            "as => as.map(a => [a.textContent, a.getAttribute('href'), a.target])"
        )
        assert links == [
            ["the plan", "/notes?path=Notes%2FPlan.md", ""],
            ["starlight://note/Shopping#Food", "/notes?open=Shopping&heading=Food", ""],
        ]  # ("bad" stays text)
        assert "bad" in await page.locator("#column .note-body").text_content()
        spoken = await page.evaluate("t => markdownToSpeech(t)", "Open starlight://note/Plan now")
        assert spoken.strip() == "Open a link now"  # (read aloud: not the address)
        await page.locator("#column .note-body a", has_text="the plan").click()  # in place
        await page.wait_for_function("() => document.title.startsWith('Plan')")
        await page.evaluate(
            "() => { window.copied = []; navigator.clipboard.writeText = async (text) =>"
            " { window.copied.push(text); }; }"
        )
        share = page.locator("#link-copy")
        assert await share.get_attribute("title") == (
            "Link zu dieser Notiz kopieren (er öffnet die Notiz aus anderen Programmen)"
        )
        await share.click()
        await page.wait_for_function("() => window.copied.length === 1")
        assert await page.evaluate("() => window.copied") == ["starlight://note/Notes/Plan.md"]
        assert await page.locator(".toast").last.text_content() == (
            "Link kopiert: starlight://note/Notes/Plan.md"
        )
        assert page.errors == []  # type: ignore[attr-defined]
