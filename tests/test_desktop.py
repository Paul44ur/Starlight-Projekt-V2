"""Step 8 'done when': "open Notepad and type hello" works end to end, and the kill
hotkey stops it halfway."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from starlight.core import Kernel, KillSwitchEngaged, Paths
from starlight.core.model import ModelResponse, ToolCall
from tests.conftest import ScriptedProvider

pytest.importorskip("PIL")


class FakeDesktop:
    """A pretend screen with a text editor: typed characters appear on screen."""

    def __init__(self, width: int = 2560, height: int = 1440) -> None:
        self.width, self.height = width, height
        self.text = ""
        self.mouse = (800, 600)
        self.events: list[tuple[Any, ...]] = []
        self.released = 0

    # Screen
    def bounds(self) -> tuple[int, int, int, int]:
        return 0, 0, self.width, self.height

    def capture(self, region: Any = None) -> Image.Image:
        image = Image.new("RGB", (self.width, self.height), "white")
        draw = ImageDraw.Draw(image)
        for i, _ in enumerate(self.text):  # each character paints a block
            draw.rectangle([40 + i * 30, 40, 60 + i * 30, 90], fill="black")
        return image

    # Input
    def position(self) -> tuple[int, int]:
        return self.mouse

    def move(self, x: int, y: int) -> None:
        self.mouse = (x, y)
        self.events.append(("move", x, y))

    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None:
        self.mouse = (x, y)
        self.events.append(("click", x, y, button, count))

    def scroll(self, dx: int, dy: int) -> None:
        self.events.append(("scroll", dx, dy))

    def type_char(self, char: str) -> None:
        self.text += char

    def press_keys(self, keys: list[str]) -> None:
        self.events.append(("keys", *keys))

    def release_all(self) -> None:
        self.released += 1

    # OCR
    def boxes(self, image: Image.Image) -> list[Any]:
        from starlight_modules.desktop.backends import Box  # loaded by the module loader

        words = [Box("File", 10, 5, 30, 12, 95.0), Box("Save", 50, 5, 30, 12, 93.0)]
        if self.text:
            words.append(Box(self.text, 40, 40, 30 * len(self.text), 50, 90.0))
        return words


@pytest.fixture
def desktop_project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("desktop")
    (paths.module_configs / "desktop.yaml").write_text(f"""
indicator: false
settle: 0.01
typing_interval: 0.01
apps:
  notepad: ["{Path(sys.executable).as_posix()}", "-c", "pass"]
