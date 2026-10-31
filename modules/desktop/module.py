"""Read the screen and emulate mouse and keyboard, always under the kill switch.

* screen reading: screenshots (mss), text via OCR (Tesseract) and a local vision model
  (e.g. Qwen2.5-VL via Ollama) to describe the screen and locate buttons
* input: mouse and keyboard (pynput); on Windows, UI Automation clicks controls by name
* act and check: after each action a new screenshot confirms that something changed,
  and ``expect_text`` confirms the right thing did
* safety: moving the mouse into a screen corner triggers the kill switch (failsafe), the
  kill hotkey works at all times (typing stops between two characters), keys and buttons
  are released on a kill, and a red banner shows while Starlight controls the input
* screenshots stay in memory, are dropped after use and only go to a model on this machine
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import ipaddress
import json
import re
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import numpy as np
from pydantic import BaseModel, Field

from starlight.core import Message, Module, ModuleContext, Permission, ToolError, tool
from starlight.core.config import ModelConfig
from starlight.core.model import ModelProvider, create_provider
from starlight.core.modules import keep_running

from .backends import (
    Box,
    DesktopUnavailable,
    Input,
    MssScreen,
    Ocr,
    PynputInput,
    Screen,
    TesseractOcr,
    UiAutomation,
    boxes_to_text,
    find_phrase,
    make_dpi_aware,
)
from .indicator import Indicator

if TYPE_CHECKING:  # Pillow is in the optional 'desktop' extra: the module must load without
    from PIL import Image

DEFAULT_APPS = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "explorer": ["explorer.exe"],
    "paint": ["mspaint.exe"],
}
LOCATE_PROMPT = (
    "Find this on the screenshot: {target}. Answer with JSON only: "
    '{{"x": <pixel x>, "y": <pixel y>}} for its centre in this {w}x{h} image, '
    'or {{"x": null, "y": null}} if it is not visible.'
)


class Config(BaseModel):
    vision: ModelConfig | None = None  # e.g. {provider: ollama, options: {model: qwen2.5vl:7b}}
    vision_max_side: int = 1280  # screenshots are scaled down before the vision model
    # Screenshots never leave this computer. Set true to allow a vision model on another
    # machine in your home network (10.x, 172.16-31.x, 192.168.x, IPv6 fd00::/8).
    vision_allow_lan: bool = False
    ocr_languages: str = "eng"  # e.g. "eng+deu" (install the German Tesseract data)
    ocr_command: str = ""  # path to tesseract.exe if not on PATH
    apps: dict[str, list[str]] = Field(default_factory=lambda: dict(DEFAULT_APPS))
    typing_interval: float = 0.03  # seconds between characters
    settle: float = 0.4  # wait after an action before checking the screen
    failsafe: bool = True  # mouse in a screen corner = kill switch
    failsafe_margin: int = 2
    indicator: bool = True
    hotkey_label: str = "Ctrl+Alt+J"


def start_app(command: list[str]) -> subprocess.Popen[bytes]:
    """Start an allow-listed app so it outlives Starlight. Started from Starlight.exe, the
    core runs in a Windows job that ends with the launcher; the app breaks away from it."""
    if sys.platform == "win32":
        breakaway = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
        try:
            return subprocess.Popen(command, creationflags=breakaway)
        except OSError:  # the job does not allow breaking away
            pass
    return subprocess.Popen(command)


def is_local_url(url: str, allow_lan: bool = False) -> bool:
    """True if ``url`` points at this machine (or, with allow_lan, your home network)."""
    # An address written without "http://" ("192.168.1.5:11434") is read as host:port:
    # urlsplit would find no host in it at all. No host (an empty URL): not local.
    host = urlsplit(url if "//" in url else f"//{url}").hostname or ""
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    lan = [
        ipaddress.ip_network(n)
        for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fd00::/8")
    ]
    return allow_lan and any(address in net for net in lan)


# How "did the action change the screen?" is measured: both screenshots shrunk to a small
# grey picture (cheap, and it blurs away noise), a pixel counts as changed when its grey
# value moved by more than PIXEL_DELTA of 255, and the screen as changed when more than
# CHANGED_SHARE of the pixels did (0.0005 of 320x180: about 29 pixels).
THUMBNAIL = (320, 180)
PIXEL_DELTA = 16
CHANGED_SHARE = 0.0005


def change_ratio(before: Image.Image, after: Image.Image) -> float:
    """Share of pixels that changed noticeably between two screenshots."""
    # int16: the uint8 grey values would wrap around when subtracted
    a = np.asarray(before.convert("L").resize(THUMBNAIL), dtype=np.int16)
    b = np.asarray(after.convert("L").resize(THUMBNAIL), dtype=np.int16)
    return float((np.abs(a - b) > PIXEL_DELTA).mean())


class DesktopModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        make_dpi_aware()
        self._screen: Screen | None = None
        self._input: Input | None = None
        self._ocr: Ocr | None = None
        self._uia: UiAutomation | None = None
        self._vision: ModelProvider | None = None
        self.indicator = Indicator(self.config.indicator, self.config.hotkey_label)
        self._bounds: tuple[float, tuple[int, int, int, int]] | None = None  # (when, bounds)
        self._acting = 0
        self._lock = threading.Lock()
        self._failsafe_stop = threading.Event()
        self._failsafe_thread: threading.Thread | None = None
        ctx.kill_switch.add_cleanup(self._on_kill, owner=ctx.name)

    async def shutdown(self) -> None:
        self._on_kill()
        self.indicator.close()
        close = getattr(self._vision, "aclose", None)
        if close is not None and self._vision is not self.ctx.model:
            await close()

    def _on_kill(self) -> None:
        """Kill switch: let go of keys and buttons and take the banner down."""
        self._failsafe_stop.set()
        if self._input is not None:
            with contextlib.suppress(Exception):
                self._input.release_all()
        self.indicator.hide(immediately=True)

    # -- lazily created backends (the module loads even without a desktop) -------------

    @property
    def screen(self) -> Screen:
        if self._screen is None:
            self._screen = MssScreen()
        return self._screen

    @screen.setter
    def screen(self, value: Screen) -> None:
        self._screen = value
        self._bounds = None

    @property
    def input(self) -> Input:
        if self._input is None:
            self._input = PynputInput()
        return self._input

    @input.setter
    def input(self, value: Input) -> None:
        self._input = value

    @property
    def ocr(self) -> Ocr:
        if self._ocr is None:
            self._ocr = TesseractOcr(self.config.ocr_languages, self.config.ocr_command)
        return self._ocr

    @ocr.setter
    def ocr(self, value: Ocr) -> None:
        self._ocr = value

    @property
    def uia(self) -> UiAutomation:
        if self._uia is None:
            self._uia = UiAutomation()
        return self._uia

    def vision(self) -> ModelProvider:
        if self._vision is None:
            if self.config.vision is not None:
                base_url = str(self.config.vision.options.get("base_url", "http://127.0.0.1"))
                if not is_local_url(base_url, self.config.vision_allow_lan):
                    raise DesktopUnavailable("the vision model must run on this machine")
                self._vision = create_provider(self.config.vision)
            elif self.ctx.model is not None:
                base_url = str(getattr(self.ctx.model, "base_url", "http://127.0.0.1"))
                if not is_local_url(base_url, self.config.vision_allow_lan):
                    raise DesktopUnavailable("the vision model must run on this machine")
                self._vision = self.ctx.model
            else:
                raise DesktopUnavailable("no vision model configured")
        return self._vision

    # -- safety ------------------------------------------------------------------------

    def _screen_bounds(self) -> tuple[int, int, int, int]:
        """The screen size for the failsafe, looked up at most once a second: it is polled
        20 times a second and every lookup opens a screen-capture session."""
        cached = self._bounds
        now = time.monotonic()
        if cached is None or now - cached[0] > 1.0:
            cached = self._bounds = (now, self.screen.bounds())
        return cached[1]

    def _in_corner(self) -> bool:
        x, y = self.input.position()
        left, top, width, height = self._screen_bounds()
        m = self.config.failsafe_margin
        at_x = x <= left + m or x >= left + width - 1 - m
        at_y = y <= top + m or y >= top + height - 1 - m
        return at_x and at_y

    def _check(self) -> None:
        """Before every single input event: kill switch and failsafe corner."""
        self.ctx.kill_switch.check()
        if self.config.failsafe and self._in_corner():
            self.ctx.kill_switch.trigger(
                "failsafe: mouse moved into a screen corner", source="failsafe"
            )
            self.ctx.kill_switch.check()

    def _failsafe_loop(self) -> None:
        reported = False
        while not self._failsafe_stop.wait(0.05):
            try:
                if self._in_corner():
                    self.ctx.kill_switch.trigger(
                        "failsafe: mouse moved into a screen corner", source="failsafe"
                    )
                    return
            except Exception as exc:  # e.g. a display change: keep watching, say so once
                if not reported:
                    reported = True
                    self.ctx.logger.warning("failsafe check failed", error=str(exc))

    @contextlib.contextmanager
    def _controlling(self) -> Iterator[None]:
        """Banner and failsafe watcher while the AI drives the input."""
        with self._lock:
            self._acting += 1
            if self._acting == 1:
                self.indicator.show()
                if self.config.failsafe:
                    self._failsafe_stop.clear()
                    self._failsafe_thread = threading.Thread(
                        target=self._failsafe_loop, name="failsafe", daemon=True
                    )
                    self._failsafe_thread.start()
        try:
            yield
        finally:
            with self._lock:
                self._acting -= 1
                if self._acting == 0:
                    self._failsafe_stop.set()
                    self.indicator.hide()

    def _act(self, action: Any, expect_text: str = "") -> dict[str, Any]:
        """Run an input action, then take a new screenshot to confirm it worked."""
        self._check()
        before = self.screen.capture()
        with self._controlling():
            action()
        time.sleep(self.config.settle)
        after = self.screen.capture()
        ratio = change_ratio(before, after)
        result: dict[str, Any] = {
            "done": True,
            "screen_changed": ratio > CHANGED_SHARE,
            "change": round(ratio, 4),
        }
        if expect_text:
            try:
                text = boxes_to_text(self.ocr.boxes(after))
            except DesktopUnavailable as exc:  # the action itself worked: do not report it
                result["verified"] = None  # as failed, or the AI would do it again
                result["note"] = f"could not check the text: {exc}"
            else:
                result["verified"] = expect_text.lower() in text.lower()
        del before, after  # screenshots are not kept
        return result

    # -- reading the screen ------------------------------------------------------------

    @tool
    def screen_info(self) -> dict[str, Any]:
        """Screen size, each monitor (numbered from 1, with its position) and where the
        mouse pointer is."""
        left, top, width, height = self.screen.bounds()
        x, y = self.input.position()
        monitors = [
            {"monitor": number, "left": m[0], "top": m[1], "width": m[2], "height": m[3]}
            for number, m in enumerate(self.screen.monitors(), start=1)
        ]
        return {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
            "monitors": monitors,
            "mouse": [x, y],
        }

    @tool
    def read_screen(
        self, x: int = 0, y: int = 0, width: int = 0, height: int = 0, monitor: int = 0
    ) -> str:
        """Text on the screen (OCR). monitor: 1, 2 ... reads only that monitor (see
        screen_info); or give a region with x, y, width and height."""
        if monitor:
            region: tuple[int, int, int, int] | None = self._monitor(monitor)
        else:
            region = (x, y, width, height) if width and height else None
        image = self.screen.capture(region)
        return boxes_to_text(self.ocr.boxes(image))

    def _monitor(self, number: int) -> tuple[int, int, int, int]:
        monitors = self.screen.monitors()
        if not 1 <= number <= len(monitors):
            raise ToolError(f"there is no monitor {number}; this PC has {len(monitors)}")
        return monitors[number - 1]

    @tool
    def find_text(self, text: str) -> list[dict[str, Any]]:
        """Where a word or phrase appears on screen (centre coordinates), via OCR."""
        left, top, _, _ = self.screen.bounds()
        matches = find_phrase(self.ocr.boxes(self.screen.capture()), text)
        return [
            {
                "text": m.text,
                "x": m.center[0] + left,
                "y": m.center[1] + top,
                "confidence": m.confidence,
            }
            for m in matches
        ]

    def _encoded(self) -> tuple[bytes, float]:
        """Screenshot scaled for the vision model, as PNG, plus the scale factor."""
        image = self.screen.capture()
        scale = min(1.0, self.config.vision_max_side / max(image.size))
        if scale < 1.0:
            image = image.resize((int(image.width * scale), int(image.height * scale)))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue(), scale

    @tool
    async def describe_screen(self, question: str = "Describe what is on the screen.") -> str:
        """Ask the local vision model about the current screen."""
        png, _ = await asyncio.to_thread(self._encoded)
        response = await self.vision().chat([Message("user", question, images=[png])], [])
        return response.content

    @tool
    async def locate(self, target: str) -> dict[str, Any]:
        """Find a button or other element by description with the vision model."""
        png, scale = await asyncio.to_thread(self._encoded)
        left, top, width, height = self.screen.bounds()
        w, h = int(width * scale), int(height * scale)
        prompt = LOCATE_PROMPT.format(target=target, w=w, h=h)
        response = await self.vision().chat([Message("user", prompt, images=[png])], [])
        match = re.search(r"\{.*?\}", response.content, re.DOTALL)
        try:
            point = json.loads(match.group(0)) if match else {}
        except json.JSONDecodeError:
            point = {}
        if point.get("x") is None or point.get("y") is None:
            return {"found": False, "target": target}
        return {
            "found": True,
            "target": target,
            "x": int(float(point["x"]) / scale) + left,
            "y": int(float(point["y"]) / scale) + top,
        }

    @tool
    def list_controls(self, window: str = "") -> list[dict[str, str]]:
        """Named controls in the foreground window (Windows UI Automation)."""
        return self.uia.list_controls(window)

    # -- input -------------------------------------------------------------------------

    @tool(permission=Permission.CONFIRM)
    def launch_app(self, name: str) -> dict[str, Any]:
        """Start an allowed application by name (see `apps` in the desktop config)."""
        command = self.config.apps.get(name.lower())
        if command is None:
            raise ValueError(f"'{name}' is not an allowed app ({', '.join(self.config.apps)})")
        self.ctx.kill_switch.check()
        before = self.screen.capture()
        keep_running(start_app(command))
        time.sleep(max(1.5, self.config.settle))
        ratio = change_ratio(before, self.screen.capture())
        changed = ratio > CHANGED_SHARE
        return {"started": name, "screen_changed": changed, "change": round(ratio, 4)}

    @tool(permission=Permission.CONFIRM)
    def move_mouse(self, x: int, y: int, relative: bool = False) -> dict[str, Any]:
        """Move the mouse pointer to the screen position x, y. With relative=true, move it
        by x, y from where it is now (x > 0: right, y > 0: down), e.g. "30 pixels to the
        right" is x=30, y=0, relative=true. The four screen corners are the emergency stop:
        moving there stops everything."""
        if relative:
            here_x, here_y = self.input.position()
            x, y = here_x + x, here_y + y
        return self._act(lambda: self.input.move(x, y))

    @tool(permission=Permission.CONFIRM)
    def click(
        self, x: int, y: int, button: str = "left", double: bool = False, expect_text: str = ""
    ) -> dict[str, Any]:
        """Click at a screen position. expect_text: text that should appear afterwards."""
        if button not in ("left", "right", "middle"):
            raise ValueError("button must be left, right or middle")
        return self._act(lambda: self.input.click(x, y, button, 2 if double else 1), expect_text)

    @tool(permission=Permission.CONFIRM)
    def click_text(self, text: str, expect_text: str = "") -> dict[str, Any]:
        """Click on visible text (found with OCR)."""
        found = self.find_text(text)
        if not found:
            return {"done": False, "error": f"'{text}' not found on screen"}
        best = max(found, key=lambda m: m["confidence"])
        return self.click(best["x"], best["y"], expect_text=expect_text)

    @tool(permission=Permission.CONFIRM)
    def click_control(
        self, name: str, control_type: str = "", window: str = "", expect_text: str = ""
    ) -> dict[str, Any]:
        """Click a control by its name (Windows UI Automation), e.g. a 'Save' button."""
        box: Box | None = self.uia.find(name, control_type, window)
        if box is None:
            return {"done": False, "error": f"no control named '{name}'"}
        x, y = box.center
        return self.click(x, y, expect_text=expect_text)

    @tool(permission=Permission.CONFIRM)
    def scroll(self, amount: int, horizontal: bool = False) -> dict[str, Any]:
        """Scroll (positive = up / right)."""
        return self._act(
            lambda: self.input.scroll(amount if horizontal else 0, 0 if horizontal else amount)
        )

    @tool(permission=Permission.CONFIRM)
    def type_text(self, text: str, expect_text: str = "") -> dict[str, Any]:
        """Type text into the focused window. The kill switch stops it between characters."""

        def typing() -> None:
            for char in text:
                self._check()
                self.input.type_char(char)
                time.sleep(self.config.typing_interval)

        result = self._act(typing, expect_text)
        result["typed"] = len(text)
        return result

    @tool(permission=Permission.CONFIRM)
    def press_keys(self, keys: str) -> dict[str, Any]:
        """Press a key or combination, e.g. 'enter', 'ctrl+s', 'alt+f4', 'win+r'."""
        parts = [k.strip() for k in keys.split("+") if k.strip()]
        if not parts:
            raise ValueError("no keys given")

        def press() -> None:
            self._check()
            self.input.press_keys(parts)

        return self._act(press)
