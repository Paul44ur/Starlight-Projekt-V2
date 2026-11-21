"""Ask before clicking (docs/plan-overlay.md, section 6, package W6).

A tool that asks first, during a wake-word request, is asked out loud and on the overlay's
Yes/No card; the first answer wins and silence is "no". ``overlay.click_target`` clicks
what was just pointed at and asks only once (not again for ``desktop.click``). Practice
mode describes the click instead of doing it.

Speech recognition, the model and playback are stand-ins; the overlay process is the fake
child of test_overlay_module.py. No Qt, no microphone.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from modules.voice.voiceui import heard_answer
from starlight.core import Kernel, Paths
from starlight.core.events import Event
from starlight.core.ui import current_ui
from tests.conftest import ModuleWriter
from tests.test_home_devices import call, fails, instance
from tests.test_overlay_module import FAKE_DESKTOP, Child, child, until
from tests.test_voice_states import pcm, wav

__all__ = ["child"]  # (the fixture, used by name)

# A tool that changes something, so it asks first.
CLICKY = """
from starlight.core import Module, Permission, tool

class Mod(Module):
    async def setup(self, ctx):
        self.pressed = 0

    @tool(permission=Permission.CONFIRM)
    def press(self, what: str = "it") -> dict:
        \"\"\"Press something (asks first).\"\"\"
        self.pressed += 1
        return {"pressed": what}
"""

# Only there so that ctx.has_tool("overlay.point_at") is true: an overlay that can answer.
STUB_OVERLAY = """
from starlight.core import Module, tool

class Mod(Module):
    @tool
    def point_at(self, target: str, window: str = "") -> dict:
        \"\"\"Stand-in.\"\"\"
        return {"found": False}
