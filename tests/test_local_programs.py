"""Programs on this PC: opening programs (asks the first time only), files and links,
handling windows, media keys, your own named commands, Word/Excel files, git, Docker and
ports, and web research with sources. The desktop is a fake; git and the commands run for
real in temporary folders."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.core.model import Message, ModelResponse


class User:
    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.questions: list[str] = []

    async def confirm(self, question: str) -> bool:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    for name in ("apps", "media", "commands", "office", "dev", "research", "web"):
        use_module(name)
    return paths


async def call(k: Kernel, name: str, /, **args: Any) -> Any:
    result = await k.dispatcher.call(name, args)
    assert result.ok, result.error
    return result.output


async def fails(k: Kernel, name: str, /, **args: Any) -> str:
    result = await k.dispatcher.call(name, args)
    assert not result.ok, result.output
    return str(result.error)


def instance(k: Kernel, name: str) -> Any:
    record = k.modules.records[name]
    assert record.instance is not None, record.error
    return record.instance


# -- apps --------------------------------------------------------------------------------


class FakeDesktop:
    name = "fake"

    def __init__(self) -> None:
        from types import SimpleNamespace

        self.started: list[str] = []
        self.opened: list[str] = []
        self.actions: list[tuple[str, str]] = []
        self.ns = SimpleNamespace

    def programs(self, extra: list[Path]) -> list[Any]:
        from starlight_modules.apps.backends import Program  # type: ignore[import-not-found]

        return [
            Program(n, f"/menu/{n}.lnk")
            for n in ("Spotify", "Microsoft Word", "Microsoft Excel", "Steam", "Notepad++")
        ]

    def start(self, program: Any) -> None:
        self.started.append(program.name)

    def open(self, target: str) -> None:
        self.opened.append(target)

    def windows(self) -> list[Any]:
        from starlight_modules.apps.backends import Window  # type: ignore[import-not-found]

        return [
            Window(1, "Inbox - Outlook", "OUTLOOK.EXE"),
            Window(2, "Budget.xlsx - Excel", "EXCEL.EXE"),
            Window(3, "Spotify Premium", "Spotify.exe"),
        ]

    def focus(self, window: Any) -> None:
        self.actions.append(("focus", window.title))

    def show(self, window: Any, state: str) -> None:
        self.actions.append((state, window.title))

    def close(self, window: Any) -> None:
        self.actions.append(("close", window.title))


async def test_open_a_program_asks_the_first_time_only(project: Paths) -> None:
    user = User(True)
    async with Kernel(project, ui=user) as k:
        desktop = FakeDesktop()
        instance(k, "apps")._backend = desktop
        assert (await call(k, "apps.open", name="spotify"))["opened"] == "Spotify"
        assert (await call(k, "apps.open", name="Spotify"))["opened"] == "Spotify"
        assert len(user.questions) == 1  # only the first time
        assert desktop.started == ["Spotify", "Spotify"]
        assert (await call(k, "apps.open", name="word"))["opened"] == "Microsoft Word"
        assert "could be" in await fails(k, "apps.open", name="microsoft")
        assert "Did you mean" in await fails(k, "apps.open", name="spotfy")
        await call(k, "apps.forget", name="spotify")
        await call(k, "apps.open", name="spotify")
        assert len(user.questions) == 3  # asks again after forget
        listed = await call(k, "apps.installed", search="micro")
        assert listed["programs"] == ["Microsoft Excel", "Microsoft Word"]


async def test_saying_no_starts_nothing(project: Paths) -> None:
    async with Kernel(project, ui=User(False)) as k:
        desktop = FakeDesktop()
        instance(k, "apps")._backend = desktop
        assert "not confirmed" in await fails(k, "apps.open", name="steam")
        assert desktop.started == []


async def test_files_links_and_windows(project: Paths, documents: Path) -> None:
    user = User(True)
    async with Kernel(project, ui=user) as k:
        desktop = FakeDesktop()
        instance(k, "apps")._backend = desktop
        out = documents / "Starlight"
        out.mkdir(exist_ok=True)
        (out / "letter.docx").write_bytes(b"x")
        (out / "setup.exe").write_bytes(b"MZ")
        await call(k, "apps.open_file", path="letter.docx")  # in the Starlight folder: no ask
        assert user.questions == []
        assert "program or script" in await fails(k, "apps.open_file", path="setup.exe")
        assert "off-limits" in await fails(k, "apps.open_file", path=str(project.config_file))
        await call(k, "apps.open_url", url="https://example.org/a?b=1")
        assert "https://" in await fails(k, "apps.open_url", url="file:///etc/passwd")
        assert "https://" in await fails(k, "apps.open_url", url="javascript:alert(1)")
        assert desktop.opened == [str(out / "letter.docx"), "https://example.org/a?b=1"]

        titles = [w["title"] for w in (await call(k, "apps.windows"))["windows"]]
        assert "Budget.xlsx - Excel" in titles
        await call(k, "apps.focus", title="excel")  # by program name
        await call(k, "apps.minimize", title="spotify")
        assert "no open window" in await fails(k, "apps.focus", title="photoshop")
        assert desktop.actions == [
            ("focus", "Budget.xlsx - Excel"),
            ("minimize", "Spotify Premium"),
        ]
        assert k.dispatcher.get("apps.close").permission.value == "confirm"  # type: ignore[union-attr]


# -- media -------------------------------------------------------------------------------


async def test_media_keys_and_volume_on_windows(project: Paths) -> None:
    async with Kernel(project) as k:
        media = instance(k, "media")
        presses: list[tuple[str, int]] = []
        media._press = lambda key, times=1: presses.append((key, times))
        media._on_windows = lambda: True
        await call(k, "media.play_pause")
        await call(k, "media.next")
        await call(k, "media.volume", change="up")  # default step 10% = 5 presses
        await call(k, "media.volume", change="set", percent=30)
        assert presses == [("play_pause", 1), ("next", 1), ("up", 5), ("down", 50), ("up", 15)]
        assert "0 to 100" in await fails(k, "media.volume", change="set", percent=150)


# -- commands ----------------------------------------------------------------------------


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


async def test_only_listed_commands_run_and_values_cannot_inject(project: Paths) -> None:
    user = User(True, True, True)
    async with Kernel(project, ui=user) as k:
        assert "no command" in await fails(k, "commands.run", name="rm-everything")
        await call(
            k,
            "commands.add",
            name="greet",
            run=[*_python("import sys; print('hello', sys.argv[1])"), "{who}"],
            description="Say hello",
        )
        assert "Add the command 'greet'" in user.questions[0]
        result = await call(k, "commands.run", name="greet", values={"who": "Paul; rm -rf /"})
        assert result["ok"] and result["output"].strip() == "hello Paul; rm -rf /"  # one argument
        assert "Run 'greet'" in user.questions[1]
        assert "needs: who" in await fails(k, "commands.run", name="greet")
        assert "not allowed" in await fails(
            k, "commands.run", name="greet", values={"who": "--evil-option"}
        )
        listed = (await call(k, "commands.list"))["commands"]
        assert listed == [
            {"name": "greet", "description": "Say hello", "needs": ["who"], "asks_first": True}
        ]


async def test_a_command_that_hangs_is_stopped(project: Paths) -> None:
    async with Kernel(project, ui=User(True)) as k:
        await call(k, "commands.add", name="slow", run=_python("import time; time.sleep(30)"),
                   ask=False, timeout=1)  # fmt: skip
        result = await call(k, "commands.run", name="slow")
    assert result["timed_out"] and not result["ok"]


async def test_declining_to_add_a_command_saves_nothing(project: Paths) -> None:
    async with Kernel(project, ui=User(False)) as k:
        await fails(k, "commands.add", name="x", run=["echo", "hi"])
        assert (await call(k, "commands.list"))["commands"] == []
    assert not (project.module_configs / "commands.yaml").exists()


# -- office ------------------------------------------------------------------------------


async def test_word_and_excel_round_trip(project: Paths, documents: Path) -> None:
    async with Kernel(project, ui=User()) as k:
        saved = await call(
            k,
            "office.write_docx",
            name="Plan",
            title="Holiday plan",
            content="# Days\nWe go to the sea.\n\n- swim\n- read\n## Budget\nAbout 900 EUR.",
        )
        assert saved["saved"].endswith("Plan.docx")
        text = (await call(k, "office.read_docx", path="Plan.docx"))["text"]
        assert "# Holiday plan" in text and "# Days" in text and "swim" in text
        assert "already exists" in await fails(k, "office.write_docx", name="Plan", content="x")

        rows = [["Item", "Price"], ["Milk", 1.29], ['=HYPERLINK("evil")', "+43 664 1234567"]]
        await call(k, "office.write_xlsx", name="shopping", rows=rows)
        await call(k, "office.append_rows", path="shopping.xlsx", rows=[["Bread", 2.5]])
        sheet = await call(k, "office.read_xlsx", path="shopping.xlsx")
        assert sheet["rows"][0] == ["Item", "Price"] and sheet["rows"][-1] == ["Bread", 2.5]
        # Kept as text, not a formula (a formula would read back as None: no cached value),
        # and text starting with + is saved as written.
        assert sheet["rows"][2] == ['=HYPERLINK("evil")', "+43 664 1234567"]
        assert sheet["total_rows"] == 4


async def test_writing_outside_the_starlight_folder_asks(
    project: Paths, tmp_path_factory: pytest.TempPathFactory
) -> None:
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    user = User(False)
    async with Kernel(project, ui=user) as k:
        assert "not confirmed" in await fails(
            k, "office.write_xlsx", name=str(elsewhere / "x.xlsx"), rows=[[1]]
        )
    assert user.questions and not (elsewhere / "x.xlsx").exists()


# -- dev ---------------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_git_status_log_and_commit(
    project: Paths, tmp_path_factory: pytest.TempPathFactory
) -> None:
    repo = tmp_path_factory.mktemp("repo")

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.org")
    git("config", "user.name", "Test")
    (repo / "a.txt").write_text("one\n")
    git("add", "a.txt")
    git("commit", "-q", "-m", "first")
    (repo / "a.txt").write_text("two\n")
    (project.module_configs / "dev.yaml").write_text(f"repos: {{mine: '{repo.as_posix()}'}}\n")
    async with Kernel(project, ui=User(True)) as k:
        status = await call(k, "dev.git_status", repo="mine")
        assert status["branch"].startswith("main") and status["changes"] == [
            {"state": "M", "file": "a.txt"}
        ]
        assert "+two" in (await call(k, "dev.git_diff", repo="mine"))["diff"]
        await call(k, "dev.git_commit", repo="mine", message="second", all_changes=True)
        log = (await call(k, "dev.git_log", repo="mine"))["commits"]
        assert [c["message"] for c in log] == ["second", "first"]
        assert (await call(k, "dev.git_status", repo="mine"))["clean"]
        own = await fails(k, "dev.git_commit", repo=str(project.root), message="x")
        assert "own folder" in own or "not a git" in own.lower()


async def test_ports_shows_who_listens(project: Paths) -> None:
    pytest.importorskip("psutil")
    import socket

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    try:
        async with Kernel(project) as k:
            result = await k.dispatcher.call("dev.ports", {"port": port})
    finally:
        server.close()
    if not result.ok:  # some systems do not let a normal user read the port list
        pytest.skip(str(result.error))
    entry = result.output["listening"][0]
    assert entry["port"] == port and entry["reachable_from"] == "this PC only"


# -- research ----------------------------------------------------------------------------


class Researcher:
    """Plans two searches, then writes a report citing source [1]."""

    name = "fake"

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelResponse:
        system = messages[0].content
        if "plan web searches" in system:
            return ModelResponse(content='Here: ["solar panel cost 2026", "solar payback time"]')
        assert "[1]" in messages[-1].content and "Panels cost" in messages[-1].content
        return ModelResponse(content="Panels pay for themselves in about 9 years [1].")


async def test_research_reads_pages_and_cites_them(project: Paths, documents: Path) -> None:
    async with Kernel(project, provider=Researcher()) as k:
        web = instance(k, "web")
        searched: list[str] = []

        async def search(query: str, max_results: int = 8) -> list[dict[str, str]]:
            searched.append(query)
            return [
                {"title": "Solar guide", "url": "https://solar.example/guide", "snippet": ""},
                {"title": "Tiny page", "url": "https://tiny.example/", "snippet": ""},
            ]

        async def fetch_many(urls: list[str], max_chars_each: int = 2000) -> list[dict[str, Any]]:
            pages = {
                "https://solar.example/guide": "Panels cost about 6000 EUR. " * 20,
                "https://tiny.example/": "too short",
            }
            return [{"url": u, "title": "", "text": pages[u]} for u in urls]

        k.dispatcher.get("web.search").func = search  # type: ignore[union-attr]
        k.dispatcher.get("web.fetch_many").func = fetch_many  # type: ignore[union-attr]
        del web
        out = await call(k, "research.research", question="Are solar panels worth it?",
                         depth="deep", save=True)  # fmt: skip
    assert searched[0] == "Are solar panels worth it?" and "solar payback time" in searched
    assert out["report"].endswith("[1].")
    assert out["sources"] == [
        {"n": 1, "title": "Solar guide", "url": "https://solar.example/guide"}
    ]
    saved = Path(out["saved"])
    assert saved.parent == documents / "Starlight" / "Research"
    assert "1. [Solar guide](https://solar.example/guide)" in saved.read_text()
