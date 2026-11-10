"""Starlight in its own window: the web pages in a desktop window, with no browser around them.

``starlight app`` opens it (the launcher does so instead of opening the browser). On Windows
the window is WebView2, the engine of Microsoft Edge that comes with Windows 11, through the
pywebview package (the ``app`` extra). The pages are the same as in the browser.

* If the web chat is not running yet, the window starts it (and stops it when it closes);
  until the chat answers, the window says that Starlight is starting.
* What the pages keep (the theme, the language, the Notes page's choices) stays from one start
  to the next: the window keeps its data in ``data/app-window``.
* A Starlight page that would open in a new tab (print, export) opens in a new Starlight
  window; any other address opens in your browser. Downloads ask where to save.
* Its own name and icon in the taskbar; the window's size is remembered.
* When an answer is ready and the window is in the background, the page says so and the
  taskbar button lights up until you come back.

When there can be no window here (not Windows, the package missing, no WebView2), the command
ends with ``UNAVAILABLE`` and says why: the launcher then opens the browser instead.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from starlight.core.paths import Paths

UNAVAILABLE = 3  # exit code: no window here; the browser should open the chat instead
STARTING_TIMEOUT = 180.0  # seconds the web chat may take to start
APP_ID = "Starlight.Chat"  # Windows groups the taskbar button by this (not with Python's)
ICON = Path(__file__).parent / "web" / "static" / "icons" / "starlight.ico"
SIZE = {"width": 1200, "height": 820}  # the first time; then the size it had
ATTENTION_TEXT = 200  # letters of what a page may say when it asks for your attention
ATTENTION_FILE = "attention.json"  # in data/app-window; the launcher's tray icon reads it
FLASH_UNTIL_FRONT = 0x2 | 0xC  # FLASHW_TRAY | FLASHW_TIMERNOFG: the button, until in front
MIN_SIZE = (420, 560)
BACKGROUND = {"dark": "#0f1115", "light": "#f6f7f9"}  # style.css --bg, until the page paints

STARTING_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
:root { color-scheme: dark light; }
body { margin: 0; height: 100vh; display: flex; flex-direction: column; align-items: center;
       justify-content: center; gap: 14px; font: 15px/1.5 "Segoe UI", sans-serif;
       background: #0f1115; color: #9aa3b2; }
@media (prefers-color-scheme: light) { body { background: #f6f7f9; color: #5a6372; } }
.star { font-size: 44px; color: #f5c542; animation: glow 1.6s ease-in-out infinite; }
@keyframes glow { 50% { opacity: .45; } }
@media (prefers-reduced-motion: reduce) { .star { animation: none; } }
</style></head><body><div class="star">✦</div><div id="text">MESSAGE</div>
<script>if (/^de/i.test(navigator.language || "")) document.getElementById("text").textContent =
"GERMAN";</script></body></html>"""
STARTING = ("Starlight is starting …", "Starlight startet …")
NOT_STARTED = (
    "Starlight's chat did not start. The reason is in logs/web.log in Starlight's folder.",
    "Der Chat von Starlight ist nicht gestartet. Den Grund nennt logs/web.log im Starlight-Ordner.",
)


def page(words: tuple[str, str]) -> str:
    """The window's own small page (while the chat starts), in English or German."""
    english, german = words
    return STARTING_PAGE.replace("MESSAGE", english).replace("GERMAN", german)


def same_origin(url: str, base: str) -> bool:
    """Is ``url`` a page of the Starlight at ``base`` (same scheme, host and port)?"""
    a, b = urllib.parse.urlsplit(url), urllib.parse.urlsplit(base)
    return a.scheme in ("http", "https") and (a.scheme, a.netloc) == (b.scheme, b.netloc)


