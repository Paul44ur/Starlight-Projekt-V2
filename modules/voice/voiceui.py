"""Answering Starlight's questions by voice (docs/plan-overlay.md, section 6, W6).

During a wake-word request the voice module sets ``VoiceUI`` as the current interface, so a
tool that asks first (CONFIRM) is asked out loud instead of being refused. At the same time
the question goes to the overlay as a Yes/No card (``overlay.ask``). The first answer wins:
a click on the card (``overlay.answer``) or a spoken yes or no. The other one is cancelled.
No answer in 20 seconds is "no".
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .module import VoiceModule

#: The dispatcher's fixed question (starlight.core.kernel.question_for), after an optional
#: reason: "Run <tool> with {args}?".
QUESTION = re.compile(r"Run (\S+) with (.*)\?$", re.S)

YES = frozenset({"ja", "jo", "klar", "mach", "yes", "okay", "ok"})
# ("nicht": "mach das nicht" is a no, although "mach" is a yes word.)
NO = frozenset({"nein", "nee", "stopp", "stop", "abbrechen", "no", "cancel", "nicht"})

ASK_SECONDS = 20.0  # no answer in this time: no

CLICK_TOOL = "overlay.click_target"


def heard_answer(text: str) -> bool | None:
    """Yes, no, or None when the words are neither. A no word wins over a yes word."""
    words = re.findall(r"[a-zäöüß]+", text.lower())
    if any(word in NO for word in words):
        return False
    if any(word in YES for word in words):
        return True
    return None


def english(profile: dict[str, str]) -> bool:
    """The profile's "Language" line says English (else German, as the overlay)."""
    return profile.get("language", "").strip().lower().startswith(("en", "engl"))


def spoken_question(tool: str, in_english: bool) -> str:
    if tool == CLICK_TOOL:
        return "Shall I click it?" if in_english else "Soll ich das anklicken?"
    name = tool or "das"
    return f"May I run {name}?" if in_english else f"Darf ich {name} ausführen?"


def choices(in_english: bool) -> list[dict[str, str]]:
    return [
        {"id": "yes", "label": "Yes" if in_english else "Ja"},
        {"id": "no", "label": "No" if in_english else "Nein"},
    ]


class VoiceUI:
    """The interface of one wake-word request: questions are said out loud and answered
    by voice or on the overlay's card."""

    def __init__(
        self,
        voice: VoiceModule,
        next_speech: Callable[[], Awaitable[bytes | None]],
        timeout: float = ASK_SECONDS,
    ) -> None:
        self.voice = voice
        self.next_speech = next_speech
        self.timeout = timeout

    @property
    def _english(self) -> bool:
        return english(self.voice.ctx.profile())

    async def confirm(self, question: str) -> bool:
        ctx = self.voice.ctx
        found = QUESTION.search(question)
        tool, args = (found.group(1), found.group(2)) if found else ("", "")
        in_english = self._english
        spoken = spoken_question(tool, in_english)
        ask_id = uuid.uuid4().hex[:12]
        card: asyncio.Future[str | None] | None = None
        if ctx.has_tool("overlay.point_at"):
            card = asyncio.get_running_loop().create_future()
            self.voice.open_asks[ask_id] = card
            await ctx.publish(
                "overlay.ask",
                {
                    "id": ask_id,
                    "tool": tool,
                    "args": args[:500],
                    "question": spoken,
                    "choices": choices(in_english),
                    "timeout": self.timeout,
                },
            )
        by_voice = asyncio.create_task(self._by_voice(spoken), name="voice-answer")
        answer = False
        answered_by_voice = False
        try:
            async with asyncio.timeout(self.timeout):
                waiting: set[asyncio.Future[Any]] = {by_voice}
                if card is not None:
                    waiting.add(card)
                while waiting:
                    done, pending = await asyncio.wait(waiting, return_when="FIRST_COMPLETED")
                    waiting = set(pending)
                    if card is not None and card in done:
                        answer = card.result() == "yes"  # (None: its timeout, the kill switch)
                        break
                    said = by_voice.result()
                    if said is not None:
                        answer, answered_by_voice = said, True
                        break
                    # Nothing said: only the card can still answer.
        except TimeoutError:
            answer = False
        finally:
            self.voice.open_asks.pop(ask_id, None)
            if not by_voice.done():
                by_voice.cancel()
                if self.voice.speaking:
                    self.voice._stop_playback()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await by_voice
            if card is not None and not card.done():
                await ctx.publish("overlay.cancel_ask", {"id": ask_id})
            await self.voice._set_state("thinking")
        by = "voice" if answered_by_voice else "card or time"
        ctx.logger.info("asked by voice", tool=tool, approved=answer, by=by)
        return answer

    async def _by_voice(self, spoken: str) -> bool | None:
        """Say the question and listen: yes, no, or None when nothing was said. Words that
        are neither: asked once more, then no."""
        from .module import wav_bytes

        try:
            for attempt in range(2):
                await self.voice._say(spoken)
                await self.voice._set_state("listening")
                speech = await self.next_speech()
                if speech is None:
                    return None
                text = await self.voice.transcribe_audio(wav_bytes(speech))
                answer = heard_answer(text)
                if answer is not None:
                    return answer
                if attempt == 0:
                    spoken = "Ja oder nein?" if not self._english else "Yes or no?"
            return False
        except asyncio.CancelledError:
            raise
        except Exception:  # (no voice, speech recognition gone): only the card can answer
            self.voice.ctx.logger.exception("voice answer failed")
            return None

    async def ask(self, question: str, secret: bool = False) -> str | None:
        """Say the question, take what is said next as the answer. Secrets never by voice."""
        from .module import wav_bytes

        if secret:
            return None
        await self.voice._say(question)
        await self.voice._set_state("listening")
        try:
            speech = await self.next_speech()
            if speech is None:
                return None
            return (await self.voice.transcribe_audio(wav_bytes(speech))).strip() or None
        finally:
            await self.voice._set_state("thinking")

    async def notify(self, text: str) -> None:
        from .module import cut

        await self.voice._emit("voice.caption", {"text": cut(text), "who": "starlight"})
        await self.voice._say(text)
