"""Small things left over from the stability reviews: a limit for notes, forgetting devices
that were only seen once, stopping a backup in the middle of a huge file, and a reply that
nests JSON absurdly deep."""

from __future__ import annotations

import json
import threading
import time
import zipfile
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from starlight.core import Kernel, Paths
from starlight.core.tools import ToolError
from starlight.models.output import SCAN_EVERY, StreamFilter, recover_tool_calls
from tests.test_home_devices import ARP_WINDOWS, User, call, fails, fake_network, instance

# -- notes ---------------------------------------------------------------------------------------


async def test_a_note_cannot_grow_past_one_megabyte(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("notes")
    async with Kernel(paths) as k:
        await call(k, "notes.note", title="Ideas", text="x" * 600_000)
        await call(k, "notes.append", title="Ideas", text="y" * 300_000)  # 0.9 MB: still fine
        message = await fails(k, "notes.append", title="Ideas", text="z" * 200_000)
        assert "over 1 MB" in message and "Ideas 2" in message
        big = await fails(k, "notes.note", title="Novel", text="w" * 1_100_000)
        assert "more than 1 MB" in big


# -- the home network --------------------------------------------------------------------------


@pytest.fixture
def lan(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("lan")
    return paths


def age(paths: Paths, mac: str, days: int) -> None:
    """Make a device look as if it was last seen ``days`` ago."""
    file = next((paths.data / "modules" / "lan").glob("devices.json"))
    known = json.loads(file.read_text())
    then = (datetime.now() - timedelta(days=days)).isoformat(timespec="minutes")
    known[mac]["last_seen"] = then
    file.write_text(json.dumps(known))


async def test_unnamed_devices_not_seen_for_months_are_forgotten(lan: Paths) -> None:
    async with Kernel(lan) as k:
        fake_network(k, ARP_WINDOWS)
        await call(k, "lan.devices")
        await call(k, "lan.name_device", device="192.168.1.23", name="Printer")
        fake_network(k, "")  # nobody answers any more
        age(lan, "a4:2b:b0:11:22:33", 120)  # the router, unnamed
        age(lan, "3c:52:82:aa:bb:cc", 120)  # the printer, named
        await call(k, "lan.devices")
        file = next((lan.data / "modules" / "lan").glob("devices.json"))
        known = json.loads(file.read_text())
    assert "a4:2b:b0:11:22:33" not in known  # unnamed, 120 days
    assert known["3c:52:82:aa:bb:cc"]["name"] == "Printer"  # a name keeps it


async def test_a_device_seen_lately_or_still_present_is_kept(lan: Paths) -> None:
    async with Kernel(lan) as k:
        fake_network(k, ARP_WINDOWS)
        await call(k, "lan.devices")
        age(lan, "a4:2b:b0:11:22:33", 10)  # recent
        age(lan, "3c:52:82:aa:bb:cc", 200)  # old, but it is on the network right now
        await call(k, "lan.devices")
        file = next((lan.data / "modules" / "lan").glob("devices.json"))
        assert len(json.loads(file.read_text())) == 2


async def test_forgetting_can_be_switched_off(lan: Paths) -> None:
    (lan.module_configs).mkdir(parents=True, exist_ok=True)
    (lan.module_configs / "lan.yaml").write_text("forget_unnamed_after_days: 0\n")
    async with Kernel(lan) as k:
        fake_network(k, ARP_WINDOWS)
        await call(k, "lan.devices")
        fake_network(k, "")
        age(lan, "a4:2b:b0:11:22:33", 900)
        await call(k, "lan.devices")
        file = next((lan.data / "modules" / "lan").glob("devices.json"))
        assert "a4:2b:b0:11:22:33" in json.loads(file.read_text())
        assert instance(k, "lan").config.forget_unnamed_after_days == 0


# -- a backup stops in the middle of a huge file ----------------------------------------------


class StopAfter(threading.Event):
    """An event that says "stopped" from its n-th look on."""

    def __init__(self, looks: int) -> None:
        super().__init__()
        self.looks = looks

    def is_set(self) -> bool:
        self.looks -= 1
        return self.looks < 0


async def test_a_backup_stops_between_the_pieces_of_one_big_file(
    paths: Paths,
    use_module: Callable[[str], Path],
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_module("backup")
    source, target = tmp_path_factory.mktemp("Big"), tmp_path_factory.mktemp("Disk")
    (source / "disk.img").write_bytes(b"0123456789" * 5000)  # 50 KB: 50 pieces of 1 KB
    async with Kernel(paths, ui=User(True)) as k:
        await call(k, "backup.add", name="big", source=str(source), target=str(target))
        module = instance(k, "backup")
        import sys

        monkeypatch.setattr(sys.modules[type(module).__module__], "CHUNK", 1024)
        plan = module._set("big")[1]
        with pytest.raises(ToolError, match="stopped"):
            module._make("big", plan, StopAfter(looks=5))  # one look per piece
        folder = target / "big"
        assert list(folder.glob("*.partial")) == [] and list(folder.glob("*.zip")) == []


async def test_a_backup_of_big_and_empty_files_is_whole_and_checksummed(
    paths: Paths,
    use_module: Callable[[str], Path],
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib
    import sys

    use_module("backup")
    source, target = tmp_path_factory.mktemp("Docs"), tmp_path_factory.mktemp("Disk")
    content = bytes(range(256)) * 40
    (source / "data.bin").write_bytes(content)
    (source / "empty.txt").write_bytes(b"")
    async with Kernel(paths, ui=User(True)) as k:
        await call(k, "backup.add", name="docs", source=str(source), target=str(target))
        module = instance(k, "backup")
        monkeypatch.setattr(sys.modules[type(module).__module__], "CHUNK", 1000)
        made = module._make("docs", module._set("docs")[1])
    assert made["files"] == 2
    with zipfile.ZipFile(next((target / "docs").glob("*.zip"))) as archive:
        assert archive.read("data.bin") == content and archive.read("empty.txt") == b""
        manifest = json.loads(archive.read("starlight-backup.json"))
    sums = {f["path"]: f["sha256"] for f in manifest["files"]}
    assert sums["data.bin"] == hashlib.sha256(content).hexdigest()


# -- a reply that nests JSON absurdly deep -------------------------------------------------------


def test_deeply_nested_text_after_a_call_start_is_just_text() -> None:
    known = {"calc.calculate"}
    text = '{"name": ' + "[" * 60_000
    assert recover_tool_calls(text, known) == ([], text)
    stream = StreamFilter()
    for start in range(0, len(text), 1000):
        stream.feed(text[start : start + 1000])
        assert stream.call_finished(known) is False
    assert stream.held()  # it is shown at the end, not lost


def test_a_call_that_is_still_being_written_is_not_rescanned_at_every_piece(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import starlight.models.output as output

    scans: list[int] = []
    real = output._scan
    monkeypatch.setattr(
        output, "_scan", lambda text, known: (scans.append(len(text)), real(text, known))[1]
    )
    stream = StreamFilter()
    stream.feed('{"name": "calc.calculate", "arguments": {"expression": "')
    started = time.monotonic()
    for _ in range(100):
        stream.feed("1+" * 100)  # 200 characters at a time, 20 000 in all
        stream.call_finished({"calc.calculate"})
    assert len(scans) <= 20_000 // SCAN_EVERY + 2 and time.monotonic() - started < 2
    stream.feed('1"}}  and then more')
    assert stream.call_finished({"calc.calculate"}) is True  # a finished call is still seen
