"""Automations: rules made from the chat run on a schedule or an event, pass results along,
stay within the permission rules (a tool that asks first runs only if approved for that
rule), run once even with two Starlights open, and survive a restart."""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.core.config import read_yaml


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
def project(
    paths: Paths, use_module: Callable[[str], Path], write_module: Callable[..., Path]
) -> Paths:
    for name in ("automations", "todo", "hello"):
        use_module(name)
    write_module(
        "probe",
        """
        from starlight.core import Module, Permission, tool

        class Mod(Module):
            async def setup(self, ctx):
                self.calls = []

            @tool
            def count(self, n: int = 3) -> dict:
                self.calls.append(("count", n))
                return {"count": n, "items": ["a", "b"][:n]}

            @tool(permission=Permission.CONFIRM)
            def wipe(self, what: str = "x") -> str:
                self.calls.append(("wipe", what))
                return "wiped " + what
        """,
    )
    return paths


def module(k: Kernel, name: str = "automations") -> Any:
    record = k.modules.records[name]
    assert record.instance is not None, record.error
    return record.instance


async def call(k: Kernel, name: str, /, **args: Any) -> Any:
    return await k.dispatcher.call(name, args)


def deliveries(k: Kernel) -> list[str]:
    seen: list[str] = []
    k.bus.subscribe("deliver", lambda e: seen.append(e.data["text"]))
    return seen


async def test_a_scheduled_rule_runs_when_due_and_passes_its_result_on(project: Paths) -> None:
    user = User(True)
    async with Kernel(project, ui=user) as k:
        told = deliveries(k)
        made = await call(
            k,
            "automations.add",
            name="Count-Check",
            when="weekdays 7:30",
            tool="probe.count",
            args={"n": 2},
            say="Found {result.count}: {result.items}",
        )
        assert made.ok, made.error
        assert "weekdays at 07:30" in user.questions[0]  # it asked, describing the rule
        auto = module(k)
        next_run = auto._next["count-check"]
        assert await auto.tick(next_run - timedelta(minutes=1)) == []
        assert await auto.tick(next_run) == ["count-check"]
        assert await auto.tick(next_run + timedelta(minutes=1)) == []  # once
        assert told == ['Automation: Found 2: ["a", "b"]']
        assert module(k, "probe").calls == [("count", 2)]
        history = (await call(k, "automations.history")).output
        assert history["runs"][0]["rule"] == "count-check" and history["runs"][0]["ok"]
    saved = read_yaml(project.module_configs / "automations.yaml")["rules"]
    assert saved[0]["name"] == "count-check" and saved[0]["when"] == "weekdays 7:30"


async def test_an_event_rule_with_a_condition(project: Paths) -> None:
    async with Kernel(project, ui=User(True)) as k:
        told = deliveries(k)
        await call(
            k,
            "automations.add",
            name="nas-down",
            on="network.server_down",
            condition="server == nas",
            say="NAS ({event.host}) is down!",
        )
        await k.bus.publish("network.server_down", {"server": "printer", "host": "10.0.0.9"})
        await k.bus.publish("network.server_down", {"server": "nas", "host": "10.0.0.5"})
        await module(k).settle()
    assert told == ["Automation: NAS (10.0.0.5) is down!"]


async def test_only_if_skips_a_step(project: Paths) -> None:
    async with Kernel(project, ui=User(True)) as k:
        told = deliveries(k)
        await call(
            k,
            "automations.add",
            name="maybe",
            when="every day at 8:00",
            tool="probe.count",
            args={"n": 0},
            say="there are some",
        )
        rule = module(k)._rule("maybe")
        rule.actions[1].only_if = "count > 0"
        entry = await module(k).run(rule, {})
    assert told == [] and entry["steps"][1]["skipped"]


async def test_a_tool_that_asks_first_runs_only_when_approved_for_the_rule(project: Paths) -> None:
    user = User(True)
    async with Kernel(project, ui=user) as k:
        told = deliveries(k)
        await call(k, "automations.add", name="cleanup", when="daily 3:00", tool="probe.wipe")
        assert "may run probe.wipe without asking" in user.questions[0]
        await call(k, "automations.run_now", name="cleanup")
        assert module(k, "probe").calls == [("wipe", "x")]  # approved when the rule was made

        # Without the approval the step is refused (nobody is there to say yes), and the
        # user is told why.
        rule = module(k)._rule("cleanup")
        rule.allow = []
        entry = await module(k).run(rule, {})
        assert not entry["ok"]
        assert any("needed your OK for probe.wipe" in t for t in told)
        assert module(k, "probe").calls == [("wipe", "x")]  # did not run again


async def test_blocked_tools_and_bad_rules_are_refused(project: Paths) -> None:
    (project.config_file).write_text(
        project.config_file.read_text() + "permissions:\n  blocked: ['probe.wipe']\n"
    )
    async with Kernel(project, ui=User(True, True, True)) as k:
        blocked = await call(k, "automations.add", name="x", when="daily 3:00", tool="probe.wipe")
        assert not blocked.ok and "blocked" in str(blocked.error)
        missing = await call(k, "automations.add", name="y", when="daily 3:00", tool="nope.nothing")
        assert not missing.ok and "no tool" in str(missing.error)
        both = await call(k, "automations.add", name="z", when="daily 3:00", on="x.y", say="hi")
        assert not both.ok and "either a schedule" in str(both.error)
        bad = await call(k, "automations.add", name="w", when="whenever", say="hi")
        assert not bad.ok and "give a time" in str(bad.error)
        nothing = await call(k, "automations.add", name="v", when="daily 3:00")
        assert not nothing.ok and "what it should do" in str(nothing.error)


