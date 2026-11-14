"""Automations: "when X, do Y".

A rule has a trigger - a schedule ("weekdays 7:30", "every 2 hours") or an event
("network.server_down", "calendar.soon", optionally with a condition) - and actions:
run a tool, ask the assistant something, or send you a message. Results flow from one
action to the next as {result}; event fields are {event.<field>}. {event.name} is the
event's name, unless the event carries a field called "name" of its own (backup.done
does): then it is that field.

Safety:
- every action goes through the dispatcher: permission rules, the kill switch and the
  audit log apply as always, and blocked tools stay blocked;
- nobody is there to say yes when a rule runs, so a tool that normally asks first only
  runs if you approved it for that rule when you made it (``allow``); anything else that
  wants a confirmation is refused, and you are told;
- making, changing or re-enabling a rule always asks first (on the Automations page the
  click on Save is that answer, and the page shows what the rule may run without asking);
- rules run only in the primary Starlight process, never twice.

A tool step a rule was not allowed to run waits in the approval queue (when the approvals
module is on), so one click runs it after all.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import json
import re
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr, ValidationError, ValidationInfo, model_validator

from starlight.core import Event, Module, ModuleContext, ToolError, tool
from starlight.core.config import write_yaml_config
from starlight.core.fsutil import write_atomic
from starlight.core.jsonfile import load_json, save_json
from starlight.core.permissions import Permission
from starlight.core.schedule import ScheduleError, parse_schedule
from starlight.core.ui import current_ui
from starlight.notebook import query

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
MAX_DEPTH = 3  # an automation triggering another one, at most this deep
MAX_TEXT = 4000
HISTORY_KEEP = 500
GRACE = timedelta(minutes=10)  # a run missed by less than this (sleep, restart) still happens
NEW_RULE = "new_rule"  # validation context: a rule being made now, not one read from the file

# How deeply automations are nested right now: a rule's tool published an event that
# started another rule. The bus calls _on_event synchronously in the publishing tool's
# context, and asyncio.create_task copies that context, so a run started from there sees
# the depth of the run that caused it.
_depth: ContextVar[int] = ContextVar("automation_depth", default=0)


class Action(BaseModel):
    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    ask: str = ""
    say: str = ""
    only_if: str = ""  # skip unless the previous result matches, e.g. "count > 0"

    @model_validator(mode="after")
    def _one_kind(self, info: ValidationInfo) -> Action:
        if sum(bool(x) for x in (self.tool, self.ask, self.say)) != 1:
            raise ValueError("an action is exactly one of: tool, ask, say")
        problem = condition_problem(self.only_if)
        if problem and (info.context or {}).get(NEW_RULE):
            raise ValueError(f"only_if cannot be read: {problem}")
        return self

    def describe(self) -> str:
        if self.tool:
            args = f" {json.dumps(self.args, ensure_ascii=False)}" if self.args else ""
            text = f"run {self.tool}{args}"
        elif self.ask:
            text = f'ask "{self.ask}"'
        else:
            text = f'tell you "{self.say}"'
        return f"{text} (only if {self.only_if})" if self.only_if else text


class Rule(BaseModel):
    name: str
    enabled: bool = True
    when: str = ""
    on: str = ""
    condition: str = ""
    actions: list[Action] = Field(min_length=1)
    # Tools that may run without asking: `allow` as the rule's own tool steps (with the
    # arguments written in the rule), `allow_anywhere` also when an "ask" step lets the
    # model pick a tool and its arguments (given with automations.allow).
    allow: list[str] = Field(default_factory=list)
    allow_anywhere: list[str] = Field(default_factory=list)
    # A saved rule whose schedule is not understood (edited by hand, or "every week at
    # 9:00", which older versions ran every day): it stays in the file but does not run,
    # and says what is wrong, so the other rules still load. A new rule is refused instead.
    _problem: str = PrivateAttr(default="")

    @model_validator(mode="after")
    def _trigger(self, info: ValidationInfo) -> Rule:
        if not NAME.match(self.name):
            raise ValueError("a name is lowercase letters, digits, - or _ (max 40)")
        if bool(self.when) == bool(self.on):
            raise ValueError("a rule has either a schedule (when) or an event (on)")
        problem = condition_problem(self.condition)
        if problem and (info.context or {}).get(NEW_RULE):
            raise ValueError(f"the condition cannot be read: {problem}")
        if self.when:
            try:
                parse_schedule(self.when)
            except ScheduleError as exc:
                if (info.context or {}).get(NEW_RULE):
                    raise  # (_build passes the message on as it is)
                self._problem = str(exc)
        return self

    @property
    def problem(self) -> str:
        """Why this saved rule cannot run (its schedule); empty when it can."""
        return self._problem

    def describe(self) -> str:
        if self.problem:
            trigger = f'"{self.when}" (does not run until the schedule is fixed: {self.problem})'
        elif self.when:
            trigger = parse_schedule(self.when).describe()
        else:
            trigger = f"when {self.on} happens" + (
                f" and {self.condition}" if self.condition else ""
            )
        steps = "; then ".join(a.describe() for a in self.actions)
        return f"{trigger}: {steps}"


class Config(BaseModel):
    check_interval: float = Field(default=30, ge=5)
    rules: list[Rule] = Field(default_factory=list)


# The events modules send, with their fields (for the rule editor and automations.events).
EVENTS: dict[str, tuple[str, list[str]]] = {
    "calendar.soon": ("an event is about to start", ["title", "start"]),
    "reminder.due": ("a reminder is due", ["id", "text"]),
    "mail.new": ("new mail arrived (with mail watch on)", ["id", "from", "subject", "date"]),
    "network.server_down": ("one of your servers stopped answering", ["server", "host"]),
    "network.server_up": ("a server answers again", ["server", "host"]),
    "lan.new_device": ("a device never seen before joined your network", ["ip", "mac"]),
    "lan.device_joined": ("a known device came onto your network", ["ip", "mac", "name"]),
    "lan.device_left": ("a known device left your network", ["ip", "mac", "name"]),
    "home.changed": ("a smart-home device changed", ["entity", "old", "new"]),
    "watch.price": ("a watched price crossed your limit", ["name", "url", "price", "message"]),
    "watch.changed": ("a watched page changed", ["name", "url", "message"]),
    "backup.done": ("a backup finished", ["name", "files"]),
    "files.saved": ("Starlight saved a file", ["path"]),
    "focus.started": ("focus mode began", ["until"]),
    "focus.ended": ("focus mode ended", []),
    "sensors.file_appeared": ("a new file in a folder you watch", ["path", "name", "folder"]),
    "sensors.drive_added": ("a USB stick or drive was plugged in", ["drive", "label"]),
    "sensors.drive_removed": ("a USB stick or drive was taken out", ["drive", "label"]),
    "sensors.battery_low": ("the battery is low, on battery", ["percent"]),
    "sensors.power_plugged": ("the charger was plugged in", ["percent"]),
    "sensors.power_unplugged": ("the charger was unplugged", ["percent"]),
    "sensors.app_started": ("a program was started", ["name"]),
    "sensors.away": ("no mouse or keyboard for a while", ["idle_minutes"]),
    "sensors.back": ("you are back at the PC", ["away_minutes"]),
}

TEMPLATES: dict[str, dict[str, Any]] = {
    "morning-briefing": {
        "when": "weekdays 7:30",
        "actions": [{"tool": "briefing.send"}],
    },
    "evening-todo": {
        "when": "every day at 18:00",
        "actions": [
            {"tool": "todo.due", "args": {"days": 1}},
            {
                "say": "Still open for today and tomorrow: {result.count} task(s)",
                "only_if": "count > 0",
            },
        ],
    },
    "daily-flashcards": {
        "when": "every day at 19:00",
        "actions": [
            {"tool": "flashcards.due", "args": {"limit": 1}},
            {
                "say": "{result.total} flashcard(s) are waiting for you today. Say 'quiz me', "
                "or study them in Notes, Cards.",
                "only_if": "total > 0",
            },
        ],
    },
    "weekly-open-notes": {
        "when": "friday 15:40",
        "actions": [
            {
                "tool": "notes.query",
                "args": {"where": "status is open or status is doing", "sort": "due", "limit": 20},
            },
            {
                "say": "{result.count} note(s) still say status: open or doing. Say 'show my "
                "open notes' to go through them.",
                "only_if": "count > 0",
            },
        ],
    },
    "nightly-backups": {
        "when": "every day at 3:00",
        "actions": [{"ask": "Run all my backups (backup.list, then backup.run for each)."}],
    },
    "memory-tidy": {
        "when": "every month on the 1st at 10:00",
        "actions": [
            {"tool": "memory.duplicates"},
            {
                "say": "{result.count} pairs of memories say nearly the same thing. "
                "Say 'tidy my memory' to merge them.",
                "only_if": "count > 0",
            },
        ],
    },
    "weekly-cert-check": {
        "when": "monday 9:00",
        "actions": [
            {"tool": "certs.expiring", "args": {"days": 30}},
            {"say": "Certificates expiring soon: {result.expiring}", "only_if": "expiring"},
        ],
    },
    "morning-briefing-aloud": {
        "when": "weekdays 7:30",
        "actions": [{"tool": "briefing.read_aloud"}],
    },
    "overnight-mail": {
        "when": "every day at 6:45",
        "actions": [{"tool": "mail.overnight", "args": {"send": True}}],
    },
    "weekly-pc-checkup": {
        "when": "sunday 18:00",
        "actions": [{"tool": "system.health_report", "args": {"send": True}}],
    },
    "monthly-review": {
        "when": "every month on the 1st at 9:00",
        "actions": [
            {"tool": "journal.month", "args": {"month": "last"}},
            {"say": "Your monthly review is ready: {result.saved}"},
        ],
    },
    "weekly-plan": {
        "when": "monday 8:00",
        "actions": [
            {
                "ask": "Plan my week: read my calendar for the next 7 days (calendar.agenda) "
                "and my open to-dos (todo.list_tasks), then save a short plan for the week as "
                "a note (notes.note) called 'Week plan' with today's date."
            }
        ],
    },
    "battery-low": {
        "on": "sensors.battery_low",
        "actions": [{"say": "The battery is at {event.percent}%: plug in the charger."}],
    },
}


class AutomationUI:
    """Who answers while a rule runs: yes only to the tools approved for this rule."""

    def __init__(self, module: AutomationsModule, rule: Rule) -> None:
        self.module, self.rule = module, rule
        self.refused: list[str] = []
        self.step_tool: str | None = None  # the tool step running now (None in "ask" steps)
        self.step_args: dict[str, Any] = {}
        self.waiting: list[tuple[str, dict[str, Any]]] = []  # refused tool steps, to approve

    async def confirm(self, question: str) -> bool:
        # The dispatcher asks "Run <tool> with <args>?" (starlight/core/kernel.py:
        # question_for); the tool name is read back from that text. Any other question -
        # a tool's own, or one with a reason in front (the injection guard's) - is refused.
        match = re.match(r"Run (\S+) with", question)
        name = match.group(1) if match else None
        if name is not None and (
            name in self.rule.allow_anywhere or (name == self.step_tool and name in self.rule.allow)
        ):
            return True
        self.refused.append(name or question[:120])
        if name is not None and name == self.step_tool:
            self.waiting.append((name, self.step_args))
        return False

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        # ctx.deliver already reaches every channel and then calls this; pass it on to
        # the terminal (the default interface) only - delivering again would loop.
        default = getattr(self.module.ctx.ui, "default", None)
        if default is not None and default is not self:
            await default.notify(text)


class AutomationsModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.state_file = ctx.data_dir / "state.json"
        self.history_file = ctx.data_dir / "history.jsonl"
        self._next: dict[str, datetime] = {}
        for rule in self.config.rules:
            if rule.problem:
                ctx.logger.warning("automation not run", rule=rule.name, problem=rule.problem)
        self._restore()
        self._runs: set[asyncio.Task[Any]] = set()
        # Every event passes here, so the check is cheap and synchronous; only a matching
        # rule starts a task.
        ctx.bus.subscribe("*", self._on_event, owner=ctx.name)
        ctx.every(self.config.check_interval, self.tick, "schedule", first_delay=1)

    def _now(self) -> datetime:
        return datetime.now().replace(microsecond=0)

    # -- persistence ---------------------------------------------------------------------

    def _write_rules(self) -> None:
        write_yaml_config(
            self.ctx.paths.module_configs / "automations.yaml",
            {
                "check_interval": self.config.check_interval,
                "rules": [r.model_dump(exclude_defaults=True) for r in self.config.rules],
            },
            "# The automations module (modules/automations). Made from the chat; see the\n"
            "# module's documentation for the rule format.",
        )

    def _restore(self) -> None:
        try:
            saved = load_json(self.state_file, {})
        except OSError:  # locked: start from the schedules instead
            saved = {}
        now = self._now()
        for rule in self.config.rules:
            if not rule.when or rule.problem:
                continue
            when = _parse_time(saved.get(rule.name))
            if when is None or when < now - GRACE:  # never set, or missed long ago: skip it
                when = parse_schedule(rule.when).next_after(now)
            self._next[rule.name] = when

    def _save_state(self) -> None:
        data = {name: when.isoformat() for name, when in self._next.items()}
        try:
            save_json(self.state_file, data)
        except OSError:  # only the next run times: a rule that is due must still run
            self.ctx.logger.exception("could not save the automation schedule")

    def _record(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self.history_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        with contextlib.suppress(OSError):
            lines = self.history_file.read_text("utf-8").splitlines()
            # Trimmed only at twice the size, so the file is not rewritten on every run.
            if len(lines) > HISTORY_KEEP * 2:
                write_atomic(self.history_file, "\n".join(lines[-HISTORY_KEEP:]) + "\n")

    # -- triggers ------------------------------------------------------------------------

    async def tick(self, now: datetime | None = None) -> list[str]:
        """Run every scheduled rule that is due. Returns the names that ran."""
        moment = now or self._now()
        ran: list[str] = []
        for rule in list(self.config.rules):
            if not (rule.enabled and rule.when) or rule.problem:
                continue
            due = self._next.get(rule.name)
            if due is None:
                self._next[rule.name] = parse_schedule(rule.when).next_after(moment)
                continue
            if due > moment:
                continue
            self._next[rule.name] = parse_schedule(rule.when).next_after(moment)
            self._save_state()
            await self.run(rule, {"time": moment.isoformat()})
            ran.append(rule.name)
        return ran

    def _on_event(self, event: Event) -> None:
        if event.source == self.ctx.name or not self.ctx.services.primary:
            return
        if _depth.get() >= MAX_DEPTH:
            return
        for rule in self.config.rules:
            if not (rule.enabled and rule.on) or not fnmatch.fnmatchcase(event.name, rule.on):
                continue
            if rule.condition and not matches(rule.condition, event.data):
                continue
            # The event's own fields come last, so a "name" field of its own wins over the
            # event's name in {event.name}.
            task = asyncio.create_task(
                self.run(rule, {"name": event.name, **event.data}), name=f"automation-{rule.name}"
            )
            self._runs.add(task)
            task.add_done_callback(self._runs.discard)

    async def settle(self) -> None:
        """Wait for automations started by events (used by the tests)."""
        while self._runs:
            await asyncio.gather(*list(self._runs), return_exceptions=True)

    async def shutdown(self) -> None:
        for task in list(self._runs):
            task.cancel()

    # -- running -------------------------------------------------------------------------

    async def run(self, rule: Rule, event: dict[str, Any]) -> dict[str, Any]:
        """Run a rule's actions in order. Stops at the first failure."""
        ui = AutomationUI(self, rule)
        token_ui = current_ui.set(ui)
        token_depth = _depth.set(_depth.get() + 1)
        steps: list[dict[str, Any]] = []
        result: Any = None
        ok = True
        try:
            for action in rule.actions:
                if action.only_if and not matches(action.only_if, result):
                    steps.append({"action": action.describe(), "skipped": True})
                    continue
                ui.step_tool = action.tool or None
                ui.step_args = fill(action.args, event, result) if action.tool else {}
                try:
                    ok, result, error = await self._act(action, event, result)
                except Exception as exc:  # the kill switch, a crashed tool: stop this run
                    ok, error = False, f"{type(exc).__name__}: {exc}"
                finally:
                    ui.step_tool = None
                steps.append({"action": action.describe(), "ok": ok, "error": error})
                if not ok:
                    break
        finally:
            _depth.reset(token_depth)
            current_ui.reset(token_ui)
        if ui.refused:
            ok = False
            queued = await self._queue(rule, ui.waiting)
            await self.ctx.deliver(
                f"'{rule.name}' needed your OK for {', '.join(sorted(set(ui.refused)))}, which "
                "it isn't allowed to give itself, so that step did not run. "
                + (
                    "It waits in the approval queue (Activity page) for one click. "
                    if queued
                    else ""
                )
                + "To let this rule run it without asking, use automations.allow.",
                title="Automation",
            )
        entry = {"time": self._now().isoformat(), "rule": rule.name, "ok": ok, "steps": steps}
        self._record(entry)
        self.ctx.logger.info("automation ran", rule=rule.name, ok=ok)
        return entry

    async def _queue(self, rule: Rule, steps: list[tuple[str, dict[str, Any]]]) -> int:
        """Put refused tool steps into the approval queue; how many went in."""
        if not steps or not self.ctx.has_tool("approvals.add"):
            return 0
        count = 0
        for name, args in steps:
            added = await self.ctx.call(
                "approvals.add",
                {"title": f"Automation '{rule.name}': {name}", "tool": name, "args": args,
                 "detail": f"From the automation '{rule.name}': {rule.describe()}"},
            )  # fmt: skip
            count += added.ok
        return count

    async def _act(
        self, action: Action, event: dict[str, Any], previous: Any
    ) -> tuple[bool, Any, str | None]:
        if action.tool:
            args = fill(action.args, event, previous)
            outcome = await self.ctx.call(action.tool, args)
            return outcome.ok, outcome.output, outcome.error
        if action.ask:
            answer = await self.ctx.run_request(fill(action.ask, event, previous))
            return True, answer, None
        await self.ctx.deliver(fill(action.say, event, previous)[:MAX_TEXT], title="Automation")
        return True, previous, None

    # -- tools ---------------------------------------------------------------------------

    def _rule(self, name: str) -> Rule:
        for rule in self.config.rules:
            if rule.name == name.strip().lower():
                return rule
        raise ToolError(f"no automation named '{name}'; see automations.list")

    def _needs_ok(self, rule: Rule) -> list[str]:
        """Tools in the rule that would normally ask first."""
        names = []
        for action in rule.actions:
            if not action.tool:
                continue
            if not self.ctx.has_tool(action.tool):
                raise ToolError(f"there is no tool '{action.tool}' (is its module enabled?)")
            permission = self.ctx.permission_of(action.tool)
            if permission is Permission.BLOCKED:
                raise ToolError(f"'{action.tool}' is blocked by your permission rules")
            if permission is Permission.CONFIRM:
                names.append(action.tool)
        return names

    async def _save_rule(self, rule: Rule, confirmed: bool = False) -> dict[str, Any]:
        """Save a new rule, after asking (``confirmed``: the user already said yes, on the
        Automations page)."""
        others = [r for r in self.config.rules if r.name != rule.name]
        if len(others) != len(self.config.rules):
            raise ToolError(f"an automation named '{rule.name}' exists; pick another name")
        needs = self._needs_ok(rule)
        question = f"Create the automation '{rule.name}': {rule.describe()}?"
        if needs:
            question += f" It may run {', '.join(needs)} without asking each time."
        if not confirmed and not await self.ctx.ui.confirm(question):
            raise ToolError("not confirmed, nothing changed")
        rule.allow = sorted(set(rule.allow) | set(needs))
        self.config.rules = [*others, rule]
        self._write_rules()
        if rule.when:
            self._next[rule.name] = parse_schedule(rule.when).next_after(self._now())
            self._save_state()
        return {"saved": rule.name, "rule": rule.describe(), "next": self._next_text(rule)}

    def _next_text(self, rule: Rule) -> str | None:
        when = self._next.get(rule.name)
        return when.strftime("%Y-%m-%d %H:%M") if when and rule.when and rule.enabled else None

    @tool
    async def add(
        self,
        name: str,
        when: str = "",
        on: str = "",
        condition: str = "",
        tool: str = "",
        args: dict[str, Any] | None = None,
        ask: str = "",
        say: str = "",
    ) -> dict[str, Any]:
        """Make an automation. Trigger: `when` is a schedule ("weekdays 7:30", "every 2
        hours", "monday 9am") OR `on` is an event name ("network.server_down",
        "calendar.soon", "sensors.drive_added"; all of them: automations.events) with an
        optional `condition` on its fields ("server == nas" for network.server_down; several
        with and, or, not: "server is nas or server is router").
        Actions, run in this order: `tool` (a tool name like "briefing.send", with
        `args`), then `ask` (a question for the assistant), then `say` (a message to you;
        may contain {result} and {event.<field>}). Asks for confirmation before saving."""
        actions: list[dict[str, Any]] = []
        if tool.strip():
            actions.append({"tool": tool.strip(), "args": args or {}})
        if ask.strip():
            actions.append({"ask": ask.strip()})
        if say.strip():
            actions.append({"say": say.strip()})
        if not actions:
            raise ToolError("say what it should do: give a `tool`, an `ask` or a `say`")
        rule = self._build(
            {
                "name": name.strip().lower(),
                "when": when,
                "on": on,
                "condition": condition,
                "actions": actions,
            }
        )
        return await self._save_rule(rule)

    @staticmethod
    def _build(data: dict[str, Any]) -> Rule:
        try:
            return Rule.model_validate(data, context={NEW_RULE: True})
        except ScheduleError as exc:
            raise ToolError(str(exc)) from exc
        except ValidationError as exc:
            problems = "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())
            raise ToolError(f"that automation is not valid: {problems}") from exc

    # -- the Automations page (a click there is the user's answer) ------------------------

    def check_rule(self, data: dict[str, Any]) -> dict[str, Any]:
        """What a rule from the page would do, and what it may run without asking."""
        rule = self._build(data)
        return {"does": rule.describe(), "may_run_without_asking": self._needs_ok(rule)}

    async def save_from_page(self, data: dict[str, Any]) -> dict[str, Any]:
        return await self._save_rule(self._build(data), confirmed=True)

    async def page_action(self, name: str, action: str) -> dict[str, Any]:
        rule = self._rule(name)
        if action == "enable":
            self._refuse_broken(rule)
            rule.enabled = True
            if rule.when:
                self._next[rule.name] = parse_schedule(rule.when).next_after(self._now())
                self._save_state()
            self._write_rules()
            return {"enabled": rule.name, "next": self._next_text(rule)}
        if action == "disable":
            return self.disable(name)
        if action == "remove":
            return self.remove(name)
        if action == "run":
            return await self.run_now(name)
        raise ToolError(f"unknown action '{action}'")

    def template_rules(self) -> dict[str, dict[str, Any]]:
        """Each template: what it does, and the rule to fill the page's form with."""
        return {
            name: {"does": self._build({"name": name, **data}).describe(),
                   "rule": {"name": name, **data}}
            for name, data in TEMPLATES.items()
        }  # fmt: skip

    @tool
    def events(self) -> dict[str, Any]:
        """The events an automation can start on ("on"), with the fields a condition or
        a message can use ({event.<field>})."""
        return {name: {"when": when, "fields": fields} for name, (when, fields) in EVENTS.items()}

    @tool
    def templates(self) -> dict[str, Any]:
        """Ready-made automations you can add with automations.use_template."""
        return {
            name: self._build({"name": name, **data}).describe() for name, data in TEMPLATES.items()
        }

    @tool
    async def use_template(self, template: str, when: str = "") -> dict[str, Any]:
        """Add a ready-made automation (see automations.templates), optionally with your
        own schedule `when`. Asks first."""
        data = TEMPLATES.get(template.strip().lower())
        if data is None:
            raise ToolError(f"no template '{template}'; see automations.templates")
        fields = {"name": template.strip().lower(), **data}
        if when.strip():
            fields["when"] = when  # your schedule instead of the template's
        return await self._save_rule(self._build(fields))

    @tool(name="list")
    def list_rules(self) -> dict[str, Any]:
        """Your automations: what triggers them, what they do, and when they run next."""
        return {
            "count": len(self.config.rules),
            "automations": [
                {
                    "name": r.name,
                    "enabled": r.enabled,
                    "does": r.describe(),
                    "next": self._next_text(r),
                    "may_run_without_asking": r.allow,
                    **({"problem": r.problem} if r.problem else {}),
                }
                for r in self.config.rules
            ],
        }

    @tool
    async def enable(self, name: str) -> dict[str, Any]:
        """Switch an automation back on. Asks first."""
        rule = self._rule(name)
        self._refuse_broken(rule)
        if not await self.ctx.ui.confirm(
            f"Switch on the automation '{rule.name}': {rule.describe()}?"
        ):
            raise ToolError("not confirmed, nothing changed")
        rule.enabled = True
        if rule.when:
            self._next[rule.name] = parse_schedule(rule.when).next_after(self._now())
            self._save_state()
        self._write_rules()
        return {"enabled": rule.name, "next": self._next_text(rule)}

    def _refuse_broken(self, rule: Rule) -> None:
        if rule.problem:
            raise ToolError(
                f"'{rule.name}' can't run until its schedule is fixed ({rule.problem}); "
                "give it a new one with automations.reschedule"
            )

    @tool
    async def reschedule(self, name: str, when: str) -> dict[str, Any]:
        """Give a scheduled automation a new schedule `when` ("every monday at 9:00"),
        keeping what it does. Also fixes a saved rule whose schedule is not understood.
        Asks first."""
        rule = self._rule(name)
        if not rule.when:
            raise ToolError(f"'{rule.name}' runs on an event ({rule.on}), not on a schedule")
        fixed = self._build({**rule.model_dump(), "when": when})
        if not await self.ctx.ui.confirm(
            f"Change the automation '{rule.name}': {fixed.describe()}?"
        ):
            raise ToolError("not confirmed, nothing changed")
        self.config.rules = [fixed if r is rule else r for r in self.config.rules]
        self._write_rules()
        self._next[fixed.name] = parse_schedule(fixed.when).next_after(self._now())
        self._save_state()
        return {"rescheduled": fixed.name, "rule": fixed.describe(), "next": self._next_text(fixed)}

    @tool
    def disable(self, name: str) -> dict[str, Any]:
        """Switch an automation off (it is kept; automations.enable turns it on again)."""
        rule = self._rule(name)
        rule.enabled = False
        self._write_rules()
        return {"disabled": rule.name}

    @tool
    def remove(self, name: str) -> dict[str, Any]:
        """Delete an automation."""
        rule = self._rule(name)
        self.config.rules.remove(rule)
        self._next.pop(rule.name, None)
        self._write_rules()
        self._save_state()
        return {"removed": rule.name}

    @tool
    async def allow(self, name: str, tool: str) -> dict[str, Any]:
        """Let an automation run a tool that normally asks first - also when one of its
        "ask" steps lets the model decide what to run it with. Asks first."""
        rule = self._rule(name)
        if not await self.ctx.ui.confirm(
            f"Let the automation '{rule.name}' run {tool} without asking each time, with "
            "whatever it decides to run it with?"
        ):
            raise ToolError("not confirmed, nothing changed")
        rule.allow = sorted(set(rule.allow) | {tool.strip()})
        rule.allow_anywhere = sorted(set(rule.allow_anywhere) | {tool.strip()})
        self._write_rules()
        return {"allowed": tool.strip(), "for": rule.name}

    @tool
    async def run_now(self, name: str) -> dict[str, Any]:
        """Run an automation once, now, to try it."""
        rule = self._rule(name)
        return await self.run(rule, {"time": self._now().isoformat(), "manual": True})

    @tool
    def history(self, limit: int = 10, name: str = "") -> dict[str, Any]:
        """What the automations did recently (newest first)."""
        try:
            lines = self.history_file.read_text("utf-8").splitlines()
        except OSError:
            lines = []
        entries = []
        for line in reversed(lines):
            with contextlib.suppress(json.JSONDecodeError):
                entry = json.loads(line)
                if not name or entry.get("rule") == name.strip().lower():
                    entries.append(entry)
            if len(entries) >= max(1, min(limit, 100)):
                break
        return {"count": len(entries), "runs": entries}

    # -- how the runs went, for the Automations page -------------------------------------------

    def _entries(self) -> list[dict[str, Any]]:
        """Every run in the history file, oldest first. A missing file, an empty one and
        lines that are not JSON give fewer entries, never an error."""
        try:
            lines = self.history_file.read_text("utf-8", errors="replace").splitlines()
        except OSError:
            return []
        entries: list[dict[str, Any]] = []
        for line in lines:
            with contextlib.suppress(json.JSONDecodeError):
                entry = json.loads(line)
                if isinstance(entry, dict) and isinstance(entry.get("rule"), str):
                    entries.append(entry)
        return entries

    def last_runs(self) -> dict[str, dict[str, str]]:
        """The newest run of every rule that ran, as run_summary() (one read of the file)."""
        newest: dict[str, dict[str, Any]] = {}
        for entry in self._entries():
            newest[entry["rule"]] = entry  # later lines are newer
        return {name: run_summary(entry) for name, entry in newest.items()}

    def rule_runs(self, name: str, limit: int = 10) -> list[dict[str, str]]:
        """The last runs of one rule, newest first. Raises ToolError for an unknown rule."""
        self._rule(name)
        mine = [e for e in self._entries() if e["rule"] == name.strip().lower()]
        return [run_summary(entry) for entry in reversed(mine[-max(1, min(limit, 100)) :])]


