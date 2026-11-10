"""The tray icon: Starlight next to the clock (Windows), standard library only.

The launcher window (launcher_gui.py) owns it. Closing that window then hides it instead of
stopping Starlight, and the icon's menu has Open, Kill switch and Quit.

Two parts. The first is plain Python and runs everywhere: what the menu and the tooltip say
for a given state (``tray_menu``, ``tray_tooltip``) and ``Tray``, which turns the icon's
events into actions. It works with a backend, so tests use a stand-in. The second is
``WinBackend``, the real icon (ctypes, ``Shell_NotifyIconW``). It cannot run on Linux, so it
is kept small and every call has its argument types set.

Threads: the icon lives on a thread of its own, with its own hidden window and message loop.
That thread never touches tkinter: it only calls ``on_action(action)``, which the launcher
turns into an event on its queue. The menu is built from ``Tray.state``, an immutable
snapshot that the launcher swaps in as a whole (a plain attribute assignment is atomic).
"""

from __future__ import annotations

import itertools
import queue
import sys
import threading
import time
import types
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

TOOLTIP_LETTERS = 127  # what Windows shows (szTip holds 128 with the end mark)
BALLOON_LETTERS = 255  # (szInfo holds 256)
TITLE_LETTERS = 63
DOUBLE_CLICK = 0.6  # seconds: a double click is two clicks, and opens the chat once
ACTIONS = ("open", "show", "kill", "quit")
ICON_FILE = Path("starlight") / "web" / "static" / "icons" / "starlight.ico"


def clean(text: object, limit: int) -> str:
    """Plain, short text for a tooltip or a balloon: control characters out, white space
    squeezed, cut to ``limit`` letters. (What a page says is never trusted.)"""
    plain = "".join(c if c.isprintable() else " " for c in str(text))
    plain = " ".join(plain.split())
    if len(plain) > limit:
        plain = plain[: limit - 1].rstrip() + "…"
    return plain


@dataclass(frozen=True)
class TrayState:
    """What the menu and the tooltip depend on."""

    chat: bool = False  # the web chat answers
    model: str = "loading"  # Status.model
    kill: bool | None = None  # None: not known yet
    model_name: str = ""
    window_hidden: bool = False  # the launcher window is hidden in the tray
    own_window: bool = True  # the chat opens in Starlight's own window (not in the browser)


@dataclass(frozen=True)
class MenuItem:
    action: str  # one of ACTIONS; "" for a separator line
    label: str = ""  # an English key: the app translates it
    enabled: bool = True
    default: bool = False  # bold: what a double click does

    @property
    def separator(self) -> bool:
        return not self.action


SEPARATOR = MenuItem("")


def tray_menu(state: TrayState) -> list[MenuItem]:
    """The right-click menu, top to bottom (English keys)."""
    items = [
        MenuItem("open", "Open Starlight" if state.own_window else "Open chat", state.chat, True)
    ]
    if state.window_hidden:
        items.append(MenuItem("show", "Show launcher window"))
    items.append(SEPARATOR)
    items.append(
        MenuItem("kill", "Resume" if state.kill else "Kill switch", state.kill is not None)
    )
    items.append(SEPARATOR)
    items.append(MenuItem("quit", "Quit Starlight"))
    return items


def tray_status(state: TrayState) -> str:
    """One short word group for the tooltip (an English key)."""
    if state.kill:
        return "kill switch on"
    if state.model == "paused":
        return "paused for a game"
    if state.model == "down":
        return "model stopped"
    if state.chat and state.model in ("ready", "off"):
        return "ready"
    return "starting"


def tray_tooltip(state: TrayState, translate: Callable[[str], str] = str) -> str:
    """ "Starlight - ready - <model>" (the model only while it is ready)."""
    status = tray_status(state)
    parts = ["Starlight", translate(status)]
    if status == "ready" and state.model_name:
        parts.append(state.model_name)
    return clean(" - ".join(parts), TOOLTIP_LETTERS)


