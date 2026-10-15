"""How the core and modules talk to you while a task runs: confirmations, questions
(for example a 2FA code) and notices. The CLI provides a terminal version; WhatsApp
can provide another."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Protocol


class UserIO(Protocol):
    async def confirm(self, question: str) -> bool: ...
    async def ask(self, question: str, secret: bool = False) -> str | None: ...
    async def notify(self, text: str) -> None: ...


class NoUser:
    """Nobody is there to answer: deny every confirmation, answer no question."""

    async def confirm(self, question: str) -> bool:
        return False

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


#: The user behind the request being handled right now (terminal, WhatsApp ...).
current_ui: ContextVar[UserIO | None] = ContextVar("current_ui", default=None)


class ContextualUI:
    """Asks whoever sent the current request; falls back to the default interface."""

    def __init__(self, default: UserIO | None = None) -> None:
        self.default: UserIO = default or NoUser()

    @property
    def target(self) -> UserIO:
        return current_ui.get() or self.default

    async def confirm(self, question: str) -> bool:
        return await self.target.confirm(question)

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return await self.target.ask(question, secret)

    async def notify(self, text: str) -> None:
        await self.target.notify(text)
