"""Where things go on the monitors (``docs/plan-overlay.md`` 4.2). Pure functions, no Qt.

Two kinds of pixels:

* **physical**: what Windows and the screenshots use (``EnumDisplayMonitors``, UI
  Automation, OCR). Targets arrive in these.
* **logical**: what Qt draws in, physical divided by the monitor's scale (``dpr``: 1.0 at
  100 %, 1.25 at 125 %, 1.5 at 150 %). Each monitor keeps its own origin, so a point is
  converted relative to the monitor that holds it, never with one factor for the desktop.

A rect is ``(x, y, w, h)`` and a point ``(x, y)``. Logical values can be fractional at
125 % or 150 %; Qt draws them with ``QPointF``/``QRectF``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

Rect = tuple[float, float, float, float]  # x, y, w, h
Pt = tuple[float, float]  # x, y

SIZE_TOLERANCE = 2.0  # logical px: physical size / dpr against Qt's size
ORB_DOWN = 30.0  # the orb's centre, below the monitor's top edge
ORB_RADIUS = 22.0  # 44 px across (section 3)
LINE_OUTSIDE = 4.0  # the line ends this far outside the target box
GAP = 16.0  # between the orb's edge and the caption or the card
MARGIN = 8.0  # the caption and the card keep this far from the monitor's edges


@dataclass(frozen=True)
class Monitor:
    name: str
    physical: Rect
    logical: Rect
    dpr: float


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= SIZE_TOLERANCE


def match_monitors(
    physical: Sequence[Rect], logical: Sequence[tuple[str, Rect, float]]
) -> list[Monitor]:
    """Pair Windows' monitors (physical rects) with Qt's screens (name, logical rect, dpr).

    For each Qt screen, in Qt's order: a physical monitor whose size divided by the
    screen's dpr is the screen's size (within 2 px), preferring one at the same top-left
    (Qt on Windows keeps the native top-left), then the first free one in Windows' order.
    A screen with no such monitor takes the first free one. Extra monitors or screens
    stay unpaired. The result is in Qt's order.
    """
    free = list(range(len(physical)))
    out: list[Monitor] = []
    for name, rect, dpr in logical:
        if not free:
            break
        lx, ly, lw, lh = rect
        sized = [
            i for i in free if _close(physical[i][2] / dpr, lw) and _close(physical[i][3] / dpr, lh)
        ]
        placed = [i for i in sized if _close(physical[i][0], lx) and _close(physical[i][1], ly)]
        index = (placed or sized or free)[0]
        free.remove(index)
        px, py, pw, ph = physical[index]
        out.append(Monitor(name, (px, py, pw, ph), (lx, ly, lw, lh), dpr))
    return out


def _contains(rect: Rect, point: Pt) -> bool:
    x, y, w, h = rect
    return x <= point[0] < x + w and y <= point[1] < y + h


def _distance2(rect: Rect, point: Pt) -> float:
    x, y, w, h = rect
    dx = max(x - point[0], 0.0, point[0] - (x + w))
    dy = max(y - point[1], 0.0, point[1] - (y + h))
    return dx * dx + dy * dy


def _monitor_for(point: Pt, monitors: Sequence[Monitor]) -> int:
    """The monitor holding the point, or (off every screen) the nearest one."""
    for i, m in enumerate(monitors):
        if _contains(m.physical, point):
            return i
    return min(range(len(monitors)), key=lambda i: _distance2(monitors[i].physical, point))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def _phys_to_logical(m: Monitor, point: Pt) -> Pt:
    px, py = m.physical[0], m.physical[1]
    lx, ly = m.logical[0], m.logical[1]
    return (lx + (point[0] - px) / m.dpr, ly + (point[1] - py) / m.dpr)


def to_logical(point: Pt, monitors: Sequence[Monitor]) -> tuple[int, Pt]:
    """(monitor index, logical point) for a physical point. A point off every screen is
    moved to the nearest edge of the nearest monitor."""
    if not monitors:
        raise ValueError("no monitors")
    i = _monitor_for(point, monitors)
    m = monitors[i]
    x, y, w, h = m.physical
    inside = (_clamp(point[0], x, x + w), _clamp(point[1], y, y + h))
    return i, _phys_to_logical(m, inside)


def to_logical_rect(box: Rect, monitors: Sequence[Monitor]) -> tuple[int, Rect] | None:
    """(monitor index, logical rect) for a physical box: the monitor holding the box's
    centre (or the nearest one), the box clipped to that monitor. None if nothing of the
    box is on that monitor."""
    if not monitors:
        raise ValueError("no monitors")
    bx, by, bw, bh = box
    i = _monitor_for((bx + bw / 2, by + bh / 2), monitors)
    m = monitors[i]
    mx, my, mw, mh = m.physical
    left, top = max(bx, mx), max(by, my)
    right, bottom = min(bx + bw, mx + mw), min(by + bh, my + mh)
    if right <= left or bottom <= top:
        return None
    lx, ly = _phys_to_logical(m, (left, top))
    return i, (lx, ly, (right - left) / m.dpr, (bottom - top) / m.dpr)


def orb_point(monitor: Monitor) -> Pt:
    """The orb's centre: the top centre of the monitor, 30 px down (logical)."""
    x, y, w, _ = monitor.logical
    return (x + w / 2, y + ORB_DOWN)


def line_end(start: Pt, box: Rect) -> Pt:
    """Where the line from `start` ends: the nearest point on the edge of the box grown by
    4 px. From inside that area it goes to the nearest edge (the top on a tie)."""
    x, y, w, h = box
    left, top = x - LINE_OUTSIDE, y - LINE_OUTSIDE
    right, bottom = x + w + LINE_OUTSIDE, y + h + LINE_OUTSIDE
    sx, sy = start
    cx, cy = _clamp(sx, left, right), _clamp(sy, top, bottom)
    if (cx, cy) != (sx, sy):
        return (cx, cy)
    sides = [
        (sy - top, (sx, top)),
        (bottom - sy, (sx, bottom)),
        (sx - left, (left, sy)),
        (right - sx, (right, sy)),
    ]
    return min(sides, key=lambda side: side[0])[1]


def _inside(x: float, y: float, size: tuple[float, float], monitor: Monitor) -> Rect:
    """The rect moved inside the monitor, MARGIN from its edges (to the top-left edge if
    it is too big)."""
    mx, my, mw, mh = monitor.logical
    w, h = size
    x = max(mx + MARGIN, min(x, mx + mw - MARGIN - w))
    y = max(my + MARGIN, min(y, my + mh - MARGIN - h))
    return (x, y, w, h)


def caption_rect(
    orb: Pt, size: tuple[float, float], monitor: Monitor, target: Rect | None = None
) -> Rect:
    """The caption beside the orb, vertically centred on it, 16 px from the orb's edge, on
    the side away from the target so the line never crosses it (no target, or a target
    straight below: the right). Kept inside the monitor. All logical."""
    w, h = size
    ox, oy = orb
    right = target is None or target[0] + target[2] / 2 <= ox
    x = ox + ORB_RADIUS + GAP if right else ox - ORB_RADIUS - GAP - w
    return _inside(x, oy - h / 2, size, monitor)


def card_rect(orb: Pt, size: tuple[float, float], monitor: Monitor) -> Rect:
    """The Yes/No card under the orb, centred on it, 16 px below the orb's edge, kept
    inside the monitor. All logical."""
    w, _ = size
    ox, oy = orb
    return _inside(ox - w / 2, oy + ORB_RADIUS + GAP, size, monitor)