async def test_declining_saves_nothing_and_enable_asks_again(project: Paths) -> None:
    async with Kernel(project, ui=User(False)) as k:
        declined = await call(k, "automations.add", name="a", when="daily 9:00", say="hi")
        assert not declined.ok and (await call(k, "automations.list")).output["count"] == 0
    async with Kernel(project, ui=User(True, False)) as k:
        await call(k, "automations.add", name="a", when="daily 9:00", say="hi")
        await call(k, "automations.disable", name="a")
        assert not (await call(k, "automations.enable", name="a")).ok  # said no
        listed = (await call(k, "automations.list")).output["automations"][0]
        assert listed["enabled"] is False and listed["next"] is None


async def test_templates_and_restart(project: Paths) -> None:
    async with Kernel(project, ui=User(True)) as k:
        names = (await call(k, "automations.templates")).output
        assert "evening-todo" in names
        made = await call(
            k, "automations.use_template", template="evening-todo", when="daily 21:00"
        )
        assert made.ok and "21:00" in made.output["rule"]
        first_next = module(k)._next["evening-todo"]
    async with Kernel(project, ui=User()) as k:  # the rule and its next run survive
        assert module(k)._next["evening-todo"] == first_next


async def test_rules_run_only_in_the_primary_starlight(project: Paths) -> None:
    async with Kernel(project, ui=User(True)) as primary:
        await call(primary, "automations.add", name="ev", on="todo.*", say="todo event")
        async with Kernel(project, ui=User()) as second:  # loads the same rule
            assert module(second)._rule("ev")
            told, told2 = deliveries(primary), deliveries(second)
            await primary.bus.publish("todo.added", {})
            await second.bus.publish("todo.added", {})
            await module(primary).settle()
            await module(second).settle()
    assert told == ["Automation: todo event"] and told2 == []


def test_conditions_and_placeholders() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "modules"))
    try:
        from automations.module import fill, matches  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    data = {"days_left": 9, "host": "nas.local", "items": [1], "none": []}
    assert matches("days_left < 14", data) and not matches("days_left >= 14", data)
    assert matches("host == NAS.local", data) and matches("host ~ nas", data)
    assert matches("items", data) and not matches("none", data) and not matches("missing", data)
    assert fill({"a": "{event.host}:{result.n}"}, data, {"n": 5}) == {"a": "nas.local:5"}
    assert fill("{result}", {}, "plain") == "plain"
    assert fill("{event.__class__}", data, None) == ""  # no attribute access, just keys


async def test_the_kill_switch_stops_a_rule(project: Paths) -> None:
    async with Kernel(project, ui=User(True)) as k:
        await call(k, "automations.add", name="k", when="daily 9:00", tool="probe.count")
        k.kill_switch.trigger("test")
        entry = await module(k).run(module(k)._rule("k"), {})
        k.kill_switch.reset()
        assert module(k, "probe").calls == []
    assert not entry["ok"] and "KillSwitch" in entry["steps"][0]["error"]


async def test_a_saved_rule_whose_schedule_is_no_longer_taken_does_not_stop_the_others(
    project: Paths,
) -> None:
    # Older versions saved "every week at 9:00" and ran it every day; now it needs a day.
    (project.module_configs / "automations.yaml").write_text(
        "rules:\n"
        "  - name: review\n    when: every week at 9:00\n    actions: [{say: weekly review}]\n"
        "  - name: brief\n    when: weekdays 7:30\n    actions: [{say: good morning}]\n",
        encoding="utf-8",
    )
    user = User(True)
    async with Kernel(project, ui=user) as k:
        auto = module(k)  # the module loaded, with both rules
        listed = {r["name"]: r for r in (await call(k, "automations.list")).output["automations"]}
        assert "say which day" in listed["review"]["problem"]
        assert "does not run" in listed["review"]["does"] and listed["review"]["next"] is None
        assert "problem" not in listed["brief"] and listed["brief"]["next"]
        assert "review" not in auto._next
        assert await auto.tick(auto._next["brief"]) == ["brief"]  # the other one runs
        refused = await call(k, "automations.enable", name="review")
        assert not refused.ok and "automations.reschedule" in str(refused.error)
        await call(k, "automations.disable", name="brief")  # the file is written again ...
        assert len(read_yaml(project.module_configs / "automations.yaml")["rules"]) == 2  # kept

        fixed = await call(k, "automations.reschedule", name="review", when="every monday at 9:00")
        assert fixed.ok, fixed.error
        assert "Monday at 09:00" in user.questions[-1] and fixed.output["next"]
        assert await auto.tick(auto._next["review"]) == ["review"]
    saved = read_yaml(project.module_configs / "automations.yaml")["rules"]
    assert [r["when"] for r in saved if r["name"] == "review"] == ["every monday at 9:00"]


async def test_a_new_rule_still_has_to_say_which_day(project: Paths) -> None:
    async with Kernel(project, ui=User(True)) as k:
        made = await call(k, "automations.add", name="review", when="weekly at 9:00", say="hi")
    assert not made.ok and "say which day" in str(made.error)