"""

# W4's find_target (from test_overlay_module) plus desktop.click, which asks first.
DESKTOP = (
    (
        FAKE_DESKTOP
        + '''
    @tool(permission=Permission.CONFIRM)
    def click(self, x: int, y: int, button: str = "left", double: bool = False,
              expect_text: str = "") -> dict[str, Any]:
        """Stand-in for desktop.click."""
        self.clicks.append((x, y))
        return {"clicked": [x, y]}
'''
    )
    .replace(
        "from starlight.core import Module, tool",
        "from starlight.core import Module, Permission, tool",
    )
    .replace(
        "self.answers: list[dict[str, Any]] = []", "self.answers = []\n        self.clicks = []"
    )
)

HIT_CENTRE = (2100 + 90 // 2, 40 + 30 // 2)  # FAKE_DESKTOP's box [2100, 40, 90, 30]


def test_yes_and_no_words() -> None:
    for said in ["Ja.", "jo", "Klar!", "mach", "Yes please", "okay", "OK"]:
        assert heard_answer(said) is True, said
    for said in ["Nein", "nee", "Stopp!", "abbrechen", "no", "Cancel", "mach das nicht"]:
        assert heard_answer(said) is False, said
    for said in ["", "hmm", "vielleicht", "jaguar"]:
        assert heard_answer(said) is None, said


# -- the voice side -----------------------------------------------------------------------


@pytest.fixture
def home(paths: Paths, use_module: Callable[[str], Path], write_module: ModuleWriter) -> Paths:
    use_module("voice")
    write_module("clicky", CLICKY)
    with paths.config_file.open("a") as config:
        config.write("memory:\n  enabled: false\nskills:\n  enabled: false\n")
    return paths


class Voice:
    """The voice module with stand-ins: ``heard`` is what speech recognition writes down,
    one entry per stretch of speech; ``replies`` what comes after the question (None:
    silence, "block": nothing for ever)."""

    def __init__(self, k: Kernel, heard: list[str]) -> None:
        self.k = k
        self.module = instance(k, "voice")
        self.module.config.wake_word = True
        self.module.ask_timeout = 1.0
        self.said: list[str] = []
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.results: list[Any] = []
        self.listening_cancelled = False
        self.heard = iter(heard)
        sounds: dict[bytes, str] = {}

        async def transcribe(audio: bytes, language: str = "") -> str:
            return next(self.heard)

        async def synthesize(text: str, language: str = "", voice: str = "") -> bytes:
            audio = wav(pcm(6000, 0.1) + bytes([len(sounds) % 256, 0]))
            sounds[audio] = text
            return audio

        def play_now(audio: bytes) -> None:
            self.said.append(sounds[audio])

        async def run_request(text: str) -> str:
            result = await k.dispatcher.call("clicky.press", {"what": text})
            self.results.append(result)
            return "Done." if result.ok else "Not done."

        def seen(event: Event) -> None:
            self.events.append((event.name, event.data))

        k.bus.subscribe("overlay.*", seen)
        self.module.transcribe_audio = transcribe
        self.module.synthesize_audio = synthesize
        self.module._play_now = play_now
        self.module.ctx.run_request = run_request

    def replies(self, *answers: str | None) -> Callable[[], Awaitable[bytes | None]]:
        queue = list(answers)

        async def next_speech() -> bytes | None:
            item = queue.pop(0) if queue else None
            if item == "block":
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.listening_cancelled = True
                    raise
            return None if item is None else b"\0\0" * 1600

        return next_speech

    async def request(self, *answers: str | None) -> None:
        await self.module.on_speech(b"\0\0" * 1600, self.replies(*answers))

    @property
    def pressed(self) -> int:
        return int(instance(self.k, "clicky").pressed)

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


async def test_yes_by_voice_runs_it(home: Paths) -> None:
    async with Kernel(home) as k:
        v = Voice(k, ["Hey Starlight, press the button", "Ja"])
        await v.request("speech")
        assert v.pressed == 1
        assert v.said == ["Darf ich clicky.press ausführen?", "Done."]
        assert v.names() == []  # no overlay: voice alone decides, no card


async def test_no_by_voice_does_not_run_it(home: Paths) -> None:
    async with Kernel(home) as k:
        v = Voice(k, ["Hey Starlight, press the button", "Nein, lass das"])
        await v.request("speech")
        assert v.pressed == 0
        assert v.results[0].error == "not confirmed by user"
        assert v.said[-1] == "Not done."


async def test_unclear_words_ask_once_more_then_no(home: Paths) -> None:
    async with Kernel(home) as k:
        v = Voice(k, ["Hey Starlight, press it", "hmm", "okay"])
        await v.request("speech", "speech")
        assert v.pressed == 1
        assert v.said[:2] == ["Darf ich clicky.press ausführen?", "Ja oder nein?"]
    async with Kernel(home) as k:
        v = Voice(k, ["Hey Starlight, press it", "hmm", "vielleicht"])
        await v.request("speech", "speech")
        assert v.pressed == 0


async def test_silence_is_no(home: Paths) -> None:
    async with Kernel(home) as k:
        v = Voice(k, ["Hey Starlight, press it"])
        await v.request(None)
        assert v.pressed == 0


async def test_the_question_also_goes_to_the_card(home: Paths, write_module: ModuleWriter) -> None:
    write_module("overlay", STUB_OVERLAY)
    async with Kernel(home) as k:
        v = Voice(k, ["Hey Starlight, press it", "yes"])
        await v.request("speech")
        assert v.pressed == 1
        (name, ask), (cancel_name, cancel) = v.events
        assert name == "overlay.ask" and cancel_name == "overlay.cancel_ask"
        assert ask["tool"] == "clicky.press" and "'what'" in ask["args"]
        assert ask["question"] == "Darf ich clicky.press ausführen?"
        assert [c["id"] for c in ask["choices"]] == ["yes", "no"]
        assert [c["label"] for c in ask["choices"]] == ["Ja", "Nein"]
        assert cancel == {"id": ask["id"]}  # answered by voice: the card goes away


async def test_a_click_on_the_card_runs_it_and_stops_listening(
    home: Paths, write_module: ModuleWriter
) -> None:
    write_module("overlay", STUB_OVERLAY)
    async with Kernel(home) as k:
        v = Voice(k, ["Hey Starlight, press it"])

        async def click_yes(event: Event) -> None:
            await asyncio.sleep(0.05)
            await k.bus.publish("overlay.answer", {"id": event.data["id"], "choice": "yes"})

        k.bus.subscribe("overlay.ask", click_yes, background=True)
        await v.request("block")
        assert v.pressed == 1
        assert v.listening_cancelled
        assert "overlay.cancel_ask" not in v.names()  # the card answered itself


async def test_no_on_the_card_or_its_timeout(home: Paths, write_module: ModuleWriter) -> None:
    write_module("overlay", STUB_OVERLAY)
    async with Kernel(home) as k:
        v = Voice(k, ["Hey Starlight, press it", "Hey Starlight, press it"])
        for choice in ["no", None]:

            async def answer(event: Event, choice: str | None = choice) -> None:
                await k.bus.publish("overlay.answer", {"id": event.data["id"], "choice": choice})

            sub = k.bus.subscribe("overlay.ask", answer, background=True)
            await v.request("block")
            k.bus._subscriptions.remove(sub)
        assert v.pressed == 0


async def test_silence_with_the_card_open_is_no_after_the_timeout(
    home: Paths, write_module: ModuleWriter
) -> None:
    write_module("overlay", STUB_OVERLAY)
    async with Kernel(home) as k:
        v = Voice(k, ["Hey Starlight, press it"])
        v.module.ask_timeout = 0.3
        started = asyncio.get_running_loop().time()
        await v.request(None)  # nothing said; the card stays open until the timeout
        assert asyncio.get_running_loop().time() - started >= 0.3
        assert v.pressed == 0
        assert v.names() == ["overlay.ask", "overlay.cancel_ask"]


async def test_the_interface_is_only_set_for_the_request(home: Paths) -> None:
    async with Kernel(home) as k:
        v = Voice(k, ["Hey Starlight, press it", "ja"])
        await v.request("speech")
        assert current_ui.get() is None
        # Typed in the web chat (no voice interface): refused as before (nobody answers).
        assert not (await k.dispatcher.call("clicky.press", {})).ok


# -- click_target ---------------------------------------------------------------------------


class Yes:
    """Says yes to everything and keeps the questions."""

    def __init__(self) -> None:
        self.questions: list[str] = []

    async def confirm(self, question: str) -> bool:
        self.questions.append(question)
        return True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


@pytest.fixture
def desk(paths: Paths, use_module: Callable[[str], Path], write_module: ModuleWriter) -> Paths:
    use_module("overlay")
    write_module("desktop", DESKTOP)
    (paths.module_configs / "overlay.yaml").write_text("enabled: false\n")
    return paths


async def click(k: Kernel, ui: Yes, **args: Any) -> Any:
    token = current_ui.set(ui)
    try:
        return await k.dispatcher.call("overlay.click_target", args)
    finally:
        current_ui.reset(token)


async def test_click_target_needs_a_recent_point(desk: Paths) -> None:
    async with Kernel(desk) as k:
        ui = Yes()
        result = await click(k, ui)
        assert not result.ok and "point at it first" in str(result.error)
        await call(k, "overlay.point_at", target="settings")
        instance(k, "overlay").last_target["at"] -= 61  # over a minute ago
        result = await click(k, ui)
        assert not result.ok and "point at it first" in str(result.error)
        assert instance(k, "desktop").clicks == []


async def test_click_target_clicks_the_centre_and_asks_once(desk: Paths) -> None:
    async with Kernel(desk) as k:
        await call(k, "overlay.point_at", target="settings")
        ui = Yes()
        result = await click(k, ui)
        assert result.ok, result.error
        assert instance(k, "desktop").clicks == [HIT_CENTRE]
        assert len(ui.questions) == 1 and "Run overlay.click_target with" in ui.questions[0]
        assert result.output["clicked"] and result.output["label"] == "Einstellungen"
        # Without an approval for click_target, desktop.click is not approved either.
        assert not (await k.dispatcher.call("desktop.click", {"x": 1, "y": 2})).ok
        assert instance(k, "desktop").clicks == [HIT_CENTRE]


async def test_click_target_with_a_target_points_first(desk: Paths) -> None:
    async with Kernel(desk) as k:
        ui = Yes()
        result = await click(k, ui, target="settings")
        assert result.ok, result.error
        assert instance(k, "desktop").calls == [{"target": "settings", "window": ""}]
        assert instance(k, "desktop").clicks == [HIT_CENTRE]
        assert len(ui.questions) == 1
        # A miss clicks nothing.
        instance(k, "desktop").answers.append({"found": False, "target": "rocket"})
        result = await click(k, ui, target="rocket")
        assert result.ok and result.output["clicked"] is False
        assert instance(k, "desktop").clicks == [HIT_CENTRE]


async def test_click_target_is_not_run_without_a_yes(desk: Paths) -> None:
    async with Kernel(desk) as k:
        await call(k, "overlay.point_at", target="settings")
        assert await fails(k, "overlay.click_target") == "not confirmed by user"
        assert instance(k, "desktop").clicks == []


async def test_practice_mode_describes_the_click(desk: Paths) -> None:
    async with Kernel(desk) as k:
        await call(k, "overlay.point_at", target="settings")
        k.dispatcher.policy.practice = True
        ui = Yes()
        result = await click(k, ui)
        assert result.ok and "Practice mode: overlay.click_target did NOT run" in result.output
        assert ui.questions == []
        assert instance(k, "desktop").clicks == []


# -- with the overlay running (the fake child answers every card with its first choice) ---


@pytest.mark.skipif(sys.platform == "win32", reason="the fake child is a #! script")
async def test_by_voice_with_the_overlay_shows_the_target_and_a_click_answers(
    desk: Paths, use_module: Callable[[str], Path], child: Child
) -> None:
    use_module("voice")
    (desk.module_configs / "overlay.yaml").write_text(
        "".join(f"{k}: {json.dumps(v)}\n" for k, v in {"enabled": True}.items())
    )
    with desk.config_file.open("a") as config:
        config.write("memory:\n  enabled: false\nskills:\n  enabled: false\n")
    async with Kernel(desk) as k:
        await until(lambda: instance(k, "overlay").running)
        await call(k, "overlay.point_at", target="settings")
        v = Voice(k, ["Hey Starlight, klick es an"])

        async def run_request(text: str) -> str:
            result = await k.dispatcher.call("overlay.click_target", {})
            v.results.append(result)
            return "Erledigt." if result.ok else "Nicht erledigt."

        v.module.ctx.run_request = run_request
        await v.request("block")  # nothing said: the card's click answers ("yes")
        assert v.results[0].ok, v.results[0].error
        assert instance(k, "desktop").clicks == [HIT_CENTRE]
        assert v.listening_cancelled
        assert v.said[0] == "Soll ich das anklicken?"
        received = child.received()
        ask_at = next(i for i, m in enumerate(received) if m["type"] == "ask")
        assert received[ask_at]["question"] == "„Einstellungen“ anklicken?"
        assert received[ask_at - 1]["type"] == "point"  # shown again just before the card
        assert received[ask_at - 1]["box"] == [2100, 40, 90, 30]
        await until(lambda: "clear" in child.types()[ask_at:])  # after the click