# -- helpers (pure) ------------------------------------------------------------------------

_RUN_TEXT = 300  # a reason can be long (a tool's own message): the page needs a short one


def run_summary(entry: dict[str, Any]) -> dict[str, str]:
    """One history entry as {time, outcome, text}. The outcome is one fixed word: done,
    failed, waiting (a tool needed your OK and went to the approval queue), skipped (every
    step's condition was false) or stopped (the kill switch). text is the reason, if any."""
    raw = str(entry.get("time") or "")
    when = _parse_time(raw)
    shown = when.strftime("%Y-%m-%d %H:%M") if when else raw[:16].replace("T", " ")
    steps = [s for s in entry.get("steps") or [] if isinstance(s, dict)]
    error = next((str(s.get("error")) for s in reversed(steps) if s.get("error")), "")
    if entry.get("ok"):
        done = [s for s in steps if not s.get("skipped")]
        return {"time": shown, "outcome": "done" if done or not steps else "skipped", "text": ""}
    if error.startswith("KillSwitchEngaged"):
        reason = error.partition(": ")[2]
        return {"time": shown, "outcome": "stopped", "text": reason[:_RUN_TEXT]}
    if not error or error == "not confirmed by user":
        # (a refused step, or a refusal inside an "ask" step: it waits for your OK)
        return {"time": shown, "outcome": "waiting", "text": ""}
    return {"time": shown, "outcome": "failed", "text": error[:_RUN_TEXT]}


