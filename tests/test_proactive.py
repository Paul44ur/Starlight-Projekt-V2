"""Automations and proactive help: the sensors (new files, drives, battery, programs, away and
back with a summary), reminders when a program opens, the approval queue, refused automation
steps waiting there, the overnight mail triage, meeting preparation, the PC check-up, devices
coming and going on the network, the briefing read aloud, and the Automations page."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.core.vault import MemoryBackend
from tests.conftest import ScriptedProvider
from tests.test_mail import User
from tests.test_mail_assistant import FakeIMAP, connect, mail


def instance(k: Kernel, name: str) -> Any:
    module = k.modules.records[name].instance
    assert module is not None, k.modules.records[name].error
    return module


async def call(k: Kernel, name: str, /, **args: Any) -> Any:
    result = await k.dispatcher.call(name, args)
    assert result.ok, result.error
    return result.output


async def fails(k: Kernel, name: str, /, **args: Any) -> str:
    result = await k.dispatcher.call(name, args)
    assert not result.ok
    return str(result.error)


def deliveries(k: Kernel) -> list[str]:
    seen: list[str] = []
    k.bus.subscribe("deliver", lambda e: seen.append(e.data["text"]))
    return seen


def events(k: Kernel, pattern: str) -> list[tuple[str, dict[str, Any]]]:
    seen: list[tuple[str, dict[str, Any]]] = []
    k.bus.subscribe(pattern, lambda e: seen.append((e.name, dict(e.data))))
    return seen


QUIET = "memory:\n  enabled: false\nskills:\n  enabled: false\n"


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    for name in ("sensors", "reminders", "approvals", "automations", "notes", "calendar"):
        use_module(name)
    with paths.config_file.open("a") as config:
        config.write(QUIET)
    return paths


class Probes:
    """Stands in for the PC: idle time, drives, battery, programs and the clock."""

    def __init__(self) -> None:
        self.idle: float | None = 0
        self.drives: dict[str, str] = {"C:": "Windows"}
        self.battery: tuple[int, bool] | None = (80, True)
        self.programs = {"explorer"}
        self.now = 1_000_000.0

    def attach(self, sensors: Any) -> None:
        sensors._idle = lambda: self.idle
        sensors._drives_now = lambda: dict(self.drives)
        sensors._battery = lambda: self.battery
        sensors._programs = lambda: set(self.programs)
        sensors._clock = lambda: self.now


# -- sensors ---------------------------------------------------------------------------------------


async def test_new_files_drives_battery_and_programs_become_events(
    project: Paths, tmp_path: Path
) -> None:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "old.pdf").write_text("x")
    async with Kernel(project) as k:
        sensors, pc = instance(k, "sensors"), Probes()
        pc.attach(sensors)
        told, seen = deliveries(k), events(k, "sensors.*")
        await call(k, "sensors.watch_folder", path=str(downloads))
        assert await sensors.check() == []  # the first look only learns what is there
        (downloads / "invoice.pdf").write_text("x")
        (downloads / "big.zip.crdownload").write_text("x")  # still downloading: not yet
        pc.drives["E:"] = "BACKUP"
        pc.battery = (15, False)
        pc.now += 10
        pc.programs.add("excel")
        assert await sensors.check() == [
            "sensors.file_appeared", "sensors.drive_added", "sensors.power_unplugged",
            "sensors.battery_low", "sensors.app_started",
        ]  # fmt: skip
        assert seen[0][1]["name"] == "invoice.pdf" and seen[1][1] == {
            "drive": "E:",
            "label": "BACKUP",
        }
        assert told == ["New files: New in Downloads: invoice.pdf"]
        pc.now += 10
        assert await sensors.check() == []  # the low battery is told once, Excel still runs
        del pc.drives["E:"]
        pc.battery = (16, True)
        pc.programs.discard("excel")
        pc.now += 10
        assert await sensors.check() == ["sensors.drive_removed", "sensors.power_plugged"]
        pc.programs.add("excel")  # opened again a few seconds later: the same session
        pc.now += 10
        assert await sensors.check() == []
        pc.programs.discard("excel")
        pc.now += 10
        await sensors.check()
        pc.programs.add("excel")
        pc.now += 120  # closed for two minutes, then opened: started again
        assert await sensors.check() == ["sensors.app_started"]
        status = await call(k, "sensors.status")
        assert status["folders"] == [str(downloads)] and status["battery"]["percent"] == 16
        assert "sensors.drive_added" in await call(k, "sensors.events")
        await call(k, "sensors.unwatch_folder", path=str(downloads))
        assert "not watched" in await fails(k, "sensors.unwatch_folder", path=str(downloads))


async def test_coming_back_brings_one_summary_of_what_came_in(project: Paths) -> None:
    (project.module_configs / "sensors.yaml").write_text("away_after_minutes: 10\n")
    async with Kernel(project) as k:
        sensors, pc = instance(k, "sensors"), Probes()
        pc.attach(sensors)
        told, seen = deliveries(k), events(k, "sensors.*")
        await sensors.check()
        pc.idle = 11 * 60
        assert await sensors.check() == ["sensors.away"]
        await k.deliver("Call the dentist", title="Reminder")
        await k.deliver("NAS is DOWN", title="Network")
        pc.now += 40 * 60
        pc.idle = 3
        assert await sensors.check() == ["sensors.back"]
        summary = told[-1]
        assert summary.startswith("While you were away: You were away for 51 min.")
        assert "Reminder: Call the dentist" in summary and "Network: NAS is DOWN" in summary
        assert seen[-1] == ("sensors.back", {"away_minutes": 51})
        pc.idle = 11 * 60
        await sensors.check()
        pc.idle = 1
        before = len(told)
        assert await sensors.check() == ["sensors.back"]
        assert len(told) == before  # nothing came in: no message


async def test_a_reminder_when_a_program_opens(project: Paths) -> None:
    async with Kernel(project) as k:
        told = deliveries(k)
        await call(k, "reminders.when_open", program="Word", text="send the report")
        await call(k, "reminders.when_open", program="excel.exe", text="check the budget",
                   every_time=True)  # fmt: skip
        listed = await call(k, "reminders.list")
        assert {r["next"] for r in listed["reminders"]} == {"when you open Word",
                                                           "when you open excel.exe"}  # fmt: skip
        assert await instance(k, "reminders").check_due() == []  # no time: never "due"
        await k.bus.publish("sensors.app_started", {"name": "chrome"})
        await k.bus.publish("sensors.app_started", {"name": "winword"})
        await k.bus.publish("sensors.app_started", {"name": "winword"})
        await k.bus.publish("sensors.app_started", {"name": "excel"})
        await k.bus.publish("sensors.app_started", {"name": "excel"})
        assert told == ["Reminder: send the report"] + ["Reminder: check the budget"] * 2
        left = await call(k, "reminders.list")
        assert [r["text"] for r in left["reminders"]] == ["check the budget"]


async def test_a_program_reminder_needs_the_sensors(paths: Paths, use_module: Any) -> None:
    use_module("reminders")
    async with Kernel(paths) as k:
        error = await fails(k, "reminders.when_open", program="Excel", text="x")
    assert "sensors module" in error


# -- the approval queue ----------------------------------------------------------------------------


@pytest.fixture
def queue_project(project: Paths, write_module: Callable[..., Path]) -> Paths:
    write_module(
        "probe",
        """
        from starlight.core import Module, Permission, tool

        class Mod(Module):
            async def setup(self, ctx):
                self.calls = []

            @tool(permission=Permission.CONFIRM)
            def wipe(self, what: str = "x") -> str:
                self.calls.append(what)
                return "wiped " + what

            @tool(permission=Permission.CONFIRM)
            def other(self) -> str:
                return "never"
        """,
    )
    return project


async def test_the_queue_runs_exactly_what_was_approved(queue_project: Paths) -> None:
    user = User(False, True)
    async with Kernel(queue_project, ui=user) as k:
        added = await call(k, "approvals.add", title="Wipe the cache", tool="probe.wipe",
                           args={"what": "cache"}, detail="It is 3 GB")  # fmt: skip
        other = await call(k, "approvals.add", title="Other", tool="probe.other")
        assert "no tool" in await fails(k, "approvals.add", title="x", tool="probe.nope")
        assert "cannot approve itself" in await fails(
            k, "approvals.add", title="x", tool="approvals.dismiss"
        )
        waiting = await call(k, "approvals.pending")
        assert (
            waiting["count"] == 2 and waiting["items"][0]["runs"] == 'probe.wipe {"what": "cache"}'
        )
        assert "still waits" in await fails(k, "approvals.approve", id=added["waiting"])
        done = await call(k, "approvals.approve", id=added["waiting"])
        assert done["ok"] and done["result"] == "wiped cache"
        assert (
            user.questions
            == [
                'Approve \'Wipe the cache\'? It runs probe.wipe {"what": "cache"}.',
            ]
            * 2
        )  # asked by the queue only, not again by the tool's own question
        assert instance(k, "probe").calls == ["cache"]
        await call(k, "approvals.dismiss", id=other["waiting"])
        assert (await call(k, "approvals.pending"))["count"] == 0
        assert "nothing with the id" in await fails(k, "approvals.approve", id=added["waiting"])


async def test_old_items_are_dropped(queue_project: Paths) -> None:
    async with Kernel(queue_project) as k:
        queue = instance(k, "approvals")
        await call(k, "approvals.add", title="Old", tool="probe.wipe")
        items = queue.items()
        items[0]["created"] = (datetime.now() - timedelta(days=20)).isoformat()
        from starlight.core.jsonfile import save_json

        save_json(queue.file, items)
        assert queue.items() == []


async def test_a_refused_automation_step_waits_for_approval(queue_project: Paths) -> None:
    async with Kernel(queue_project, ui=User(True)) as k:
        told = deliveries(k)
        rules = instance(k, "automations")
        saved = await rules.save_from_page(
            {"name": "nightly", "when": "every day at 3:00",
             "actions": [{"tool": "probe.wipe", "args": {"what": "temp"}}]}
        )  # fmt: skip
        assert saved["saved"] == "nightly"
        rules.config.rules[0].allow = []  # (as if made before the tool started asking)
        entry = await call(k, "automations.run_now", name="nightly")
        assert not entry["ok"]
        assert "waits in the approval queue" in told[-1]
        waiting = (await call(k, "approvals.pending"))["items"]
        assert waiting[0]["tool"] == "probe.wipe" and waiting[0]["args"] == {"what": "temp"}
        done = await instance(k, "approvals").run_item(waiting[0]["id"])
        assert done["ok"] and instance(k, "probe").calls == ["temp"]


async def test_new_templates_and_the_event_list(project: Paths) -> None:
    async with Kernel(project) as k:
        templates = await call(k, "automations.templates")
        assert {"morning-briefing-aloud", "overnight-mail", "weekly-pc-checkup",
                "battery-low"} <= set(templates)  # fmt: skip
        assert "sensors.battery_low happens" in templates["battery-low"]
        listed = await call(k, "automations.events")
        assert listed["sensors.drive_added"]["fields"] == ["drive", "label"]
        assert listed["lan.device_joined"]["fields"] == ["ip", "mac", "name"]


# -- mail overnight --------------------------------------------------------------------------------


class SearchIMAP(FakeIMAP):
    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        if command == "SEARCH" and str(args[1]).startswith("TEXT "):
            word = str(args[1])[6:-1].lower()
            found = [u for u, raw in sorted(self.messages.items()) if word in raw.decode().lower()]
            return "OK", [" ".join(str(u) for u in found).encode()]
        return super().uid(command, *args)


@pytest.fixture
def overnight() -> SearchIMAP:
    return SearchIMAP(
        {
            21: mail(21, "Anna <anna@x.org>", "Budget for 2027", "Can you send the numbers?"),
            22: mail(
                22,
                "Shop <news@shop.example>",
                "Weekly deals",
                "50% off",
                List_Unsubscribe="<mailto:off@shop.example>",
            ),
            23: mail(23, "Bank <no-reply@bank.example>", "Statement", "Your statement is ready."),
            24: mail(24, "Ben <ben@x.org>", "Old", "Read long ago"),
        },
        unseen={21, 22, 23},
    )


async def test_overnight_mail_is_sorted_and_replies_wait_for_approval(
    paths: Paths, use_module: Callable[[str], Path], overnight: SearchIMAP
) -> None:
    for name in ("mail", "approvals"):
        use_module(name)
    with paths.config_file.open("a") as config:
        config.write(QUIET)
    provider = ScriptedProvider(
        "Important: Anna wants the budget numbers.\nCan wait: the bank statement.",
        "Shop: 50% off this week.",
        "Hi Anna, here are the numbers: [numbers]",
    )
    async with Kernel(paths, provider=provider, ui=User(), vault_backend=MemoryBackend()) as k:
        module = connect(k, overnight)
        sent: list[tuple[str, str]] = []
        module._smtp_send = lambda password, to, subject, body, headers=None: sent.append(
            (to, body)
        )
        told = deliveries(k)
        out = await call(k, "mail.overnight", send=True)
        assert out["count"] == 3 and out["newsletters"] == 1
        assert out["drafted"] == ["Budget for 2027"]  # not the shop, not the no-reply bank
        assert "Newsletters (1):\nShop: 50% off" in out["text"] and "approval queue" in out["text"]
        assert told[-1].startswith("Mail overnight: Important")
        assert overnight.unseen == {21, 22, 23}  # nothing marked as read
        item = (await call(k, "approvals.pending"))["items"][0]
        assert item["tool"] == "mail.reply" and item["args"]["id"] == "21"
        assert "Hi Anna" in item["detail"]
        done = await instance(k, "approvals").run_item(item["id"])
        assert done["ok"] and sent == [
            ("Anna <anna@x.org>", "Hi Anna, here are the numbers: [numbers]")
        ]
        found = await call(k, "mail.search", text="budget")
        assert [m["id"] for m in found["messages"]] == ["21"]


# -- meeting preparation ---------------------------------------------------------------------------


async def test_a_meeting_is_prepared_with_your_notes(project: Paths, documents: Path) -> None:
    async with Kernel(project) as k:
        start = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=8)
        await call(k, "calendar.add_event", title="Budget call with Anna",
                   start=start.strftime("%Y-%m-%d %H:%M"), remind_minutes=0)  # fmt: skip
        await call(k, "notes.note", title="Budget", text="Anna wants the 2027 numbers by May")
        await call(k, "notes.note", title="Groceries", text="eggs")
        prepared = await call(k, "calendar.prepare", event="budget")
        assert [n["note"] for n in prepared["notes"]] == ["Budget"]
        assert "Your notes:\n- Budget:" in prepared["text"]
        told = deliveries(k)
        calendar = instance(k, "calendar")
        assert await calendar.check_prepare() == ["Budget call with Anna"]
        assert await calendar.check_prepare() == []  # once
        assert told[0].startswith("Meeting prep: Budget call with Anna at")


def test_the_words_a_meeting_is_about() -> None:
    from modules.calendar.module import keywords

    assert keywords("Budget call with Anna") == ["budget", "anna"]
    assert keywords("Teams-Termin: Jour fixe") == []


# -- the PC check-up, the network, the briefing aloud ----------------------------------------------


async def test_the_pc_check_up(paths: Paths, use_module: Callable[[str], Path]) -> None:
    use_module("system")
    async with Kernel(paths) as k:
        told = deliveries(k)
        report = await call(k, "system.health_report", send=True)
        assert report["text"].startswith("PC check-up")
        assert any("programs start at login" in line for line in report["fine"])
        assert any("free" in line for line in report["fine"] + report["warnings"])
        assert told[0].startswith("PC check-up")


async def test_known_devices_coming_and_going(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("lan")
    table = {"192.168.1.1": "a4:2b:b0:11:22:33", "192.168.1.40": "aa:bb:cc:00:00:01"}
    async with Kernel(paths) as k:
        lan = instance(k, "lan")
        lan.networks = lambda: [ipaddress.IPv4Network("192.168.1.0/24")]
        lan.neighbours = lambda: dict(table)
        seen = events(k, "lan.device_*")
        await lan.check()  # learns what is there
        await call(k, "lan.name_device", device="192.168.1.40", name="Anna's phone")
        del table["192.168.1.40"]
        await lan.check()  # missing once: maybe asleep
        assert seen == []
        await lan.check()
        assert seen == [("lan.device_left", {"mac": "aa:bb:cc:00:00:01", "ip": "192.168.1.40",
                                             "name": "Anna's phone"})]  # fmt: skip
        table["192.168.1.40"] = "aa:bb:cc:00:00:01"
        await lan.check()
        assert seen[-1][0] == "lan.device_joined" and seen[-1][1]["name"] == "Anna's phone"


def test_the_briefing_as_it_is_spoken() -> None:
    from modules.briefing.module import spoken_text

    text = (
        "Good morning! Friday\n\nCalendar:\n  • 09:00 Dentist\n⚠ Certificate for nas expires today"
    )
    assert spoken_text(text) == (
        "Good morning! Friday. Calendar: 09:00 Dentist. Certificate for nas expires today."
    )


async def test_reading_the_briefing_aloud_needs_the_voice(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("briefing")
    async with Kernel(paths) as k:
        assert "voice module" in await fails(k, "briefing.read_aloud")


# -- the Automations page and the queue on the Activity page --------------------------------------


@pytest.fixture
def web_home(queue_project: Paths) -> Path:
    with queue_project.config_file.open("a") as config:
        config.write("model:\n  provider: echo\n")
    return queue_project.root


def test_the_automations_page_makes_and_runs_rules(web_home: Path) -> None:
    from tests.test_web_chat import LOCAL, client

    with client(web_home) as c:
        assert "When" in c.get("/automations", headers=LOCAL).text
        data = c.get("/api/automations", headers=LOCAL).json()
        assert "sensors.drive_added" in data["events"]
        assert data["templates"]["battery-low"]["rule"]["on"] == "sensors.battery_low"
        assert any(t["name"] == "probe.wipe" for t in data["tools"])
        rule = {"name": "usb", "on": "sensors.drive_added", "condition": "label ~ backup",
                "actions": [{"tool": "probe.wipe", "args": {"what": "{event.drive}"}}]}  # fmt: skip
        checked = c.post("/api/automations/check", json=rule, headers=LOCAL).json()
        assert checked["may_run_without_asking"] == ["probe.wipe"]
        assert "sensors.drive_added happens and label ~ backup" in checked["does"]
        saved = c.post("/api/automations", json=rule, headers=LOCAL)
        assert saved.status_code == 200, saved.text
        again = c.post("/api/automations", json=rule, headers=LOCAL)
        assert again.status_code == 400 and "exists" in again.json()["detail"]
        bad = c.post("/api/automations", json={"name": "x", "actions": []}, headers=LOCAL)
        assert bad.status_code == 400
        listed = c.get("/api/automations", headers=LOCAL).json()["rules"]
        assert listed[0]["name"] == "usb" and listed[0]["may_run_without_asking"] == ["probe.wipe"]
        off = c.post("/api/automations/usb/disable", json={}, headers=LOCAL)
        assert off.json() == {"disabled": "usb"}
        assert (
            c.post("/api/automations/usb/enable", json={}, headers=LOCAL).json()["enabled"] == "usb"
        )
        assert c.post("/api/automations/usb/remove", json={}, headers=LOCAL).status_code == 200


def test_approving_from_the_activity_page(web_home: Path) -> None:
    from tests.test_web_chat import LOCAL, client

    with client(web_home) as c:
        kernel = c.app.state.kernel  # type: ignore[attr-defined]
        queue = instance(kernel, "approvals")
        queue.add(title="Wipe temp", tool="probe.wipe", args={"what": "temp"})
        queue.add(title="Other", tool="probe.other")
        assert c.get("/api/status", headers=LOCAL).json()["waiting"] == 2
        assert "Waiting for your OK" in c.get("/activity", headers=LOCAL).text
        items = c.get("/api/approvals", headers=LOCAL).json()["items"]
        done = c.post(f"/api/approvals/{items[0]['id']}", json={"action": "approve"}, headers=LOCAL)
        assert done.json()["ok"] and instance(kernel, "probe").calls == ["temp"]
        gone = c.post(f"/api/approvals/{items[1]['id']}", json={"action": "dismiss"}, headers=LOCAL)
        assert gone.json() == {"dismissed": "Other"}
        assert c.get("/api/approvals", headers=LOCAL).json()["count"] == 0
        assert (
            c.post("/api/approvals/nope", json={"action": "approve"}, headers=LOCAL).status_code
            == 404
        )