""")
    return paths


class Yes:
    async def confirm(self, question: str) -> bool:
        return True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        pass


def install(kernel: Kernel, fake: FakeDesktop) -> Any:
    module = kernel.modules.records["desktop"].instance
    assert module is not None
    module.screen = fake  # type: ignore[attr-defined]
    module.input = fake  # type: ignore[attr-defined]
    module.ocr = fake  # type: ignore[attr-defined]
    return module


async def test_open_notepad_and_type_hello(desktop_project: Paths) -> None:
    provider = ScriptedProvider(
        ModelResponse(tool_calls=[ToolCall("desktop.launch_app", {"name": "notepad"})]),
        ModelResponse(
            tool_calls=[ToolCall("desktop.type_text", {"text": "hello", "expect_text": "hello"})]
        ),
        "Notepad is open and says hello.",
    )
    fake = FakeDesktop()
    async with Kernel(desktop_project, provider=provider, ui=Yes()) as kernel:
        install(kernel, fake)
        result = await kernel.ask("Open Notepad and type hello")
    launched, typed = (r.output for r in result.tool_results)
    assert launched["started"] == "notepad"
    assert fake.text == "hello"
    assert typed["screen_changed"] and typed["verified"]  # act, then check
    assert result.answer == "Notepad is open and says hello."


async def test_input_needs_confirmation(desktop_project: Paths) -> None:
    fake = FakeDesktop()
    async with Kernel(desktop_project) as kernel:  # nobody there to confirm
        install(kernel, fake)
        typed = await kernel.dispatcher.call("desktop.type_text", {"text": "x"})
        read = await kernel.dispatcher.call("desktop.read_screen", {})
    assert typed.error == "not confirmed by user" and fake.text == ""
    assert read.ok and "File Save" in read.output  # reading the screen is safe


async def test_kill_hotkey_stops_typing_halfway(desktop_project: Paths) -> None:
    text = "hello, this sentence is long enough to be interrupted"
    provider = ScriptedProvider(
        ModelResponse(tool_calls=[ToolCall("desktop.type_text", {"text": text})])
    )
    fake = FakeDesktop()
    async with Kernel(desktop_project, provider=provider, ui=Yes()) as kernel:
        install(kernel, fake)

        # the hotkey listener runs in its own thread, like pynput's: press it once typing
        # has visibly started
        def press_hotkey_midway() -> None:
            deadline = time.monotonic() + 10
            while len(fake.text) < 5 and time.monotonic() < deadline:
                time.sleep(0.005)
            kernel.kill_switch.trigger("hotkey <ctrl>+<alt>+k", source="hotkey")

        threading.Thread(target=press_hotkey_midway, daemon=True).start()
        result = await kernel.ask("type a long sentence")
        time.sleep(0.05)  # the typing thread notices at its next character
        assert result.stopped
        assert 0 < len(fake.text) < len(text)
        assert fake.released >= 1  # keys and buttons were let go


async def test_failsafe_corner_triggers_kill_switch(desktop_project: Paths) -> None:
    fake = FakeDesktop()
    async with Kernel(desktop_project, ui=Yes()) as kernel:
        install(kernel, fake)
        fake.mouse = (0, 0)  # you slam the mouse into the top-left corner
        with pytest.raises(KillSwitchEngaged, match="failsafe"):
            await kernel.dispatcher.call("desktop.click", {"x": 500, "y": 500})
        assert kernel.kill_switch.engaged
    assert ("click", 500, 500, "left", 1) not in fake.events


async def test_find_text_click_text_and_keys(desktop_project: Paths) -> None:
    fake = FakeDesktop()
    async with Kernel(desktop_project, ui=Yes()) as kernel:
        install(kernel, fake)
        found = await kernel.dispatcher.call("desktop.find_text", {"text": "save"})
        assert found.output == [{"text": "Save", "x": 65, "y": 11, "confidence": 93.0}]
        await kernel.dispatcher.call("desktop.click_text", {"text": "Save"})
        await kernel.dispatcher.call("desktop.press_keys", {"keys": "ctrl+s"})
    assert ("click", 65, 11, "left", 1) in fake.events
    assert ("keys", "ctrl", "s") in fake.events


async def test_vision_locate_scales_back_and_stays_local(desktop_project: Paths) -> None:
    provider = ScriptedProvider('The button is here: {"x": 100, "y": 50}')
    fake = FakeDesktop()
    async with Kernel(desktop_project, provider=provider) as kernel:
        install(kernel, fake)
        located = await kernel.dispatcher.call("desktop.locate", {"target": "OK button"})
        sent = provider.calls[0][0]
    # 2560 px wide screen scaled to 1280 for the model: coordinates scale back x2
    assert located.output == {"found": True, "target": "OK button", "x": 200, "y": 100}
    assert sent.images and sent.images[0][:4] == b"\x89PNG"  # type: ignore[attr-defined]

    config = desktop_project.module_configs / "desktop.yaml"
    config.write_text(
        config.read_text() + "vision:\n  provider: ollama\n"
        "  options: {base_url: 'http://8.8.8.8:11434'}\n"
    )
    async with Kernel(desktop_project) as kernel:
        install(kernel, fake)
        remote = await kernel.dispatcher.call("desktop.describe_screen", {})
    assert "must run on this machine" in (remote.error or "")


def test_local_url_rules() -> None:
    from starlight.core.modules import discover, import_module_class
    from tests.test_network import REPO

    found, _ = discover(REPO / "modules")
    import_module_class(*found["desktop"])
    from starlight_modules.desktop.module import is_local_url

    assert is_local_url("http://127.0.0.1:11434") and is_local_url("http://localhost:8080")
    assert not is_local_url("http://192.168.1.5:11434")  # another PC: only when allowed
    assert is_local_url("http://192.168.1.5:11434", allow_lan=True)
    assert not is_local_url("http://203.0.113.9", allow_lan=True)  # not a home network
    assert not is_local_url("https://api.example.com", allow_lan=True)


def test_ocr_helpers() -> None:
    from starlight.core.modules import discover, import_module_class
    from tests.test_network import REPO

    found, _ = discover(REPO / "modules")
    import_module_class(*found["desktop"])
    from starlight_modules.desktop.backends import Box, boxes_to_text, find_phrase

    boxes = [
        Box("Save", 50, 5, 30, 12, 90),
        Box("File", 10, 6, 30, 12, 95),
        Box("Save", 10, 40, 30, 12, 80),
        Box("as", 45, 41, 15, 12, 85),
    ]
    assert boxes_to_text(boxes) == "File Save\nSave as"
    [match] = find_phrase(boxes, "save AS")
    assert (match.x, match.width, match.text) == (10, 50, "Save as")


@pytest.fixture
def xvfb() -> Iterator[str]:
    if not shutil.which("Xvfb"):
        pytest.skip("Xvfb not installed")
    display = ":77"
    proc = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", "1024x768x24"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.8)
    old = os.environ.get("DISPLAY")
    os.environ["DISPLAY"] = display
    yield display
    proc.terminate()
    proc.wait()
    if old is None:
        os.environ.pop("DISPLAY", None)
    else:
        os.environ["DISPLAY"] = old


def test_real_screen_and_mouse_on_virtual_display(xvfb: str) -> None:
    from starlight.core.modules import discover, import_module_class
    from tests.test_network import REPO

    found, _ = discover(REPO / "modules")
    import_module_class(*found["desktop"])
    from starlight_modules.desktop.backends import DesktopUnavailable, MssScreen, PynputInput

    screen = MssScreen()
    assert screen.bounds() == (0, 0, 1024, 768)
    assert screen.capture().size == (1024, 768)
    try:
        mouse = PynputInput()
    except DesktopUnavailable as exc:
        pytest.skip(str(exc))
    mouse.move(300, 200)
    assert mouse.position() == (300, 200)
    mouse.release_all()
