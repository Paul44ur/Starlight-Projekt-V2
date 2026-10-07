"""The email module: setup stores the app-password in the vault (not on disk), reading is
free, sending asks first. The IMAP/SMTP calls are stubbed so no real server is needed."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.core.config import read_yaml
from starlight.core.vault import MemoryBackend


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
    use_module("mail")
    return paths


def kernel(paths: Paths, user: User) -> Kernel:
    return Kernel(paths, ui=user, vault_backend=MemoryBackend())


def mod(k: Kernel) -> Any:
    instance = k.modules.records["mail"].instance
    assert instance is not None
    return instance


def _configure(k: Kernel) -> None:
    module = mod(k)
    module.config.username = "me@web.de"
    module.config.imap_host = "imap.web.de"
    module.config.smtp_host = "smtp.web.de"
    module.ctx.vault.set("mail.password", "app-pw")


async def test_inbox_needs_setup(project: Paths) -> None:
    async with kernel(project, User()) as k:
        result = await k.dispatcher.call("mail.inbox", {})
    assert not result.ok and "not set up" in str(result.error)


async def test_setup_stores_password_in_vault_not_on_disk(project: Paths) -> None:
    user = User(True)  # confirms the save
    async with kernel(project, user) as k:
        result = await k.dispatcher.call(
            "mail.configure",
            {
                "username": "me@web.de",
                "password": "secret-app-pw",
                "imap_host": "imap.web.de",
                "smtp_host": "smtp.web.de",
            },
        )
        assert result.ok and result.output["configured"]
        assert mod(k).ctx.vault.get("mail.password") == "secret-app-pw"
    saved = read_yaml(project.module_configs / "mail.yaml")
    assert saved["username"] == "me@web.de" and saved["imap_host"] == "imap.web.de"
    assert "secret-app-pw" not in (project.module_configs / "mail.yaml").read_text()


async def test_inbox_lists_messages(project: Paths) -> None:
    async with kernel(project, User()) as k:
        _configure(k)
        mod(k)._imap_list = lambda password, count: [
            {"id": "5", "from": "Boss <boss@x.com>", "subject": "Hi", "date": "today"}
        ]
        result = await k.dispatcher.call("mail.inbox", {"limit": 5})
    assert result.output["count"] == 1
    assert result.output["messages"][0]["subject"] == "Hi"


async def test_send_asks_first(project: Paths) -> None:
    async with kernel(project, User(False)) as k:  # denies the send
        _configure(k)
        sent: list[Any] = []
        mod(k)._smtp_send = lambda *a: sent.append(a)
        result = await k.dispatcher.call(
            "mail.send", {"to": "x@y.com", "subject": "Hey", "body": "hello"}
        )
    assert not result.ok and "not confirmed" in str(result.error)
    assert sent == []  # nothing was sent


async def test_send_when_confirmed(project: Paths) -> None:
    async with kernel(project, User(True)) as k:  # approves the send
        _configure(k)
        sent: list[Any] = []
        mod(k)._smtp_send = lambda password, to, subject, body: sent.append((to, subject, body))
        result = await k.dispatcher.call(
            "mail.send", {"to": "x@y.com", "subject": "Hey", "body": "hello"}
        )
    assert result.ok and result.output["sent"]
    assert sent == [("x@y.com", "Hey", "hello")]
