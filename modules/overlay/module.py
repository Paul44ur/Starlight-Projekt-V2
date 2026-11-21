"""The overlay module (``docs/plan-overlay.md`` 4.4).

It starts the overlay process (``python -m starlight.overlay``, the optional ``overlay``
extra) with the core's own Python, talks to it in JSON lines over the child's stdin and
stdout (``starlight.overlay.protocol``) and restarts it when it dies. It forwards the voice
module's events to it, and its ``point_at`` tool finds a target through
``desktop.find_target`` and draws a line to it.

Only the primary Starlight process runs the overlay (``ctx.background``). This module never
imports Qt: everything that draws is in the child.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import subprocess
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, ValidationError

from starlight.core import Event, Module, ModuleContext, ToolError, tool
from starlight.core.fsutil import write_atomic
from starlight.overlay import protocol

QUEUE_SIZE = 200  # forwarded events waiting for the child; more are dropped
LOG_LIMIT = 1_000_000  # bytes: overlay.log (the child's own output) starts afresh above this


class Config(BaseModel):
    """Settings from config/modules/overlay.yaml."""

    enabled: bool = False
    orb: Literal["always", "awake"] = "always"
    orb_monitor: int = Field(default=1, ge=1, le=protocol.MAX_ORB_MONITOR)
    accent: str = Field(default="#4FC8FF", pattern=r"^#[0-9A-Fa-f]{6}$")
    reduce_motion: bool = False
    point_seconds: float = Field(default=6.0, gt=0, le=60)


def overlay_installed() -> bool:
    """Whether the ``overlay`` extra (PySide6) is installed."""
    try:
        return importlib.util.find_spec("PySide6") is not None
    except (ImportError, ValueError):
        return False


def language_of(profile: dict[str, str]) -> protocol.Language:
    """The overlay's language from the profile's "Language" line: German unless it says
    English."""
    said = profile.get("language", "").strip().lower()
    return "en" if said.startswith(("en", "engl")) else "de"


# The settings the Settings page changes (docs/plan-overlay.md 7), and how the file is
# written then: every line keeps its comment.
SETTINGS = ("enabled", "orb", "orb_monitor", "accent", "reduce_motion")
CONFIG_FILE = """\
# The overlay (modules/overlay): a see-through layer over the screen with Starlight's orb,
# which points at things ("where is the settings menu?"). It never takes the mouse or the
# keyboard. Needs the optional "overlay" extra (PySide6). Changed on the Settings page.
enabled: {enabled}  # on or off (Settings, Overlay)
orb: {orb}  # always: a dim orb at the top edge; awake: only while it listens or talks
orb_monitor: {orb_monitor}  # which monitor (1, 2 ...) holds the orb
accent: {accent}  # the overlay's colour
reduce_motion: {reduce_motion}  # also on when Windows' animation effects are off
point_seconds: {point_seconds}  # how long a point stays
"""
TEST_SECONDS = 3.0  # how long the Settings page's test point stays
HEADING = {"de": "Einstellungen", "en": "Settings"}  # the test point's label

CAPTIONS: dict[protocol.Language, dict[protocol.Confidence, str]] = {
    "de": {"sure": "Hier ist es", "maybe": "Vielleicht hier"},
    "en": {"sure": "Here you go", "maybe": "Maybe here"},
}


class OverlayModule(Module):
    Config = Config
    subscriptions: ClassVar[list[str]] = [
        "voice.state",
        "voice.level",
        "voice.speech",
        "voice.caption",
        "overlay.ask",
        "overlay.cancel_ask",
    ]

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        # How the child is started and restarted (instance values, so tests can shorten them).
        self.command: list[str] = [sys.executable, "-m", "starlight.overlay"]
        self.ready_timeout = 10.0  # seconds to wait for "ready" after "hello"
        self.quit_timeout = 2.0  # seconds to wait after "quit" before killing it
        self.backoff_first = 2.0  # seconds before the first restart, doubled each time
        self.backoff_max = 60.0
        self.give_up_after = 5  # failures ...
        self.give_up_window = 600.0  # ... in this many seconds
        self.capture_pause = 0.15  # after "clear", when the overlay shows in screenshots
        self.level_rate = 15  # "level" messages a second, at most

        self._proc: asyncio.subprocess.Process | None = None
        self._ready = asyncio.Event()
        self._stopping = False
        self._screens: list[dict[str, Any]] = []
        self._exclude_from_capture = False
        self._failures: deque[float] = deque()
        self._restarts = 0
        self._gave_up = False
        self.last_error = ""
        self.last_target: dict[str, Any] | None = None
        self._open_asks: set[str] = set()
        self._levels: deque[float] = deque()
        self._write_lock = asyncio.Lock()
        self._queue: asyncio.Queue[protocol.Message] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._task: asyncio.Task[Any] | None = None

        self._forwarder: asyncio.Task[Any] | None = None

        ctx.kill_switch.add_cleanup(self._on_kill, owner=ctx.name)
        if self.config.enabled:
            self._start()

    def _start(self) -> None:
        """Start watching the child (it starts it at once), unless it already runs."""
        if self._task is not None and not self._task.done():
            return
        if not overlay_installed():
            self.last_error = "the overlay extra (PySide6) is not installed"
            self.ctx.logger.warning(
                "overlay is on but not installed", hint="Starlight.exe installs it"
            )
            return
        self._stopping = False
        self._failures.clear()
        self._gave_up = False
        self.last_error = ""
        self._task = self.ctx.background(self._supervise, "overlay")
        if self._forwarder is None or self._forwarder.done():
            self._forwarder = self.ctx.background(self._forward, "overlay events")

    async def shutdown(self) -> None:
        await self._stop()

    async def _stop(self) -> None:
        """Say "quit" to the child (kill it after 2 s) and stop watching it."""
        self._stopping = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            await self._send(protocol.Quit())
            try:
                await asyncio.wait_for(proc.wait(), self.quit_timeout)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self._task = None

    # -- the child process ----------------------------------------------------------------

    @property
    def running(self) -> bool:
        """The child is alive and answered "hello"."""
        proc = self._proc
        return proc is not None and proc.returncode is None and self._ready.is_set()

    async def _supervise(self) -> None:
        """Run the child; when it dies, wait 2, 4, 8 ... (at most 60) seconds and start it
        again. After 5 failures in 10 minutes, stop trying (one warning)."""
        while not self._stopping:
            reason = await self._run_child()
            if self._stopping:
                return
            now = time.monotonic()
            self._failures.append(now)
            while self._failures and now - self._failures[0] > self.give_up_window:
                self._failures.popleft()
            self.last_error = reason
            if len(self._failures) >= self.give_up_after:
                self._gave_up = True
                self.ctx.logger.warning(
                    "overlay stopped after repeated failures",
                    failures=len(self._failures),
                    last=reason,
                )
                return
            delay = min(self.backoff_first * 2 ** (len(self._failures) - 1), self.backoff_max)
            self.ctx.logger.info("overlay ended, restarting", reason=reason, wait=delay)
            await asyncio.sleep(delay)
            self._restarts += 1

    async def _run_child(self) -> str:
        """Start the child, say hello, read what it says until it ends. Returns why it
        ended."""
        self._ready.clear()
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW
        try:
            with self._log_file() as errors:
                proc = await asyncio.create_subprocess_exec(
                    *self.command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=errors,
                    limit=2 * protocol.MAX_LINE,
                    creationflags=flags,
                )
        except OSError as exc:
            return f"could not start: {exc}"
        self._proc = proc
        try:
            await self._send(self._hello(), before_ready=True)
            reader = asyncio.create_task(self._read(proc))
            ready = asyncio.create_task(self._ready.wait())
            await asyncio.wait(
                {reader, ready}, timeout=self.ready_timeout, return_when="FIRST_COMPLETED"
            )
            if not ready.done():
                ready.cancel()
                ended = reader.done()  # (stdout closed: the child ended)
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reader
                if not ended:
                    return f"no answer within {self.ready_timeout:g} s"
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                return f"ended before it was ready (exit code {await proc.wait()})"
            self.ctx.logger.info("overlay ready", screens=len(self._screens))
            await reader
            code = await proc.wait()
            return f"ended (exit code {code})"
        finally:
            self._ready.clear()
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()

    def _log_file(self) -> Any:
        """The child's own output (Qt's warnings, a traceback) goes to overlay.log in the
        module's data folder, kept across restarts until it grows over 1 MB."""
        folder: Path = self.ctx.data_dir
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "overlay.log"
        mode = "wb" if path.exists() and path.stat().st_size > LOG_LIMIT else "ab"
        return path.open(mode)

    def _hello(self) -> protocol.Hello:
        return protocol.Hello(
            accent=self.config.accent,
            orb_monitor=self.config.orb_monitor,
            orb=self.config.orb,
            reduce_motion=self.config.reduce_motion,
            language=language_of(self.ctx.profile()),
        )

    # -- the Settings page (W7) ------------------------------------------------------------

    async def change_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Check and save settings from the Settings page (config/modules/overlay.yaml).
        A running overlay gets a "config" message (no restart); on and off start or stop
        it. Returns what changed."""
        unknown = sorted(set(changes) - set(SETTINGS))
        if unknown:
            raise ToolError(f"not an overlay setting: {', '.join(unknown)}")
        try:
            new = Config.model_validate({**self.config.model_dump(), **changes})
        except ValidationError as exc:
            problems = "; ".join(f"{e['loc'][0]}: {e['msg']}" for e in exc.errors())
            raise ToolError(problems) from exc
        old = self.config
        changed = {k: getattr(new, k) for k in SETTINGS if getattr(new, k) != getattr(old, k)}
        self.config = new
        self.ctx.config = new
        self._save_config()
        if "enabled" in changed:
            if new.enabled:
                self._start()  # (its "hello" carries the other settings)
            else:
                await self._stop()
            return changed
        shown = {k: v for k, v in changed.items() if k != "enabled"}
        if shown and self.running:
            await self._send(protocol.Config(**shown))
        return changed

    def _save_config(self) -> None:
        values = {k: json.dumps(v) for k, v in self.config.model_dump().items()}
        values["point_seconds"] = f"{self.config.point_seconds:g}"
        write_atomic(self.ctx.paths.module_configs / "overlay.yaml", CONFIG_FILE.format(**values))

    async def show_test_point(self, box: tuple[int, int, int, int]) -> bool:
        """The Settings page's "Show a test point": a line to its own heading for 3 s.
        False if the overlay is not running."""
        if self.ctx.kill_switch.engaged:
            raise ToolError("Starlight is stopped (kill switch)")
        if not self.running:
            return False
        language = language_of(self.ctx.profile())
        label = HEADING[language]
        try:
            point = protocol.Point(
                id=uuid.uuid4().hex[:12],
                box=box,
                label=label,
                confidence="sure",
                seconds=TEST_SECONDS,
            )
            protocol.encode(point)
        except (TypeError, ValueError) as exc:
            raise ToolError(f"not a place on the screen: {exc}") from exc
        caption = protocol.Caption(
            text=f"{CAPTIONS[language]['sure']}: {label}", who="starlight", seconds=TEST_SECONDS
        )
        return await self._send(point) and await self._send(caption)

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        stdout = proc.stdout
        assert stdout is not None
        while True:
            try:
                line = await stdout.readline()
            except ValueError:  # a line over the limit: skipped
                self.ctx.logger.warning("overlay line too long, skipped")
                continue
            if not line:
                return
            message = protocol.decode(line)
            if message is not None:
                await self._received(message)

    async def _received(self, message: protocol.Message) -> None:
        match message:
            case protocol.Ready():
                self._screens = [
                    {"name": s.name, "x": s.x, "y": s.y, "w": s.w, "h": s.h, "dpr": s.dpr}
                    for s in message.screens
                ]
                self._exclude_from_capture = message.exclude_from_capture
                self._ready.set()
            case protocol.Answer():
                self._open_asks.discard(message.id)
                await self.ctx.publish(
                    "overlay.answer", {"id": message.id, "choice": message.choice}
                )
            case protocol.Error():
                self.last_error = message.text[:300]
                self.ctx.logger.info("overlay reported an error", text=message.text[:300])
            case _:
                self.ctx.logger.info("overlay sent an unexpected message", type=message.type)

    async def _send(self, message: protocol.Message, before_ready: bool = False) -> bool:
        """Write one message to the child. False if it is not running (or not ready yet,
        unless ``before_ready``) or the pipe is gone."""
        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            return False
        if not (before_ready or self._ready.is_set()):
            return False
        try:
            line = protocol.encode(message)
        except ValueError as exc:
            self.ctx.logger.info("overlay message not sent", reason=str(exc)[:200])
            return False
        stdin = proc.stdin
        assert stdin is not None
        async with self._write_lock:
            try:
                stdin.write(line)
                await asyncio.wait_for(stdin.drain(), 2.0)
            except (ConnectionError, RuntimeError, OSError):
                return False
            except TimeoutError:  # the child stopped reading: start it afresh
                self.ctx.logger.warning("overlay stopped reading, restarting it")
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                return False
        return True

    # -- kill switch ----------------------------------------------------------------------

    async def _on_kill(self) -> None:
        """Take everything down: the line, the frame, the caption, an open question
        (answered "no": no choice) and the orb goes to sleep."""
        await self._send(protocol.Clear())
        for ask in sorted(self._open_asks):
            await self._send(protocol.CancelAsk(id=ask))
            await self.ctx.publish("overlay.answer", {"id": ask, "choice": None})
        self._open_asks.clear()
        await self._send(protocol.StateMsg(state="asleep"))

    # -- events from the voice module (and W6's questions) ---------------------------------

    async def on_event(self, event: Event) -> None:
        """Only puts the message in the queue, so a slow overlay never holds up the voice
        module's loop. ``_forward`` writes the queue to the child in order."""
        if self.ctx.kill_switch.engaged or not self.running:
            return
        message = self._message_for(event)
        if message is None:
            return
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            self.ctx.logger.info("overlay busy, message dropped", type=message.type)

    async def _forward(self) -> None:
        while True:
            message = await self._queue.get()
            if self.ctx.kill_switch.engaged:
                continue
            if isinstance(message, protocol.Ask):
                if await self._send(message):
                    self._open_asks.add(message.id)
                continue
            if isinstance(message, protocol.CancelAsk):
                self._open_asks.discard(message.id)
            await self._send(message)

    def _message_for(self, event: Event) -> protocol.Message | None:
        data = event.data
        try:
            match event.name:
                case "voice.state":
                    return protocol.StateMsg(state=data["state"])
                case "voice.level":
                    if not self._level_allowed():
                        return None
                    return protocol.Level(level=float(data["level"]))
                case "voice.speech":
                    return protocol.Speech(
                        envelope=tuple(float(v) for v in data["envelope"]),
                        rate=float(data.get("rate", 20.0)),
                    )
                case "voice.caption":
                    return protocol.Caption(text=str(data["text"]), who=data["who"])
                case "overlay.ask":
                    return protocol.Ask(
                        id=str(data["id"]),
                        question=str(data["question"]),
                        choices=tuple(
                            protocol.Choice(id=str(c["id"]), label=str(c["label"]))
                            for c in data["choices"]
                        ),
                        timeout=float(data["timeout"]),
                    )
                case "overlay.cancel_ask":
                    return protocol.CancelAsk(id=str(data["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            self.ctx.logger.info("overlay event skipped", name=event.name, reason=str(exc)[:200])
        return None

    def _level_allowed(self) -> bool:
        """At most ``level_rate`` mic levels in any second; the rest are dropped."""
        now = time.monotonic()
        while self._levels and now - self._levels[0] >= 1.0:
            self._levels.popleft()
        if len(self._levels) >= self.level_rate:
            return False
        self._levels.append(now)
        return True

    # -- tools ----------------------------------------------------------------------------

    @tool
    async def point_at(self, target: str, window: str = "") -> dict[str, Any]:
        """Show where something is on the screen: draws a line from the top of the screen to
        it and frames it. Use it whenever the user asks where something is on their screen."""
        if self.ctx.kill_switch.engaged:
            raise ToolError("Starlight is stopped (kill switch)")
        if self.running and not self._exclude_from_capture:
            # The overlay would show in the screenshot find_target takes.
            await self._send(protocol.Clear())
            await asyncio.sleep(self.capture_pause)
        result = await self.ctx.call("desktop.find_target", {"target": target, "window": window})
        if not result.ok:
            raise ToolError(result.error or "finding the target failed")
        hit = result.output
        if not isinstance(hit, dict) or not hit.get("found"):
            return hit if isinstance(hit, dict) else {"found": False, "target": target}
        if self.ctx.kill_switch.engaged:
            raise ToolError("Starlight is stopped (kill switch)")
        self.last_target = {**hit, "at": time.time()}
        shown = await self._show(hit)
        return {**hit, "shown": shown}

    async def _show(self, hit: dict[str, Any]) -> bool:
        if not self.running:
            return False
        confidence: protocol.Confidence = "sure" if hit.get("confidence") == "sure" else "maybe"
        label = str(hit.get("label", ""))
        box = hit.get("box")
        try:
            point = protocol.Point(
                id=uuid.uuid4().hex[:12],
                box=tuple(int(v) for v in box),  # type: ignore[arg-type,union-attr]
                label=label,
                confidence=confidence,
                seconds=self.config.point_seconds,
            )
            protocol.encode(point)  # (a bad box from find_target: not shown)
        except (TypeError, ValueError) as exc:
            self.ctx.logger.info("target not shown", reason=str(exc)[:200])
            return False
        words = CAPTIONS[language_of(self.ctx.profile())][confidence]
        caption = protocol.Caption(
            text=f"{words}: {label}" if label else words,
            who="starlight",
            seconds=self.config.point_seconds,
        )
        return await self._send(point) and await self._send(caption)

    @tool
    async def clear_pointer(self) -> dict[str, Any]:
        """Remove the line and the frame that point_at drew."""
        return {"cleared": await self._send(protocol.Clear())}

    @tool
    def overlay_status(self) -> dict[str, Any]:
        """Whether the overlay is on and running, its screens and its last error."""
        return {
            "enabled": self.config.enabled,
            "installed": overlay_installed(),
            "running": self.running,
            "screens": list(self._screens),
            "hidden_from_screenshots": self._exclude_from_capture,
            "restarts": self._restarts,
            "gave_up": self._gave_up,
            "last_error": self.last_error,
        }
