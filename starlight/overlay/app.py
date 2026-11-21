"""The overlay process (``docs/plan-overlay.md`` 4.3): one click-through window per monitor,
the Yes/No card, and the JSON lines on stdin and stdout.

- ``Overlay`` owns the windows and the ``Scene``. ``handle(msg)`` applies one message;
  ``send`` writes one to the core. Tests drive it in-process; ``run()`` wires it to the
  real stdin and stdout.
- stdin is read on a plain thread that hands each line to the GUI thread through a Qt
  signal: Qt objects are only touched on the GUI thread.
- stdin closed or ``quit``: everything fades out and the process ends within 1 s.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from typing import BinaryIO

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCursor,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QScreen,
)
from PySide6.QtWidgets import QApplication, QWidget

from . import geometry, protocol, winapi
from .scene import CardLayout, Scene, card_font, card_layout, paint_card, text

log = logging.getLogger("starlight.overlay")

QUIT_FADE_MS = 300  # then the process ends (within 1 s of quit or stdin closing)
MOUSE_CHECK_MS = 100  # 10 times a second while a point shows
SCREENS_SETTLE_MS = 250  # screens added or removed: rebuild after this pause


class _Lines(QObject):
    """Carries lines from the reader thread to the GUI thread."""

    line = Signal(bytes)
    closed = Signal()


def read_lines(stream: BinaryIO, bridge: _Lines) -> None:
    """Read lines (each at most 64 KB; longer ones are dropped whole) until the stream
    ends, then say so. Runs on its own thread."""
    try:
        while True:
            chunk = stream.readline(protocol.MAX_LINE + 1)
            if not chunk:
                break
            if len(chunk) > protocol.MAX_LINE and not chunk.endswith(b"\n"):
                log.warning("overlay message skipped: line over %d bytes", protocol.MAX_LINE)
                while chunk and not chunk.endswith(b"\n"):  # skip the rest of that line
                    chunk = stream.readline(protocol.MAX_LINE + 1)
                continue
            if chunk.strip():
                bridge.line.emit(chunk)
    except (OSError, ValueError) as exc:
        log.warning("overlay: stdin failed: %s", exc)
    finally:
        bridge.closed.emit()


class OverlayWindow(QWidget):
    """A transparent, click-through, always-on-top window over one whole monitor."""

    FLAGS = (
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.Tool
        | Qt.WindowType.WindowTransparentForInput
        | Qt.WindowType.WindowDoesNotAcceptFocus
    )

    def __init__(self, scene: Scene, index: int, screen: QScreen) -> None:
        super().__init__(None, self.FLAGS)
        self.scene = scene
        self.index = index
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle("Starlight overlay")
        self.setScreen(screen)
        self.setGeometry(screen.geometry())

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            painter.setClipRect(event.rect())
            origin = self.scene.monitors[self.index].logical if self.scene.monitors else None
            if origin is not None:
                painter.translate(-origin[0], -origin[1])
                self.scene.paint(painter, self.index)
        finally:
            painter.end()

    def update_global(self, rect: QRectF) -> None:
        """Repaint a part, given in global logical pixels."""
        origin = self.scene.monitors[self.index].logical
        self.update(rect.translated(-origin[0], -origin[1]).toAlignedRect())


class CardWindow(QWidget):
    """The Yes/No card: the only part of the overlay that takes clicks. It never takes the
    keyboard focus by itself; once it has it (the user clicked it), Enter picks the first
    choice and Esc the last."""

    chosen = Signal(str, str)  # ask id, choice id

    def __init__(self, accent: Callable[[], str]) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self._accent = accent
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setMouseTracking(True)
        self.setWindowTitle("Starlight")
        self.ask: protocol.Ask | None = None
        self.layout_: CardLayout | None = None
        self.hover: int | None = None

    def open(self, ask: protocol.Ask, labels: list[str], rect: QRect) -> None:
        self.ask = ask
        self.layout_ = card_layout(ask.question, labels, card_font())
        self.hover = None
        self.setGeometry(rect)
        self.show()
        self.raise_()
        self.update()

    def size_for(self, ask: protocol.Ask, labels: list[str]) -> tuple[float, float]:
        return card_layout(ask.question, labels, card_font()).size

    def close_card(self) -> None:
        self.ask = None
        self.layout_ = None
        self.hide()

    def button_at(self, pos: QPointF) -> int | None:
        if self.layout_ is None:
            return None
        for i, rect in enumerate(self.layout_.buttons):
            if rect.contains(pos):
                return i
        return None

    def pick(self, index: int) -> None:
        if self.ask is None or not 0 <= index < len(self.ask.choices):
            return
        ask = self.ask
        self.close_card()
        self.chosen.emit(ask.id, ask.choices[index].id)

    def paintEvent(self, event: QPaintEvent) -> None:
        if self.layout_ is None:
            return
        from PySide6.QtGui import QColor

        painter = QPainter(self)
        try:
            paint_card(painter, self.layout_, QColor(self._accent()), self.hover, self.hasFocus())
        finally:
            painter.end()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        hover = self.button_at(event.position())
        if hover != self.hover:
            self.hover = hover
            self.update()

    def leaveEvent(self, event: object) -> None:
        if self.hover is not None:
            self.hover = None
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.button_at(event.position())
            if index is not None:
                self.pick(index)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.ask is None:
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.pick(0)
        elif event.key() == Qt.Key.Key_Escape:
            self.pick(len(self.ask.choices) - 1)
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event: object) -> None:
        self.update()

    def focusOutEvent(self, event: object) -> None:
        self.update()


def physical_rects(screens: list[QScreen]) -> list[geometry.Rect]:
    """Windows' monitor rects in physical pixels; elsewhere worked out from Qt's view."""
    found = winapi.monitor_rects()
    if found:
        return [tuple(float(v) for v in r) for r in found]  # type: ignore[misc]
    out: list[geometry.Rect] = []
    for s in screens:
        g, dpr = s.geometry(), s.devicePixelRatio()
        out.append((g.x() * dpr, g.y() * dpr, g.width() * dpr, g.height() * dpr))
    return out


def qt_screens() -> list[QScreen]:
    """Qt's screens, the primary first (monitor 1), then the others in Qt's order."""
    screens = list(QGuiApplication.screens())
    primary = QGuiApplication.primaryScreen()
    if primary in screens:
        screens.remove(primary)
        screens.insert(0, primary)
    return screens


class Overlay(QObject):
    """The overlay: its windows, its scene, and its messages."""

    def __init__(
        self,
        send: Callable[[protocol.Message], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        on_quit: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._send = send
        self._on_quit = on_quit or (lambda: QApplication.quit())
        self.scene = Scene(clock=clock, system_reduce_motion=not winapi.animations_enabled())
        self.windows: list[OverlayWindow] = []
        self.card = CardWindow(lambda: self.scene.settings.accent)
        self.card.chosen.connect(self._answer)
        self.greeted = False
        self.quitting = False
        self.exclude_from_capture = False
        self._ask_timer = QTimer(self)
        self._ask_timer.setSingleShot(True)
        self._ask_timer.timeout.connect(self._ask_timed_out)
        self._frame = QTimer(self)
        self._frame.setSingleShot(True)
        self._frame.timeout.connect(self._on_frame)
        self._mouse = QTimer(self)
        self._mouse.setInterval(MOUSE_CHECK_MS)
        self._mouse.timeout.connect(self._check_mouse)
        self._rebuild = QTimer(self)
        self._rebuild.setSingleShot(True)
        self._rebuild.setInterval(SCREENS_SETTLE_MS)
        self._rebuild.timeout.connect(self._screens_changed)
        app = QGuiApplication.instance()
        if isinstance(app, QGuiApplication):
            app.screenAdded.connect(self._watch_screen)
            app.screenAdded.connect(lambda _s: self._rebuild.start())
            app.screenRemoved.connect(lambda _s: self._rebuild.start())
            for s in QGuiApplication.screens():
                self._watch_screen(s)
        self.build_windows()

    # --- Windows and screens -------------------------------------------------------------

    def _watch_screen(self, screen: QScreen) -> None:
        screen.geometryChanged.connect(lambda _g: self._rebuild.start())

    def build_windows(self) -> None:
        """One window per screen, from scratch (at start and when the screens change)."""
        for w in self.windows:
            w.hide()
            w.deleteLater()
        screens = qt_screens()
        logical = [
            (
                s.name(),
                (
                    float(s.geometry().x()),
                    float(s.geometry().y()),
                    float(s.geometry().width()),
                    float(s.geometry().height()),
                ),
                float(s.devicePixelRatio()),
            )
            for s in screens
        ]
        self.scene.monitors = geometry.match_monitors(physical_rects(screens), logical)
        self.windows = [OverlayWindow(self.scene, i, s) for i, s in enumerate(screens)]
        if self.greeted:
            self.show_windows()

    def show_windows(self) -> None:
        hidden = True
        for w in self.windows:
            w.show()
            hidden = winapi.exclude_from_capture(int(w.winId())) and hidden
        if self.card.isVisible() or self.card.ask is not None:
            winapi.exclude_from_capture(int(self.card.winId()))
        self.exclude_from_capture = hidden and bool(self.windows)
        self.redraw()

    def _screens_changed(self) -> None:
        self.build_windows()
        if self.card.ask is not None:
            self._place_card()
        if self.greeted:
            self.send_ready()

    def screens(self) -> list[protocol.Screen]:
        return [
            protocol.Screen(
                name=m.name,
                x=round(m.logical[0]),
                y=round(m.logical[1]),
                w=max(1, round(m.logical[2])),
                h=max(1, round(m.logical[3])),
                dpr=m.dpr,
            )
            for m in self.scene.monitors
        ]

    def send_ready(self) -> None:
        self.send(protocol.Ready(tuple(self.screens()), self.exclude_from_capture))

    # --- Messages ------------------------------------------------------------------------

    def send(self, msg: protocol.Message) -> None:
        try:
            self._send(msg)
        except (OSError, ValueError) as exc:
            log.warning("overlay: cannot send %s: %s", msg.type, exc)

    def handle_line(self, line: bytes) -> None:
        msg = protocol.decode(line)
        if msg is not None:
            self.handle(msg)

    def handle(self, msg: protocol.Message) -> None:
        if self.quitting:
            return
        try:
            self._handle(msg)
        except Exception as exc:  # a bad message must never end the overlay
            log.exception("overlay: %s failed", msg.type)
            self.send(protocol.Error(f"{msg.type}: {type(exc).__name__}: {exc}"))

    def _handle(self, msg: protocol.Message) -> None:
        scene = self.scene
        match msg:
            case protocol.Hello():
                scene.configure(msg)
                self.greeted = True
                self.show_windows()
                self.send_ready()
            case protocol.Config():
                scene.configure(msg)
                if self.card.ask is not None:
                    self._place_card()
            case protocol.Point():
                if not scene.show_point(msg):
                    self.send(protocol.Error(f"point {msg.id}: the box is on no screen"))
            case protocol.Clear():
                scene.clear()
            case protocol.StateMsg():
                scene.set_state(msg.state)
            case protocol.Level():
                scene.set_level(msg.level)
            case protocol.Speech():
                scene.set_speech(msg)
            case protocol.Caption():
                scene.show_caption(msg)
            case protocol.Ask():
                self.open_ask(msg)
            case protocol.CancelAsk():
                if self.card.ask is not None and self.card.ask.id == msg.id:
                    self._ask_timer.stop()
                    self.card.close_card()
            case protocol.Quit():
                self.quit()
            case _:
                log.info("overlay: %s is not for the overlay; ignored", msg.type)
        self.redraw()

    # --- The card ------------------------------------------------------------------------

    def labels(self, ask: protocol.Ask) -> list[str]:
        language = self.scene.settings.language
        fallback = [text(language, "yes"), text(language, "no")]
        return [
            c.label or (fallback[i] if i < len(fallback) else c.id)
            for i, c in enumerate(ask.choices)
        ]

    def open_ask(self, ask: protocol.Ask) -> None:
        old = self.card.ask
        if old is not None:  # a new question replaces an open one: the old one gets "no answer"
            self._ask_timer.stop()
            self.card.close_card()
            self.send(protocol.Answer(old.id, None))
        self.card.ask = ask
        self._place_card()
        self._ask_timer.start(max(1, round(ask.timeout * 1000)))

    def _place_card(self) -> None:
        ask = self.card.ask
        if ask is None or not self.scene.monitors:
            return
        labels = self.labels(ask)
        monitor = self.scene.monitors[self.scene.orb_index()]
        size = self.card.size_for(ask, labels)
        x, y, w, h = geometry.card_rect(geometry.orb_point(monitor), size, monitor)
        self.card.open(ask, labels, QRect(round(x), round(y), round(w), round(h)))
        winapi.exclude_from_capture(int(self.card.winId()))

    def _answer(self, ask_id: str, choice: str) -> None:
        self._ask_timer.stop()
        self.send(protocol.Answer(ask_id, choice))

    def _ask_timed_out(self) -> None:
        ask = self.card.ask
        if ask is not None:
            self.card.close_card()
            self.send(protocol.Answer(ask.id, None))

    # --- Drawing and time ----------------------------------------------------------------

    def redraw(self) -> None:
        """Repaint every window and plan the next frame."""
        self.scene.tick()
        for w in self.windows:
            w.update()
        self._plan()

    def _plan(self) -> None:
        wait = self.scene.next_frame()
        if wait is None:
            self._frame.stop()
        else:
            self._frame.start(max(1, round(wait * 1000)))
        if self.scene.point is not None:
            if not self._mouse.isActive():
                self._mouse.start()
        else:
            self._mouse.stop()

    def _on_frame(self) -> None:
        had_point = self.scene.point is not None
        had_caption = self.scene.caption is not None
        self.scene.tick()
        changed = (self.scene.point is None) != (not had_point) or (self.scene.caption is None) != (
            not had_caption
        )
        for w in self.windows:
            orb, other = self.scene.moving(w.index)
            if other or changed:
                w.update()
            elif orb:
                w.update_global(self.scene.orb_rect(w.index))
        self._plan()

    def _check_mouse(self) -> None:
        pos: QPoint = QCursor.pos()
        before = self.scene.point.until if self.scene.point is not None else None
        self.scene.mouse_at((float(pos.x()), float(pos.y())))
        if self.scene.point is not None and self.scene.point.until != before:
            self.redraw()

    # --- The end -------------------------------------------------------------------------

    def quit(self) -> None:
        """Fade out, answer an open question with "no answer", and end."""
        if self.quitting:
            return
        ask = self.card.ask
        if ask is not None:
            self._ask_timer.stop()
            self.card.close_card()
            self.send(protocol.Answer(ask.id, None))
        self.scene.clear()
        self.redraw()
        self.quitting = True
        QTimer.singleShot(QUIT_FADE_MS, self._on_quit)


def write_stdout(msg: protocol.Message) -> None:
    sys.stdout.buffer.write(protocol.encode(msg))
    sys.stdout.buffer.flush()


def run(setup: Callable[[Overlay], object] | None = None) -> int:
    """Start Qt and the windows, and return the exit code. Without `setup` the overlay
    reads its messages from stdin; with it (the demo), `setup` drives it instead."""
    winapi.set_dpi_awareness()
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Starlight overlay")
    overlay = Overlay(write_stdout)
    keep: list[object] = []  # what must live as long as the overlay
    reader: threading.Thread | None = None
    if setup is not None:
        keep.append(setup(overlay))
    else:
        bridge = _Lines()
        bridge.line.connect(overlay.handle_line)
        bridge.closed.connect(overlay.quit)
        keep.append(bridge)
        reader = threading.Thread(
            target=read_lines, args=(sys.stdin.buffer, bridge), name="overlay-stdin", daemon=True
        )
        reader.start()
    code = int(app.exec())
    if reader is not None and reader.is_alive():
        # The reader still waits in stdin (a `quit` came before stdin closed). Python
        # aborts at shutdown when a daemon thread holds stdin's lock, so end right here.
        logging.shutdown()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    keep.clear()
    return code
