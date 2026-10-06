"""Terminal input: a paste is one request, and only a deliberate answer answers a question.

Found in real use: a list of pasted paths became one request per line, and one of those
lines was taken as the answer to "Allow memory.forget? [y]es / [n]o".
"""

from __future__ import annotations

import os
import select
import subprocess
import sys
import textwrap
import time

import pytest

from tests.conftest import REPO

CHILD = textwrap.dedent(
    """
    import time
    from starlight import console

    print("READY", flush=True)
    request = console.read_request("you> ")
    print("REQUEST", repr(request), flush=True)
    print("WAIT", flush=True)
    time.sleep(0.6)  # the user types "y" before any question is on screen
    answer = console.answer("Allow? ")
    print("ANSWER", repr(answer), flush=True)
    """
)


class Terminal:
    """A child process on a pseudo-terminal, driven like a person at the keyboard."""

    def __init__(self, code: str) -> None:
        import pty

        self.master, slave = pty.openpty()
        self.process = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=REPO,
            close_fds=True,
        )
        os.close(slave)
        self.output = ""

    def wait_for(self, text: str, timeout: float = 20) -> None:
        deadline = time.monotonic() + timeout
        while text not in self.output:
            left = deadline - time.monotonic()
            if left <= 0:
                raise AssertionError(f"never saw {text!r}; output so far:\n{self.output}")
            ready, _, _ = select.select([self.master], [], [], left)
            if ready:
                try:
                    self.output += os.read(self.master, 4096).decode(errors="replace")
                except OSError:  # the child closed the terminal
                    break

    def type(self, text: str) -> None:
        os.write(self.master, text.encode())

    def close(self) -> None:
        self.process.kill()
        self.process.wait()
        os.close(self.master)


@pytest.mark.skipif(sys.platform == "win32", reason="pseudo-terminals are POSIX")
def test_a_paste_is_one_request_and_typeahead_never_answers_a_question() -> None:
    term = Terminal(CHILD)
    try:
        term.wait_for("you> ")
        term.type("first line\nsecond line\nthird line\n")  # pasted in one go
        term.wait_for("WAIT")
        term.type("y\n")  # typed ahead, before the question appears
        term.wait_for("Allow? ")
        term.type("n\n")  # the real answer
        term.wait_for("ANSWER")
    finally:
        term.close()
    assert "REQUEST 'first line\\nsecond line\\nthird line'" in term.output
    assert "Text typed before this question was ignored" in term.output
    assert "ANSWER 'n'" in term.output


def test_piped_input_stays_one_line_at_a_time() -> None:
    """Scripts that pipe commands into Starlight keep one request per line."""
    code = (
        "from starlight import console; "
        "print(repr(console.read_request(''))); print(repr(console.read_request('')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        input="first\nsecond\n",
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=60,
    )
    assert result.stdout.splitlines() == ["'first'", "'second'"]
