"""Block R8: each rule in ``GET /api/automations`` says when it last ran and how it went
(``last_run``), and ``GET /api/automations/{name}/history`` gives its last ten runs. Both read
the module's ``history.jsonl``; nothing about what is written there changes."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from starlight.core import Paths
from tests.conftest import REPO
from tests.test_remote import LOCAL, PHONE, client, home, pair, phone

RULES = ["fine", "broken", "waits", "skips", "stopped", "never"]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = home(tmp_path / "h")
    shutil.copytree(REPO / "modules" / "automations", root / "modules" / "automations")
    rules = [
        {"name": name, "when": "every day at 7:30", "actions": [{"say": "hi"}]} for name in RULES
    ]
    (root / "config" / "modules" / "automations.yaml").write_text(
        yaml.safe_dump({"rules": rules}), encoding="utf-8"
    )
    return root


def history_file(root: Path) -> Path:
    folder = Paths(root).module_data("automations")
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "history.jsonl"


def entry(rule: str, time: str, ok: bool, steps: list[dict[str, Any]]) -> str:
    return json.dumps({"time": time, "rule": rule, "ok": ok, "steps": steps})


def write(root: Path, *lines: str) -> None:
    history_file(root).write_text("\n".join(lines) + "\n", encoding="utf-8")


def rules_of(c: Any) -> dict[str, Any]:
    response = c.get("/api/automations", headers=LOCAL)
    assert response.status_code == 200, response.text
    return {r["name"]: r for r in response.json()["rules"]}


def test_last_run_for_each_way_a_run_can_end(root: Path) -> None:
    write(
        root,
        entry("fine", "2026-11-12T07:30:00.123456", True, [{"action": "tell", "ok": True}]),
        entry(
            "broken",
            "2026-11-12T07:31:00",
            False,
            [{"action": "run a.b", "ok": False, "error": "timed out after 30s"}],
        ),
        entry(
            "waits",
            "2026-11-12T07:32:00",
            False,
            [{"action": "run mail.send", "ok": False, "error": "not confirmed by user"}],
        ),
        entry("skips", "2026-11-12T07:33:00", True, [{"action": "tell", "skipped": True}]),
        entry(
            "stopped",
            "2026-11-12T07:34:00",
            False,
            [{"action": "run a.b", "ok": False, "error": "KillSwitchEngaged: panic button"}],
        ),
    )
    with client(root) as c:
        rules = rules_of(c)
    assert rules["fine"]["last_run"] == {
        "time": "2026-11-12 07:30", "outcome": "done", "text": ""
    }  # fmt: skip
    assert rules["broken"]["last_run"] == {
        "time": "2026-11-12 07:31", "outcome": "failed", "text": "timed out after 30s"
    }  # fmt: skip
    assert rules["waits"]["last_run"]["outcome"] == "waiting"
    assert rules["skips"]["last_run"]["outcome"] == "skipped"
    assert rules["stopped"]["last_run"] == {
        "time": "2026-11-12 07:34", "outcome": "stopped", "text": "panic button"
    }  # fmt: skip
    assert rules["never"]["last_run"] is None


def test_the_newest_run_wins_and_broken_lines_are_ignored(root: Path) -> None:
    write(
        root,
        entry("fine", "2026-11-10T07:30:00", False, [{"action": "x", "ok": False, "error": "old"}]),
        "this is not json",
        "[1, 2]",
        '{"rule": 5}',
        entry("fine", "2026-11-11T07:30:00", True, [{"action": "x", "ok": True}]),
        "",
        '{"time": "2026-11-1',
    )
    with client(root) as c:
        last = rules_of(c)["fine"]["last_run"]
        runs = c.get("/api/automations/fine/history", headers=LOCAL).json()["runs"]
    assert last["time"] == "2026-11-11 07:30" and last["outcome"] == "done"
    assert [r["outcome"] for r in runs] == ["done", "failed"]


def test_a_missing_or_empty_file_is_no_error(root: Path) -> None:
    with client(root) as c:
        assert all(r["last_run"] is None for r in rules_of(c).values())
        assert c.get("/api/automations/fine/history", headers=LOCAL).json()["runs"] == []
        history_file(root).write_text("", encoding="utf-8")
        assert all(r["last_run"] is None for r in rules_of(c).values())
        assert c.get("/api/automations/fine/history", headers=LOCAL).json()["runs"] == []


def test_the_history_gives_ten_newest_first_of_that_rule_only(root: Path) -> None:
    lines = []
    for day in range(1, 15):
        lines.append(entry("fine", f"2026-11-{day:02d}T07:30:00", True, []))
        lines.append(entry("broken", f"2026-11-{day:02d}T08:00:00", True, []))
    write(root, *lines)
    with client(root) as c:
        answer = c.get("/api/automations/fine/history", headers=LOCAL).json()
    assert answer["rule"] == "fine"
    times = [r["time"] for r in answer["runs"]]
    assert len(times) == 10
    assert times[0] == "2026-11-14 07:30" and times[-1] == "2026-11-05 07:30"
    assert times == sorted(times, reverse=True)


def test_an_unknown_rule_has_no_history(root: Path) -> None:
    write(root, entry("gone", "2026-11-12T07:30:00", True, []))
    with client(root) as c:
        assert c.get("/api/automations/fine/history", headers=LOCAL).status_code == 200
        gone = c.get("/api/automations/gone/history", headers=LOCAL)
        assert gone.status_code == 404 and "no automation named 'gone'" in gone.json()["detail"]
        assert c.get("/api/automations/nothing/history", headers=LOCAL).status_code == 404


def test_the_history_is_read_only_and_open_to_a_paired_phone(root: Path) -> None:
    write(root, entry("fine", "2026-11-12T07:30:00", True, []))
    with client(root) as c:
        token = pair(c)
        seen = c.get("/api/automations/fine/history", headers=phone(token))
        assert seen.status_code == 200 and len(seen.json()["runs"]) == 1
        assert c.get("/api/automations/fine/history", headers=PHONE).status_code in (401, 403)
        # nothing else answers on this address: it is a read, not an action
        assert c.post("/api/automations/fine/history", headers=LOCAL, json={}).status_code == 400