def answers(base: str, timeout: float = 2.0) -> bool:
    """The web chat at ``base`` answers (any HTTP answer counts: it is running)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # (never a proxy)
    try:
        with opener.open(base.rstrip("/") + "/api/status", timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except (OSError, ValueError):
        return False


class Bridge:
    """What the pages may ask of the window (``window.pywebview.api`` in JavaScript): to open
    another page of this Starlight in a new window, and to ask for your attention when an
    answer is ready (the taskbar button lights up)."""

    def __init__(
        self,
        base: str,
        opener: Callable[[str], Any],
        attention: Callable[[str], bool] | None = None,
    ) -> None:
        self._base = base  # (names with _ are not offered to the pages)
        self._opener = opener
        self._attention = attention or (lambda _text: flash_taskbar())

    def open_window(self, url: str) -> bool:
        if not isinstance(url, str) or not same_origin(url, self._base):
            return False  # another site: the page lets the browser open it
        self._opener(url)
        return True

    def attention(self, text: str = "") -> bool:
        """The page has something for you (an answer, a question) while the window is in the
        background. ``text`` says what, in a few words (never the answer itself)."""
        return bool(self._attention(str(text)[:ATTENTION_TEXT]))


class Remembered:
    """The window's size and whether it was maximised, kept in data/app-window/window.json."""

    def __init__(self, folder: Path) -> None:
        self.path = folder / "window.json"
        self.width, self.height, self.maximized = SIZE["width"], SIZE["height"], False
        with contextlib.suppress(OSError, ValueError, TypeError, KeyError):
            data = json.loads(self.path.read_text(encoding="utf-8"))
            width = max(MIN_SIZE[0], min(int(data["width"]), 10_000))
            height = max(MIN_SIZE[1], min(int(data["height"]), 10_000))
            # (all of it read before any is used: a broken file keeps the first-time size)
            self.width, self.height = width, height
            self.maximized = bool(data.get("maximized", False))

    def save(self) -> None:
        data = {"width": self.width, "height": self.height, "maximized": self.maximized}
        with contextlib.suppress(OSError):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data) + "\n", encoding="utf-8")


def why_unavailable() -> str | None:
    """Why there can be no window here, or None when there can."""
    if sys.platform != "win32":
        return "Starlight's own window needs Windows; the browser opens the chat instead."
    try:
        import webview  # noqa: F401
    except ImportError:
        return (
            "Starlight's own window needs the pywebview package (the 'app' extra): "
            "start Starlight.exe again, or run  uv sync --extra all"
        )
    try:
        from webview.platforms import winforms
    except Exception as exc:  # (pythonnet or .NET could not start)
        return f"Starlight's own window could not start .NET: {exc}"
    if not getattr(winforms, "is_chromium", False):
        return (
            "Starlight's own window needs the Microsoft Edge WebView2 Runtime "
            "(https://go.microsoft.com/fwlink/p/?LinkId=2124703)"
        )
    return None


