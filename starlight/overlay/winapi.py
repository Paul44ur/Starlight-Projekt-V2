"""The few Windows calls the overlay needs (``docs/plan-overlay.md`` 4.3), with ctypes.

Every function works on every platform: off Windows, or when a call fails, it returns a
safe answer (False, or an empty list) and the overlay goes on. Every foreign function has
its ``argtypes`` and ``restype`` set, so a wrong argument fails loudly instead of
corrupting the stack.

- ``set_dpi_awareness()``: per-monitor aware (v2), before Qt starts.
- ``exclude_from_capture(hwnd)``: hide a window from screenshots (mss, OCR, the vision
  model and the user's own), ``WDA_EXCLUDEFROMCAPTURE``. Windows 10 2004 and newer.
- ``monitor_rects()``: each monitor's rect in physical pixels (``EnumDisplayMonitors``).
- ``animations_enabled()``: Windows' "Animation effects" setting.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11
# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 is the handle value -4.
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
SPI_GETCLIENTAREAANIMATION = 0x1042
MONITORINFOF_PRIMARY = 0x1

Rect = tuple[int, int, int, int]  # x, y, w, h


class RECT(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    )


class MONITORINFO(ctypes.Structure):
    _fields_ = (
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.c_ulong),
    )


def rect_of(r: RECT) -> Rect:
    """(x, y, w, h) of a Windows RECT (left, top, right, bottom)."""
    return (int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top))


def order_monitors(found: list[tuple[Rect, bool]]) -> list[Rect]:
    """The primary monitor first, then the others in Windows' order."""
    primary = [rect for rect, is_primary in found if is_primary]
    others = [rect for rect, is_primary in found if not is_primary]
    return primary + others


def _user32() -> Any:
    if sys.platform != "win32":
        return None
    try:
        return ctypes.WinDLL("user32", use_last_error=True)
    except OSError:  # pragma: no cover - user32 is always there on Windows
        return None


def _warn_once(key: str, message: str, *args: object) -> None:
    if key not in _warned:
        _warned.add(key)
        log.warning(message, *args)


_warned: set[str] = set()


def set_dpi_awareness() -> bool:
    """Make the process per-monitor DPI aware (v2). Call before Qt starts. True if set."""
    if sys.platform != "win32":
        return False
    user32 = _user32()
    if user32 is None:
        return False
    try:
        fn = user32.SetProcessDpiAwarenessContext
    except AttributeError:  # older than Windows 10 1703
        return False
    fn.argtypes = (ctypes.c_void_p,)
    fn.restype = ctypes.c_bool
    ok = bool(fn(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)))
    if not ok:
        # ERROR_ACCESS_DENIED: already set (by a manifest, or Qt); that is fine.
        log.info("SetProcessDpiAwarenessContext: error %d", ctypes.get_last_error())
    return ok


def exclude_from_capture(hwnd: int) -> bool:
    """Hide the window from screenshots. False (logged once) where Windows cannot."""
    if sys.platform != "win32":
        return False
    user32 = _user32()
    if user32 is None:
        return False
    fn = user32.SetWindowDisplayAffinity
    fn.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    fn.restype = ctypes.c_bool
    if fn(ctypes.c_void_p(hwnd), WDA_EXCLUDEFROMCAPTURE):
        return True
    _warn_once(
        "affinity",
        "overlay: cannot hide its windows from screenshots (error %d); "
        "they are cleared before each screenshot instead",
        ctypes.get_last_error(),
    )
    return False


def monitor_rects() -> list[Rect]:
    """Each monitor's rect in physical pixels, the primary first. Empty off Windows."""
    if sys.platform != "win32":
        return []
    user32 = _user32()
    if user32 is None:
        return []
    proc_type = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_void_p
    )
    enum = user32.EnumDisplayMonitors
    enum.argtypes = (ctypes.c_void_p, ctypes.c_void_p, proc_type, ctypes.c_void_p)
    enum.restype = ctypes.c_bool
    info_fn = user32.GetMonitorInfoW
    info_fn.argtypes = (ctypes.c_void_p, ctypes.POINTER(MONITORINFO))
    info_fn.restype = ctypes.c_bool
    found: list[tuple[Rect, bool]] = []

    def each(hmonitor: int, _hdc: int, _rect: Any, _data: int) -> bool:
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if info_fn(hmonitor, ctypes.byref(info)):
            found.append((rect_of(info.rcMonitor), bool(info.dwFlags & MONITORINFOF_PRIMARY)))
        return True

    callback: Callable[..., bool] = proc_type(each)
    if not enum(None, None, callback, None):
        _warn_once("monitors", "overlay: EnumDisplayMonitors failed")
        return []
    return order_monitors(found)


def animations_enabled() -> bool:
    """Windows' "Animation effects" (Settings, Accessibility, Visual effects). True when it
    cannot be read (and off Windows)."""
    if sys.platform != "win32":
        return True
    user32 = _user32()
    if user32 is None:
        return True
    fn = user32.SystemParametersInfoW
    fn.argtypes = (ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)
    fn.restype = ctypes.c_bool
    value = ctypes.c_int(1)
    if not fn(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0):
        return True
    return bool(value.value)
