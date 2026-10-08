"""Fixes from a bug check: a request too long for the model no longer breaks the
conversation, mail headers and bodies that used to crash or vanish, scripts are never
saved silently outside the Starlight folder, the profile keeps new preferences, uploads in
the old Windows encoding keep their umlauts, and shell output in the console code page."""

from __future__ import annotations

from collections.abc import Callable
from email import message_from_bytes
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.core import Kernel, Paths
from starlight.core.config import ModelConfig
from starlight.core.context import ContextManager
from starlight.core.model import Message, ModelResponse


class TooLong:
    """A model server that turns every request down like llama-server does when the prompt
    is longer than its context."""

    name = "llamacpp"

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> Any:
        request = httpx.Request("POST", "http://127.0.0.1:8080/v1/chat/completions")
        raise httpx.HTTPStatusError(
            "400 Bad Request", request=request, response=httpx.Response(400, request=request)
        )


async def test_a_request_the_model_refuses_is_not_kept(paths: Paths) -> None:
    async with Kernel(paths, provider=TooLong()) as k:
        result = await k.ask("x" * 1000, keep_history=True)
        assert result.failed and "too long" in result.answer
        assert k.history == []  # retrying in the same conversation works


async def test_one_huge_message_is_cut_to_fit_the_context() -> None:
    class Model:
        name = "m"

        async def chat(self, messages: list[Message], tools: Any) -> ModelResponse:
            return ModelResponse(content="summary")

    config = ModelConfig(context_window=4096, reserve_tokens=1024)
    context = ContextManager(Model(), config)  # type: ignore[arg-type]
    messages = [Message("system", "You are Starlight."), Message("user", "y" * 60_000)]
    fitted = await context.fit(messages, [])
    assert len(fitted[1].content) < 12_000
    assert "too long for the model's context window" in fitted[1].content
    assert messages[1].content == "y" * 60_000  # nothing changed in place


def mail_module() -> Any:
    import sys

    sys.path.append(str(Path(__file__).resolve().parent.parent / "modules"))
    try:
        import mail.module as m  # type: ignore[import-not-found]
    finally:
        sys.path.pop()
    return m


def test_mail_headers_and_bodies() -> None:
    m = mail_module()
    assert m._decode("A long subject that was\r\n folded") == "A long subject that was folded"
    odd = message_from_bytes(b"Content-Type: text/plain; charset=unknown-8bit\r\n\r\nHallo")
    assert m._body(odd) == "Hallo"
    html = message_from_bytes(
        b"MIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary=x\r\n\r\n"
        b"--x\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><body><p>Hello <b>you</b></p></body></html>\r\n--x--\r\n"
    )
    assert "Hello" in m._body(html) and "you" in m._body(html)


class Yes:
    def __init__(self) -> None:
        self.questions: list[str] = []

    async def confirm(self, question: str) -> bool:
        self.questions.append(question)
        return True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


async def test_scripts_outside_the_starlight_folder_still_ask(
    paths: Paths, use_module: Callable[[str], Path], tmp_path_factory: pytest.TempPathFactory
) -> None:
    use_module("files")
    paths.config_file.write_text(
        paths.config_file.read_text() + "output:\n  write_anywhere: true\n"
    )
    startup = tmp_path_factory.mktemp("Startup").resolve()
    user = Yes()
    async with Kernel(paths, ui=user) as k:
        note = await k.dispatcher.call(
            "files.save", {"path": str(startup / "note.txt"), "content": "hi"}
        )
        assert note.ok and user.questions == []
        script = await k.dispatcher.call(
            "files.save", {"path": str(startup / "run.bat"), "content": "@echo off"}
        )
        assert script.ok and len(user.questions) == 1
        assert "Windows can run .bat files" in user.questions[0]


async def test_new_preferences_always_fit(paths: Paths) -> None:
    async with Kernel(paths) as k:
        for i in range(60):
            k.memory._update_profile("preferences", f"preference number {i} about something")
        prefs = k.memory.profile.read()["preferences"]
        assert prefs.endswith("preference number 59 about something")
        assert len(prefs) <= 1000 and "preference number 0 " not in prefs


def test_uploads_in_the_old_windows_encoding() -> None:
    from starlight.web.app import extract_text

    assert extract_text("liste.csv", "Größe;Äpfel\n".encode("cp1252")) == "Größe;Äpfel\n"
    assert extract_text("a.txt", "﻿Über".encode()) == "Über"


def test_shell_output_in_the_console_code_page() -> None:
    import sys

    sys.path.append(str(Path(__file__).resolve().parent.parent / "modules"))
    try:
        from commands.module import decode_output, shell_argv  # type: ignore[import-not-found]
    finally:
        sys.path.pop()
    assert decode_output("Grüße".encode()) == "Grüße"
    assert "�" not in decode_output("Grüße".encode("cp850")) or sys.platform != "win32"
    windows = shell_argv("Get-Date", "win32")
    assert windows[0] == "powershell.exe" and windows[-1].endswith("Get-Date")
    assert "UTF8" in windows[-1]
