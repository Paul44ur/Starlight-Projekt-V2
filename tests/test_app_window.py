"""Starlight in its own window (`starlight app`): the web pages in a desktop window, no
browser. pywebview and WebView2 exist only on Windows, so here a stand-in for pywebview records
what the window is asked to do; the launcher's side runs small stand-in processes."""

from __future__ import annotations

import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from starlight import app_window, cli
from starlight.core import Paths
from tests.conftest import REPO
from tests.test_chat_page import CHROMIUM, static_site  # noqa: F401  (a fixture)
from tests.test_feedback_look import CHAT, browser_page
from tests.test_launcher import L


class FakeEvent:
    def __init__(self) -> None:
        self.handlers: list[Callable[[], Any]] = []

    def __iadd__(self, handler: Callable[[], Any]) -> FakeEvent:
        self.handlers.append(handler)
        return self

    def fire(self) -> None:
        for handler in self.handlers:
            handler()


class FakeWindow:
    def __init__(self, title: str, **options: Any) -> None:
        self.title, self.options = title, options
        self.width, self.height = options["width"], options["height"]
        self.urls: list[str] = []
        self.pages: list[str] = []
        self.events = types.SimpleNamespace(
            loaded=FakeEvent(), closing=FakeEvent(), maximized=FakeEvent(), restored=FakeEvent()
        )

    def load_url(self, url: str) -> None:
        self.urls.append(url)

    def load_html(self, html: str) -> None:
        self.pages.append(html)

    def evaluate_js(self, script: str) -> str:
        return "Starlight notes"

    def set_title(self, title: str) -> None:
        self.title = title


@pytest.fixture
def webview(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> types.ModuleType:
    """A stand-in for pywebview: windows are recorded, start() runs the window's work and
    then closes every window."""
    module = types.ModuleType("webview")
    module.settings = {  # type: ignore[attr-defined]
        "ALLOW_DOWNLOADS": False, "OPEN_EXTERNAL_LINKS_IN_BROWSER": False,
        "OPEN_DEVTOOLS_IN_DEBUG": True, "ALLOW_FILE_URLS": True,
    }  # fmt: skip
    windows: list[FakeWindow] = []
    module.windows = windows  # type: ignore[attr-defined]

    def create_window(title: str, **options: Any) -> FakeWindow:
        window = FakeWindow(title, **options)
        windows.append(window)
        return window

    def start(func: Callable[[], None] | None = None, **options: Any) -> None:
        module.started = options  # type: ignore[attr-defined]
        pid = tmp_path / "home" / "data" / "app-window" / "window.pid"
        module.pid_file = pid.read_text()  # type: ignore[attr-defined]
        if func is not None:
            func()
        for window in list(windows):
            window.events.loaded.fire()
            window.events.closing.fire()

    module.create_window = create_window  # type: ignore[attr-defined]
    module.start = start  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", module)
    monkeypatch.setattr(app_window, "why_unavailable", lambda: None)
    return module


@pytest.fixture
def chat() -> Iterator[str]:
    """A web chat that answers (only /api/status)."""

    class Status(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Status)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def home(tmp_path: Path) -> Paths:
    root = tmp_path / "home"
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "starlight.yaml").write_text("model:\n  provider: echo\n")
    return Paths(root)


