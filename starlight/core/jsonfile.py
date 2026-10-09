"""Small JSON files that modules keep their lists in (tasks, reminders, watches, devices).

They may be edited by hand, so reading is forgiving: a byte-order mark (Notepad's "UTF-8
with BOM") is fine, and a file that can't be read is moved aside (``todo.json`` becomes
``todo.unreadable-20261009-101500.json``) instead of being overwritten by the next save.
Saving goes through a temporary file, so a crash never leaves half a file behind.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from starlight.core.logging import get_logger


def load_json[T: (list[Any], dict[str, Any])](path: Path, empty: T) -> T:
    """The file's list or dict, or `empty` if there is no file yet (or it was unreadable and
    has been moved aside)."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return empty
    except OSError as exc:
        # Locked or not permitted: say so rather than act as if it were empty, which
        # would make the next save overwrite it.
        raise OSError(f"could not read {path}: {exc}") from exc
    try:
        data = json.loads(text) if text.strip() else type(empty)()
    except json.JSONDecodeError as exc:
        _move_aside(path, f"{exc.msg} at line {exc.lineno}")
        return empty
    if not isinstance(data, type(empty)):
        _move_aside(path, f"expected a JSON {type(empty).__name__}")
        return empty
    return data


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _move_aside(path: Path, reason: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    kept = path.with_name(f"{path.stem}.unreadable-{stamp}{path.suffix}")
    try:
        path.replace(kept)
    except OSError:
        get_logger("jsonfile").exception("could not move an unreadable file aside", file=str(path))
        raise
    get_logger("jsonfile").warning(
        "unreadable file moved aside; starting empty", file=str(path), kept=str(kept), reason=reason
    )
