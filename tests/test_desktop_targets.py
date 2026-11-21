"""desktop.find_target: "where is the settings menu?" gives a box to point at. UI
Automation first (foreground window, then the others), then OCR on each monitor, then the
vision model. A miss never comes with a box."""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from starlight.core import Kernel, Paths
from tests.conftest import ScriptedProvider
from tests.test_desktop import FakeDesktop

pytest.importorskip("PIL")

if TYPE_CHECKING:
    from PIL import Image

FULL_HD = 1920, 1080


def load_desktop() -> None:
    from starlight.core.modules import discover, import_module_class
    from tests.test_network import REPO

    found, _ = discover(REPO / "modules")
    import_module_class(*found["desktop"])


def window(name: str, rect: tuple[int, int, int, int], foreground: bool = False) -> Any:
    from starlight_modules.desktop.backends import WindowInfo

    return WindowInfo(name, rect, foreground)


def control(name: str, rect: tuple[int, int, int, int], offscreen: bool = False) -> Any:
    from starlight_modules.desktop.backends import ControlInfo

    return ControlInfo(name, rect, offscreen, "ButtonControl")


class FakeUia:
    """A small control tree: windows, each with its controls."""

    def __init__(self, *tree: tuple[Any, list[Any]]) -> None:
        self.tree = list(tree)
        self.searched: list[str] = []
        self.sessions = 0

    @contextlib.contextmanager
    def session(self) -> Iterator[None]:
        self.sessions += 1
        yield

    def windows(self) -> list[Any]:
        return [w for w, _ in self.tree]

    def controls(self, win: Any, deadline: float) -> list[Any]:
        self.searched.append(win.title)
        return next(c for w, c in self.tree if w is win)


