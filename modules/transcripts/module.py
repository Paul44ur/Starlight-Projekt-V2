"""YouTube videos without watching them: get the transcript (the video's captions), a short
summary, or an answer to a question about it. Needs the 'transcripts' extra
(youtube-transcript-api); only videos with captions work.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Message, Module, ModuleContext, ToolError, tool

# A YouTube video id is 11 characters of A-Z, a-z, 0-9, "_" and "-": found after the usual
# URL markers (watch?v=, /shorts/, youtu.be/ ...) or given on its own.
VIDEO_ID = re.compile(
    r"(?:v=|/shorts/|/embed/|/live/|youtu\.be/|/v/)(?P<in_url>[A-Za-z0-9_-]{11})"
    r"|^(?P<bare>[A-Za-z0-9_-]{11})$"
)
SUMMARY = (
    "Summarize this video transcript for the user in their language ({language}): first one "
    "sentence on what it is about, then the key points as a short list. Mention timestamps "
    "[mm:ss] for the important moments."
)
QUESTION = (
    "Answer the user's question using only this video transcript. Quote the timestamp "
    "[mm:ss] where the answer is said. If the video does not answer it, say so."
)


class Config(BaseModel):
    languages: list[str] = Field(default_factory=lambda: ["de", "en"])
    max_chars: int = Field(default=60000, ge=2000)


def video_id(url: str) -> str:
    match = VIDEO_ID.search(url.strip())
    if not match:
        raise ToolError("give a YouTube link (youtube.com/watch?v=..., youtu.be/...)")
    return match.group("in_url") or match.group("bare")


def _clock(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    return (
        f"{hours}:{rest // 60:02d}:{rest % 60:02d}"
        if hours
        else f"{rest // 60:02d}:{rest % 60:02d}"
    )


class TranscriptsModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    def _download(self, vid: str) -> tuple[str, list[tuple[float, str]]]:
        """(language, [(start seconds, text)]) - blocking, run in a thread."""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            from youtube_transcript_api._errors import YouTubeTranscriptApiException
        except ImportError as exc:
            raise ToolError(
                "this needs the 'transcripts' extra (youtube-transcript-api). "
                "Install it to use this."
            ) from exc
        try:
            fetched = YouTubeTranscriptApi().fetch(vid, languages=self.config.languages)
        except YouTubeTranscriptApiException as exc:
            reason = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            raise ToolError(f"no transcript for this video: {reason}") from exc
        lines = [(float(s.start), s.text.replace("\n", " ").strip()) for s in fetched]
        return str(getattr(fetched, "language_code", "")), [line for line in lines if line[1]]

    async def _transcript(self, url: str) -> tuple[str, str, str]:
        """(video id, language, text with [mm:ss] marks)."""
        self.ctx.kill_switch.check()
        vid = video_id(url)
        language, lines = await asyncio.to_thread(self._download, vid)
        parts: list[str] = []
        last_mark = -60.0  # long enough ago that the first line gets a mark
        for start, text in lines:
            if start - last_mark >= 30:  # a time mark every half minute
                parts.append(f"\n[{_clock(start)}] ")
                last_mark = start
            parts.append(text + " ")
        text = "".join(parts).strip()
        if len(text) > self.config.max_chars:
            text = text[: self.config.max_chars] + " …(shortened)"
        return vid, language, text

    async def _model(self, system: str, text: str) -> str:
        model = self.ctx.model_for("summary")
        if model is None:
            raise ToolError("no model is configured")
        reply = await model.chat([Message("system", system), Message("user", text)], [])
        return reply.content.strip()

    @tool
    async def youtube(self, url: str) -> dict[str, Any]:
        """The transcript of a YouTube video, with [mm:ss] time marks."""
        vid, language, text = await self._transcript(url)
        return {"video": vid, "language": language, "transcript": text}

    @tool
    async def summarize(
        self, url: str, language: str = "the language of the request"
    ) -> dict[str, Any]:
        """A short summary of a YouTube video with the key points and their times."""
        vid, _, text = await self._transcript(url)
        summary = await self._model(SUMMARY.format(language=language), text)
        return {"video": vid, "summary": summary}

    @tool
    async def ask(self, url: str, question: str) -> dict[str, Any]:
        """Answer a question about a YouTube video from what is said in it."""
        if not question.strip():
            raise ToolError("what do you want to know about the video?")
        vid, _, text = await self._transcript(url)
        answer = await self._model(QUESTION, f"Question: {question}\n\nTranscript:\n{text}")
        return {"video": vid, "answer": answer}