def light_system() -> bool:
    """Windows uses its light look for apps (the window starts in that colour)."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            return bool(winreg.QueryValueEx(handle, "AppsUseLightTheme")[0])
    except OSError:
        return False


def flash_taskbar(pid: int | None = None) -> bool:
    """Light up the taskbar button of this process's windows until one comes to the front
    (Windows). False: nothing to light up, or it is in front already."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    class FlashInfo(ctypes.Structure):  # FLASHWINFO
        _fields_ = (
            ("size", wintypes.UINT),
            ("window", wintypes.HWND),
            ("flags", wintypes.DWORD),
            ("count", wintypes.UINT),
            ("timeout", wintypes.DWORD),
        )

    user32 = ctypes.WinDLL("user32")  # (its own handle: the argument types below stay here)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindow.argtypes, user32.GetWindow.restype = (
        [wintypes.HWND, wintypes.UINT],
        wintypes.HWND,
    )
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.FlashWindowEx.argtypes = [ctypes.POINTER(FlashInfo)]
    mine = os.getpid() if pid is None else pid
    found: list[int] = []

    def each(handle: int, _param: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        shown = user32.IsWindowVisible(handle) and not user32.GetWindow(handle, 4)  # GW_OWNER
        if owner.value == mine and shown:  # (a visible top-level window of this process)
            found.append(handle)
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(each)
    user32.EnumWindows(callback, 0)
    front = user32.GetForegroundWindow()
    lit = False
    for handle in found:
        if handle == front:
            continue
        info = FlashInfo(ctypes.sizeof(FlashInfo), handle, FLASH_UNTIL_FRONT, 0, 0)
        user32.FlashWindowEx(ctypes.byref(info))
        lit = True
    return lit


def clean_text(text: object, limit: int = ATTENTION_TEXT) -> str:
    """Plain text for a notification: control characters out, white space squeezed, cut.
    (The launcher does the same again when it reads it: same lines as in its module.)"""
    plain = " ".join("".join(c if c.isprintable() else " " for c in str(text)).split())
    return plain[:limit].rstrip()


def write_attention(folder: Path, text: str) -> bool:
    """Tell the launcher's tray icon that something is ready, in data/app-window/attention.json
    ({"id", "text"}; a new id is a new message). Atomic: a reader never sees half a file."""
    path = folder / ATTENTION_FILE
    part = path.with_name(path.name + ".part")
    try:
        part.write_text(
            json.dumps({"id": time.time_ns(), "text": clean_text(text)}), encoding="utf-8"
        )
        os.replace(part, path)
    except OSError:
        return False
    return True


def _own_taskbar_button() -> None:
    """On Windows, the window gets Starlight's own taskbar button and icon, not Python's."""
    if sys.platform == "win32":
        import ctypes

        with contextlib.suppress(Exception):
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def start_web(paths: Paths) -> subprocess.Popen[bytes]:
    """The web chat, in a process of its own (the window stops it when it closes)."""
    paths.logs.mkdir(parents=True, exist_ok=True)
    with (paths.logs / "web.log").open("ab") as log:
        return subprocess.Popen(
            [sys.executable, "-m", "starlight", "--home", str(paths.root), "web"],
            cwd=paths.root,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def run(paths: Paths, url: str, base: str) -> int:
    """Open Starlight at ``url`` (a page of the chat at ``base``) in its own window; return
    when it is closed."""
    problem = why_unavailable()
    if problem is not None:
        print(problem, file=sys.stderr, flush=True)
        return UNAVAILABLE
    os.environ.setdefault("PYWEBVIEW_LOG", "warning")  # (debug=True below would log a lot)
    import webview

    _own_taskbar_button()
    folder = paths.data / "app-window"
    folder.mkdir(parents=True, exist_ok=True)
    server = None if answers(base) else start_web(paths)
    remembered = Remembered(folder)
    background = BACKGROUND["light" if light_system() else "dark"]
    # Downloads ask where to save; a new window for another site opens in the browser.
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
    webview.settings["ALLOW_FILE_URLS"] = False  # (only Starlight's pages: no file:// reading)

    def attend(text: str) -> bool:
        """The taskbar button lights up, and the launcher's tray icon may show a notification."""
        lit = flash_taskbar()
        return write_attention(folder, text) or lit

    def open_window(address: str) -> Any:
        window = webview.create_window(
            "Starlight", url=address, width=remembered.width, height=remembered.height,
            min_size=MIN_SIZE, background_color=background, text_select=True, zoomable=True,
            js_api=Bridge(base, open_window, attend),
        )  # fmt: skip
        window.events.loaded += lambda: _retitle(window)
        return window

    first = webview.create_window(
        "Starlight", url=url if server is None else None,
        html=page(STARTING) if server is not None else None,
        width=remembered.width, height=remembered.height, min_size=MIN_SIZE,
        maximized=remembered.maximized, background_color=background, text_select=True,
        zoomable=True, js_api=Bridge(base, open_window, attend),
    )  # fmt: skip
    first.events.loaded += lambda: _retitle(first)
    first.events.maximized += lambda: setattr(remembered, "maximized", True)
    first.events.restored += lambda: setattr(remembered, "maximized", False)

    def closing() -> None:
        if not remembered.maximized:
            remembered.width, remembered.height = first.width, first.height
        remembered.save()

    first.events.closing += closing
    pid_file = folder / "window.pid"  # (the launcher brings this window forward)
    with contextlib.suppress(OSError):
        pid_file.write_text(str(os.getpid()), encoding="utf-8")

    def when_ready() -> None:
        if server is None:
            return
        end = time.monotonic() + STARTING_TIMEOUT
        with contextlib.suppress(Exception):  # (the window may have been closed meanwhile)
            while time.monotonic() < end and server.poll() is None:
                if answers(base):
                    first.load_url(url)
                    return
                time.sleep(0.5)
            first.load_html(page(NOT_STARTED))

    try:
        # debug: the right-click menu (copy, paste, spelling), Ctrl+F and F5 work as in a
        # browser; the developer tools stay closed.
        webview.start(
            when_ready, debug=True, private_mode=False, storage_path=str(folder / "webview"),
            icon=str(ICON) if ICON.is_file() else None,
        )  # fmt: skip
    finally:
        with contextlib.suppress(OSError):
            if pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_file.unlink()  # (only its own: another window may have written its id)
        if server is not None:
            server.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                server.wait(timeout=15)
    return 0


def _retitle(window: Any) -> None:
    """The window's title follows the page's (Notes, Settings ...)."""
    with contextlib.suppress(Exception):
        title = str(window.evaluate_js("document.title") or "").strip()
        window.set_title(title or "Starlight")