class Screens(FakeDesktop):
    """Monitors in any layout; OCR words per monitor, relative to its corner."""

    def __init__(self, monitors: list[tuple[int, int, int, int]]) -> None:
        left = min(m[0] for m in monitors)
        top = min(m[1] for m in monitors)
        right = max(m[0] + m[2] for m in monitors)
        bottom = max(m[1] + m[3] for m in monitors)
        super().__init__(right - left, bottom - top)
        self.left, self.top = left, top
        self._monitors = monitors
        self.words: dict[tuple[int, int, int, int], list[Any]] = {}
        self.captured: list[Any] = []

    def bounds(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.width, self.height

    def monitors(self) -> list[tuple[int, int, int, int]]:
        return self._monitors

    def capture(self, region: Any = None) -> Image.Image:
        from PIL import Image

        self.captured.append(region)
        size = (self.width, self.height) if region is None else (8, 8)
        image = Image.new("RGB", size, "white")
        image.info["region"] = region
        return image

    def boxes(self, image: Image.Image) -> list[Any]:
        from starlight_modules.desktop.backends import Box

        return [Box(*w) for w in self.words.get(image.info["region"], [])]


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("desktop")
    (paths.module_configs / "desktop.yaml").write_text("indicator: false\nsettle: 0.01\n")
    return paths


def with_vision(paths: Paths) -> None:
    config = paths.module_configs / "desktop.yaml"
    config.write_text(
        config.read_text() + "vision:\n  provider: ollama\n"
        "  options: {base_url: 'http://127.0.0.1:11434'}\n"
    )


def install(kernel: Kernel, screens: Screens, uia: FakeUia | None) -> Any:
    module = kernel.modules.records["desktop"].instance
    assert module is not None
    module.screen = screens  # type: ignore[attr-defined]
    module.input = screens  # type: ignore[attr-defined]
    module.ocr = screens  # type: ignore[attr-defined]
    if uia is not None:
        module.uia = uia  # type: ignore[attr-defined]
    return module


async def find(
    paths: Paths,
    screens: Screens,
    uia: FakeUia | None,
    target: str,
    vision: str | None = None,
    **args: str,
) -> dict[str, Any]:
    if vision is not None:
        with_vision(paths)
    async with Kernel(paths) as kernel:  # nobody to confirm: find_target is safe
        module = install(kernel, screens, uia)
        if vision is not None:
            module._vision = ScriptedProvider(vision)
        result = await kernel.dispatcher.call("desktop.find_target", {"target": target, **args})
    assert result.ok, result.error
    output: dict[str, Any] = result.output
    return output


def two_side_by_side() -> Screens:
    return Screens([(0, 0, *FULL_HD), (1920, 0, *FULL_HD)])


async def test_a_control_in_the_foreground_window(project: Paths) -> None:
    load_desktop()
    front = window("Editor - notes.txt", (100, 100, 800, 600), foreground=True)
    big = window("Browser", (0, 0, 1920, 1080))
    uia = FakeUia(
        (big, [control("Settings", (1800, 10, 40, 30))]),  # larger, but behind
        (front, [control("File", (110, 110, 30, 20)), control("Settings", (150, 110, 60, 20))]),
    )
    hit = await find(project, two_side_by_side(), uia, "settings")
    assert hit == {
        "found": True,
        "box": [150, 110, 60, 20],
        "monitor": 1,
        "method": "uia",
        "label": "Settings",
        "confidence": "sure",
        "window": "Editor - notes.txt",
    }
    assert uia.searched == ["Editor - notes.txt"]  # found there: the others not searched
    assert uia.sessions == 1  # one COM session around windows and controls


async def test_a_control_only_in_a_background_window_on_monitor_2(project: Paths) -> None:
    load_desktop()
    front = window("Terminal", (0, 0, 800, 600), foreground=True)
    small = window("Calculator", (2000, 100, 300, 400))
    large = window("Explorer", (1920, 0, 1920, 1080))
    gone = window("Hidden", (0, 0, 0, 0))  # zero size: never searched
    uia = FakeUia(
        (front, [control("Close", (780, 0, 20, 20))]),
        (small, [control("Save", (2010, 120, 50, 20))]),
        (gone, [control("Save", (0, 0, 10, 10))]),
        (large, [control("Save as...", (2500, 300, 80, 20))]),
    )
    hit = await find(project, two_side_by_side(), uia, "save")
    # foreground first, then the largest: Explorer's "Save as..." starts with "save"
    assert uia.searched == ["Terminal", "Explorer"]
    assert (hit["box"], hit["monitor"], hit["window"]) == ([2500, 300, 80, 20], 2, "Explorer")
    assert hit["label"] == "Save as..."


async def test_settings_finds_einstellungen_and_filler_words_go(project: Paths) -> None:
    load_desktop()
    front = window("Explorer", (0, 0, 1920, 1080), foreground=True)
    tree = [
        control("Einstellungen öffnen", (300, 10, 90, 20)),  # only "starts with"
        control("Einstellungen", (10, 10, 90, 20)),  # exact: wins
        control("Menü", (10, 40, 50, 20)),
    ]
    for target in ("settings", "the settings menu", "die Einstellungen", "Optionen"):
        uia = FakeUia((front, tree))
        hit = await find(project, two_side_by_side(), uia, target)
        assert (hit["label"], hit["box"]) == ("Einstellungen", [10, 10, 90, 20]), target
    uia = FakeUia((front, tree))
    hit = await find(project, two_side_by_side(), uia, "das Menü")
    assert hit["label"] == "Menü"  # stripping left nothing: the bare word stays


def test_name_variants_and_filler() -> None:
    load_desktop()
    from starlight_modules.desktop.targets import match_rank, name_variants, strip_filler

    assert strip_filler("The Settings Menu") == "settings"
    assert strip_filler("die Schaltfläche Speichern") == "speichern"
    assert strip_filler("das Menü") == "menü" and strip_filler("the menu") == "menu"
    assert name_variants("settings") == ["settings", "einstellungen", "optionen"]
    assert name_variants("Datei") == ["datei", "file"]
    assert name_variants("Schließen") == ["schließen", "close"]
    assert name_variants("save file") == [
        "save file", "speichern file", "save datei"
    ]  # fmt: skip
    assert name_variants("the") == ["the"] and name_variants("  ") == []
    v = ["save", "speichern"]
    assert match_rank("SAVE", v) == (0, 0)
    assert match_rank("Speichern unter", v) == (1, 1)
    assert match_rank("Autosave", v) == (2, 0)
    assert match_rank("Open", v) is None and match_rank("", v) is None


async def test_offscreen_and_empty_controls_are_skipped(project: Paths) -> None:
    load_desktop()
    front = window("Settings app", (0, 0, 1920, 1080), foreground=True)
    uia = FakeUia(
        (
            front,
            [
                control("Settings", (10, 10, 50, 20), offscreen=True),  # scrolled away
                control("Settings", (10, 40, 0, 0)),  # no size
                control("Open settings", (10, 70, 90, 20)),
            ],
        )
    )
    hit = await find(project, two_side_by_side(), uia, "settings")
    assert (hit["label"], hit["box"]) == ("Open settings", [10, 70, 90, 20])


async def test_ocr_on_monitor_2_side_by_side(project: Paths) -> None:
    load_desktop()
    screens = two_side_by_side()
    second = (1920, 0, *FULL_HD)
    screens.words[second] = [("Einstellungen", 100, 50, 120, 20, 91.0)]
    uia = FakeUia((window("Spiel", (0, 0, 1920, 1080), foreground=True), []))
    hit = await find(project, screens, uia, "the settings")
    assert hit == {
        "found": True,
        "box": [2020, 50, 120, 20],  # relative to monitor 2, which starts at x=1920
        "monitor": 2,
        "method": "ocr",
        "label": "Einstellungen",
        "confidence": "sure",
        "window": "",
    }
    assert screens.captured == [(0, 0, *FULL_HD), second]  # each monitor on its own


async def test_ocr_on_a_monitor_left_of_the_main_one(project: Paths) -> None:
    load_desktop()
    left = (-1920, 0, *FULL_HD)
    screens = Screens([(0, 0, *FULL_HD), left])
    screens.words[left] = [("Help", 10, 1000, 40, 18, 40.0)]
    hit = await find(project, screens, FakeUia(), "Hilfe")
    assert (hit["box"], hit["monitor"], hit["method"]) == ([-1910, 1000, 40, 18], 2, "ocr")
    assert hit["confidence"] == "maybe"  # OCR was not sure of the word


@pytest.mark.skipif(sys.platform == "win32", reason="the real UI Automation is there")
async def test_without_ui_automation_ocr_is_tried_first(project: Paths) -> None:
    load_desktop()
    hit = await find(project, two_side_by_side(), None, "nothing here")
    assert hit == {"found": False, "target": "nothing here", "tried": ["ocr"], "windows": []}


async def test_ocr_prefers_the_foreground_window(project: Paths) -> None:
    load_desktop()
    screens = two_side_by_side()
    screens.words[(0, 0, *FULL_HD)] = [("Save", 50, 50, 40, 20, 96.0)]
    screens.words[(1920, 0, *FULL_HD)] = [("Speichern", 100, 100, 80, 20, 70.0)]
    front = window("Word", (1920, 0, 1920, 1080), foreground=True)
    other = window("Notepad", (0, 0, 800, 600))
    hit = await find(project, screens, FakeUia((front, []), (other, [])), "save")
    assert (hit["label"], hit["box"], hit["window"]) == ("Speichern", [2020, 100, 80, 20], "Word")
    # window= names the window to prefer: Notepad's "Save" now wins
    named = await find(project, screens, FakeUia((front, []), (other, [])), "save", window="note")
    assert (named["label"], named["window"]) == ("Save", "Notepad")


async def test_vision_box_is_scaled_back(project: Paths) -> None:
    load_desktop()
    screens = Screens([(0, 0, 1280, 1440), (1280, 0, 1280, 1440)])  # 2560 wide: scale 0.5
    hit = await find(
        project, screens, FakeUia(), "gear icon", vision='Sure: [{"bbox_2d": [700, 50, 740, 70]}]'
    )
    assert hit == {
        "found": True,
        "box": [1400, 100, 80, 40],
        "monitor": 2,
        "method": "vision",
        "label": "gear icon",
        "confidence": "maybe",
        "window": "",
    }


async def test_vision_point_only_answer_becomes_a_small_box(project: Paths) -> None:
    load_desktop()
    screens = Screens([(-1280, 0, 1280, 1440), (0, 0, 1280, 1440)])  # one left of main
    hit = await find(project, screens, FakeUia(), "gear icon", vision='{"x": 100, "y": 50}')
    # centre 200, 100 in the full-size picture, which starts at x=-1280
    assert hit["box"] == [-1080 - 32, 100 - 20, 64, 40] and hit["monitor"] == 1


async def test_a_miss_lists_the_windows_and_never_has_a_box(project: Paths) -> None:
    load_desktop()
    long_title = "A" * 100
    front = window("Mail", (0, 0, 600, 400), foreground=True)
    others = [window(f"Window {i}", (0, 0, 100 * i, 100)) for i in range(1, 6)]
    uia = FakeUia(
        (front, [control("Inbox", (0, 0, 50, 20))]),
        (window(long_title, (0, 0, 1920, 1080)), []),
        *((w, []) for w in others),
    )
    for answer in ('{"bbox_2d": null}', "I can't see it.", '{"bbox_2d": [5, 5, 5, 9]}'):
        missed = await find(project, two_side_by_side(), uia, "settings", vision=answer)
        assert missed == {
            "found": False,
            "target": "settings",
            "tried": ["uia", "ocr", "vision"],
            "windows": ["Mail", "A" * 80, "Window 5", "Window 4", "Window 3"],
        }
        (project.module_configs / "desktop.yaml").write_text("indicator: false\n")
    without_vision = await find(project, two_side_by_side(), uia, "settings")
    assert without_vision["tried"] == ["uia", "ocr"] and "box" not in without_vision


def test_parse_vision() -> None:
    load_desktop()
    from starlight_modules.desktop.targets import parse_vision

    assert parse_vision('{"bbox_2d": [10, 20, 30, 60]}', 1.0, (0, 0)) == (10, 20, 20, 40)
    assert parse_vision('{"bbox_2d": [30, 60, 10, 20]}', 0.5, (100, 0)) == (120, 40, 40, 80)
    assert parse_vision('{"bbox_2d": null}', 1.0, (0, 0)) is None
    assert parse_vision('{"bbox_2d": ["a", 1, 2, 3]}', 1.0, (0, 0)) is None
    assert parse_vision('{"x": null, "y": null}', 1.0, (0, 0)) is None
    assert parse_vision("nothing", 1.0, (0, 0)) is None


class RecordingAutomation:
    """Stands in for the uiautomation package: says whether COM was set up for the
    thread when a control is touched."""

    def __init__(self) -> None:
        self.depth = 0
        self.touched_without_com = 0
        outer = self

        class Initializer:
            def __enter__(self) -> None:
                outer.depth += 1

            def __exit__(self, *exc: object) -> None:
                outer.depth -= 1

        self.UIAutomationInitializerInThread = Initializer

    def _touch(self) -> None:
        if self.depth == 0:
            self.touched_without_com += 1

    def GetForegroundControl(self) -> Any:
        self._touch()
        outer = self

        class Control:
            Name = "Save"
            ControlTypeName = "ButtonControl"

            def GetTopLevelControl(self) -> Any:
                outer._touch()
                return self

            def Control(self, **criteria: Any) -> Any:
                outer._touch()
                return self

            def Exists(self, seconds: float) -> bool:
                outer._touch()
                return True

            class BoundingRectangle:
                left, top = 5, 6

                @staticmethod
                def width() -> int:
                    return 10

                @staticmethod
                def height() -> int:
                    return 4

        return Control()

    def WalkControl(self, root: Any, maxDepth: int) -> Iterator[tuple[Any, int]]:
        self._touch()
        yield root, 1


def test_ui_automation_calls_set_up_com_in_their_thread() -> None:
    """Sync tools run in a worker thread; UI Automation there needs COM set up first
    (UIAutomationInitializerInThread), or click_control and list_controls fail."""
    load_desktop()
    from starlight_modules.desktop.backends import UiAutomation

    fake = RecordingAutomation()
    uia = object.__new__(UiAutomation)  # the real one only starts on Windows
    uia._auto = fake
    assert uia.list_controls() == [{"name": "Save", "type": "ButtonControl"}]
    box = uia.find("Save")
    assert box is not None and (box.x, box.width) == (5, 10)
    assert fake.touched_without_com == 0 and fake.depth == 0


def test_tesseract_reads_german_when_it_is_installed() -> None:
    load_desktop()
    from starlight_modules.desktop.backends import TesseractOcr

    class Tess:
        def __init__(self, languages: list[str]) -> None:
            self.languages = languages

        def get_languages(self, config: str = "") -> list[str]:
            return self.languages

    for installed, expected, warned in (
        (["eng", "deu", "osd"], "eng+deu", False),
        (["eng"], "eng", True),
    ):
        ocr = object.__new__(TesseractOcr)
        ocr._tess = Tess(installed)
        ocr.warning = ""
        assert ocr._default_languages() == expected
        assert bool(ocr.warning) is warned
