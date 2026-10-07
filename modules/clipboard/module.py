"""Read and set the clipboard.

Useful for "summarize what I copied", "copy that to my clipboard", "put this on my
clipboard so I can paste it". Needs a desktop clipboard (Windows, macOS, or Linux with
wl-clipboard, xclip or xsel); on a server with no desktop it says so clearly.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool

from .backends import Backend, ClipboardUnavailable, detect


class Config(BaseModel):
    max_chars: int = Field(default=100_000, ge=1)  # largest text read or written


class ClipboardModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.backend: Backend | None = detect()
        ctx.logger.info("ready", backend=self.backend.name if self.backend else "none")

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
