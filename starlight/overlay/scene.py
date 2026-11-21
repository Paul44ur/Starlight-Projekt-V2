"""What the overlay shows and how it is drawn (``docs/plan-overlay.md`` section 3).

``Scene`` holds the state (the orb's state, the mic level, a point, a caption) and draws one
monitor's part of it with a ``QPainter``. It works in Qt's global logical pixels (the
monitors' ``logical`` rects from ``geometry``); a window translates its painter by its
monitor's origin. Time comes from a clock that tests replace, so every frame can be drawn
at an exact moment.

All text is drawn with ``drawText`` as plain text, never as rich text.
"""

from __future__ import annotations

import math
import textwrap
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)

from . import geometry, protocol
from .geometry import Monitor, Pt, Rect

# --- The look (section 3; W8 tunes these on the user's PC) -------------------------------

ORB_RADIUS = geometry.ORB_RADIUS  # 44 px across
GLOW_REACH = 1.2  # the glow fades out at 1.2x the radius
RINGS = ((18.0, 20.0), (22.0, -35.0))  # (radius, tilt in degrees)
RING_SQUASH = 0.35
OPACITY = {"asleep": 0.35, "idle": 0.70, "listening": 1.0, "thinking": 1.0, "speaking": 1.0}
TURN_IDLE = 12.0  # seconds per turn of the rings
TURN_THINKING = 1.2
LINE_DRAW = 0.30  # seconds for the line to draw out from the orb
PULSE = 0.60  # seconds per pulse of the frame
PULSES = 2
PULSE_SCALE = 0.04
FADE = 0.25  # seconds to fade out a point or a caption
FADE_IN = 0.15  # seconds to fade in a caption
FRAME_OUT = 4.0  # the frame sits this far outside the box
FRAME_RADIUS = 6.0
CAPTION_CHARS = 60  # per line
CAPTION_LINES = 2
CAPTION_FONT = 15  # px
CAPTION_PAD = (14.0, 9.0)  # x, y
GLASS = QColor(12, 18, 28, 204)  # rgba 12,18,28,0.80
GLASS_EDGE = QColor(255, 255, 255, 51)  # white at 20 %
HALO = QColor(10, 20, 30, 89)  # rgba 10,20,30,0.35
CARD_WIDTH = 320.0
CARD_PAD = 16.0
CARD_LINES = 3
CARD_BUTTON_H = 34.0
CARD_GAP = 8.0

FRAME_FAST = 1 / 60  # seconds between frames while something moves
FRAME_IDLE = 1 / 20  # while only the idle rings turn (30 degrees a second)

# The child's own texts (section 7: "a tiny table of its own six texts").
TEXTS: dict[str, dict[str, str]] = {
    "de": {
        "yes": "Ja",
        "no": "Nein",
        "here": "Hier ist es",
        "maybe": "Vielleicht hier",
        "demo_ask": "Soll ich das anklicken?",
        "demo_caption": "Wo sind die Einstellungen?",
    },
    "en": {
        "yes": "Yes",
        "no": "No",
        "here": "Here you go",
        "maybe": "Maybe here",
        "demo_ask": "Shall I click it?",
        "demo_caption": "Where are the settings?",
    },
}


def text(language: str, key: str) -> str:
    return TEXTS.get(language, TEXTS["en"])[key]


# --- Pure helpers ------------------------------------------------------------------------


def ease_out(t: float) -> float:
    """Cubic ease-out, 0..1 to 0..1."""
    t = min(max(t, 0.0), 1.0)
    return 1.0 - (1.0 - t) ** 3


def caption_lines(value: str) -> list[str]:
    """The caption's lines: words wrapped at 60 characters, at most 2 lines; longer text
    ends in "…". Line breaks and tabs in the text count as spaces."""
    flat = " ".join(value.split())
    if not flat:
        return []
    lines = textwrap.wrap(flat, CAPTION_CHARS, break_on_hyphens=False)
    if len(lines) <= CAPTION_LINES:
        return lines
    last = lines[CAPTION_LINES - 1]
    if len(last) >= CAPTION_CHARS:
        last = last[: CAPTION_CHARS - 1]
    return [*lines[: CAPTION_LINES - 1], last.rstrip() + "…"]


