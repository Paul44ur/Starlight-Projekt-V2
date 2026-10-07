"""Home and devices: Home Assistant (a fake one), the devices on your home network (a fake
neighbour table, and the guarantee that nothing outside your own /24 is ever pinged), and
versioned backups with checksums, verify and a restore that asks first."""

from __future__ import annotations

import ipaddress
import json
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.core import Kernel, Paths
from starlight.core.vault import MemoryBackend
from tests.conftest import REPO


class User:
    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.questions: list[str] = []

    async def confirm(self, question: str) -> bool:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    for name in ("home", "lan", "backup"):
        use_module(name)
    return paths


async def call(k: Kernel, name: str, /, **args: Any) -> Any:
    result = await k.dispatcher.call(name, args)
    assert result.ok, result.error
    return result.output


async def fails(k: Kernel, name: str, /, **args: Any) -> str:
    result = await k.dispatcher.call(name, args)
    assert not result.ok, result.output
    return str(result.error)


def instance(k: Kernel, name: str) -> Any:
    record = k.modules.records[name]
    assert record.instance is not None, record.error
    return record.instance


# -- Home Assistant ----------------------------------------------------------------------------


class FakeHA:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        lamp = {"friendly_name": "Living room lamp", "brightness": 128}
        outside = {"friendly_name": "Outside", "unit_of_measurement": "°C"}
        self.states = {
            "light.living_room": {"state": "on", "attributes": lamp},
            "lock.front_door": {"state": "locked", "attributes": {"friendly_name": "Front door"}},
            "sensor.outside": {"state": "12.5", "attributes": outside},
            "scene.movie": {"state": "scening", "attributes": {"friendly_name": "Movie night"}},
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != "Bearer secret-token":
            return httpx.Response(401)
        path = request.url.path
        if path == "/api/":
            return httpx.Response(200, json={"message": "API running."})
        if path == "/api/states":
            return httpx.Response(200, json=[{"entity_id": k, **v} for k, v in self.states.items()])
        if path.startswith("/api/states/"):
            entity = path.rsplit("/", 1)[1]
            if entity not in self.states:
                return httpx.Response(404)
            return httpx.Response(200, json={"entity_id": entity, **self.states[entity]})
        if path.startswith("/api/services/"):
            service = path.removeprefix("/api/services/").replace("/", ".")
            self.calls.append((service, json.loads(request.content)))
            return httpx.Response(200, json=[])
        return httpx.Response(404)


async def test_home_assistant_switches_freely_but_asks_for_locks(project: Paths) -> None:
    ha = FakeHA()
    user = User(True, False)  # yes to connect, no to the lock
    async with Kernel(project, ui=user, vault_backend=MemoryBackend()) as k:
        home = instance(k, "home")
        home.transport = httpx.MockTransport(ha)
        assert "not set up" in await fails(k, "home.states")
        await call(k, "home.connect", url="http://ha.local:8123", token="secret-token")
        assert k.vault.get("home.token") == "secret-token"
        listed = await call(k, "home.states", domain="sensor")
        assert listed["entities"] == [
            {"entity": "sensor.outside", "name": "Outside", "state": "12.5 °C"}
        ]
        lamp = await call(k, "home.get", entity="living room lamp")
        assert lamp["brightness"] == "50%"
        await call(k, "home.turn_off", entity="Living room lamp")  # by name, no question
        await call(k, "home.set", entity="light.living_room", value=30)
        assert "not confirmed" in await fails(k, "home.turn_off", entity="front door")
        await call(k, "home.turn_on", entity="scene.movie")
    assert ha.calls == [
        ("homeassistant.turn_off", {"entity_id": "light.living_room"}),
        ("light.turn_on", {"entity_id": "light.living_room", "brightness_pct": 30.0}),
        ("scene.turn_on", {"entity_id": "scene.movie"}),
    ]
    assert len(user.questions) == 2 and "lock.front_door" in user.questions[1]
    assert "secret-token" not in (project.module_configs / "home.yaml").read_text()


async def test_watched_entities_announce_changes(project: Paths) -> None:
    ha = FakeHA()
    (project.module_configs / "home.yaml").write_text(
        "url: http://ha.local:8123\nwatch: [lock.front_door]\nwatch_interval: 3600\n"
    )
    async with Kernel(project, vault_backend=MemoryBackend()) as k:
        k.vault.set("home.token", "secret-token")
        home = instance(k, "home")
        home.transport = httpx.MockTransport(ha)
        events: list[dict[str, Any]] = []
        k.bus.subscribe("home.changed", lambda e: events.append(e.data))
        assert await home.check_watched() == []  # first look: learns the state
        ha.states["lock.front_door"]["state"] = "unlocked"
        await home.check_watched()
    assert events == [{"entity": "lock.front_door", "old": "locked", "new": "unlocked"}]


# -- the home network ----------------------------------------------------------------------------

ARP_WINDOWS = """
Interface: 192.168.1.20 --- 0x7
  Internet Address      Physical Address      Type
  192.168.1.1           a4-2b-b0-11-22-33     dynamic
  192.168.1.23          3c-52-82-aa-bb-cc     dynamic
  192.168.1.255         ff-ff-ff-ff-ff-ff     static
  10.9.9.9              00-11-22-33-44-55     dynamic
"""


def test_neighbour_tables_are_read_on_both_systems() -> None:
    sys.path.insert(0, str(REPO / "modules"))
    try:
        from lan.module import parse_neighbours  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    assert parse_neighbours(ARP_WINDOWS) == {
        "192.168.1.1": "a4:2b:b0:11:22:33",
        "192.168.1.23": "3c:52:82:aa:bb:cc",
        "10.9.9.9": "00:11:22:33:44:55",
    }
    linux = "192.168.1.1 dev wlan0 lladdr a4:2b:b0:11:22:33 REACHABLE\n192.168.1.9 dev wlan0 FAILED"
    assert parse_neighbours(linux) == {"192.168.1.1": "a4:2b:b0:11:22:33"}


def fake_network(k: Kernel, table: str) -> list[str]:
    lan = instance(k, "lan")
    pinged: list[str] = []
    lan.networks = lambda: [ipaddress.IPv4Network("192.168.1.0/24")]
    lan.neighbours = lambda: _parse(table)

    async def ping(address: str) -> bool:
        pinged.append(address)
        return address.endswith((".1", ".23"))

    lan.ping = ping
    return pinged


def _parse(table: str) -> dict[str, str]:
    sys.path.insert(0, str(REPO / "modules"))
    try:
        from lan.module import parse_neighbours  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    return dict(parse_neighbours(table))


async def test_devices_only_on_your_own_network_and_new_ones_are_reported(project: Paths) -> None:
    async with Kernel(project) as k:
        pinged = fake_network(k, ARP_WINDOWS)
        found = await call(k, "lan.devices", sweep=True)
        assert [d["ip"] for d in found["devices"]] == [
            "192.168.1.1",
            "192.168.1.23",
        ]  # not 10.9.9.9
        assert found["answered_ping"] == 2
        assert len(pinged) == 254 and all(p.startswith("192.168.1.") for p in pinged)
        await call(k, "lan.name_device", device="192.168.1.23", name="Printer")
        listed = await call(k, "lan.devices")
        assert listed["devices"][1]["name"] == "Printer"

        told: list[str] = []
        k.bus.subscribe("deliver", lambda e: told.append(e.data["text"]))
        lan = instance(k, "lan")
        assert await lan.check() == []  # nothing new
        newcomer = ARP_WINDOWS + "  192.168.1.77          de-ad-be-ef-00-01     dynamic\n"
        fake_network(k, newcomer)
        new = await lan.check()
    assert [d["ip"] for d in new] == ["192.168.1.77"]
    assert told and "192.168.1.77" in told[0]


# -- backups -----------------------------------------------------------------------------------


async def test_backup_versions_verify_and_restore(
    project: Paths, tmp_path_factory: pytest.TempPathFactory
) -> None:
    source = tmp_path_factory.mktemp("photos")
    (source / "2026").mkdir()
    (source / "2026" / "beach.jpg").write_bytes(b"\xff\xd8 beach")
    (source / "notes.txt").write_text("hello")
    (source / "Thumbs.db").write_bytes(b"junk")
    (source / "big.iso").write_bytes(b"iso")
    drive = tmp_path_factory.mktemp("external")
    user = User(True)
    async with Kernel(project, ui=user) as k:
        await call(k, "backup.add", name="photos", source=str(source), target=str(drive), keep=2,
                   exclude=["*.iso"])  # fmt: skip
        first = await call(k, "backup.run", name="photos")
        assert first["files"] == 2  # not Thumbs.db, not the .iso
        import asyncio

        await asyncio.sleep(1.1)  # versions are named by the second
        await call(k, "backup.run", name="photos")
        await asyncio.sleep(1.1)
        third = await call(k, "backup.run", name="photos")
        versions = (await call(k, "backup.versions", name="photos"))["versions"]
        assert len(versions) == 2 and third["removed_old"]  # keeps the newest 2
        assert (await call(k, "backup.verify", name="photos"))["ok"]

        # damage a backup: verify notices
        newest = drive / "photos" / versions[-1]["version"]
        with zipfile.ZipFile(newest) as archive:
            entries = {n: archive.read(n) for n in archive.namelist()}
        entries["notes.txt"] = b"tampered"
        with zipfile.ZipFile(newest, "w") as archive:
            for n, data in entries.items():
                archive.writestr(n, data)
        damaged = await call(k, "backup.verify", name="photos")
        assert not damaged["ok"] and damaged["damaged"] == ["notes.txt"]

        target = tmp_path_factory.mktemp("restore")
        (target / "notes.txt").write_text("keep me")
        restored = await call(k, "backup.restore", name="photos", to=str(target),
                              version=versions[0]["version"])  # fmt: skip
        assert restored == {"restored": 1, "skipped_existing": 1, "into": str(target),
                            "from": versions[0]["version"]}  # fmt: skip
        assert (target / "notes.txt").read_text() == "keep me"  # not overwritten
        assert (target / "2026" / "beach.jpg").read_bytes() == b"\xff\xd8 beach"
        restore = k.dispatcher.get("backup.restore")
        assert restore is not None and restore.permission.value == "confirm"


async def test_backup_refuses_bad_places(
    project: Paths, tmp_path_factory: pytest.TempPathFactory
) -> None:
    source = tmp_path_factory.mktemp("src")
    async with Kernel(project, ui=User(True)) as k:
        inside_itself = await fails(k, "backup.add", name="loop", source=str(source),
                                    target=str(source / "backups"))  # fmt: skip
        assert "inside the folder it backs up" in inside_itself
        own = await fails(k, "backup.add", name="own", source=str(source), target=str(project.root))
        assert "Starlight's own folder" in own
        assert "no backup called" in await fails(k, "backup.run", name="nothing")
