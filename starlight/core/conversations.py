"""Saved conversations: each one a JSON file in data/conversations/, so a chat survives a
restart and the web chat can list, reopen, rename and delete them. Pictures sent in a
conversation are not saved (only a note that there was one).

A private conversation is never written to disk: it lives in this program's memory until
you delete it or Starlight stops, and nothing is learned from it (the kernel)."""

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
        self._private: dict[str, dict[str, Any]] = {}  # id -> its data, only in memory
        self._private_changes: dict[str, int] = {}  # its stamp: one more on every save

    def is_private(self, conversation: str) -> bool:
        return conversation in self._private

    def _file(self, conversation: str) -> Path:
        if not ID.match(conversation):
            raise KeyError(f"no conversation '{conversation}'")
        return self.folder / f"{conversation}.json"

    def _read(self, conversation: str) -> dict[str, Any]:
        if conversation in self._private:
            return dict(self._private[conversation])
        try:
            data = json.loads(self._file(conversation).read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KeyError(f"no conversation '{conversation}'") from exc
        return data if isinstance(data, dict) else {}

    def _write(self, conversation: str, data: dict[str, Any]) -> None:
        if conversation in self._private:
            self._private[conversation] = data
            self._private_changes[conversation] = self._private_changes.get(conversation, 0) + 1
            return
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
        if conversation in self._private:
            return  # the terminal and a fresh page never continue a private one
        self._file(conversation)  # validates
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / CURRENT).write_text(conversation, encoding="utf-8")

    # -- operations -----------------------------------------------------------------------

    def create(self, title: str = "", private: bool = False) -> str:
        """A new conversation; it becomes the current one, unless it is private."""
        conversation = uuid.uuid4().hex[:12]
        data: dict[str, Any] = {
            "id": conversation,
            "title": title.strip(),
            "created": _now(),
            "updated": _now(),
            "messages": [],
        }
        if private:
            self._private[conversation] = {**data, "private": True}
            return conversation
        self._write(conversation, data)
        self.set_current(conversation)
        return conversation

    def stamp(self, conversation: str) -> tuple[int, int] | None:
        """Changes whenever the file does, e.g. when another Starlight (the terminal and
        the web chat as separate programs) saved a turn to it."""
        if conversation in self._private:
            return self._private_changes.get(conversation, 0), 0
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

    def organize(
        self,
        conversation: str,
        pinned: bool | None = None,
        archived: bool | None = None,
        folder: str | None = None,
    ) -> dict[str, Any]:
        """Pin it to the top, archive it (out of the list until you show the archive), or
        put it in a folder ("" takes it out). Its time of last change stays."""
        data = self._read(conversation)
        if pinned is not None:
            data["pinned"] = pinned
        if archived is not None:
            data["archived"] = archived
        if folder is not None:
            data["folder"] = " ".join(folder.split())[:40]
        self._write(conversation, data)
        return {k: data.get(k) for k in ("pinned", "archived", "folder")}

    def branch(self, conversation: str, turns: int) -> str:
        """A new conversation with this one's first ``turns`` exchanges (your message and
        everything up to your next one), to go another way from there. The original
        stays as it is."""
        data = self._read(conversation)
        messages = data.get("messages", [])
        asked = [i for i, m in enumerate(messages) if m.get("role") == "user"]
        if not 1 <= turns <= len(asked):
            raise KeyError(f"no exchange {turns} in '{conversation}'")
        end = asked[turns] if turns < len(asked) else len(messages)
        title = f"{data.get('title') or 'Conversation'} (branch)"[:80]
        branch = self.create(title, private=bool(data.get("private")))
        copy = self._read(branch)
        copy["messages"] = messages[:end]
        copy["folder"] = data.get("folder") or ""
        self._write(branch, copy)
        return branch

    def delete(self, conversation: str) -> None:
        if self._private.pop(conversation, None) is not None:
            self._private_changes.pop(conversation, None)
            return
        self._file(conversation).unlink(missing_ok=False)

    def get(self, conversation: str) -> dict[str, Any]:
        data = self._read(conversation)
        return {
            "id": conversation,
            "title": data.get("title") or "New conversation",
            "created": data.get("created"),
            "updated": data.get("updated"),
            "private": bool(data.get("private")),
            "pinned": bool(data.get("pinned")),
            "archived": bool(data.get("archived")),
            "folder": data.get("folder") or "",
            "messages": _shown(data.get("messages", [])),
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

    def list(
        self, search: str = "", limit: int = 200, archived: bool = False
    ) -> list[dict[str, Any]]:
        """The conversations, newest first. A file that did not change since the last call
        is not read again (unless searching, which looks into the messages): the page asks
        after every answer. Another program may write the files, so the key is the file's own
        time, size and inode, as in ``stamp``."""
        wanted = search.strip().casefold()
        found: list[dict[str, Any]] = []
        for conversation, data in self._private.items():
            messages = data.get("messages", [])
            if (
                wanted
                and wanted not in (data.get("title") or "").casefold()
                and not any(wanted in str(m.get("content", "")).casefold() for m in messages)
            ):
                continue
            found.append({
                "id": conversation,
                "title": data.get("title") or "Private conversation",
                "updated": data.get("updated") or "",
                "count": sum(1 for m in messages if m.get("role") == "user"),
                "private": True,
                "pinned": bool(data.get("pinned")),
                "archived": bool(data.get("archived")),
                "folder": data.get("folder") or "",
            })  # fmt: skip
        if not self.folder.is_dir():
            return _ordered(found, archived, limit)
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
                "pinned": bool(data.get("pinned")),
                "archived": bool(data.get("archived")),
                "folder": data.get("folder") or "",
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
        return _ordered(found, archived, limit)


def _shown(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Your messages and the answers, each answer with the tools used for it since your
    message ({"tool", "ok"}), for the chat's tool timeline."""
    shown: list[dict[str, Any]] = []
    used: list[dict[str, Any]] = []
    names: dict[str, str] = {}
    for message in messages:
        role = message.get("role")
        if role == "user":
            used = []
        for call in message.get("tool_calls") or []:
            names[str(call.get("id"))] = str(call.get("name", ""))
        if role == "tool":
            name = message.get("name") or names.get(str(message.get("tool_call_id")), "")
            ok = not str(message.get("content", "")).startswith("ERROR:")
            used.append({"tool": name, "ok": ok})
        if role in ("user", "assistant") and message.get("content"):
            entry = {"role": role, "content": message["content"]}
            if role == "assistant" and used:
                entry["tools"], used = used, []
            shown.append(entry)
    return shown


def _ordered(found: list[dict[str, Any]], archived: bool, limit: int) -> list[dict[str, Any]]:
    """The archived ones or the others; pinned first, then the newest."""
    kept = [c for c in found if bool(c.get("archived")) == archived]
    kept.sort(key=lambda c: c["updated"], reverse=True)
    kept.sort(key=lambda c: not c.get("pinned"))  # stable: newest first within each
    return kept[:limit]
