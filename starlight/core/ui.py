"""How the core and modules talk to you while a task runs: confirmations, questions
(for example a 2FA code) and notices. The CLI provides a terminal version; WhatsApp
(Step 10) can provide another."""

from __future__ import annotations

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