def frame_scale(since_line_done: float, reduce_motion: bool) -> float:
    """The frame's scale: two pulses from 1.00 to 1.04 (600 ms each), then 1.00."""
    if reduce_motion or since_line_done < 0 or since_line_done >= PULSE * PULSES:
        return 1.0
    phase = (since_line_done % PULSE) / PULSE
    return 1.0 + PULSE_SCALE * math.sin(math.pi * phase)


def mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def with_alpha(color: QColor, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(min(max(alpha, 0.0), 1.0))
    return c


def _covers_orb(rect: Rect, orb: Pt) -> bool:
    """True if the rect overlaps the orb's square (radius plus a 4 px gap)."""
    x, y, w, h = rect
    r = ORB_RADIUS + 4
    return x < orb[0] + r and orb[0] - r < x + w and y < orb[1] + r and orb[1] - r < y + h


def _unit(dx: float, dy: float) -> Pt:
    length = math.hypot(dx, dy)
    return (0.0, 1.0) if length < 1e-6 else (dx / length, dy / length)


# --- State -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    accent: str = "#4FC8FF"
    orb_monitor: int = 1  # 1-based
    orb: protocol.OrbMode = "always"
    reduce_motion: bool = False
    language: protocol.Language = "en"


@dataclass
class PointView:
    id: str
    box: Rect  # global logical, clipped to its monitor
    monitor: int  # index into Scene.monitors
    confidence: protocol.Confidence
    start: float
    until: float  # the fade starts here


@dataclass
class CaptionView:
    lines: list[str]
    who: protocol.Who
    start: float
    until: float


@dataclass
class SpeechView:
    envelope: tuple[float, ...]
    rate: float
    start: float


@dataclass
class Scene:
    clock: Callable[[], float] = time.monotonic
    monitors: list[Monitor] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)
    system_reduce_motion: bool = False  # Windows' "Animation effects" off
    state: protocol.State = "asleep"
    level: float = 0.0  # the latest mic level
    glow: float = 1.0  # the glow's scale as drawn (smoothed)
    point: PointView | None = None
    caption: CaptionView | None = None
    speech: SpeechView | None = None
    _last_tick: float | None = None

    # --- Changes -------------------------------------------------------------------------

    @property
    def reduce_motion(self) -> bool:
        return self.settings.reduce_motion or self.system_reduce_motion

    @property
    def accent(self) -> QColor:
        return QColor(self.settings.accent)

    def configure(self, msg: protocol.Hello | protocol.Config) -> None:
        changes = {
            name: getattr(msg, name)
            for name in ("accent", "orb_monitor", "orb", "reduce_motion", "language")
            if getattr(msg, name) is not None
        }
        self.settings = replace(self.settings, **changes)

    def set_state(self, state: protocol.State) -> None:
        self.state = state
        if state != "listening":
            self.level = 0.0
        if state != "speaking":
            self.speech = None

    def set_level(self, level: float) -> None:
        self.level = level

    def set_speech(self, msg: protocol.Speech) -> None:
        self.speech = SpeechView(msg.envelope, msg.rate, self.clock())

    def show_point(self, msg: protocol.Point) -> bool:
        """Show a point (physical box). False if the box is on no monitor."""
        if not self.monitors:
            return False
        placed = geometry.to_logical_rect(msg.box, self.monitors)
        if placed is None:
            return False
        index, box = placed
        now = self.clock()
        self.point = PointView(msg.id, box, index, msg.confidence, now, now + msg.seconds)
        return True

    def show_caption(self, msg: protocol.Caption) -> None:
        lines = caption_lines(msg.text)
        if not lines:
            self.caption = None
            return
        now = self.clock()
        self.caption = CaptionView(lines, msg.who, now, now + msg.seconds)

    def clear(self) -> None:
        """Fade out the point and the caption (section 4.1: not the card)."""
        now = self.clock()
        if self.point is not None:
            self.point.until = min(self.point.until, now)
        if self.caption is not None:
            self.caption.until = min(self.caption.until, now)

    def mouse_at(self, pos: Pt) -> None:
        """The mouse is at `pos` (global logical): inside the box ends the point."""
        p = self.point
        if p is None:
            return
        x, y, w, h = p.box
        if x <= pos[0] <= x + w and y <= pos[1] <= y + h:
            p.until = min(p.until, self.clock())

    # --- Time ----------------------------------------------------------------------------

    def _glow_target(self, now: float) -> float:
        if self.reduce_motion:
            return 1.0
        if self.state == "listening":
            return 1.0 + 0.5 * self.level
        if self.state == "thinking":
            return 1.0 + 0.08 * (0.5 + 0.5 * math.sin(2 * math.pi * now))
        if self.state == "speaking" and self.speech is not None:
            s = self.speech
            i = int((now - s.start) * s.rate)
            return 1.0 + 0.5 * s.envelope[i] if 0 <= i < len(s.envelope) else 1.0
        return 1.0

    def tick(self) -> None:
        """Move time on: smooth the glow and drop what has faded out."""
        now = self.clock()
        dt = 0.0 if self._last_tick is None else max(0.0, now - self._last_tick)
        self._last_tick = now
        target = self._glow_target(now)
        if self.state == "thinking" or self.reduce_motion:
            self.glow = target  # already smooth (a sine), or no motion at all
        else:
            k = min(1.0, dt * 12.0)
            self.glow += (target - self.glow) * k
            if abs(self.glow - target) < 0.002:
                self.glow = target
        if self.point is not None and now >= self.point.until + FADE:
            self.point = None
        if self.caption is not None and now >= self.caption.until + FADE:
            self.caption = None
        if self.speech is not None:
            s = self.speech
            if now - s.start > len(s.envelope) / s.rate:
                self.speech = None

    def orb_visible(self) -> bool:
        return bool(self.monitors) and not (self.state == "asleep" and self.settings.orb == "awake")

    def orb_index(self) -> int:
        """The orb's monitor (index); the first one if the setting names none."""
        n = self.settings.orb_monitor - 1
        return n if 0 <= n < len(self.monitors) else 0

    def orb_moves(self) -> bool:
        return self.orb_visible() and not self.reduce_motion and self.state != "asleep"

    def _point_moves(self, now: float) -> bool:
        p = self.point
        if p is None:
            return False
        if now >= p.until:
            return True  # fading out
        if self.reduce_motion:
            return False
        return now - p.start < LINE_DRAW + PULSE * PULSES

    def _caption_moves(self, now: float) -> bool:
        c = self.caption
        return c is not None and (now >= c.until or now - c.start < FADE_IN)

    def moving(self, monitor: int) -> tuple[bool, bool]:
        """(the orb moves on this monitor, something else moves on it)."""
        now = self.clock()
        orb = self.orb_moves() and monitor == self.orb_index()
        p = self.point
        other = (p is not None and p.monitor == monitor and self._point_moves(now)) or (
            monitor == self.orb_index() and self._caption_moves(now)
        )
        glow_settling = monitor == self.orb_index() and abs(self.glow - self._glow_target(now)) > 0
        return orb or glow_settling, other

    def next_frame(self) -> float | None:
        """Seconds until something must be drawn again, or None if nothing will change."""
        now = self.clock()
        if (
            self._point_moves(now)
            or self._caption_moves(now)
            or (self.orb_moves() and self.state != "idle")
            or abs(self.glow - self._glow_target(now)) > 0
        ):
            return FRAME_FAST
        if self.orb_moves():
            return FRAME_IDLE
        deadlines = [x.until for x in (self.point, self.caption) if x is not None]
        if self.speech is not None:
            deadlines.append(self.speech.start + len(self.speech.envelope) / self.speech.rate)
        if not deadlines:
            return None
        return max(0.0, min(deadlines) - now)

    # --- Where things are ----------------------------------------------------------------

    def line_for(self, monitor: int) -> tuple[Pt, Pt, Pt] | None:
        """(orb centre, line start, line end) for the point on this monitor, if any. On a
        monitor without the orb, the line starts from that monitor's own orb point."""
        p = self.point
        if p is None or p.monitor != monitor or not self.monitors:
            return None
        centre = geometry.orb_point(self.monitors[monitor])
        end = geometry.line_end(centre, p.box)
        ux, uy = _unit(end[0] - centre[0], end[1] - centre[1])
        start = (centre[0] + ux * ORB_RADIUS, centre[1] + uy * ORB_RADIUS)
        return centre, start, end

    def caption_layout(self, font: QFont) -> tuple[Rect, list[str]] | None:
        c = self.caption
        if c is None or not self.monitors:
            return None
        metrics = QFontMetricsF(font)
        width = max(metrics.horizontalAdvance(line) for line in c.lines) + 2 * CAPTION_PAD[0]
        height = metrics.lineSpacing() * len(c.lines) + 2 * CAPTION_PAD[1]
        index = self.orb_index()
        monitor = self.monitors[index]
        orb = geometry.orb_point(monitor)
        size = (math.ceil(width), math.ceil(height))
        p = self.point
        target = p.box if p is not None and p.monitor == index else None
        rect = geometry.caption_rect(orb, size, monitor, target)
        if _covers_orb(rect, orb):
            # Too wide for its side on a small monitor: the other side, else under the orb.
            left_side = rect[0] + rect[2] / 2 < orb[0]
            flipped = (orb[0] + (-1 if left_side else 1), orb[1], 1.0, 1.0)
            rect = geometry.caption_rect(orb, size, monitor, flipped)
            if _covers_orb(rect, orb):
                rect = geometry.card_rect(orb, size, monitor)
        return rect, c.lines

    # --- Drawing -------------------------------------------------------------------------

    def paint(self, painter: QPainter, monitor: int) -> None:
        """Draw this monitor's part (global logical coordinates)."""
        if not self.monitors or monitor >= len(self.monitors):
            return
        now = self.clock()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        line = self.line_for(monitor)
        if line is not None and self.point is not None:
            self._paint_point(painter, self.point, line, now)
        if monitor == self.orb_index():
            if self.orb_visible():
                self._paint_orb(painter, geometry.orb_point(self.monitors[monitor]), now)
            self._paint_caption(painter, now)
        elif line is not None:
            self._paint_ghost(painter, line[0], now)

    def orb_rect(self, monitor: int) -> QRectF:
        """The area the orb can draw in (global logical), for partial repaints."""
        cx, cy = geometry.orb_point(self.monitors[monitor])
        reach = ORB_RADIUS * GLOW_REACH * 1.5 + 4
        return QRectF(cx - reach, cy - reach, 2 * reach, 2 * reach)

    def _angle(self, now: float) -> float:
        if self.reduce_motion or self.state == "asleep":
            return 0.0
        period = TURN_THINKING if self.state == "thinking" else TURN_IDLE
        return (now % period) / period * 360.0

    def _paint_orb(self, painter: QPainter, centre: Pt, now: float) -> None:
        opacity = OPACITY[self.state]
        accent = self.accent
        c = QPointF(*centre)
        reach = ORB_RADIUS * GLOW_REACH * self.glow
        painter.save()
        painter.setOpacity(painter.opacity() * opacity)
        painter.setPen(Qt.PenStyle.NoPen)
        glow = QRadialGradient(c, reach)
        glow.setColorAt(0.0, QColor(255, 255, 255, 255))
        glow.setColorAt(0.16, mix(QColor(255, 255, 255), accent, 0.35))
        glow.setColorAt(0.38, with_alpha(accent, 0.85))
        glow.setColorAt(0.70, with_alpha(accent, 0.22))
        glow.setColorAt(1.0, with_alpha(accent, 0.0))
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(c, reach, reach)

        angle = self._angle(now)
        for n, (radius, tilt) in enumerate(RINGS):
            turn = angle if n == 0 else -angle
            painter.save()
            painter.translate(c)
            painter.rotate(tilt)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(with_alpha(accent, 0.75), 1.2))
            painter.drawEllipse(QPointF(0, 0), radius, radius * RING_SQUASH)
            # A bright bead runs round the ring, so the turning shows.
            theta = math.radians(turn + (0 if n == 0 else 140))
            bead = QPointF(radius * math.cos(theta), radius * RING_SQUASH * math.sin(theta))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 230))
            painter.drawEllipse(bead, 1.6, 1.6)
            painter.restore()
        painter.restore()

    def _paint_ghost(self, painter: QPainter, centre: Pt, now: float) -> None:
        """A small faint orb where a line on another monitor starts."""
        p = self.point
        fade = self._point_alpha(p, now) if p is not None else 1.0
        accent = self.accent
        c = QPointF(*centre)
        reach = ORB_RADIUS * 0.75
        painter.save()
        painter.setOpacity(painter.opacity() * 0.6 * fade)
        painter.setPen(Qt.PenStyle.NoPen)
        glow = QRadialGradient(c, reach)
        glow.setColorAt(0.0, QColor(255, 255, 255, 220))
        glow.setColorAt(0.35, with_alpha(accent, 0.6))
        glow.setColorAt(1.0, with_alpha(accent, 0.0))
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(c, reach, reach)
        painter.restore()

    def _point_alpha(self, p: PointView, now: float) -> float:
        if now < p.until:
            return 1.0
        return max(0.0, 1.0 - (now - p.until) / FADE)

    def _paint_point(
        self, painter: QPainter, p: PointView, line: tuple[Pt, Pt, Pt], now: float
    ) -> None:
        alpha = self._point_alpha(p, now)
        if alpha <= 0:
            return
        accent = self.accent
        _, start, end = line
        grow = 1.0 if self.reduce_motion else ease_out((now - p.start) / LINE_DRAW)
        a = QPointF(*start)
        b = QPointF(start[0] + (end[0] - start[0]) * grow, start[1] + (end[1] - start[1]) * grow)
        painter.save()
        painter.setOpacity(painter.opacity() * alpha)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        def stroke(brush: QColor | QLinearGradient, width: float) -> None:
            pen = QPen(QBrush(brush), width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(a, b)

        stroke(HALO, 5.0)
        stroke(with_alpha(accent, 0.12), 10.0)
        core = QLinearGradient(QPointF(*start), QPointF(*end))
        core.setColorAt(0.0, with_alpha(accent, 0.15))
        core.setColorAt(1.0, with_alpha(accent, 0.90))
        stroke(core, 1.6)

        if grow >= 1.0:
            since = now - p.start - (0.0 if self.reduce_motion else LINE_DRAW)
            self._paint_frame(painter, p, frame_scale(since, self.reduce_motion))
        painter.restore()

    def _paint_frame(self, painter: QPainter, p: PointView, scale: float) -> None:
        accent = self.accent
        x, y, w, h = p.box
        cx, cy = x + w / 2, y + h / 2
        fw, fh = (w + 2 * FRAME_OUT) * scale, (h + 2 * FRAME_OUT) * scale
        frame = QRectF(cx - fw / 2, cy - fh / 2, fw, fh)
        dashed = p.confidence == "maybe"
        for out, alpha in ((10.0, 25), (5.0, 60)):
            ring = frame.adjusted(-out, -out, out, out)
            # The glow stays solid; only the frame itself is dashed for "maybe".
            painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), alpha), 3.0))
            painter.drawRoundedRect(ring, FRAME_RADIUS + out, FRAME_RADIUS + out)
        # A dark halo under the frame, so it shows on light windows too.
        painter.setPen(QPen(HALO, 4.0))
        painter.drawRoundedRect(frame, FRAME_RADIUS, FRAME_RADIUS)
        pen = QPen(accent, 2.0)
        if dashed:
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([4.0, 3.0])
        painter.setPen(pen)
        painter.drawRoundedRect(frame, FRAME_RADIUS, FRAME_RADIUS)

    def caption_font(self) -> QFont:
        font = QFont()
        font.setFamilies(["Segoe UI", "Inter", "DejaVu Sans", "sans-serif"])
        font.setPixelSize(CAPTION_FONT)
        return font

    def _paint_caption(self, painter: QPainter, now: float) -> None:
        c = self.caption
        if c is None:
            return
        font = self.caption_font()
        layout = self.caption_layout(font)
        if layout is None:
            return
        (x, y, w, h), lines = layout
        if now >= c.until:
            alpha = max(0.0, 1.0 - (now - c.until) / FADE)
        elif self.reduce_motion:
            alpha = 1.0
        else:
            alpha = min(1.0, (now - c.start) / FADE_IN)
        if alpha <= 0:
            return
        painter.save()
        painter.setOpacity(painter.opacity() * alpha)
        paint_glass(painter, QRectF(x, y, w, h), 10.0)
        painter.setFont(font)
        # The user's own words are a little dimmer than Starlight's.
        painter.setPen(QColor(255, 255, 255, 185 if c.who == "you" else 255))
        metrics = QFontMetricsF(font)
        for i, line in enumerate(lines):
            top = y + CAPTION_PAD[1] + i * metrics.lineSpacing()
            painter.drawText(
                QRectF(x + CAPTION_PAD[0], top, w - 2 * CAPTION_PAD[0], metrics.lineSpacing()),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                line,
            )
        painter.restore()


