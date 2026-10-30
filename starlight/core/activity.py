"""What Starlight did, in plain words: logs/audit.log read back for the activity page.

Each entry has a time, a kind (action, denied, practice, failed, answer, kill switch,
setting, Starlight) and a sentence. Arguments are shown as the audit log has them, with
secrets already hidden there. Module loading and other bookkeeping is left out."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TAIL_BYTES = 2_000_000  # read at most this much from the end of the log


def _args(value: Any) -> str:
    if not value:
        return ""
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= 300 else text[:297] + "..."


def describe(entry: dict[str, Any]) -> tuple[str, str] | None:
    """(kind, sentence) for one audit entry; None for bookkeeping."""
    event = str(entry.get("event", ""))
    tool = entry.get("tool", "")
    if event == "tool.call":
        how = " (you approved it)" if entry.get("permission") == "confirm" else ""
        return "action", f"ran {tool}{how} {_args(entry.get('args'))}".rstrip()
    if event == "tool.denied":
        return "denied", f"did not run {tool}: {entry.get('reason', 'denied')}"
    if event == "tool.practice":
        return "practice", f"practice mode: described {tool} {_args(entry.get('args'))}".rstrip()
    if event == "tool.result" and entry.get("ok") is False:
        return "failed", f"{tool} failed: {entry.get('error') or 'unknown error'}"
    if event == "tool.killed":
        return "kill switch", f"{tool} was stopped by the kill switch"
    if event == "task.completed":
        calls = entry.get("tool_calls") or 0
        used = f", {calls} tool call{'s' if calls != 1 else ''}" if calls else ""
        return "answer", f"answered a request ({entry.get('steps', 1)} steps{used})"
    if event == "task.failed":
        return "failed", "could not answer a request (the model did not respond)"
    if event in ("task.killed", "task.cancelled"):
        return "kill switch", "a request was stopped"
    if event == "killswitch.triggered":
        return (
            "kill switch",
            f"kill switch ENGAGED: {entry.get('reason') or entry.get('source') or ''}",
        )
    if event == "killswitch.reset":
        return "kill switch", "kill switch released"
    if event == "web.permission":
        return "setting", f"rule changed: {entry.get('pattern')} -> {entry.get('setting')}"
    if event == "web.pair":
        if entry.get("ok"):
            return "setting", f"a device was paired ({entry.get('client') or 'unknown address'})"
        return "denied", f"a wrong pairing code was tried ({entry.get('client') or 'unknown'})"
    if event == "web.revoke":
        return "setting", f"a device was removed: {entry.get('device') or ''}"
    if event == "web.practice":
        return "practice", "practice mode " + ("on" if entry.get("on") else "off")
    if event == "model.resumed":
        return "setting", "the paused model was resumed"
    if event == "core.started":
        return "starlight", "Starlight started"
    if event == "core.stopped":
        return "starlight", "Starlight stopped"
    return None


def read_activity(
    logs: Path, limit: int = 300, kind: str = "", search: str = ""
) -> list[dict[str, Any]]:
    """The latest entries, newest first (with the rotated log when the current one is short)."""
    lines: list[str] = []
    for name in ("audit.log", "audit.log.1"):
        path = logs / name
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - TAIL_BYTES))
                chunk = handle.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        part = chunk.splitlines()
        if size > TAIL_BYTES:
            part = part[1:]  # the first line was cut in the middle
        lines = part + lines
        if len(lines) >= limit * 4:
            break
    wanted = search.strip().casefold()
    found: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        described = describe(entry)
        if described is None or (kind and described[0] != kind):
            continue
        if wanted and wanted not in described[1].casefold():
            continue
        found.append(
            {"time": entry.get("timestamp", ""), "kind": described[0], "text": described[1]}
        )
        if len(found) >= limit:
            break
    return found
