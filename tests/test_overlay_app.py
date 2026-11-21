"""The overlay process (docs/plan-overlay.md 4.3, W2), under Qt's offscreen platform.

Two kinds of tests: the real child (`python -m starlight.overlay`) as a subprocess, talked
to in JSON lines; and the `Overlay` and `Scene` in this process, for clicks on the card and
for rendered frames. Click-through, the focus and hiding from screenshots only show on
Windows (W8).

The rendered frames are also saved as PNGs over a made-up desktop (light and dark), in
`$STARLIGHT_OVERLAY_SHOTS` if set, else in the test's temporary folder.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6.QtGui", reason="Qt (the overlay extra) or its libraries missing")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from starlight.overlay import geometry, protocol, scene, winapi
from starlight.overlay.__main__ import over_desktop
from starlight.overlay.app import Overlay, read_lines
from starlight.overlay.geometry import Monitor
from starlight.overlay.scene import Scene

ACCENT = "#4FC8FF"
HELLO = protocol.Hello(ACCENT, 1, "always", False, "de")


def offscreen_env(tmp_path: Path, screens: list[dict[str, Any]] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    platform = "offscreen"
    if screens is not None:
        config = tmp_path / "screens.json"
        config.write_text(json.dumps({"synthesizedGl": False, "screens": screens}))
        platform = f"offscreen:configfile={config}"
    env["QT_QPA_PLATFORM"] = platform
    env["PYTHONUNBUFFERED"] = "1"
    return env


def screen(name: str, x: int, y: int, w: int = 1920, h: int = 1080) -> dict[str, Any]:
    return {
        "name": name,
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "logicalDpi": 96,
        "logicalBaseDpi": 96,
        "dpr": 1,
    }


# --- The child as a subprocess -----------------------------------------------------------


class Child:
    """`python -m starlight.overlay` with pipes; its stdout lines are collected."""

    def __init__(self, env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "starlight.overlay"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.lines: queue.Queue[bytes] = queue.Queue()
        self.stderr = b""
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.put(line)

    def _read_err(self) -> None:
        assert self.proc.stderr is not None
        self.stderr = self.proc.stderr.read()

    def write(self, data: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, msg: protocol.Message) -> None:
        self.write(protocol.encode(msg))

    def expect(self, timeout: float = 10.0) -> protocol.Message:
        line = self.lines.get(timeout=timeout)
        msg = protocol.decode(line)
        assert msg is not None, line
        return msg

    def nothing_more(self, wait: float = 0.3) -> None:
        with pytest.raises(queue.Empty):
            self.lines.get(timeout=wait)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(5)


@pytest.fixture
def child(tmp_path: Path) -> Iterator[Child]:
    c = Child(offscreen_env(tmp_path))
    yield c
    c.close()


def test_hello_is_answered_with_ready(child: Child) -> None:
    child.send(HELLO)
    ready = child.expect()
    assert isinstance(ready, protocol.Ready)
    assert ready.v == 1
    assert len(ready.screens) == 1
    s = ready.screens[0]
    assert (s.x, s.y, s.dpr) == (0, 0, 1.0)
    assert s.w > 0 and s.h > 0
    assert ready.exclude_from_capture is False  # only Windows can hide the windows


def test_two_monitors_side_by_side(tmp_path: Path) -> None:
    c = Child(offscreen_env(tmp_path, [screen("ONE", 0, 0), screen("TWO", 1920, 0)]))
    try:
        c.send(HELLO)
        ready = c.expect()
        assert isinstance(ready, protocol.Ready)
        assert [(s.name, s.x, s.y, s.w, s.h) for s in ready.screens] == [
            ("ONE", 0, 0, 1920, 1080),
            ("TWO", 1920, 0, 1920, 1080),
        ]
        # A point on monitor 2 is fine; one far off every screen is an error, not a crash.
        c.send(protocol.Point("p2", (2500, 500, 100, 40), "Gear", "maybe"))
        c.send(protocol.Point("off", (-9000, -9000, 10, 10), "Nowhere", "sure"))
        err = c.expect()
        assert isinstance(err, protocol.Error)
        assert "off" in err.text
        c.send(protocol.Quit())
        assert c.proc.wait(5) == 0
    finally:
        c.close()


def test_bad_lines_are_skipped(child: Child) -> None:
    child.write(b"not json\n")
    child.write(b'{"type": "no such type"}\n')
    child.write(b'{"type": "point", "id": "x", "box": [1, 2, 0, 5]}\n')  # no size
    child.write(b"[1, 2, 3]\n")
    child.write(b"\xff\xfe\n")
    child.write(b'{"type": "caption", "text": "' + b"a" * (70 * 1024) + b'"}\n')  # over 64 KB
    child.send(HELLO)
    ready = child.expect()
    assert isinstance(ready, protocol.Ready)
    child.nothing_more()
    assert child.proc.poll() is None


def test_point_then_clear(child: Child) -> None:
    child.send(HELLO)
    child.expect()
    child.send(protocol.StateMsg("idle"))
    child.send(protocol.Point("p1", (300, 300, 120, 40), "Settings", "sure", 6.0))
    child.send(protocol.Caption("Hier ist es", "starlight"))
    child.send(protocol.Clear())
    child.send(protocol.StateMsg("speaking"))
    child.send(protocol.Speech((0.1, 0.5, 0.9), 20.0))
    child.send(protocol.Level(0.4))
    child.send(protocol.Config(accent="#3DDC97", reduce_motion=True))
    child.nothing_more(0.5)  # no error for any of them
    child.send(protocol.Quit())
    assert child.proc.wait(5) == 0


def test_ask_times_out_with_no_answer(child: Child) -> None:
    child.send(HELLO)
    child.expect()
    ask = protocol.Ask(
        "a1",
        "Soll ich das anklicken?",
        (protocol.Choice("yes", "Ja"), protocol.Choice("no", "Nein")),
        0.3,
    )
    child.send(ask)
    answer = child.expect(5)
    assert answer == protocol.Answer("a1", None)


def test_cancel_ask_sends_nothing(child: Child) -> None:
    child.send(HELLO)
    child.expect()
    choices = (protocol.Choice("yes", "Ja"), protocol.Choice("no", "Nein"))
    child.send(protocol.Ask("a1", "Q?", choices, 0.4))
    child.send(protocol.CancelAsk("a1"))
    child.nothing_more(0.8)


def test_quit_answers_an_open_ask_and_ends(child: Child) -> None:
    child.send(HELLO)
    child.expect()
    choices = (protocol.Choice("yes", "Ja"), protocol.Choice("no", "Nein"))
    child.send(protocol.Ask("a1", "Q?", choices, 30.0))
    child.send(protocol.Quit())
    assert child.expect(5) == protocol.Answer("a1", None)
    start = time.monotonic()
    assert child.proc.wait(5) == 0
    assert time.monotonic() - start < 1.5


def test_stdin_closed_ends_it_within_two_seconds(child: Child) -> None:
    child.send(HELLO)
    child.expect()
    assert child.proc.stdin is not None
    start = time.monotonic()
    child.proc.stdin.close()
    assert child.proc.wait(5) == 0
    assert time.monotonic() - start < 2.0


def test_demo_runs_and_saves_shots(tmp_path: Path) -> None:
    shots = tmp_path / "shots"
    proc = subprocess.run(
        [sys.executable, "-m", "starlight.overlay", "--demo", "--shots", str(shots)],
        env=offscreen_env(tmp_path, [screen("ONE", 0, 0), screen("TWO", 1920, 0)]),
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    names = sorted(p.name for p in shots.glob("*.png"))
    assert "04-point-monitor1.png" in names
    assert "05-maybe-monitor2.png" in names
    assert "06-ask-card.png" in names
    out = [protocol.decode(line) for line in proc.stdout.splitlines()]
    assert isinstance(out[0], protocol.Ready)
    assert protocol.Answer("demo-ask", None) in out


# --- The reader thread -------------------------------------------------------------------


def test_reader_drops_long_lines_whole_and_says_when_closed() -> None:
    import io

    class Bridge:
        def __init__(self) -> None:
            self.got: list[bytes] = []
            self.closed_ = False
            self.line = self
            self.closed = self

        def emit(self, value: bytes | None = None) -> None:
            if value is None:
                self.closed_ = True
            else:
                self.got.append(value)

    data = b'{"a":1}\n' + b"x" * (protocol.MAX_LINE * 2 + 5) + b"\n\n" + b'{"b":2}\n'
    bridge = Bridge()
    read_lines(io.BytesIO(data), bridge)  # type: ignore[arg-type]
    assert bridge.got == [b'{"a":1}\n', b'{"b":2}\n']
    assert bridge.closed_


# --- In this process ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    assert isinstance(app, QApplication)
    app.setQuitOnLastWindowClosed(False)
    return app


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def overlay(qapp: QApplication) -> Iterator[tuple[Overlay, list[protocol.Message], Clock]]:
    sent: list[protocol.Message] = []
    clock = Clock()
    o = Overlay(sent.append, clock=clock, on_quit=lambda: None)
    o.handle(HELLO)
    yield o, sent, clock
    o.card.close_card()
    for w in o.windows:
        w.close()
        w.deleteLater()
    o.card.deleteLater()
    o.deleteLater()
    qapp.processEvents()


ASK = protocol.Ask(
    "a1",
    "Soll ich „Einstellungen“ anklicken?",
    (protocol.Choice("yes", "Ja"), protocol.Choice("no", "Nein")),
    30.0,
)


def test_a_click_on_the_card_answers(overlay: tuple[Overlay, list[Any], Clock]) -> None:
    o, sent, _ = overlay
    assert isinstance(sent[0], protocol.Ready)
    o.handle(ASK)
    assert o.card.isVisible()
    assert o.card.layout_ is not None
    no = o.card.layout_.buttons[1].center().toPoint()
    QTest.mouseClick(o.card, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, no)
    assert sent[-1] == protocol.Answer("a1", "no")
    assert not o.card.isVisible()
    # A second click does nothing: the question is answered.
    QTest.mouseClick(o.card, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, no)
    assert len([m for m in sent if isinstance(m, protocol.Answer)]) == 1

    o.handle(ASK)
    assert o.card.layout_ is not None
    yes = o.card.layout_.buttons[0].center().toPoint()
    QTest.mouseClick(o.card, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, yes)
    assert sent[-1] == protocol.Answer("a1", "yes")


def test_a_click_beside_the_buttons_answers_nothing(
    overlay: tuple[Overlay, list[Any], Clock],
) -> None:
    o, sent, _ = overlay
    o.handle(ASK)
    QTest.mouseClick(
        o.card, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(4, 4)
    )
    assert not any(isinstance(m, protocol.Answer) for m in sent)
    assert o.card.isVisible()


def test_enter_and_escape_on_the_card(overlay: tuple[Overlay, list[Any], Clock]) -> None:
    o, sent, _ = overlay
    o.handle(ASK)
    QTest.keyClick(o.card, Qt.Key.Key_Escape)
    assert sent[-1] == protocol.Answer("a1", "no")
    o.handle(ASK)
    QTest.keyClick(o.card, Qt.Key.Key_Return)
    assert sent[-1] == protocol.Answer("a1", "yes")


def test_a_new_ask_ends_the_open_one(overlay: tuple[Overlay, list[Any], Clock]) -> None:
    o, sent, _ = overlay
    o.handle(ASK)
    o.handle(protocol.Ask("a2", "Next?", ASK.choices, 30.0))
    assert sent[-1] == protocol.Answer("a1", None)
    assert o.card.ask is not None and o.card.ask.id == "a2"


def test_card_sits_under_the_orb(overlay: tuple[Overlay, list[Any], Clock]) -> None:
    o, _, _ = overlay
    o.handle(ASK)
    orb = geometry.orb_point(o.scene.monitors[0])
    g = o.card.geometry()
    assert g.top() >= orb[1] + geometry.ORB_RADIUS
    assert abs(g.center().x() - orb[0]) <= 1


def test_overlay_windows_never_take_input(overlay: tuple[Overlay, list[Any], Clock]) -> None:
    o, _, _ = overlay
    for w in o.windows:
        flags = w.windowFlags()
        for flag in (
            Qt.WindowType.WindowTransparentForInput,
            Qt.WindowType.WindowDoesNotAcceptFocus,
            Qt.WindowType.WindowStaysOnTopHint,
            Qt.WindowType.FramelessWindowHint,
            Qt.WindowType.Tool,
        ):
            assert flags & flag, flag
        assert w.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert w.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    card = o.card.windowFlags()
    assert not card & Qt.WindowType.WindowTransparentForInput  # the card must be clickable
    assert o.card.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


# --- Rendered frames ---------------------------------------------------------------------


def shots_dir(tmp_path: Path) -> Path:
    folder = Path(os.environ.get("STARLIGHT_OVERLAY_SHOTS") or tmp_path / "shots")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def accent_like(c: QColor) -> bool:
    """Blue-cyan like the accent (#4FC8FF), visible, not white or grey."""
    return c.alpha() > 8 and c.blue() > c.red() + 40 and c.green() > c.red() + 20


def save_both(image: QImage, folder: Path, name: str) -> None:
    over_desktop(image, light=False).save(str(folder / f"{name}-dark.png"))
    over_desktop(image, light=True).save(str(folder / f"{name}-light.png"))


def test_rendered_point_frame(overlay: tuple[Overlay, list[Any], Clock], tmp_path: Path) -> None:
    o, _, clock = overlay
    o.handle(protocol.StateMsg("idle"))
    box = (500, 420, 160, 44)
    o.handle(protocol.Point("p1", box, "Settings", "sure", 6.0))
    o.handle(protocol.Caption("Hier ist es", "starlight"))
    clock.now += 2.0  # the line is out, the frame has stopped pulsing
    o.scene.tick()
    window = o.windows[0]
    image = window.grab().toImage()
    save_both(image, shots_dir(tmp_path), "point")

    line = o.scene.line_for(0)
    assert line is not None
    _, start, end = line
    for t in (0.2, 0.4, 0.6, 0.8, 0.95):
        x = start[0] + (end[0] - start[0]) * t
        y = start[1] + (end[1] - start[1]) * t
        hits = [
            image.pixelColor(round(x) + dx, round(y) + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
        ]
        assert any(accent_like(c) for c in hits), (t, [c.name() for c in hits])
    # The frame: on the box's top edge, 4 px out.
    top = image.pixelColor(box[0] + box[2] // 2, box[1] - 4)
    assert accent_like(top), top.name(QColor.NameFormat.HexArgb)
    # Inside the box nothing is drawn: the user must see the target.
    inside = image.pixelColor(box[0] + box[2] // 2, box[1] + box[3] // 2)
    assert inside.alpha() == 0
    # Far from the orb, the line and the box: nothing at all.
    far = [(20, image.height() - 20), (image.width() - 20, image.height() - 20), (40, 300)]
    for x, y in far:
        assert image.pixelColor(x, y).alpha() == 0, (x, y)


def test_line_draws_out_from_the_orb(overlay: tuple[Overlay, list[Any], Clock]) -> None:
    o, _, clock = overlay
    o.handle(protocol.Point("p1", (500, 600, 100, 40), "X", "sure"))
    line = o.scene.line_for(0)
    assert line is not None
    _, start, end = line
    near_end = (
        round(start[0] + (end[0] - start[0]) * 0.9),
        round(start[1] + (end[1] - start[1]) * 0.9),
    )
    clock.now += 0.05  # early: the line has not reached the target yet
    early = o.windows[0].grab().toImage()
    assert early.pixelColor(*near_end).alpha() == 0
    clock.now += 1.0
    late = o.windows[0].grab().toImage()
    assert late.pixelColor(*near_end).alpha() > 0


def test_reduce_motion_draws_the_line_at_once(overlay: tuple[Overlay, list[Any], Clock]) -> None:
    o, _, clock = overlay
    o.handle(protocol.Config(reduce_motion=True))
    o.handle(protocol.StateMsg("thinking"))
    o.handle(protocol.Point("p1", (500, 600, 100, 40), "X", "sure"))
    line = o.scene.line_for(0)
    assert line is not None
    _, start, end = line
    near_end = (
        round(start[0] + (end[0] - start[0]) * 0.9),
        round(start[1] + (end[1] - start[1]) * 0.9),
    )
    clock.now += 0.01
    image = o.windows[0].grab().toImage()
    assert image.pixelColor(*near_end).alpha() > 0
    # No turning, no pulse: with the point drawn, only the point's end is waited for.
    assert o.scene.next_frame() == pytest.approx(6.0 - 0.01)


def test_point_lifetime_and_clear(overlay: tuple[Overlay, list[Any], Clock]) -> None:
    o, _, clock = overlay
    o.handle(protocol.Point("p1", (500, 600, 100, 40), "X", "sure", 2.0))
    clock.now += 2.0 + scene.FADE + 0.01
    o.scene.tick()
    assert o.scene.point is None
    o.handle(protocol.Point("p2", (500, 600, 100, 40), "X", "sure", 6.0))
    o.handle(protocol.Caption("Hi", "starlight", 4.0))
    o.handle(protocol.Clear())
    clock.now += scene.FADE + 0.01
    o.scene.tick()
    assert o.scene.point is None and o.scene.caption is None


def test_the_mouse_on_the_target_ends_the_point(
    overlay: tuple[Overlay, list[Any], Clock],
) -> None:
    o, _, clock = overlay
    o.handle(protocol.Point("p1", (500, 600, 100, 40), "X", "sure", 6.0))
    o.scene.mouse_at((10.0, 10.0))
    assert o.scene.point is not None and o.scene.point.until == clock.now + 6.0
    o.scene.mouse_at((550.0, 620.0))
    assert o.scene.point is not None and o.scene.point.until == clock.now
    clock.now += scene.FADE + 0.01
    o.scene.tick()
    assert o.scene.point is None


def test_asleep_with_nothing_shown_needs_no_timer(
    overlay: tuple[Overlay, list[Any], Clock],
) -> None:
    o, _, _ = overlay
    o.handle(protocol.StateMsg("asleep"))
    assert o.scene.next_frame() is None
    o.handle(protocol.StateMsg("idle"))
    assert o.scene.next_frame() == scene.FRAME_IDLE
    o.handle(protocol.StateMsg("thinking"))
    assert o.scene.next_frame() == scene.FRAME_FAST


def test_orb_awake_hides_the_sleeping_orb(overlay: tuple[Overlay, list[Any], Clock]) -> None:
    o, _, _ = overlay
    orb = geometry.orb_point(o.scene.monitors[0])
    o.handle(protocol.StateMsg("asleep"))
    shown = o.windows[0].grab().toImage().pixelColor(round(orb[0]), round(orb[1]))
    assert shown.alpha() > 0
    o.handle(protocol.Config(orb="awake"))
    hidden = o.windows[0].grab().toImage().pixelColor(round(orb[0]), round(orb[1]))
    assert hidden.alpha() == 0


def test_orb_states_and_listening_level(
    overlay: tuple[Overlay, list[Any], Clock], tmp_path: Path
) -> None:
    o, _, clock = overlay
    folder = shots_dir(tmp_path)
    orb = geometry.orb_point(o.scene.monitors[0])
    alphas = {}
    for state in protocol.STATES:
        o.handle(protocol.StateMsg(state))
        clock.now += 0.5
        o.scene.tick()
        image = o.windows[0].grab().toImage()
        alphas[state] = image.pixelColor(round(orb[0]) + 12, round(orb[1])).alpha()
        save_both(image.copy(round(orb[0]) - 200, 0, 400, 120), folder, f"orb-{state}")
    assert alphas["asleep"] < alphas["idle"] < alphas["listening"]
    # A loud mic makes the glow bigger (smoothed over a few frames).
    o.handle(protocol.StateMsg("listening"))
    quiet = o.scene.glow
    o.handle(protocol.Level(1.0))
    for _ in range(10):
        clock.now += 0.05
        o.scene.tick()
    assert o.scene.glow > quiet + 0.3


def test_speaking_follows_the_envelope(overlay: tuple[Overlay, list[Any], Clock]) -> None:
    o, _, clock = overlay
    o.handle(protocol.StateMsg("speaking"))
    o.handle(protocol.Speech((0.0,) * 10 + (1.0,) * 10, 20.0))
    clock.now += 0.25  # still silent
    o.scene.tick()
    quiet = o.scene.glow
    for _ in range(8):
        clock.now += 0.05  # into the loud half (from 0.5 s)
        o.scene.tick()
    assert o.scene.glow > quiet + 0.2
    clock.now += 2.0  # after the envelope
    o.scene.tick()
    assert o.scene.speech is None


def test_caption_and_card_shots(overlay: tuple[Overlay, list[Any], Clock], tmp_path: Path) -> None:
    o, _, clock = overlay
    folder = shots_dir(tmp_path)
    o.handle(protocol.StateMsg("idle"))
    long = (
        "Ich habe die Einstellungen gefunden, sie sind oben rechts im Menü unter dem Zahnrad, "
        "gleich neben der Suche."
    )
    o.handle(protocol.Caption(long, "starlight"))
    o.handle(protocol.Point("p", (560, 420, 60, 30), "Gear", "maybe"))
    clock.now += 2.0
    image = o.windows[0].grab().toImage()
    save_both(image, folder, "caption-maybe")
    # The caption never covers the orb, even when it is too wide for its side (800 px).
    layout = o.scene.caption_layout(o.scene.caption_font())
    assert layout is not None
    (x, y, w, _h), lines = layout
    assert len(lines) == 2
    ox, oy = geometry.orb_point(o.scene.monitors[0])
    r = geometry.ORB_RADIUS
    assert x >= ox + r or x + w <= ox - r or y >= oy + r
    o.handle(ASK)
    card = o.card.grab().toImage()
    over_desktop(card, light=False, window=False).save(str(folder / "card.png"))
    assert card.width() == round(scene.CARD_WIDTH)


# --- Two monitors, drawn by the scene alone ----------------------------------------------


def test_target_on_the_other_monitor_starts_there(qapp: QApplication, tmp_path: Path) -> None:
    clock = Clock()
    s = Scene(clock=clock)
    s.monitors = [
        Monitor("ONE", (0, 0, 1920, 1080), (0, 0, 1920, 1080), 1.0),
        Monitor("TWO", (1920, 0, 2880, 1620), (1920, 0, 1920, 1080), 1.5),
    ]
    s.configure(HELLO)
    s.set_state("idle")
    # Physical box on monitor 2 (150 %): 2400..2550 x 900..960 -> logical 2240, 600, 100, 40.
    assert s.show_point(protocol.Point("p", (2400, 900, 150, 60), "X", "sure"))
    assert s.point is not None
    assert s.point.monitor == 1
    assert s.point.box == pytest.approx((2240.0, 600.0, 100.0, 40.0))
    assert s.line_for(0) is None
    line = s.line_for(1)
    assert line is not None
    assert line[0] == (2880.0, 30.0)  # monitor 2's own orb point, never across the gap
    clock.now += 2.0
    images = []
    for i, m in enumerate(s.monitors):
        image = QImage(
            int(m.logical[2]), int(m.logical[3]), QImage.Format.Format_ARGB32_Premultiplied
        )
        image.fill(Qt.GlobalColor.transparent)
        p = QPainter(image)
        p.translate(-m.logical[0], -m.logical[1])
        s.paint(p, i)
        p.end()
        images.append(image)
        save_both(image, shots_dir(tmp_path), f"two-monitors-{i + 1}")
    # Monitor 1: the orb, and no line going down from it.
    assert images[0].pixelColor(960, 30).alpha() > 0
    assert all(images[0].pixelColor(960 + dx, 300).alpha() == 0 for dx in range(-300, 300, 7))
    # Monitor 2: a faint ghost at its own orb point, the line, the frame.
    assert images[1].pixelColor(960, 30).alpha() > 0
    assert accent_like(images[1].pixelColor(320 + 50, 600 - 4))


# --- Pure parts --------------------------------------------------------------------------


def test_caption_lines() -> None:
    assert scene.caption_lines("") == []
    assert scene.caption_lines("  Hier\n ist   es ") == ["Hier ist es"]
    words = " ".join(["Wort"] * 40)  # 199 characters
    lines = scene.caption_lines(words)
    assert len(lines) == 2
    assert all(len(line) <= 60 for line in lines)
    assert lines[1].endswith("…")
    assert scene.caption_lines("x" * 130) == ["x" * 60, "x" * 59 + "…"]


def test_frame_pulses_twice_then_rests() -> None:
    assert scene.frame_scale(0.0, False) == 1.0
    assert scene.frame_scale(0.3, False) == pytest.approx(1.04)
    assert scene.frame_scale(0.9, False) == pytest.approx(1.04)
    assert scene.frame_scale(1.2, False) == 1.0
    assert scene.frame_scale(5.0, False) == 1.0
    assert scene.frame_scale(0.3, True) == 1.0


def test_ease_out() -> None:
    assert scene.ease_out(0) == 0
    assert scene.ease_out(1) == 1
    assert scene.ease_out(2) == 1
    assert scene.ease_out(0.5) == pytest.approx(0.875)


def test_wrap_to_width_keeps_three_lines(qapp: QApplication) -> None:
    from PySide6.QtGui import QFontMetricsF

    metrics = QFontMetricsF(scene.card_font())
    lines = scene.wrap_to_width("Soll ich das anklicken? " * 20, metrics, 288, 3)
    assert len(lines) == 3
    assert lines[-1].endswith("…")
    assert all(metrics.horizontalAdvance(line) <= 288 for line in lines)
    assert scene.wrap_to_width("x" * 200, metrics, 100, 3)[0] != "x" * 200


def test_texts_in_both_languages() -> None:
    assert scene.text("de", "yes") == "Ja"
    assert scene.text("en", "no") == "No"
    assert scene.text("fr", "maybe") == "Maybe here"
    assert set(scene.TEXTS["de"]) == set(scene.TEXTS["en"])


def test_overlay_labels_fall_back_to_yes_no(overlay: tuple[Overlay, list[Any], Clock]) -> None:
    o, _, _ = overlay
    ask = protocol.Ask("a", "Q", (protocol.Choice("y", ""), protocol.Choice("n", "")), 5.0)
    assert o.labels(ask) == ["Ja", "Nein"]


def test_winapi_pure_parts() -> None:
    r = winapi.RECT(-1920, 0, 0, 1080)
    assert winapi.rect_of(r) == (-1920, 0, 1920, 1080)
    found = [((-1920, 0, 1920, 1080), False), ((0, 0, 2560, 1440), True)]
    assert winapi.order_monitors(found) == [(0, 0, 2560, 1440), (-1920, 0, 1920, 1080)]
    assert winapi.WDA_EXCLUDEFROMCAPTURE == 0x11


@pytest.mark.skipif(sys.platform == "win32", reason="the safe answers off Windows")
def test_winapi_is_harmless_off_windows() -> None:
    assert winapi.set_dpi_awareness() is False
    assert winapi.exclude_from_capture(1234) is False
    assert winapi.monitor_rects() == []
    assert winapi.animations_enabled() is True
