"""The nightly check on your own PC: the tool-use exam and the speed benchmark against the
model you really use, compared with the night before - a small local CI, since GitHub's
free minutes are scarce and its machines have no graphics card.

``starlight model nightly`` runs it once and writes logs/nightly/<date>.json (and one line
to logs/nightly.log); ``--schedule`` makes Windows run it every night (Task Scheduler,
your own account, no administrator rights), ``--unschedule`` stops that. The model server
has to be running then (Starlight open, or `starlight service install`).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from starlight.core.model import ModelProvider
from starlight.core.stats import Stats
from starlight.models.bench import benchmark
from starlight.models.exam import exam

TASK_NAME = "Starlight nightly check"
SLOWER = 0.8  # this much of last night's speed or less counts as slower


def compare(before: dict[str, Any] | None, now: dict[str, Any]) -> list[str]:
    """What got worse since the last report (nothing: an empty list)."""
    if not before:
        return []
    worse = []
    if now["exam"]["score"] < before["exam"]["score"]:
        lost = sorted(set(before["exam"].get("passed", [])) - set(now["exam"].get("passed", [])))
        worse.append(
            f"tool-use exam {now['exam']['score']}/{now['exam']['of']}, was "
            f"{before['exam']['score']}/{before['exam']['of']}"
            + (f" (now failing: {', '.join(lost)})" if lost else "")
        )
    old, new = before.get("tokens_per_second") or 0, now.get("tokens_per_second") or 0
    if old and new < old * SLOWER:
        worse.append(f"speed {new:.1f} tokens/s, was {old:.1f}")
    return worse


def latest(folder: Path) -> dict[str, Any] | None:
    for path in sorted(folder.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and "exam" in data:
            return data
    return None


async def nightly(
    provider: ModelProvider, logs: Path, stats: Stats | None = None
) -> dict[str, Any]:
    """Run the exam and the benchmark, compare with the last report, save this one."""
    folder = logs / "nightly"
    before = latest(folder)
    tested = await exam(provider, stats=stats)
    speed = await benchmark(provider, runs=3, stats=stats)
    report: dict[str, Any] = {
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "provider": provider.name,
        "exam": {
            "score": tested["score"],
            "of": tested["of"],
            "passed": [t["task"] for t in tested["tasks"] if t["passed"]],
            "failed": {t["task"]: t["note"] for t in tested["tasks"] if not t["passed"]},
        },
        "tokens_per_second": speed["tokens_per_second"],
        "ttft_s": speed["ttft_s"],
    }
    report["worse"] = compare(before, report)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{time.strftime('%Y-%m-%d')}.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    line = (
        f"{report['date']}  exam {report['exam']['score']}/{report['exam']['of']}  "
        f"{report['tokens_per_second']:.1f} tokens/s"
        + (f"  WORSE: {'; '.join(report['worse'])}" if report["worse"] else "")
    )
    with (logs / "nightly.log").open("a", encoding="utf-8") as out:
        out.write(line + "\n")
    return report


def schedule_command(root: Path, at: str = "03:15", remove: bool = False) -> list[str]:
    """The schtasks command that adds (or removes) the nightly run on Windows."""
    if remove:
        return ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]
    python = Path(sys.executable)
    quiet = python.with_name("pythonw.exe")
    run = quiet if quiet.exists() else python
    action = f'"{run}" -m starlight --home "{root}" model nightly'
    return ["schtasks", "/Create", "/SC", "DAILY", "/ST", at, "/TN", TASK_NAME, "/TR", action, "/F"]


def cron_line(root: Path, at: str = "03:15") -> str:
    """The crontab line for the same on Linux."""
    hour, minute = at.split(":")
    return (
        f"{int(minute)} {int(hour)} * * * cd {root} && {sys.executable} -m starlight model nightly"
    )
