"""Automations: "when X, do Y".

A rule has a trigger - a schedule ("weekdays 7:30", "every 2 hours") or an event
("network.server_down", "calendar.soon", optionally with a condition) - and actions:
run a tool, ask the assistant something, or send you a message. Results flow from one
action to the next as {result}; event fields are {event.<field>}.

Safety:
- every action goes through the dispatcher: permission rules, the kill switch and the
  audit log apply as always, and blocked tools stay blocked;
- nobody is there to say yes when a rule runs, so a tool that normally asks first only
  runs if you approved it for that rule when you made it (``allow``); anything else that
  wants a confirmation is refused, and you are told;
- making, changing or re-enabling a rule always asks first;
- rules run only in the primary Starlight process, never twice.
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

from pydantic import BaseModel, Field, ValidationError, model_validator

from starlight.core import Event, Module, ModuleContext, ToolError, tool
from starlight.core.config import write_yaml_config
from starlight.core.permissions import Permission
from starlight.core.schedule import ScheduleError, parse_schedule
from starlight.core.ui import current_ui

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
MAX_DEPTH = 3  # an automation triggering another one, at most this deep
MAX_TEXT = 4000
HISTORY_KEEP = 500
GRACE = timedelta(minutes=10)  # a run missed by less than this (sleep, restart) still happens

_running: ContextVar[int] = ContextVar("automation_depth", default=0)


class Action(BaseModel):
    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    ask: str = ""
    say: str = ""
    only_if: str = ""  # skip unless the previous result matches, e.g. "count > 0"

    @model_validator(mode="after")
    def _one_kind(self) -> Action:
        if sum(bool(x) for x in (self.tool, self.ask, self.say)) != 1:
            raise ValueError("an action is exactly one of: tool, ask, say")
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

    @model_validator(mode="after")
    def _trigger(self) -> Rule:
        if not NAME.match(self.name):
            raise ValueError("a name is lowercase letters, digits, - or _ (max 40)")
        if bool(self.when) == bool(self.on):
            raise ValueError("a rule has either a schedule (when) or an event (on)")
        if self.when:
            parse_schedule(self.when)  # raises a clear error
        return self

    def describe(self) -> str:
        if self.when:
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
}


class AutomationUI:
    """Who answers while a rule runs: yes only to the tools approved for this rule."""

    def __init__(self, module: AutomationsModule, rule: Rule) -> None:
        self.module, self.rule = module, rule
        self.refused: list[str] = []
        self.step_tool: str | None = None  # the tool step running now (None in "ask" steps)

    async def confirm(self, question: str) -> bool:
        match = re.match(r"Run (\S+) with", question)
        name = match.group(1) if match else None
        if name is not None and (
            name in self.rule.allow_anywhere or (name == self.step_tool and name in self.rule.allow)
        ):
            return True
        self.refused.append(match.group(1) if match else question[:120])
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
            saved = json.loads(self.state_file.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            saved = {}
        now = self._now()
        for rule in self.config.rules:
            if not rule.when:
                continue
            due = saved.get(rule.name)
            when = datetime.fromisoformat(due) if isinstance(due, str) else None
            if when is None or when < now - GRACE:  # never set, or missed long ago: skip it
                when = parse_schedule(rule.when).next_after(now)
            self._next[rule.name] = when

    def _save_state(self) -> None:
        data = {name: when.isoformat() for name, when in self._next.items()}
        self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _record(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self.history_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        with contextlib.suppress(OSError):
            lines = self.history_file.read_text("utf-8").splitlines()
            if len(lines) > HISTORY_KEEP * 2:
                self.history_file.write_text("\n".join(lines[-HISTORY_KEEP:]) + "\n", "utf-8")

    # -- triggers ------------------------------------------------------------------------

    async def tick(self, now: datetime | None = None) -> list[str]:
        """Run every scheduled rule that is due. Returns the names that ran."""
        moment = now or self._now()
        ran: list[str] = []
        for rule in list(self.config.rules):
            if not (rule.enabled and rule.when):
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
        if _running.get() >= MAX_DEPTH:
            return
        for rule in self.config.rules:
            if not (rule.enabled and rule.on) or not fnmatch.fnmatchcase(event.name, rule.on):
                continue
            if rule.condition and not matches(rule.condition, event.data):
                continue
            task = asyncio.create_task(
                self.run(rule, {"name": event.name, **event.data}), name=f"automation-{rule.name}"
            )
            self._runs.add(task)
            task.add_done_callback(self._runs.discard)

    async def settle(self) -> None:
        """Wait for automations started by events (tests, shutdown)."""
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
        token_depth = _running.set(_running.get() + 1)
        steps: list[dict[str, Any]] = []
        result: Any = None
        ok = True
        try:
            for action in rule.actions:
                if action.only_if and not matches(action.only_if, result):
                    steps.append({"action": action.describe(), "skipped": True})
                    continue
                ui.step_tool = action.tool or None
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
            _running.reset(token_depth)
            current_ui.reset(token_ui)
        if ui.refused:
            ok = False
            await self.ctx.deliver(
                f"'{rule.name}' needed your OK for {', '.join(sorted(set(ui.refused)))}, which "
                "it isn't allowed to give itself, so that step did not run. Approve it for this "
                "rule with automations.allow if you want it to.",
                title="Automation",
            )
        entry = {"time": self._now().isoformat(), "rule": rule.name, "ok": ok, "steps": steps}
        self._record(entry)
        self.ctx.logger.info("automation ran", rule=rule.name, ok=ok)
        return entry

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
            if self._permission(action.tool) is Permission.BLOCKED:
                raise ToolError(f"'{action.tool}' is blocked by your permission rules")
            if self._permission(action.tool) is Permission.CONFIRM:
                names.append(action.tool)
        return names

    def _permission(self, name: str) -> Permission:
        return self.ctx.permission_of(name)

    async def _save_rule(self, rule: Rule, replace: bool = False) -> dict[str, Any]:
        others = [r for r in self.config.rules if r.name != rule.name]
        if len(others) == len(self.config.rules) and replace:
            raise ToolError(f"no automation named '{rule.name}'")
        if len(others) != len(self.config.rules) and not replace:
            raise ToolError(f"an automation named '{rule.name}' exists; pick another name")
        needs = self._needs_ok(rule)
        question = f"Create the automation '{rule.name}': {rule.describe()}?"
        if needs:
            question += f" It may run {', '.join(needs)} without asking each time."
        if not await self.ctx.ui.confirm(question):
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
        "calendar.soon", "reminder.due", "files.saved") with an optional `condition` on its
        fields ("server == nas" for network.server_down). Actions, run in this order: `tool` (a
        tool name like "briefing.send", with `args`), then `ask` (a question for the
        assistant), then `say` (a message to you; may contain {result} and
        {event.<field>}). Asks for confirmation before saving."""
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
            return Rule.model_validate(data)
        except ScheduleError as exc:
            raise ToolError(str(exc)) from exc
        except ValidationError as exc:
            problems = "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())
            raise ToolError(f"that automation is not valid: {problems}") from exc

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
        rule = self._build(
            {"name": template.strip().lower(), **data, **({"when": when} if when.strip() else {})}
        )
        return await self._save_rule(rule)

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
                }
                for r in self.config.rules
            ],
        }

    @tool
    async def enable(self, name: str) -> dict[str, Any]:
        """Switch an automation back on. Asks first."""
        rule = self._rule(name)
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


# -- helpers (pure) ------------------------------------------------------------------------

_OPS = ("<=", ">=", "==", "!=", "<", ">", "~")


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


def matches(condition: str, data: Any) -> bool:
    """`field op value` (op: == != < <= > >= ~ for "contains"), or just `field` (true when
    present and not empty/zero). Numbers compare as numbers, everything else as text."""
    text = condition.strip()
    for op in _OPS:
        left, found, right = text.partition(f" {op} ")
        if found:
            break
    else:
        return bool(_lookup(data, text))
    value = _lookup(data, left.strip())
    target = right.strip().strip("'\"")
    if value is None:  # no such field (a typo?): only "!=" holds, nothing fires by accident
        return op == "!="
    if op == "~":
        return target.lower() in str(value if value is not None else "").lower()
    a: Any
    b: Any
    try:
        a, b = float(value), float(target)
    except (TypeError, ValueError):
        a, b = str(value if value is not None else "").lower(), target.lower()
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
