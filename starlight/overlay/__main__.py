"""``python -m starlight.overlay``: the overlay process (``docs/plan-overlay.md`` 4.3).

The core starts it and talks to it in JSON lines on stdin and stdout. ``--demo`` shows each
state, a point and a Yes/No card one after another, for looking at it by hand; with
``--shots DIR`` it also saves a PNG of every monitor (and the card) at each step, drawn
over a made-up desktop.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from . import protocol

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

STEP_MS = 2600  # each demo step
SHOT_MS = 1700  # into a step: the line is out and the frame has stopped pulsing


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m starlight.overlay", description=__doc__)
    p.add_argument("--demo", action="store_true", help="show every part once, then end")
    p.add_argument("--shots", metavar="DIR", help="with --demo: save a PNG at each step")
    p.add_argument("--language", choices=protocol.LANGUAGES, default="de")
    p.add_argument("--accent", default="#4FC8FF")
    p.add_argument("--reduce-motion", action="store_true")
    return p


class Demo:
    """Plays a fixed list of messages into the overlay, one step every 2.6 s."""

    def __init__(self, overlay: object, args: argparse.Namespace) -> None:
        from PySide6.QtCore import QTimer

        from .app import Overlay
        from .scene import text

        assert isinstance(overlay, Overlay)
        self.overlay = overlay
        self.args = args
        self.shots = Path(args.shots) if args.shots else None
        lang = args.language
        self._level = QTimer()
        self._level.setInterval(66)
        self._level.timeout.connect(self._wobble)
        self._t = 0
        m = overlay.scene.monitors
        first = m[0].physical
        last = m[-1].physical
        sure = (
            round(first[0] + first[2] * 0.62),
            round(first[1] + first[3] * 0.46),
            180,
            44,
        )
        maybe = (round(last[0] + last[2] * 0.22), round(last[1] + last[3] * 0.68), 120, 36)
        envelope = tuple(
            round(abs(math.sin(i / 3.0)) * (0.4 + 0.5 * abs(math.sin(i / 11.0))), 3)
            for i in range(60)
        )
        yes, no = text(lang, "yes"), text(lang, "no")
        self.steps: list[tuple[str, list[protocol.Message], Callable[[], None] | None]] = [
            (
                "asleep",
                [
                    protocol.Hello(args.accent, 1, "always", args.reduce_motion, lang),
                    protocol.StateMsg("asleep"),
                ],
                None,
            ),
            ("idle", [protocol.StateMsg("idle")], None),
            (
                "listening",
                [
                    protocol.StateMsg("listening"),
                    protocol.Caption(text(lang, "demo_caption"), "you", 2.4),
                ],
                self._level.start,
            ),
            ("thinking", [protocol.StateMsg("thinking")], self._level.stop),
            (
                "point",
                [
                    protocol.StateMsg("speaking"),
                    protocol.Speech(envelope, 20.0),
                    protocol.Point("demo-1", sure, "Settings", "sure", 2.5),
                    protocol.Caption(text(lang, "here"), "starlight", 2.4),
                ],
                None,
            ),
            (
                "maybe",
                [
                    protocol.StateMsg("idle"),
                    protocol.Point("demo-2", maybe, "Gear", "maybe", 2.5),
                    protocol.Caption(text(lang, "maybe"), "starlight", 2.4),
                ],
                None,
            ),
            (
                "ask",
                [
                    protocol.Ask(
                        "demo-ask",
                        text(lang, "demo_ask"),
                        (protocol.Choice("yes", yes), protocol.Choice("no", no)),
                        2.4,
                    ),
                ],
                None,
            ),
            ("end", [protocol.StateMsg("asleep"), protocol.Quit()], None),
        ]

    def start(self) -> Demo:
        from PySide6.QtCore import QTimer

        for i, (name, messages, extra) in enumerate(self.steps):
            QTimer.singleShot(i * STEP_MS, lambda m=messages, e=extra: self._play(m, e))
            if self.shots is not None and name != "end":
                QTimer.singleShot(i * STEP_MS + SHOT_MS, lambda n=name, i=i: self._shoot(i, n))
        return self

    def _play(self, messages: list[protocol.Message], extra: Callable[[], None] | None) -> None:
        for msg in messages:
            self.overlay.handle(msg)
        if extra is not None:
            extra()

    def _wobble(self) -> None:
        self._t += 1
        level = 0.5 + 0.45 * math.sin(self._t / 2.2) * abs(math.sin(self._t / 7.0))
        self.overlay.handle(protocol.Level(min(1.0, max(0.0, level))))

    def _shoot(self, index: int, name: str) -> None:
        assert self.shots is not None
        self.shots.mkdir(parents=True, exist_ok=True)
        for w in self.overlay.windows:
            image = over_desktop(w.grab().toImage(), light=w.index % 2 == 1)
            image.save(str(self.shots / f"{index:02d}-{name}-monitor{w.index + 1}.png"))
        card = self.overlay.card
        if card.isVisible():
            image = over_desktop(card.grab().toImage(), light=False, window=False)
            image.save(str(self.shots / f"{index:02d}-{name}-card.png"))


def over_desktop(layer: QImage, light: bool, window: bool = True) -> QImage:
    """The overlay's transparent layer drawn over a made-up desktop: a dark wallpaper with
    a light window on it (or the other way round), so the PNGs show how it reads on both."""
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter

    out = QImage(layer.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.setDevicePixelRatio(layer.devicePixelRatio())
    w = layer.width() / layer.devicePixelRatio()
    h = layer.height() / layer.devicePixelRatio()
    p = QPainter(out)
    wall = QLinearGradient(QPointF(0, 0), QPointF(w, h))
    wall.setColorAt(0.0, QColor("#E9EDF2") if light else QColor("#0E1A2B"))
    wall.setColorAt(1.0, QColor("#C9D2DC") if light else QColor("#24364D"))
    p.fillRect(QRectF(0, 0, w, h), wall)
    if window:
        rect = QRectF(w * 0.30, h * 0.30, w * 0.62, h * 0.60)
        p.fillRect(rect, QColor("#1B1F24") if light else QColor("#F6F7F9"))
        title = QRectF(rect.x(), rect.y(), rect.width(), 34)
        p.fillRect(title, QColor("#2A3038") if light else QColor("#E1E5EA"))
    p.drawImage(QPointF(0, 0), layer)
    p.end()
    return out


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    from .app import run

    if args.demo:
        return run(lambda overlay: Demo(overlay, args).start())
    return run()


if __name__ == "__main__":
    sys.exit(main())
