"""Pausing the model to free the graphics card (game mode, or when you ask).

A pause is a small file, ``data/model.pause.json``. The launcher watches it: while it is
there, the model server is stopped (or, with LM Studio, the model is unloaded) and the video
memory is free for a game; when it goes away, the model comes back. Starlight answers
requests meanwhile with a note instead of an error. The launcher has its own copy of these
few lines (it runs without Starlight's libraries).
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any

from starlight.core.paths import Paths

PAUSE_FILE = "model.pause.json"


def pause_path(paths: Paths) -> Path:
    return paths.data / PAUSE_FILE


def read_pause(paths: Paths) -> dict[str, Any] | None:
    """The pause in force ({"reason", "by", "since"}), or None."""
    try:
        data = json.loads(pause_path(paths).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else {"reason": "paused", "by": "", "since": 0}


def pause_model(paths: Paths, reason: str, by: str) -> None:
    path = pause_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"reason": reason, "by": by, "since": time.time()}), "utf-8")
    temp.replace(path)


def resume_model(paths: Paths) -> bool:
    """End the pause. True if there was one."""
    path = pause_path(paths)
    if not path.exists():
        return False
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    return True


def pause_note(paths: Paths) -> str:
    """What Starlight answers while the model is paused ("" when it is not)."""
    pause = read_pause(paths)
    if pause is None:
        return ""
    return (
        f"Starlight's model is paused to free the graphics card ({pause.get('reason')}). It "
        "comes back when that ends, or press Resume now in the Starlight window (or run "
        "`starlight model resume`)."
    )