def paint_glass(painter: QPainter, rect: QRectF, radius: float) -> None:
    """Dark glass: the caption's and the card's background."""
    painter.save()
    painter.setPen(QPen(GLASS_EDGE, 1.0))
    painter.setBrush(GLASS)
    painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    painter.restore()


# --- The Yes/No card ---------------------------------------------------------------------


@dataclass(frozen=True)
class CardLayout:
    size: tuple[float, float]
    lines: list[str]
    buttons: list[QRectF]  # in the card's own coordinates
    labels: list[str]


def card_font() -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI", "Inter", "DejaVu Sans", "sans-serif"])
    font.setPixelSize(CAPTION_FONT)
    return font


def wrap_to_width(value: str, metrics: QFontMetricsF, width: float, max_lines: int) -> list[str]:
    """Words wrapped to a pixel width, at most `max_lines` lines (the last ends in "…" if
    text was left over). A word wider than the line is cut."""
    words = value.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}" if current else word
        if metrics.horizontalAdvance(trial) <= width:
            current = trial
            continue
        if current:
            lines.append(current)
        current = word
        while metrics.horizontalAdvance(current) > width and len(current) > 1:
            cut = len(current)
            while cut > 1 and metrics.horizontalAdvance(current[:cut]) > width:
                cut -= 1
            lines.append(current[:cut])
            current = current[cut:]
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = metrics.elidedText(lines[-1] + " …", Qt.TextElideMode.ElideRight, width)
        if not lines[-1].endswith("…"):
            lines[-1] = metrics.elidedText(lines[-1] + "……", Qt.TextElideMode.ElideRight, width)
    return lines


