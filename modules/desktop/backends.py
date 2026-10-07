"""Hardware access for the desktop module, behind small interfaces so the module logic
can be tested without a screen.

* screen: ``mss`` (fast screenshots of all monitors)
* input: ``pynput`` (mouse and keyboard)
* OCR: Tesseract via ``pytesseract``
* Windows UI Automation: ``uiautomation`` (click controls by name, more reliable than
  coordinates)
"""

from __future__ import annotations

import contextlib
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from PIL import Image


class DesktopUnavailable(Exception):
    pass


@dataclass
class Box:
    text: str
    x: int
    y: int
    width: int
    height: int
    confidence: float

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


# -- screen --------------------------------------------------------------------------------


class Screen(Protocol):
    def bounds(self) -> tuple[int, int, int, int]:
        """left, top, width, height of the whole virtual screen."""
        ...

    def monitors(self) -> list[tuple[int, int, int, int]]:
        """left, top, width, height of each monitor, in the order Windows numbers them."""
        ...

    def capture(self, region: tuple[int, int, int, int] | None = None) -> Image.Image: ...


class MssScreen:
    def __init__(self) -> None:
        try:
            import mss
        except ImportError as exc:
            raise DesktopUnavailable("install the 'desktop' extra (mss)") from exc
        # mss 10 renamed mss.mss to mss.MSS
        self._open = getattr(mss, "MSS", None) or mss.mss

    def bounds(self) -> tuple[int, int, int, int]:
        with self._open() as sct:
            m = sct.monitors[0]
            return m["left"], m["top"], m["width"], m["height"]

    def monitors(self) -> list[tuple[int, int, int, int]]:
        with self._open() as sct:
            return [(m["left"], m["top"], m["width"], m["height"]) for m in sct.monitors[1:]]

    def capture(self, region: tuple[int, int, int, int] | None = None) -> Image.Image:
        with self._open() as sct:
            if region is None:
                m = sct.monitors[0]
                area = {
                    "left": m["left"],
                    "top": m["top"],
                    "width": m["width"],
                    "height": m["height"],
                }
            else:
                left, top, width, height = region
                area = {"left": left, "top": top, "width": width, "height": height}
            shot = sct.grab(area)
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


# -- input ---------------------------------------------------------------------------------


class Input(Protocol):
    def position(self) -> tuple[int, int]: ...
    def move(self, x: int, y: int) -> None: ...
    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None: ...
    def scroll(self, dx: int, dy: int) -> None: ...
    def type_char(self, char: str) -> None: ...
    def press_keys(self, keys: list[str]) -> None: ...
    def release_all(self) -> None: ...


class PynputInput:
    def __init__(self) -> None:
        try:
            from pynput import keyboard, mouse
        except Exception as exc:  # ImportError, or no display on Linux
            raise DesktopUnavailable(f"input emulation unavailable: {exc}") from exc
        self._kb_mod = keyboard
        self._mouse_mod = mouse
        self._mouse = mouse.Controller()
        self._keyboard = keyboard.Controller()
        self._pressed_keys: list[Any] = []
        self._pressed_buttons: list[Any] = []

    def position(self) -> tuple[int, int]:
        x, y = self._mouse.position
        return int(x), int(y)

    def move(self, x: int, y: int) -> None:
        self._mouse.position = (x, y)

    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None:
        self.move(x, y)
        time.sleep(0.05)
        self._mouse.click(getattr(self._mouse_mod.Button, button), count)

    def scroll(self, dx: int, dy: int) -> None:
        self._mouse.scroll(dx, dy)

    def type_char(self, char: str) -> None:
        self._keyboard.type(char)

    def _key(self, name: str) -> Any:
        aliases = {
            "win": "cmd",
            "windows": "cmd",
            "super": "cmd",
            "control": "ctrl",
            "return": "enter",
            "escape": "esc",
            "del": "delete",
            "pgup": "page_up",
            "pgdn": "page_down",
        }
        name = aliases.get(name.lower(), name.lower())
        if len(name) == 1:
            return name
        try:
            return getattr(self._kb_mod.Key, name)
        except AttributeError as exc:
            raise ValueError(f"unknown key '{name}'") from exc

    def press_keys(self, keys: list[str]) -> None:
        resolved = [self._key(k) for k in keys]
        try:
            for key in resolved:
                self._keyboard.press(key)
                self._pressed_keys.append(key)
            time.sleep(0.05)
        finally:
            for key in reversed(resolved):
                self._keyboard.release(key)
                if key in self._pressed_keys:
                    self._pressed_keys.remove(key)

    def release_all(self) -> None:
        """Kill switch: let go of every key and button."""
        for key in reversed(self._pressed_keys):
            with contextlib.suppress(Exception):
                self._keyboard.release(key)
        self._pressed_keys.clear()
        for name in ("left", "right", "middle"):
            with contextlib.suppress(Exception):
                self._mouse.release(getattr(self._mouse_mod.Button, name))


