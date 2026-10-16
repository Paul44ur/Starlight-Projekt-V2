"""Saved conversations: each one a JSON file in data/conversations/, so a chat survives a
restart and the web chat can list, reopen, rename and delete them. Pictures sent in a
conversation are not saved (only a note that there was one)."""

from __future__ import annotations

import contextlib
import json
import re
import threading
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from starlight.core.fsutil import write_atomic
from starlight.core.model import Message, ToolCall

ID = re.compile(r"^[a-f0-9]{12}$")
CURRENT = "current.txt"


def _now() -> str:
    # to the microsecond: conversations saved within the same second still sort correctly
    return datetime.now().isoformat(timespec="microseconds")


def to_dict(message: Message) -> dict[str, Any]:
    data: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        data["tool_calls"] = [asdict(c) for c in message.tool_calls]
    if message.tool_call_id:
        data["tool_call_id"] = message.tool_call_id
    if message.name:
        data["name"] = message.name
    if message.images:
        data["images"] = len(message.images)  # how many there were; the pixels stay out
    return data


def from_dict(data: dict[str, Any]) -> Message:
    return Message(
        role=data.get("role", "user"),
        content=str(data.get("content", "")),
        tool_calls=[
            ToolCall(
                c.get("name", ""), c.get("arguments") or {}, c.get("id") or uuid.uuid4().hex[:12]
            )
            for c in data.get("tool_calls") or []
        ],
        tool_call_id=data.get("tool_call_id"),
        name=data.get("name"),
    )


class ConversationStore:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._lock = threading.Lock()
        # What the sidebar shows per file, kept until the file changes (see ``list``).
        self._summaries: dict[Path, tuple[tuple[int, int, int], dict[str, Any]]] = {}

    def _file(self, conversation: str) -> Path:
        if not ID.match(conversation):
            raise KeyError(f"no conversation '{conversation}'")
        return self.folder / f"{conversation}.json"

    def _read(self, conversation: str) -> dict[str, Any]:
        try:
            data = json.loads(self._file(conversation).read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KeyError(f"no conversation '{conversation}'") from exc
        return data if isinstance(data, dict) else {}

    def _write(self, conversation: str, data: dict[str, Any]) -> None:
        target = self._file(conversation)
        with self._lock:
            write_atomic(target, json.dumps(data, ensure_ascii=False, indent=1))

    # -- the conversation in use ------------------------------------------------------------

    @property
    def current(self) -> str:
        """The conversation the terminal and a fresh browser page continue."""
        with contextlib.suppress(OSError):
            found = (self.folder / CURRENT).read_text("utf-8").strip()
            if ID.match(found) and self._file(found).exists():
                return found
        return self.create()

    def set_current(self, conversation: str) -> None:
        self._file(conversation)  # validates
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / CURRENT).write_text(conversation, encoding="utf-8")

    # -- operations -----------------------------------------------------------------------

    def create(self, title: str = "") -> str:
        conversation = uuid.uuid4().hex[:12]
        self._write(
            conversation,
            {
                "id": conversation,
                "title": title.strip(),
                "created": _now(),
                "updated": _now(),
                "messages": [],
            },
        )
        self.set_current(conversation)
        return conversation

    def stamp(self, conversation: str) -> tuple[int, int] | None:
        """Changes whenever the file does, e.g. when another Starlight (the terminal and
        the web chat as separate programs) saved a turn to it."""
        try:
            info = self._file(conversation).stat()
        except (OSError, KeyError):
            return None
        return info.st_mtime_ns, info.st_size

    def messages(self, conversation: str) -> list[Message]:
        return [from_dict(m) for m in self._read(conversation).get("messages", [])]

    def save(self, conversation: str, messages: list[Message]) -> None:
        data = self._read(conversation)
        data["messages"] = [to_dict(m) for m in messages]
        data["updated"] = _now()
        if not data.get("title"):
            first = next((m.content for m in messages if m.role == "user" and m.content), "")
            data["title"] = " ".join(first.split())[:60]
        self._write(conversation, data)

    def rename(self, conversation: str, title: str) -> None:
        data = self._read(conversation)
        data["title"] = " ".join(title.split())[:80]
        self._write(conversation, data)

    def delete(self, conversation: str) -> None:
        self._file(conversation).unlink(missing_ok=False)

    def get(self, conversation: str) -> dict[str, Any]:
        data = self._read(conversation)
        return {
            "id": conversation,
            "title": data.get("title") or "New conversation",
            "created": data.get("created"),
            "updated": data.get("updated"),
            "messages": [
                m
                for m in data.get("messages", [])
                if m.get("role") in ("user", "assistant") and m.get("content")
            ],
        }

    def markdown(self, conversation: str) -> str:
        """The conversation as a Markdown document (your messages and the answers)."""
        data = self.get(conversation)
        lines = [f"# {data['title']}", ""]
        if data.get("created"):
            lines += [f"*{str(data['created'])[:16].replace('T', ' ')}*", ""]
        for message in data["messages"]:
            who = "You" if message["role"] == "user" else "Starlight"
            lines += [f"**{who}:**", "", str(message["content"]).strip(), ""]
        return "\n".join(lines).rstrip() + "\n"

    def list(self, search: str = "", limit: int = 200) -> list[dict[str, Any]]:
        """The conversations, newest first. A file that did not change since the last call
        is not read again (unless searching, which looks into the messages): the page asks
        after every answer. Another program may write the files, so the key is the file's own
        time, size and inode, as in ``stamp``."""
        wanted = search.strip().casefold()
        found: list[dict[str, Any]] = []
        if not self.folder.is_dir():
            return found
        known: dict[Path, tuple[tuple[int, int, int], dict[str, Any]]] = {}
        for path in self.folder.glob("*.json"):
            try:
                info = path.stat()
                key = (info.st_mtime_ns, info.st_size, info.st_ino)
                cached = self._summaries.get(path)
                if cached is not None and cached[0] == key and not wanted:
                    known[path] = cached
                    found.append(dict(cached[1]))
                    continue
                data = json.loads(path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            messages = data.get("messages", [])
            summary = {
                "id": path.stem,
                "title": data.get("title") or "New conversation",
                "updated": data.get("updated") or "",
                "count": sum(1 for m in messages if m.get("role") == "user"),
            }
            known[path] = (key, summary)
            if (
                wanted
                and wanted not in (data.get("title") or "").casefold()
                and not any(wanted in str(m.get("content", "")).casefold() for m in messages)
            ):
                continue
            found.append(dict(summary))
        self._summaries = known
        found.sort(key=lambda c: c["updated"], reverse=True)
        return found[:limit]