def test_the_window_shows_the_chat_and_keeps_what_the_pages_keep(
    webview: Any, chat: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = home(tmp_path)
    monkeypatch.setattr(app_window, "start_web", lambda p: pytest.fail("the chat runs already"))
    assert app_window.run(paths, chat + "/notes", chat) == 0
    window = webview.windows[0]
    assert window.options["url"] == chat + "/notes" and window.options["html"] is None
    assert window.options["text_select"] is True  # (answers can be copied)
    assert window.options["min_size"] == app_window.MIN_SIZE
    assert webview.settings == {
        "ALLOW_DOWNLOADS": True,  # a download asks where to save
        "OPEN_EXTERNAL_LINKS_IN_BROWSER": True,  # another site opens in the browser
        "OPEN_DEVTOOLS_IN_DEBUG": False,
        "ALLOW_FILE_URLS": False,  # only Starlight's pages: no file:// reading
    }
    started = webview.started
    assert started["private_mode"] is False  # the theme, the language ... stay
    assert started["storage_path"] == str(paths.data / "app-window" / "webview")
    assert started["debug"] is True  # (right-click menu, Ctrl+F, F5 as in a browser)
    assert started["icon"] == str(app_window.ICON) and app_window.ICON.is_file()
    assert window.title == "Starlight notes"  # the title follows the page
    assert webview.pid_file.strip().isdigit()  # (the launcher brings this window forward)
    assert not (paths.data / "app-window" / "window.pid").exists()  # gone when it closed
    # the same icon as Starlight.exe
    assert app_window.ICON.read_bytes() == (REPO / "launcher" / "starlight.ico").read_bytes()


def test_the_window_remembers_its_size(webview: Any, chat: str, tmp_path: Path) -> None:
    paths = home(tmp_path)
    webview.create_window_original = webview.create_window

    def resized(title: str, **options: Any) -> FakeWindow:
        window: FakeWindow = webview.create_window_original(title, **options)
        window.width, window.height = 900, 700  # the user made it smaller
        return window

    webview.create_window = resized
    app_window.run(paths, chat + "/", chat)
    saved = json.loads((paths.data / "app-window" / "window.json").read_text())
    assert saved == {"width": 900, "height": 700, "maximized": False}
    webview.create_window = webview.create_window_original
    webview.windows.clear()
    app_window.run(paths, chat + "/", chat)
    assert (webview.windows[0].width, webview.windows[0].height) == (900, 700)
    (paths.data / "app-window" / "window.json").write_text('{"width": 5, "height": "x"}')
    assert app_window.Remembered(paths.data / "app-window").width == app_window.SIZE["width"]


def test_the_window_starts_the_chat_when_it_does_not_run(
    webview: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = home(tmp_path)
    base = "http://127.0.0.1:9"
    tries = iter([False, False, True])
    monkeypatch.setattr(app_window, "answers", lambda b, timeout=2.0: next(tries))

    class Web:
        stopped = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            Web.stopped = True

        def wait(self, timeout: float) -> int:
            return 0

    monkeypatch.setattr(app_window, "start_web", lambda p: Web())
    app_window.run(paths, base + "/", base)
    window = webview.windows[0]
    assert window.options["url"] is None and "Starlight is starting" in window.options["html"]
    assert "Starlight startet" in window.options["html"]  # (German, when Windows is)
    assert window.urls == [base + "/"]  # the chat, once it answered
    assert Web.stopped  # the window started it, so its closing stops it


def test_the_window_says_so_when_the_chat_does_not_start(
    webview: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_window, "answers", lambda b, timeout=2.0: False)
    dead = types.SimpleNamespace(poll=lambda: 1, terminate=lambda: None, wait=lambda timeout: 1)
    monkeypatch.setattr(app_window, "start_web", lambda p: dead)
    app_window.run(home(tmp_path), "http://127.0.0.1:9/", "http://127.0.0.1:9")
    assert "logs/web.log" in webview.windows[0].pages[0]


def test_only_starlights_own_pages_open_in_its_windows() -> None:
    opened: list[str] = []
    bridge = app_window.Bridge("http://127.0.0.1:8700", opened.append)
    assert bridge.open_window("http://127.0.0.1:8700/api/conversations/c1/export?show=true")
    for other in ("https://example.org/", "http://127.0.0.1:8765/", "file:///C:/x", 42):
        assert bridge.open_window(other) is False  # type: ignore[arg-type]
    assert opened == ["http://127.0.0.1:8700/api/conversations/c1/export?show=true"]
    assert [name for name in dir(bridge) if not name.startswith("_")] == [
        "attention",
        "open_window",
    ]


def test_a_page_may_ask_for_attention_with_a_few_words() -> None:
    asked: list[str] = []

    def lit(text: str) -> bool:
        asked.append(text)
        return True

    bridge = app_window.Bridge("http://127.0.0.1:8700", lambda url: None, attention=lit)
    assert bridge.attention("x" * 500) is True
    assert asked == ["x" * app_window.ATTENTION_TEXT]  # (cut: never a whole answer)
    assert bridge.attention("Your answer is ready") is True
    assert asked[-1] == "Your answer is ready"
    off = app_window.Bridge("http://127.0.0.1:8700", lambda url: None, attention=lambda t: False)
    assert off.attention("x") is False


def test_without_windows_there_is_nothing_to_light_up() -> None:
    assert sys.platform != "win32"  # (the real one is for Windows, see the launcher notes)
    assert app_window.flash_taskbar() is False
    bridge = app_window.Bridge("http://127.0.0.1:8700", lambda url: None)
    assert bridge.attention("Your answer is ready") is False


def test_starlight_app_opens_the_page_it_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(app_window, "run", lambda paths, url, base: calls.append((url, base)) or 0)
    paths = home(tmp_path)
    for flags in ([], ["--url", "http://127.0.0.1:8700/notes?path=a.md"], ["--page", "/settings"]):
        args = cli.build_parser().parse_args(["--home", str(paths.root), "app", *flags])
        assert cli.cmd_app(paths, args) == 0
    assert calls == [
        ("http://127.0.0.1:8700/", "http://127.0.0.1:8700"),
        ("http://127.0.0.1:8700/notes?path=a.md", "http://127.0.0.1:8700"),
        ("http://127.0.0.1:8700/settings", "http://127.0.0.1:8700"),
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows can have the window")
def test_without_windows_the_app_says_why_and_the_launcher_uses_the_browser(
    tmp_path: Path,
) -> None:
    root = home(tmp_path).root
    done = subprocess.run(
        [sys.executable, "-m", "starlight", "--home", str(root), "app"],
        capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    assert done.returncode == app_window.UNAVAILABLE == L.APP_UNAVAILABLE
    assert "needs Windows" in done.stderr


def test_the_app_extra_brings_the_window_on_windows_only() -> None:
    project = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "app = [\"pywebview>=6.2; sys_platform == 'win32'\"]" in project
    assert ",voice,app]" in project  # part of "all", which the launcher installs


# -- the pages: a Starlight page that would open a new tab opens a new Starlight window --------


async def test_in_the_window_starlight_pages_open_in_a_new_starlight_window(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    tabs = "window.tabs = []; window.open = (u) => { window.tabs.push(String(u)); return null; };\n"
    app = (
        "window.windows = [];"
        " window.pywebview = {api: {open_window: (u) => window.windows.push(u)}};\n"
    )
    click = """(href) => {
        const a = document.createElement('a'); a.href = href; a.target = '_blank';
        a.textContent = 'x'; document.body.append(a);
        let prevented = null;
        document.addEventListener('click', (e) => {
            prevented = e.defaultPrevented; e.preventDefault();  // (no real navigation)
        }, {once: true});
        a.click(); a.remove(); return prevented;
    }"""
    async with async_api.async_playwright() as playwright:
        browser, page = await browser_page(playwright, static_site, "chat.html", tabs + app + CHAT)
        try:
            await page.wait_for_selector(".conv")
            export = "/api/conversations/c1/export?show=true"
            await page.evaluate("e => window.open(e, '_blank')", export)
            await page.evaluate("() => window.open('https://example.org/', '_blank')")
            assert await page.evaluate(click, "/notes?path=a.md") is True
            assert await page.evaluate(click, "https://example.org/b") is False
            windows, tabs_opened = await page.evaluate("() => [window.windows, window.tabs]")
            assert windows == [static_site + export, f"{static_site}/notes?path=a.md"]
            assert tabs_opened == ["https://example.org/"]  # (the window sends it to the browser)
        finally:
            await browser.close()
        browser, page = await browser_page(playwright, static_site, "chat.html", tabs + CHAT)
        try:  # in a browser: as before
            await page.wait_for_selector(".conv")
            await page.evaluate("() => window.open('/notes', '_blank')")
            assert await page.evaluate("() => window.tabs") == ["/notes"]
            assert await page.evaluate(click, "/notes?path=a.md") is False
        finally:
            await browser.close()


# -- the launcher: the chat in Starlight's own window, the browser when it cannot open ---------


@pytest.fixture
def window_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Callable[[str], Any]]:
    """A ChatWindow whose `starlight app` is a small stand-in script."""
    browser: list[str] = []
    monkeypatch.setattr(L, "_open_browser", browser.append)
    monkeypatch.setattr(L.ChatWindow, "wanted", property(lambda self: self.settings.app_window))
    monkeypatch.setattr(L, "APP_START", 1.5)
    (tmp_path / "logs").mkdir()

    def make(script: str) -> Any:
        commands: list[list[str]] = []

        def command(uv: Path, *args: str) -> list[str]:
            commands.append(list(args))
            return [sys.executable, "-c", script]

        monkeypatch.setattr(L, "starlight", command)
        group = L.ProcessGroup()
        groups.append(group)
        window = L.ChatWindow(L.Settings(root=tmp_path), tmp_path / "uv", group)
        window.browser, window.commands = browser, commands
        return window

    groups: list[Any] = []
    yield make
    for group in groups:
        group.stop()


def test_the_launcher_opens_the_chat_in_starlights_own_window(window_process: Any) -> None:
    window = window_process("import time; time.sleep(5)")
    window.open("http://127.0.0.1:8700/notes", wait=True)
    assert window.commands == [["app", "--url", "http://127.0.0.1:8700/notes"]]
    assert window.running() and window.browser == [] and window.reason == ""


def test_the_browser_opens_the_chat_when_the_window_cannot(window_process: Any) -> None:
    script = "import sys; print('needs the Microsoft Edge WebView2 Runtime'); sys.exit(3)"
    window = window_process(script)
    window.open("http://127.0.0.1:8700/", wait=True)
    assert window.browser == ["http://127.0.0.1:8700/"]
    assert window.reason == "needs the Microsoft Edge WebView2 Runtime"
    assert "the chat opens in the browser" in window.note
    closed = window_process("pass")  # closed again at once, by hand: nothing more
    closed.browser.clear()
    closed.open("http://127.0.0.1:8700/", wait=True)
    assert closed.browser == [] and closed.reason == ""


def test_the_browser_stays_a_choice(window_process: Any, tmp_path: Path) -> None:
    window = window_process("pass")
    window.settings.app_window = False
    window.open("http://127.0.0.1:8700/")
    assert window.browser == ["http://127.0.0.1:8700/"] and window.commands == []
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "starlight"\n')
    parse = L.build_parser().parse_args
    assert L.load_settings(parse(["--root", str(tmp_path)])).app_window is True  # the default
    assert L.load_settings(parse(["--root", str(tmp_path), "--browser"])).app_window is False
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "launcher.json").write_text('{"app_window": false}')
    assert L.load_settings(parse(["--root", str(tmp_path)])).app_window is False
    started = time.monotonic()
    assert L.focus_app_window(tmp_path) is False  # nothing open (and not Windows here)
    assert time.monotonic() - started < 1


def test_the_launcher_window_steps_aside_for_the_chat_and_comes_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("tkinter")
    sys.path.insert(0, str(REPO / "launcher"))
    try:
        import launcher_gui as gui
    finally:
        sys.path.pop(0)
    shown: list[str] = []
    opened: list[str] = []
    chat = types.SimpleNamespace(wanted=True, reason="", open=opened.append, running=lambda: True)
    window = types.SimpleNamespace(
        L=types.SimpleNamespace(chat_url=lambda: "http://127.0.0.1:8700"),
        chat_window=chat, closing=False, window_was_open=False, told_why=False,
        root=types.SimpleNamespace(
            iconify=lambda: shown.append("taskbar"), deiconify=lambda: shown.append("back")
        ),
    )  # fmt: skip
    gui.App.open_chat(window, "/notes")  # type: ignore[arg-type]
    assert opened == ["http://127.0.0.1:8700/notes"] and shown == ["taskbar"]
    gui.App._follow_chat_window(window)  # type: ignore[arg-type]
    assert window.window_was_open and shown == ["taskbar"]  # (open: nothing to do)
    chat.running = lambda: False  # the chat's window was closed
    gui.App._follow_chat_window(window)  # type: ignore[arg-type]
    assert shown == ["taskbar", "back"]  # this window says Starlight still runs
    chat.reason = "needs WebView2"  # it could not open (the browser has the chat)
    gui.App._follow_chat_window(window)  # type: ignore[arg-type]
    gui.App._follow_chat_window(window)  # type: ignore[arg-type]
    assert shown == ["taskbar", "back", "back"]  # it comes back once, to say why
    for text in (
        "Open the chat in Starlight's own window, not in the browser",
        "The chat is in its own window (Open chat brings it back). Close this window to stop "
        "Starlight.",
        "Starlight's own window could not open ({reason}); the chat is in the browser.",
    ):
        assert text in gui.GERMAN, text


def test_a_second_window_keeps_its_own_id_file(
    webview: Any, chat: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = home(tmp_path)
    started = webview.start

    def another_window_opens_meanwhile(func: Any = None, **options: Any) -> None:
        started(func, **options)
        (paths.data / "app-window" / "window.pid").write_text("424242")

    webview.start = another_window_opens_meanwhile
    app_window.run(paths, chat + "/", chat)
    assert (paths.data / "app-window" / "window.pid").read_text() == "424242"


def test_after_the_window_failed_once_the_browser_opens_at_once(window_process: Any) -> None:
    window = window_process("import sys; print('needs WebView2'); sys.exit(3)")
    window.open("http://127.0.0.1:8700/", wait=True)
    assert window.reason == "needs WebView2"
    window.commands.clear()
    window.open("http://127.0.0.1:8700/notes", wait=True)  # Open chat again
    assert window.commands == []  # no second try (and no 15 s wait)
    assert window.browser[-1] == "http://127.0.0.1:8700/notes"


def test_the_reason_is_this_starts_own(window_process: Any, tmp_path: Path) -> None:
    (tmp_path / "logs" / "app.log").write_text("an old run said this\n")
    window = window_process("import sys; sys.exit(1)")  # says nothing itself
    window.open("http://127.0.0.1:8700/", wait=True)
    assert window.reason == "see logs/app.log"  # not the old run's words


def test_the_window_waits_until_the_chat_answers(
    window_process: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter([None, None, {"local": True}])
    monkeypatch.setattr(L, "fetch_json", lambda url, **kw: next(answers))
    window = window_process("import time; time.sleep(5)")
    window.open_when_ready("http://127.0.0.1:8700/")
    end = time.monotonic() + 10
    while not window.commands and time.monotonic() < end:
        time.sleep(0.05)
    assert window.commands == [["app", "--url", "http://127.0.0.1:8700/"]]
    with pytest.raises(StopIteration):  # it asked until the chat answered, then opened
        next(answers)


def test_a_restart_brings_the_chat_window_back(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("tkinter")
    sys.path.insert(0, str(REPO / "launcher"))
    try:
        import launcher_gui as gui
    finally:
        sys.path.pop(0)
    events: list[Any] = []
    window = types.SimpleNamespace(
        chat_window=types.SimpleNamespace(wanted=True, reason="", running=lambda: True),
        opened_browser=True, _stop_services=lambda: None,
        events=types.SimpleNamespace(put=events.append),
    )  # fmt: skip
    gui.App._stop_then_begin(window)  # type: ignore[arg-type]
    assert window.opened_browser is False and events == [("begin",)]  # opens again when ready
    # a window that could not open: the browser has the chat; this window stays in view
    shown: list[str] = []
    window.chat_window = types.SimpleNamespace(wanted=True, reason="no WebView2", open=print)
    window.L = types.SimpleNamespace(chat_url=lambda: "http://127.0.0.1:8700")
    window.root = types.SimpleNamespace(iconify=lambda: shown.append("taskbar"))
    gui.App.open_chat(window)  # type: ignore[arg-type]
    assert shown == []


async def test_no_page_script_replaces_what_the_browser_has() -> None:
    """A page's top-level function becomes a property of the window, replacing the browser's
    own of that name: the chat's open() replaced window.open, and printing a conversation
    opened nothing. Here no page script declares a name the window already has."""
    async_api = pytest.importorskip("playwright.async_api")
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    async with async_api.async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=executable)
        try:
            page = await browser.new_page()
            await page.goto("about:blank")
            names = set(
                await page.evaluate(
                    "() => { const all = new Set(); let o = window;"
                    " while (o) { Object.getOwnPropertyNames(o).forEach(n => all.add(n));"
                    " o = Object.getPrototypeOf(o); } return [...all]; }"
                )
            )
        finally:
            await browser.close()
    assert {"open", "close", "print", "scroll", "status", "fetch"} <= names
    static = REPO / "starlight" / "web" / "static"
    clashes = []
    for path in sorted([*static.glob("*.html"), *static.glob("*.js")]):
        text = path.read_text(encoding="utf-8")
        declared = re.finditer(
            r"^(?:async\s+)?function\s+([\w$]+)\s*\(|^var\s+([\w$]+)", text, re.M
        )
        for match in declared:
            name = match.group(1) or match.group(2)
            if name in names:
                clashes.append(f"{path.name}: {name}")
    assert clashes == []