_OPS = ("<=", ">=", "==", "!=", "<", ">", "~")


def _parse_time(value: Any) -> datetime | None:
    """A saved time from state.json, or None if it is missing or not a usable time."""
    try:
        when = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return when if when is not None and when.tzinfo is None else None


def _lookup(data: Any, path: str) -> Any:
    value = data
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


# and, or, not (also in German) or a bracket: several conditions, read by the query language
_LOGIC = re.compile(r"(?<![\w-])(?:and|or|not|und|oder|nicht)(?![\w-])|[()]", re.IGNORECASE)


def _old_style(text: str) -> bool:
    """One `field op value` with spaces around the op, or a field alone: read as it always
    was (below), so every rule written before keeps its meaning."""
    if _LOGIC.search(text):
        return False
    return any(f" {op} " in text for op in _OPS) or bool(re.fullmatch(r"[\w.\-]+", text))


def condition_problem(condition: str) -> str:
    """Why a condition cannot be read (plain words), or "" when it can."""
    text = condition.strip()
    if not text or _old_style(text):
        return ""
    try:
        query.parse(text)
    except query.QueryError as exc:
        return str(exc)
    return ""


def matches(condition: str, data: Any) -> bool:
    """`field op value` (op: == != < <= > >= ~ for "contains"), or just `field` (true when
    present and not empty/zero). Numbers compare as numbers, everything else as text.

    Several conditions with and, or, not and brackets (und, oder, nicht), and the words is,
    is not, has, is empty, are read by the notes' query language (starlight.notebook.query):
    `count > 0 and server is nas`, `not (status is done)`. A condition it cannot read never
    holds (a new rule with one is refused when it is saved)."""
    text = condition.strip()
    if not _old_style(text):
        try:
            return query.evaluate(query.parse(text), data)
        except query.QueryError:
            return False
    # The first operator of _OPS found in the text (with a space on each side) splits it.
    # The spaces keep "<" from matching inside "<=", so the order of _OPS only decides
    # which operator wins when a condition contains two.
    op = next((op for op in _OPS if f" {op} " in text), None)
    if op is None:  # just a field name: true when present and not empty/zero
        return bool(_lookup(data, text))
    left, _, right = text.partition(f" {op} ")
    value = _lookup(data, left.strip())
    target = right.strip().strip("'\"")
    if value is None:  # no such field (a typo?): only "!=" holds, nothing fires by accident
        return op == "!="
    if op == "~":
        return target.lower() in str(value).lower()
    a: Any
    b: Any
    try:
        a, b = float(value), float(target)
    except (TypeError, ValueError):
        a, b = str(value).lower(), target.lower()
    return bool({
        "==": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b,
    }[op])  # fmt: skip


_PLACEHOLDER = re.compile(r"\{(event|result)((?:\.[A-Za-z0-9_]+)*)\}")


def fill(value: Any, event: dict[str, Any], result: Any) -> Any:
    """Put {event.field} and {result} / {result.field} into text (and into nested args)."""
    if isinstance(value, dict):
        return {k: fill(v, event, result) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, event, result) for v in value]
    if not isinstance(value, str):
        return value

    def swap(match: re.Match[str]) -> str:
        source = event if match.group(1) == "event" else result
        path = match.group(2).lstrip(".")
        found = _lookup(source, path) if path else source
        if isinstance(found, dict | list):
            return json.dumps(found, ensure_ascii=False, default=str)[:MAX_TEXT]
        return "" if found is None else str(found)[:MAX_TEXT]

    return _PLACEHOLDER.sub(swap, value)
