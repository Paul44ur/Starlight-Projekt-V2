"""Read and set the clipboard.

Useful for "summarize what I copied", "copy that to my clipboard", "put this on my
clipboard so I can paste it". Needs a desktop clipboard (Windows, macOS, or Linux with
wl-clipboard, xclip or xsel); on a server with no desktop it says so clearly.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool

from .backends import Backend, ClipboardUnavailable, detect


class Config(BaseModel):
    max_chars: int = Field(default=100_000, ge=1)  # largest text read or written
    # Clipboard history: what you copied, kept in memory only (never on disk) while
    # Starlight runs. Off unless you turn it on; text that looks like a password is skipped.
    history: bool = False
    history_size: int = Field(default=30, ge=1, le=200)


def looks_secret(text: str) -> bool:
    """A password or key someone copied: one word, 8 to 128 characters, with letters and
    digits or symbols mixed. Kept out of the history."""
    word = text.strip()
    if not 8 <= len(word) <= 128 or any(c.isspace() for c in word):
        return False
    kinds = (any(c.isalpha() for c in word), any(c.isdigit() for c in word),
             any(not c.isalnum() for c in word))  # fmt: skip
    return sum(kinds) >= 2 and not word.startswith(("http://", "https://", "www."))


class ClipboardModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.backend: Backend | None = detect()
        self.copied: list[str] = []  # the history, newest first (memory only)
        ctx.logger.info("ready", backend=self.backend.name if self.backend else "none")
        if self.config.history and self.backend is not None:
            ctx.every(2, self._sample, name="clipboard-history", first_delay=2)

    async def _sample(self) -> None:
        """Note what is on the clipboard now, when it is new."""
        try:  # (xclip and friends are programs: not run on the loop that answers the chat)
            text = await asyncio.to_thread(self._backend().get)
        except (ClipboardUnavailable, ToolError):
            return
        if not text.strip() or len(text) > self.config.max_chars or looks_secret(text):
            return
        if self.copied and self.copied[0] == text:
            return
        self.copied = [text, *(t for t in self.copied if t != text)][: self.config.history_size]

    def _backend(self) -> Backend:
        if self.backend is None:
            raise ToolError(
                "no clipboard is available on this computer (needs a desktop; on Linux "
                "install wl-clipboard, xclip or xsel)"
            )
        return self.backend

    @tool
    def read(self) -> dict[str, Any]:
        """Read the text currently on the clipboard."""
        try:
            text = self._backend().get()
        except ClipboardUnavailable as exc:
            raise ToolError(str(exc)) from exc
        cut = len(text) > self.config.max_chars
        result: dict[str, Any] = {"text": text[: self.config.max_chars], "length": len(text)}
        if not text.strip():
            result["note"] = "the clipboard is empty"
        if cut:
            result["note"] = f"only the first {self.config.max_chars} characters"
        return result

    @tool
    def write(self, text: str) -> dict[str, Any]:
        """Put text on the clipboard, so the user can paste it anywhere."""
        if len(text) > self.config.max_chars:
            raise ToolError(f"too long (limit {self.config.max_chars} characters)")
        try:
            self._backend().set(text)
        except ClipboardUnavailable as exc:
            raise ToolError(str(exc)) from exc
        self.ctx.logger.info("clipboard set", chars=len(text))
        return {"copied": True, "length": len(text)}

    @tool
    def clear(self) -> dict[str, Any]:
        """Empty the clipboard."""
        try:
            self._backend().set("")
        except ClipboardUnavailable as exc:
            raise ToolError(str(exc)) from exc
        return {"cleared": True}

    @tool
    def history(self, count: int = 10) -> dict[str, Any]:
        """What you copied recently, newest first (1 = the latest), if the clipboard history
        is on (history: true in config/modules/clipboard.yaml)."""
        if not self.config.history:
            raise ToolError(
                "the clipboard history is off; turn it on with history: true in "
                "config/modules/clipboard.yaml (it stays in memory only)"
            )
        shown = [
            {"n": i, "text": t[:200] + ("..." if len(t) > 200 else ""), "length": len(t)}
            for i, t in enumerate(self.copied[: max(1, count)], 1)
        ]
        return {"count": len(self.copied), "copied": shown}

    @tool
    def copy_again(self, n: int) -> dict[str, Any]:
        """Put an earlier copied text (its number in clipboard.history) back on the
        clipboard, ready to paste."""
        if not 1 <= n <= len(self.copied):
            raise ToolError(f"the history has {len(self.copied)} entries")
        return self.write(self.copied[n - 1])

    @tool
    def forget_history(self) -> dict[str, Any]:
        """Empty the clipboard history."""
        count, self.copied = len(self.copied), []
        return {"forgotten": count}
