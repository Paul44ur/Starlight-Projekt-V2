"""Finding a thing on the screen by its name, for pointing at it: "where is the settings
menu?". The order is UI Automation (the control's own name, exact), then OCR on each
monitor, then the vision model (a guess, marked "maybe"). It never guesses on its own: a
miss says what was tried and which windows are open, so the model can ask "which window?".

Coordinates are physical pixels on the virtual desktop, the same as ``screen_info``.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from .backends import Box, ControlInfo, WindowInfo, find_phrase

MAX_WINDOWS = 8  # UI Automation looks at most in this many top-level windows ...
UIA_BUDGET = 2.0  # ... and for at most this many seconds in all
TITLE_LENGTH = 80
MISS_WINDOWS = 5  # window titles listed with a miss
POINT_BOX = (64, 40)  # a vision answer with only a point becomes a box this size
SURE_OCR = 60.0  # OCR words below this confidence (0-100) are only a "maybe"

# English and German names of the same thing, in both directions.
NAME_GROUPS: tuple[tuple[str, ...], ...] = (
    ("settings", "einstellungen", "optionen"),
    ("file", "datei"),
    ("edit", "bearbeiten"),
    ("view", "ansicht"),
    ("help", "hilfe"),
    ("search", "suche", "suchen"),
    ("save", "speichern"),
    ("open", "öffnen"),
    ("close", "schließen"),
    ("new", "neu"),
    ("print", "drucken"),
    ("back", "zurück"),
    ("menu", "menü"),
    ("more", "mehr"),
    ("share", "teilen"),
    ("download", "herunterladen"),
)
_GROUP_OF = {name: group for group in NAME_GROUPS for name in group}

# Words that describe a thing rather than name it: "the settings menu" is "settings".
FILLER_PHRASES = ("das menü",)
FILLER_WORDS = frozenset({"the", "menu", "button", "der", "die", "das", "knopf", "schaltfläche"})


def strip_filler(target: str) -> str:
    """The name without filler words, lower case. If nothing is left ("the menu"), the
    bare word stays ("menu")."""
    text = " ".join(target.lower().split())
    padded = f" {text} "
    for phrase in FILLER_PHRASES:
        padded = padded.replace(f" {phrase} ", " ")
    words = [w for w in padded.split() if w not in FILLER_WORDS]
    if words:
        return " ".join(words)
    bare = [w for w in text.split() if w not in ("the", "der", "die", "das")]
    return " ".join(bare) or text


def name_variants(target: str) -> list[str]:
    """The names to look for, the user's own first: "settings" also finds
    "Einstellungen" and "Optionen", "Datei" also finds "File"."""
    base = strip_filler(target)
    if not base:
        return []
    out = [base]
    for other in _GROUP_OF.get(base, ()):
        if other not in out:
            out.append(other)
    words = base.split()
    if len(words) > 1:  # "save file" -> "speichern file", "save datei"
        for i, word in enumerate(words):
            for other in _GROUP_OF.get(word, ()):
                variant = " ".join([*words[:i], other, *words[i + 1 :]])
                if variant not in out:
                    out.append(variant)
    return out


EXACT, STARTS, CONTAINS = 0, 1, 2


def match_rank(name: str, variants: list[str]) -> tuple[int, int] | None:
    """How well a control's name matches: (exact 0 / starts with 1 / contains 2, which
    variant), lower is better; None when it does not match at all."""
    text = " ".join(name.lower().split())
    if not text:
        return None
    best: tuple[int, int] | None = None
    for index, variant in enumerate(variants):
        if text == variant:
            rank = (EXACT, index)
        elif text.startswith(variant):
            rank = (STARTS, index)
        elif variant in text:
            rank = (CONTAINS, index)
        else:
            continue
        if best is None or rank < best:
            best = rank
    return best


# -- the parts find_target needs, as small interfaces (the real ones are in backends) ------


class Uia(Protocol):
    def session(self) -> Any:
        """A context manager: UI Automation (COM) for the calling thread."""
        ...

    def windows(self) -> list[WindowInfo]: ...

    def controls(self, window: WindowInfo, deadline: float) -> Iterable[ControlInfo]: ...


class Screen(Protocol):
    def monitors(self) -> list[tuple[int, int, int, int]]: ...
    def capture(self, region: tuple[int, int, int, int] | None = None) -> Any: ...


class Ocr(Protocol):
    def boxes(self, image: Any) -> list[Box]: ...


@dataclass
class Hit:
    box: tuple[int, int, int, int]  # x, y, w, h on the virtual desktop
    method: str  # uia, ocr, vision
    label: str
    confidence: str  # sure, maybe
    window: str = ""


def _usable(rect: tuple[int, int, int, int]) -> bool:
    return rect[2] > 0 and rect[3] > 0


def _contains(rect: tuple[int, int, int, int], x: int, y: int) -> bool:
    return rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]


def _centre(box: tuple[int, int, int, int]) -> tuple[int, int]:
    return box[0] + box[2] // 2, box[1] + box[3] // 2


def title(text: str) -> str:
    return text[:TITLE_LENGTH]


def order_windows(windows: list[WindowInfo], name: str = "") -> list[WindowInfo]:
    """Visible windows to search: the foreground one first, then the rest largest first,
    at most MAX_WINDOWS. ``name`` limits them to windows whose title contains it."""
    usable = [w for w in windows if _usable(w.rect)]
    if name:
        usable = [w for w in usable if name.lower() in w.title.lower()]
    usable.sort(key=lambda w: (not w.foreground, -(w.rect[2] * w.rect[3])))
    return usable[:MAX_WINDOWS]


def search_uia(
    uia: Uia,
    windows: list[WindowInfo],
    variants: list[str],
    clock: Callable[[], float] = time.monotonic,
) -> Hit | None:
    """The best matching control in the first window (in ``windows`` order) that has
    one. Off-screen and zero-size controls are skipped."""
    deadline = clock() + UIA_BUDGET
    for window in windows:
        if clock() > deadline:
            break
        best: tuple[tuple[int, int], ControlInfo] | None = None
        for control in uia.controls(window, deadline):
            if control.offscreen or not _usable(control.rect):
                continue
            rank = match_rank(control.name, variants)
            if rank is not None and (best is None or rank < best[0]):
                best = (rank, control)
                if rank == (EXACT, 0):
                    break
        if best is not None:
            control = best[1]
            return Hit(control.rect, "uia", control.name, "sure", title(window.title))
    return None


def search_ocr(
    screen: Screen,
    ocr: Ocr,
    variants: list[str],
    windows: list[WindowInfo],
    prefer: WindowInfo | None = None,
) -> Hit | None:
    """OCR on each monitor. A hit inside ``prefer`` (default: the foreground window)
    wins; among the rest the one OCR is most sure of."""
    found: list[Box] = []
    for left, top, width, height in screen.monitors():
        image = screen.capture((left, top, width, height))
        boxes = ocr.boxes(image)
        del image  # screenshots are not kept
        for variant in variants:
            for match in find_phrase(boxes, variant):
                match.x += left  # the image starts at the monitor's corner
                match.y += top
                found.append(match)
    if not found:
        return None
    foreground = prefer or next((w for w in windows if w.foreground), None)

    def key(box: Box) -> tuple[bool, float]:
        inside = foreground is not None and _contains(foreground.rect, *box.center)
        return inside, box.confidence

    best = max(found, key=key)
    rect = (best.x, best.y, best.width, best.height)
    sure = "sure" if best.confidence >= SURE_OCR else "maybe"
    return Hit(rect, "ocr", best.text, sure, window_at(windows, rect))


def window_at(windows: list[WindowInfo], box: tuple[int, int, int, int]) -> str:
    """The title of the window the box is in (the foreground one if several)."""
    x, y = _centre(box)
    for window in sorted(windows, key=lambda w: not w.foreground):
        if _usable(window.rect) and _contains(window.rect, x, y):
            return title(window.title)
    return ""


VISION_PROMPT = (
    "Find this on the screenshot: {target}. Answer with JSON only: "
    '{{"bbox_2d": [x1, y1, x2, y2]}} with its box in pixels of this {w}x{h} image, '
    'or {{"bbox_2d": null}} if it is not visible.'
)
_BBOX = re.compile(r'"bbox_2d"\s*:\s*(null|\[[^\]]*\])')
_OBJECT = re.compile(r"\{[^{}]*\}")


def parse_vision(
    content: str, scale: float, origin: tuple[int, int]
) -> tuple[int, int, int, int] | None:
    """The vision model's answer as a box on the virtual desktop. Qwen2.5-VL answers in
    pixels of the image it got; ``scale`` is how much that image was shrunk. An answer
    with only a point (``{"x": .., "y": ..}``, as for ``locate``) becomes a small box."""
    left, top = origin
    found = _BBOX.search(content)
    if found:
        try:
            values = json.loads(found.group(1))
        except json.JSONDecodeError:
            return None
        if not isinstance(values, list) or len(values) != 4:
            return None
        try:
            x1, y1, x2, y2 = (float(v) / scale for v in values)
        except (TypeError, ValueError):
            return None
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        if x2 - x1 < 1 or y2 - y1 < 1:
            return None
        return int(x1) + left, int(y1) + top, int(x2 - x1), int(y2 - y1)
    for raw in _OBJECT.findall(content):
        try:
            point = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(point, dict):
            continue
        x, y = point.get("x"), point.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            cx, cy = int(x / scale) + left, int(y / scale) + top
            w, h = POINT_BOX
            return cx - w // 2, cy - h // 2, w, h
    return None


def monitor_of(monitors: list[tuple[int, int, int, int]], box: tuple[int, int, int, int]) -> int:
    """The monitor (numbered from 1, as screen_info) the box's centre is on; 0 if none."""
    x, y = _centre(box)
    for number, rect in enumerate(monitors, start=1):
        if _contains(rect, x, y):
            return number
    return 0