class Backend(Protocol):
    """The icon itself. ``on_event`` gets "left" (a click on the icon), "balloon" (a click
    on a notification) or "menu:<action>"; ``menu`` is asked for the items when the menu
    opens. Both run on the backend's own thread."""

    def start(
        self, tip: str, on_event: Callable[[str], None], menu: Callable[[], list[MenuItem]]
    ) -> bool: ...

    def set_tip(self, tip: str) -> None: ...

    def balloon(self, text: str) -> None: ...

    def stop(self) -> None: ...


class Tray:
    """The icon, its tooltip and its menu, for a backend. ``on_action`` is called with an
    entry of ACTIONS, on the backend's thread: it must only hand the action on."""

    def __init__(
        self,
        backend: Backend,
        on_action: Callable[[str], None],
        translate: Callable[[str], str] = str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backend = backend
        self.on_action = on_action
        self.translate = translate
        self.clock = clock
        self.state = TrayState()  # replaced as a whole, never changed in place
        self.running = False
        self._tip = ""
        self._last_open = float("-inf")

    def start(self, state: TrayState | None = None) -> bool:
        """Show the icon. False: it could not be created (the caller carries on without)."""
        if state is not None:
            self.state = state
        self._tip = tray_tooltip(self.state, self.translate)
        try:
            self.running = bool(self.backend.start(self._tip, self.handle, self.menu))
        except Exception:
            self.running = False
        return self.running

    def update(self, state: TrayState) -> None:
        """A new state: the menu is built from it the next time it opens; the tooltip is
        only rewritten when its words change."""
        self.state = state
        tip = tray_tooltip(state, self.translate)
        if self.running and tip != self._tip:
            self._tip = tip
            self.backend.set_tip(tip)

    def balloon(self, text: str) -> None:
        """A notification next to the clock (the title is always "Starlight")."""
        text = clean(text, BALLOON_LETTERS)
        if self.running and text:
            self.backend.balloon(text)

    def stop(self) -> None:
        if self.running:
            self.running = False
            self.backend.stop()

    def menu(self) -> list[MenuItem]:
        """The items for the state now, in the app's language."""
        return [replace(i, label=self.translate(i.label)) for i in tray_menu(self.state)]

    def handle(self, event: str) -> None:
        action = ""
        if event in ("left", "balloon"):
            action = "open"
        elif event.startswith("menu:") and event[5:] in ACTIONS:
            action = event[5:]
        if not action:
            return
        if event != "menu:open" and action == "open":  # (a double click: one opening)
            now = self.clock()
            if now - self._last_open < DOUBLE_CLICK:
                return
            self._last_open = now
        self.on_action(action)


# -- Windows ----------------------------------------------------------------------------------

# Win32 numbers (winuser.h, shellapi.h)
WM_NULL, WM_DESTROY, WM_CLOSE = 0x0, 0x2, 0x10
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x202, 0x203, 0x205
NIN_BALLOONUSERCLICK = 0x405
WM_TRAY = 0x8001  # (WM_APP + 1) what the icon sends to our window
WM_SET_TIP, WM_SHOW_BALLOON = 0x8002, 0x8003  # our own, from other threads
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO = 0x1
MF_STRING, MF_GRAYED, MF_SEPARATOR = 0x0, 0x1, 0x800
TPM_RIGHTBUTTON, TPM_BOTTOMALIGN, TPM_RETURNCMD = 0x2, 0x20, 0x100
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
SM_CXSMICON, SM_CYSMICON = 49, 50
IDI_APPLICATION = 32512
ICON_ID = 1

_class_numbers = itertools.count(1)


def _win_api() -> Any:
    """The Win32 functions and structures used here, with their argument types set. Handles
    are 64 bits wide on a 64-bit Windows: a call without argtypes would cut them to 32 bits."""
    if sys.platform != "win32":
        raise OSError("the tray icon needs Windows")
    import ctypes
    from ctypes import wintypes

    lresult = ctypes.c_ssize_t
    wndproc = ctypes.WINFUNCTYPE(lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                 wintypes.LPARAM)  # fmt: skip

    class WNDCLASSW(ctypes.Structure):
        _fields_ = (
            ("style", wintypes.UINT),
            ("lpfnWndProc", wndproc),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        )

    class GUID(ctypes.Structure):
        _fields_ = (
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        )

    class NOTIFYICONDATAW(ctypes.Structure):  # the full Windows 7 size (976 bytes on 64 bits)
        _fields_ = (
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", ctypes.c_wchar * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", ctypes.c_wchar * 256),
            ("uVersion", wintypes.UINT),  # (a union with uTimeout)
            ("szInfoTitle", ctypes.c_wchar * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", GUID),
            ("hBalloonIcon", wintypes.HICON),
        )

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    hwnd, uint, cint, ptr = wintypes.HWND, wintypes.UINT, ctypes.c_int, ctypes.c_void_p
    bool_, menu, inst, text = wintypes.BOOL, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPCWSTR
    msg_ptr = ctypes.POINTER(wintypes.MSG)
    # (dll, function, argument types, result type): every function used, none left untyped
    table: list[tuple[Any, str, list[Any], Any]] = [
        (user32, "RegisterClassW", [ctypes.POINTER(WNDCLASSW)], wintypes.ATOM),
        (user32, "UnregisterClassW", [text, inst], bool_),
        (
            user32,
            "CreateWindowExW",
            [
                wintypes.DWORD,
                text,
                text,
                wintypes.DWORD,
                cint,
                cint,
                cint,
                cint,
                hwnd,
                menu,
                inst,
                ptr,
            ],
            hwnd,
        ),
        (user32, "DefWindowProcW", [hwnd, uint, wintypes.WPARAM, wintypes.LPARAM], lresult),
        (user32, "DestroyWindow", [hwnd], bool_),
        (user32, "GetMessageW", [msg_ptr, hwnd, uint, uint], bool_),
        (user32, "TranslateMessage", [msg_ptr], bool_),
        (user32, "DispatchMessageW", [msg_ptr], lresult),
        (user32, "PostMessageW", [hwnd, uint, wintypes.WPARAM, wintypes.LPARAM], bool_),
        (user32, "PostQuitMessage", [cint], None),
        (user32, "RegisterWindowMessageW", [text], uint),
        (user32, "GetCursorPos", [ctypes.POINTER(wintypes.POINT)], bool_),
        (user32, "CreatePopupMenu", [], menu),
        (user32, "AppendMenuW", [menu, uint, ctypes.c_size_t, text], bool_),
        (user32, "SetMenuDefaultItem", [menu, uint, uint], bool_),
        (user32, "SetForegroundWindow", [hwnd], bool_),
        (user32, "TrackPopupMenu", [menu, uint, cint, cint, cint, hwnd, ptr], bool_),
        (user32, "DestroyMenu", [menu], bool_),
        (user32, "LoadImageW", [inst, text, uint, cint, cint, uint], wintypes.HANDLE),
        (user32, "LoadIconW", [inst, ptr], wintypes.HICON),
        (user32, "DestroyIcon", [wintypes.HICON], bool_),
        (user32, "GetSystemMetrics", [cint], cint),
        (shell32, "Shell_NotifyIconW", [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)], bool_),
        (kernel32, "GetModuleHandleW", [text], wintypes.HMODULE),
    ]
    api = types.SimpleNamespace(
        WNDCLASSW=WNDCLASSW,
        NOTIFYICONDATAW=NOTIFYICONDATAW,
        WNDPROC=wndproc,
        MSG=wintypes.MSG,
        POINT=wintypes.POINT,
        byref=ctypes.byref,
        sizeof=ctypes.sizeof,
    )
    for dll, name, argtypes, restype in table:
        function = getattr(dll, name)
        function.argtypes = argtypes
        function.restype = restype
        setattr(api, name, function)
    return api