def card_layout(question: str, labels: Sequence[str], font: QFont) -> CardLayout:
    metrics = QFontMetricsF(font)
    inner = CARD_WIDTH - 2 * CARD_PAD
    lines = wrap_to_width(question, metrics, inner, CARD_LINES)
    text_h = metrics.lineSpacing() * max(1, len(lines))
    top = CARD_PAD + text_h + 12.0
    n = len(labels)
    bw = (inner - CARD_GAP * (n - 1)) / n
    buttons = [QRectF(CARD_PAD + i * (bw + CARD_GAP), top, bw, CARD_BUTTON_H) for i in range(n)]
    height = top + CARD_BUTTON_H + CARD_PAD
    return CardLayout((CARD_WIDTH, math.ceil(height)), lines, buttons, list(labels))


def paint_card(
    painter: QPainter, layout: CardLayout, accent: QColor, hover: int | None, focus: bool
) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    w, h = layout.size
    paint_glass(painter, QRectF(0, 0, w, h), 12.0)
    font = card_font()
    painter.setFont(font)
    metrics = QFontMetricsF(font)
    painter.setPen(QColor(255, 255, 255))
    for i, line in enumerate(layout.lines):
        painter.drawText(
            QRectF(CARD_PAD, CARD_PAD + i * metrics.lineSpacing(), w, metrics.lineSpacing()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            line,
        )
    for i, (rect, label) in enumerate(zip(layout.buttons, layout.labels, strict=True)):
        first = i == 0
        lit = hover == i
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 8.0, 8.0)
        if first:
            painter.fillPath(path, with_alpha(accent, 0.95 if lit else 0.80))
            painter.setPen(QPen(with_alpha(accent, 1.0), 1.0))
        else:
            painter.fillPath(path, QColor(255, 255, 255, 40 if lit else 18))
            painter.setPen(QPen(QColor(255, 255, 255, 90), 1.0))
        painter.drawPath(path)
        if focus and first:
            painter.setPen(QPen(QColor(255, 255, 255, 200), 1.0, Qt.PenStyle.DotLine))
            painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 10.0, 10.0)
        painter.setPen(QColor(8, 16, 24) if first else QColor(255, 255, 255))
        painter.drawText(
            rect,
            int(Qt.AlignmentFlag.AlignCenter),
            metrics.elidedText(label, Qt.TextElideMode.ElideRight, rect.width() - 12),
        )
