"""Translate text between languages with your own model - offline, nothing leaves the PC.

Uses the model set for the "translate" role (``roles:`` in starlight.yaml) or the main
model. Long texts are translated paragraph by paragraph in parts, so nothing is cut off.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Message, Module, ModuleContext, ToolError, tool

SYSTEM = (
    "You are a professional translator. Translate the user's text into {target}{source}. "
    "Keep the meaning, tone, formatting, line breaks, names, numbers and code exactly. "
    "Reply with the translation only - no notes, no quotes, no explanations."
)
DETECT = (
    "Which language is the user's text written in? Reply with only the English name of "
    "the language, e.g. 'German'."
)


class Config(BaseModel):
    default_language: str = "English"
    max_chars: int = Field(default=12000, ge=500)


class TranslateModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    async def _ask(self, system: str, text: str) -> str:
        model = self.ctx.model_for("translate")
        if model is None:
            raise ToolError("no model is configured")
        response = await model.chat([Message("system", system), Message("user", text)], [])
        return response.content.strip()

    @tool
    async def translate(self, text: str, to: str = "", source: str = "") -> dict[str, Any]:
        """Translate `text` into the language `to` (e.g. "German", "English", "French";
        default: English). `source` is optional - the language it is written in."""
        if not text.strip():
            raise ToolError("give the text to translate")
        target = to.strip() or self.config.default_language
        system = SYSTEM.format(
            target=target, source=f" from {source.strip()}" if source.strip() else ""
        )
        parts = [await self._ask(system, part) for part in split(text, self.config.max_chars)]
        return {"to": target, "translation": "\n\n".join(p for p in parts if p)}

    @tool
    async def detect(self, text: str) -> dict[str, Any]:
        """Which language a text is written in."""
        if not text.strip():
            raise ToolError("give some text")
        answer = await self._ask(DETECT, text[:2000])
        return {"language": answer.strip(" .'\"\n").split("\n")[0]}


def split(text: str, limit: int) -> list[str]:
    """Paragraph-sized parts of at most about ``limit`` characters."""
    parts: list[str] = []
    current = ""
    for paragraph in text.strip().split("\n\n"):
        rest = paragraph
        if current and len(rest) > limit:  # keep the order: earlier paragraphs go first
            parts.append(current)
            current = ""
        while len(rest) > limit:  # one huge paragraph: cut at a sentence end
            cut = rest.rfind(". ", 0, limit)
            cut = cut + 1 if cut > limit // 2 else limit
            parts.append(rest[:cut].strip())
            rest = rest[cut:].strip()
        if current and len(current) + len(rest) + 2 > limit:
            parts.append(current)
            current = ""
        current = f"{current}\n\n{rest}" if current else rest
    if current:
        parts.append(current)
    return parts