class WinBackend:
    """The real icon. Everything Win32 happens on the "tray" thread, which creates the hidden
    window (a window belongs to the thread that made it), registers its procedure and runs
    the message loop. Other threads only post it a message."""

    def __init__(self, icon_file: Path | None = None) -> None:
        self.icon_file = icon_file
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()  # set when the first try to show the icon is over
        self._ok = False
        self._api: Any = None
        self._hwnd: int | None = None
        self._proc: Any = None  # the window procedure: kept, or it is collected and crashes
        self._icon: Any = None
        self._icon_loaded = False  # we made it, so we destroy it
        self._added = False
        self._taskbar_created = 0  # the message Explorer broadcasts when it starts again
        self._tip = ""
        self._balloons: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._on_event: Callable[[str], None] = lambda _event: None
        self._menu: Callable[[], list[MenuItem]] = lambda: []

    def start(
        self, tip: str, on_event: Callable[[str], None], menu: Callable[[], list[MenuItem]]
    ) -> bool:
        if sys.platform != "win32":
            return False
        self._tip, self._on_event, self._menu = tip, on_event, menu
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        if not self._ready.wait(5) or not self._ok:
            self.stop()  # (no icon later, after the launcher has given up on it)
            return False
        return True

    def set_tip(self, tip: str) -> None:
        self._tip = tip
        self._post(WM_SET_TIP)

    def balloon(self, text: str) -> None:
        self._balloons.put(text)
        self._post(WM_SHOW_BALLOON)

    def stop(self) -> None:
        """Remove the icon and end the thread (the window deletes the icon as it closes)."""
        if sys.platform != "win32":
            return
        self._post(WM_CLOSE)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(2)

    def _post(self, message: int) -> None:
        if sys.platform != "win32":
            return
        thread = self._thread
        if self._api is not None and self._hwnd and thread is not None and thread.is_alive():
            self._api.PostMessageW(self._hwnd, message, 0, 0)

    # (the rest runs on the tray thread)

    def _run(self) -> None:
        if sys.platform != "win32":
            return
        api: Any = None
        hwnd = None
        hinst = None
        class_name = f"StarlightTrayWindow{next(_class_numbers)}"
        registered = False
        try:
            api = _win_api()
            self._api = api
            hinst = api.GetModuleHandleW(None)
            self._taskbar_created = api.RegisterWindowMessageW("TaskbarCreated")
            self._proc = api.WNDPROC(self._window_proc)
            window_class = api.WNDCLASSW()
            window_class.lpfnWndProc = self._proc
            window_class.hInstance = hinst
            window_class.lpszClassName = class_name
            registered = bool(api.RegisterClassW(api.byref(window_class)))
            # A plain hidden window, not a message-only one: only a real top-level window
            # gets the "TaskbarCreated" broadcast (the icon comes back after Explorer restarts).
            hwnd = api.CreateWindowExW(
                0, class_name, "Starlight", 0, 0, 0, 0, 0, None, None, hinst, None
            )
            if not hwnd:
                raise OSError("the tray window was not created")
            self._hwnd = hwnd
            self._icon = self._load_icon(api)
            self._ok = self._add(hwnd)
        except Exception:
            self._ok = False
        finally:
            self._ready.set()
        try:
            if self._ok and api is not None:
                message = api.MSG()
                while api.GetMessageW(api.byref(message), None, 0, 0) > 0:
                    api.TranslateMessage(api.byref(message))
                    api.DispatchMessageW(api.byref(message))
        except Exception:
            pass
        finally:
            self._cleanup(api, hwnd, hinst, class_name, registered)

    def _cleanup(
        self, api: Any, hwnd: int | None, hinst: Any, class_name: str, registered: bool
    ) -> None:
        if api is None:
            return
        try:
            if self._added:  # (the loop ended without a WM_DESTROY: still remove the icon)
                self._notify(NIM_DELETE)
            if hwnd:
                api.DestroyWindow(hwnd)  # (fails quietly when it is gone already)
            if registered:
                api.UnregisterClassW(class_name, hinst)
            if self._icon_loaded and self._icon:
                api.DestroyIcon(self._icon)
        except Exception:
            pass
        finally:
            self._hwnd = None
            self._added = False

    def _load_icon(self, api: Any) -> Any:
        icon = None
        if self.icon_file is not None and self.icon_file.is_file():
            size = (api.GetSystemMetrics(SM_CXSMICON), api.GetSystemMetrics(SM_CYSMICON))
            icon = api.LoadImageW(None, str(self.icon_file), IMAGE_ICON, *size, LR_LOADFROMFILE)
        self._icon_loaded = bool(icon)
        if not icon:  # (no file, as in a lone Starlight.exe): the plain application icon
            icon = api.LoadIconW(None, IDI_APPLICATION)
        return icon

    def _data(self) -> Any:
        data = self._api.NOTIFYICONDATAW()
        data.cbSize = self._api.sizeof(data)
        data.hWnd = self._hwnd
        data.uID = ICON_ID
        return data

    def _notify(self, command: int, data: Any = None) -> bool:
        data = self._data() if data is None else data
        return bool(self._api.Shell_NotifyIconW(command, self._api.byref(data)))

    def _add(self, hwnd: int) -> bool:
        data = self._data()
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self._icon
        data.szTip = self._tip
        self._added = self._notify(NIM_ADD, data)
        return self._added

    def _modify_tip(self) -> None:
        data = self._data()
        data.uFlags = NIF_TIP
        data.szTip = self._tip
        self._notify(NIM_MODIFY, data)

    def _show_balloon(self) -> None:
        text = ""
        while True:
            try:
                text = self._balloons.get_nowait()
            except queue.Empty:
                break
        if not text:
            return
        data = self._data()
        data.uFlags = NIF_INFO
        data.szInfoTitle = "Starlight"
        data.szInfo = text[:BALLOON_LETTERS]
        data.dwInfoFlags = NIIF_INFO
        self._notify(NIM_MODIFY, data)

    def _window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        api = self._api
        try:
            if message == WM_TRAY:
                self._mouse(hwnd, lparam & 0xFFFF)  # (the low word is the mouse message)
                return 0
            if message == WM_SET_TIP:
                self._modify_tip()
                return 0
            if message == WM_SHOW_BALLOON:
                self._show_balloon()
                return 0
            if message == self._taskbar_created and message:
                self._add(hwnd)  # Explorer started again: its icons are gone
                return 0
            if message == WM_CLOSE:
                api.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                if self._added:
                    self._notify(NIM_DELETE)
                    self._added = False
                api.PostQuitMessage(0)
                return 0
        except Exception:  # (an error must not leave this procedure)
            pass
        return int(api.DefWindowProcW(hwnd, message, wparam, lparam))

    def _mouse(self, hwnd: int, message: int) -> None:
        if message in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
            self._on_event("left")
        elif message == NIN_BALLOONUSERCLICK:
            self._on_event("balloon")
        elif message == WM_RBUTTONUP:
            self._show_menu(hwnd)

    def _show_menu(self, hwnd: int) -> None:
        api = self._api
        items = self._menu()
        menu = api.CreatePopupMenu()
        if not menu:
            return
        chosen = 0
        numbered: dict[int, MenuItem] = {}
        try:
            default = 0
            for number, item in enumerate(items, start=1):
                if item.separator:
                    api.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                    continue
                flags = MF_STRING | (0 if item.enabled else MF_GRAYED)
                api.AppendMenuW(menu, flags, number, item.label)
                numbered[number] = item
                if item.default:
                    default = number
            if default:
                api.SetMenuDefaultItem(menu, default, 0)  # (0: the number, not the position)
            point = api.POINT()
            api.GetCursorPos(api.byref(point))
            # Without this, the menu stays open when you click somewhere else.
            api.SetForegroundWindow(hwnd)
            chosen = api.TrackPopupMenu(
                menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_BOTTOMALIGN, point.x, point.y, 0,
                hwnd, None,
            )  # fmt: skip
            api.PostMessageW(hwnd, WM_NULL, 0, 0)  # (and this, for the same reason)
        finally:
            api.DestroyMenu(menu)
        picked = numbered.get(int(chosen))
        if picked is not None and picked.enabled:
            self._on_event(f"menu:{picked.action}")


def default_backend(root: Path) -> Backend | None:
    """The real icon on Windows; None elsewhere (no tray there)."""
    if sys.platform != "win32":
        return None
    return WinBackend(root / ICON_FILE)
