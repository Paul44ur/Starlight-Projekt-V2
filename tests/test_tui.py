"""The chat view: a friendlier terminal when rich and a real terminal are present, and an
unchanged plain fallback for pipes and scripts."""

from __future__ import annotations

import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from starlight.tui import ChatView
from tests.conftest import REPO


def test_plain_fallback_never_crashes(capsys: pytest.CaptureFixture[str]) -> None:
    """Not a terminal (as under pytest): plain output, and confirm defaults safely."""
    view = ChatView(assume_yes=False)
    assert view.rich is False  # pytest captures stdout, so not a real terminal
    view.banner("0.1.0", {"home": "/x", "model": "echo"}, "<ctrl>+<alt>+j")
    view.info("hello")
    view.error("something broke")
    view.answer("A **plain** answer")
    out = capsys.readouterr().out
    assert "Starlight" in out and "plain" in out


async def test_confirm_safely_denies_without_a_terminal() -> None:
    view = ChatView(assume_yes=False)
    assert await view.confirm("do the thing?") is False  # nobody to answer -> no
    yes = ChatView(assume_yes=True)
    assert await yes.confirm("do the thing?") is True  # -y approves


def _minimal_home(root: Path) -> Path:
    """A tiny Starlight project: the echo model, no hotkey, only the hello module - so the
    chat starts fast and loads nothing heavy."""
    import shutil

    (root / "config" / "modules").mkdir(parents=True)
    (root / "config" / "starlight.yaml").write_text(
        "model:\n  provider: echo\nkill_switch:\n  hotkey: null\n", encoding="utf-8"
    )
    shutil.copytree(REPO / "modules" / "hello", root / "modules" / "hello")
    return root


@pytest.mark.skipif(sys.platform == "win32", reason="pseudo-terminals are POSIX")
def test_rich_chat_session_over_a_real_terminal(tmp_path: Path) -> None:
    import pty

    home = _minimal_home(tmp_path / "home")
    master, slave = pty.openpty()
    env = dict(os.environ, TERM="xterm-256color", COLUMNS="100", LINES="40")
    env.pop("STARLIGHT_HOME", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "starlight", "--home", str(home), "run"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        cwd=str(home),
    )
    os.close(slave)
    output = ""

    def wait_for(text: str, timeout: float = 60) -> None:
        nonlocal output
        end = time.monotonic() + timeout
        while text not in output and time.monotonic() < end:
            ready, _, _ = select.select([master], [], [], 0.5)
            if ready:
                try:
                    output += os.read(master, 4096).decode(errors="replace")
                except OSError:
                    break
        assert text in output, f"never saw {text!r} in:\n{output[-2000:]}"

    try:
        wait_for("Starlight")  # the banner rendered
        os.write(master, b'!hello.shout {"text": "hi"}\n')  # a confirm-gated tool
        wait_for("confirm")  # the styled confirm box appeared
        os.write(master, b"n\n")  # deny
        wait_for("you")  # back to the prompt
        os.write(master, b"/quit\n")
        with __import__("contextlib").suppress(subprocess.TimeoutExpired):
            process.wait(timeout=30)  # clean exit if it is quick; otherwise killed below
    finally:
        process.kill()
        process.wait()
        os.close(master)
    # the banner showed the model, and the confirm box and prompt rendered with rich
    assert "echo" in output