# -- OCR -----------------------------------------------------------------------------------


class Ocr(Protocol):
    def boxes(self, image: Image.Image) -> list[Box]: ...


class TesseractOcr:
    WINDOWS_DEFAULT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")

    def __init__(self, languages: str = "eng", command: str = "") -> None:
        try:
            import pytesseract
        except ImportError as exc:
            raise DesktopUnavailable("install the 'desktop' extra (pytesseract)") from exc
        cmd = (
            command
            or shutil.which("tesseract")
            or (str(self.WINDOWS_DEFAULT) if sys.platform == "win32" else "")
        )
        if not cmd or (not Path(cmd).exists() and not shutil.which(cmd)):
            raise DesktopUnavailable("Tesseract not found; install it or set ocr_command")
        pytesseract.pytesseract.tesseract_cmd = cmd
        self._tess = pytesseract
        self.languages = languages

    def boxes(self, image: Image.Image) -> list[Box]:
        data = self._tess.image_to_data(
            image, lang=self.languages, output_type=self._tess.Output.DICT
        )
        out = []
        for i, text in enumerate(data["text"]):
            conf = float(data["conf"][i])
            if text.strip() and conf >= 0:
                out.append(
                    Box(
                        text,
                        data["left"][i],
                        data["top"][i],
                        data["width"][i],
                        data["height"][i],
                        conf,
                    )
                )
        return out


def boxes_to_text(boxes: list[Box]) -> str:
    """Words back into lines, top to bottom."""
    lines: list[list[Box]] = []
    for box in sorted(boxes, key=lambda b: (b.y, b.x)):
        if lines and abs(lines[-1][0].y - box.y) <= max(6, box.height // 2):
            lines[-1].append(box)
        else:
            lines.append([box])
    return "\n".join(" ".join(b.text for b in sorted(line, key=lambda b: b.x)) for line in lines)


def find_phrase(boxes: list[Box], phrase: str) -> list[Box]:
    """Boxes (merged across words) whose text matches ``phrase``, case-insensitive."""
    words = phrase.lower().split()
    if not words:
        return []
    ordered = sorted(boxes, key=lambda b: (b.y, b.x))
    matches = []
    for i in range(len(ordered)):
        window = ordered[i : i + len(words)]
        if len(window) < len(words):
            break
        if all(w in b.text.lower() for w, b in zip(words, window, strict=True)):
            left = min(b.x for b in window)
            top = min(b.y for b in window)
            right = max(b.x + b.width for b in window)
            bottom = max(b.y + b.height for b in window)
            matches.append(
                Box(
                    " ".join(b.text for b in window),
                    left,
                    top,
                    right - left,
                    bottom - top,
                    min(b.confidence for b in window),
                )
            )
    return matches


# -- Windows UI Automation -----------------------------------------------------------------


class UiAutomation:
    """Find controls by name in the foreground (or a named) window. Windows only."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise DesktopUnavailable("UI Automation is only available on Windows")
        try:
            import uiautomation
        except ImportError as exc:
            raise DesktopUnavailable("install the 'desktop' extra (uiautomation)") from exc
        self._auto: Any = uiautomation

    def _root(self, window: str) -> Any:
        auto = self._auto
        if window:
            root = auto.WindowControl(searchDepth=1, SubName=window)
            if not root.Exists(2):
                raise DesktopUnavailable(f"no window matching '{window}'")
            return root
        return auto.GetForegroundControl().GetTopLevelControl()

    def find(self, name: str, control_type: str = "", window: str = "") -> Box | None:
        root = self._root(window)
        criteria: dict[str, Any] = {"searchDepth": 15, "Name": name}
        if control_type:
            criteria["ControlTypeName"] = control_type
        control = root.Control(**criteria)
        if not control.Exists(2):
            criteria.pop("Name")
            criteria["SubName"] = name
            control = root.Control(**criteria)
            if not control.Exists(1):
                return None
        rect = control.BoundingRectangle
        return Box(name, rect.left, rect.top, rect.width(), rect.height(), 100.0)

    def list_controls(self, window: str = "", limit: int = 150) -> list[dict[str, str]]:
        root = self._root(window)
        controls = []
        for control, _depth in self._auto.WalkControl(root, maxDepth=8):
            if control.Name:
                controls.append({"name": control.Name, "type": control.ControlTypeName})
            if len(controls) >= limit:
                break
        return controls


def make_dpi_aware() -> None:
    """On Windows, use physical pixels so screenshots and clicks share coordinates."""
    if sys.platform != "win32":
        return
    with contextlib.suppress(Exception):
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
