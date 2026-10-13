"""Small bugs found in a second review: each test is the reported case, now working."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from starlight import service
from starlight.cli import main
from starlight.core import Kernel, Paths
from starlight.core.logging import recent_errors
from starlight.core.situation import local_now
from starlight.core.stats import Stats
from starlight.memory.store import slugify
from starlight.viewer import StatsReader
from tests.conftest import ModuleWriter


def test_the_viewer_counts_every_failed_tool_call(tmp_path: Path) -> None:
    db = tmp_path / "stats.db"
    stats = Stats(db)
    for _ in range(25):
        stats.record("tool.calls", module="web", tool="web.fetch", ok=False)
    stats.record("tool.calls", module="web", tool="web.fetch", ok=True)
    stats.record("errors", source="model")
    stats.close()
    summary = StatsReader(db).summary(60)
    assert summary["totals"]["errors"] == 26  # not 11: the list shows 10, the total all
    assert len(summary["recent_errors"]) == 10


def test_service_runs_every_command_even_after_one_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[list[str]] = []
    unit = tmp_path / "starlight-core.service"
    unit.write_text("[Unit]\n")

    def fake_run(command: list[str], check: bool = False) -> Any:
        ran.append(command)
        assert unit.exists()  # `systemctl disable` still finds the unit it disables
        return subprocess.CompletedProcess(command, 1 if len(ran) == 1 else 0)

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    plan = service.Plan(remove=[unit], commands=[["first"], ["second"]])
    assert service.apply(plan) == 1  # the failure is still reported
    assert ran == [["first"], ["second"]] and not unit.exists()


def test_recent_errors_show_local_time_and_respect_a_limit_of_zero(tmp_path: Path) -> None:
    lines = [
        {"timestamp": "2026-10-09T09:04:42.123Z", "level": "error", "event": f"boom {i}"}
        for i in range(3)
    ]
    (tmp_path / "starlight.log").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    errors = recent_errors(tmp_path, limit=5)
    assert len(errors) == 3
    import datetime as dt

    local = dt.datetime(2026, 10, 9, 9, 4, 42, tzinfo=dt.UTC).astimezone()
    assert errors[0]["time"] == local.strftime("%Y-%m-%d %H:%M:%S")
    assert recent_errors(tmp_path, limit=0) == []


def test_a_time_zone_that_is_a_folder_falls_back_to_the_pcs() -> None:
    assert local_now("Europe").tzinfo is not None  # used to raise IsADirectoryError
    assert local_now("Europe/Vienna").tzinfo is not None


def test_eszett_survives_in_file_names() -> None:
    assert slugify("Straße") == "strasse" and slugify("Weißwurst-Rezept") == "weisswurst-rezept"


def test_memory_ids_outside_the_folder_are_an_error_not_a_crash(
    paths: Paths, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--home", str(paths.root), "memory", "show", "../../outside"]) == 1
    assert "invalid memory id" in capsys.readouterr().err


async def test_a_tool_with_its_own_time_limit_may_run_longer(
    paths: Paths, write_module: ModuleWriter
) -> None:
    write_module(
        "slow",
        """
        import asyncio
        from starlight.core import Module, tool
        class Mod(Module):
            @tool(own_timeout=True)
            async def long(self) -> str:
                "a backup that takes a while"
                await asyncio.sleep(0.5)
                return "done"
            @tool
            async def short(self) -> str:
                "an ordinary tool"
                await asyncio.sleep(0.5)
                return "done"
        """,
    )
    paths.config_file.write_text("kill_switch:\n  hotkey: null\ntools:\n  timeout: 0.2\n")
    async with Kernel(paths) as k:
        long, short = await asyncio.gather(
            k.dispatcher.call("slow.long", {}), k.dispatcher.call("slow.short", {})
        )
    assert long.ok and long.output == "done"
    assert not short.ok and "timed out" in str(short.error)


# -- slips in the previous fix pass ------------------------------------------------------------


def test_ip_abbreviations_and_following_dmesg_are_not_read_only() -> None:
    from modules.network.readonly import is_read_only

    for command in ("ip a a 10.0.0.5/24 dev eth0", "ip l s eth0 down", "ip r d default",
                    "ip addr fl dev eth0", "ip monitor", "dmesg -w", "dmesg -Tw"):  # fmt: skip
        assert not is_read_only(command), command
    for command in ("ip a", "ip -br addr", "ip route show", "ip route get 1.1.1.1", "dmesg -T"):
        assert is_read_only(command), command


def test_a_windows_1252_file_keeps_its_last_letters() -> None:
    from starlight.text import decode_text

    assert decode_text("Café".encode("cp1252")) == "Café"
    assert decode_text("Preis: 5 €".encode("cp1252")) == "Preis: 5 €"
    assert decode_text("Grüß".encode()[:-1], cut_off=True) == "Grü"  # a read cut mid-letter


def test_iso_times_and_uhr_count_as_times() -> None:
    from starlight.core.schedule import has_clock_time

    assert has_clock_time("2026-12-24T18:30") and has_clock_time("9:00uhr")
    assert not has_clock_time("24.12.2026")


def test_calc_percent_applies_to_everything_before_it_and_round_handles_big_numbers() -> None:
    from modules.calc.module import evaluate

    assert evaluate("100 / 4 + 25%") == pytest.approx(31.25)
    assert evaluate("100 - 20 - 10%") == pytest.approx(72)
    assert evaluate("2 * -5%") == pytest.approx(-0.1)
    assert evaluate("round(1e30)") == 10**30 and evaluate("round(2**100)") == 2**100
    assert evaluate("round(-2.5)") == -3


def test_updating_a_flat_archive_keeps_models_once_and_survives_a_locked_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import zipfile

    from tests.test_launcher import L

    def archive() -> Path:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("whisper-server", "new")  # the program at the top level
        path = tmp_path / "whisper.zip"
        path.write_bytes(buffer.getvalue())
        return path

    target = tmp_path / "whisper"
    (target / "models").mkdir(parents=True)
    (target / "models" / "ggml.bin").write_bytes(b"model")
    L.extract_member(archive(), "whisper-server", target, keep=("models",))
    assert sorted(p.name for p in target.iterdir()) == ["models", "whisper-server"]

    def locked(path: Any, *args: Any, **kwargs: Any) -> None:
        raise PermissionError("[WinError 32] being used by another process")

    monkeypatch.setattr(L.shutil, "rmtree", locked)
    with pytest.raises(PermissionError):
        L.extract_member(archive(), "whisper-server", target, keep=("models",))
    assert (target / "models" / "ggml.bin").read_bytes() == b"model"
