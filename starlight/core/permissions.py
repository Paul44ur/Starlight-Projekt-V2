"""Permissions. Every tool is tagged safe, needs-confirmation or blocked. Config patterns
can override the tag, so you choose which actions run on their own and which ask first."""

from __future__ import annotations

import fnmatch
import time
from collections import deque
from collections.abc import Iterable
from contextvars import ContextVar
from enum import StrEnum
from pathlib import Path

from starlight.core.config import (
    LIMIT_UNITS,
    PermissionsConfig,
    parse_limit,
    read_yaml,
    write_yaml_config,
)

# Rules changed from the chat (access module), kept apart from your own config files:
# each pattern maps to "safe", "confirm", "blocked" or "default" (rule removed). They are
# applied over config/starlight.yaml at start, so new defaults shipped there still apply.
CHAT_RULES_FILE = "permissions.chat.yaml"


class Permission(StrEnum):
    SAFE = "safe"  # runs without asking
    CONFIRM = "confirm"  # asks you first (logins, input emulation, running code ...)
    BLOCKED = "blocked"  # never runs


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


#: Extra "always ask" patterns for the request being handled - set while a request from
#: another device (the web chat over Tailscale) runs, so desktop control, code and your
#: commands ask on that device even when they normally run on their own.
extra_confirm: ContextVar[tuple[str, ...]] = ContextVar("extra_confirm", default=())
#: Patterns that never run for the request being handled (set for another device: changing
#: permissions, access or automations stays on the PC).
extra_blocked: ContextVar[tuple[str, ...]] = ContextVar("extra_blocked", default=())


class Taint:
    """What a request has read from outside so far (see PermissionsConfig.untrusted_sources):
    one is made per request (Agent.run), and the dispatcher fills it in."""

    def __init__(self) -> None:
        self.sources: list[str] = []


#: The request being handled; None outside one (automations, direct tool calls).
request_taint: ContextVar[Taint | None] = ContextVar("request_taint", default=None)
#: Why a question is asked, when it is not the tool's usual one (the confirmers show it).
confirm_reason: ContextVar[str] = ContextVar("confirm_reason", default="")


class PermissionPolicy:
    def __init__(self, config: PermissionsConfig | None = None) -> None:
        self.config = config or PermissionsConfig()
        self._session_allowed: set[str] = set()
        self._timed: dict[str, float] = {}  # pattern -> until when it runs without asking
        self._runs: dict[str, deque[float]] = {}  # limit pattern -> when its tools ran
        # Practice mode: tools that ask first by their nature (they change something) are
        # only described, not run; looking things up still works (the dispatcher).
        self.practice = False

    def resolve(self, tool_name: str, default: Permission) -> Permission:
        """Effective permission. Order: blocked (yours, the tool's own, the other
        device's) > the other device's confirm > this tool allowed in particular ("always"
        this session, its exact name in auto_approve and not also in confirm, or a pattern
        allowed for a while) > confirm patterns > auto_approve patterns > the tool's own
        tag. So allowing `desktop.click` works even while `desktop.*` asks."""
        if (
            _matches(tool_name, self.config.blocked)
            or default is Permission.BLOCKED
            or _matches(tool_name, list(extra_blocked.get()))
        ):
            return Permission.BLOCKED
        if _matches(tool_name, list(extra_confirm.get())):
            return Permission.CONFIRM
        if (
            tool_name in self._session_allowed
            or (tool_name in self.config.auto_approve and tool_name not in self.config.confirm)
            or _matches(tool_name, list(self.timed_rules()))
        ):
            return Permission.SAFE
        if _matches(tool_name, self.config.confirm):
            return Permission.CONFIRM
        if _matches(tool_name, self.config.auto_approve):
            return Permission.SAFE
        return default

    def set_rule(self, pattern: str, permission: Permission) -> None:
        """Make ``pattern`` (a tool name or glob like 'network.*') run on its own
        (SAFE/auto_approve), always ask (CONFIRM) or never run (BLOCKED). Applies now; the
        access module also saves it so it survives a restart."""
        for rules in (self.config.auto_approve, self.config.confirm, self.config.blocked):
            while pattern in rules:
                rules.remove(pattern)
        target = {
            Permission.SAFE: self.config.auto_approve,
            Permission.CONFIRM: self.config.confirm,
            Permission.BLOCKED: self.config.blocked,
        }[permission]
        target.append(pattern)

    def clear_rule(self, pattern: str) -> bool:
        """Remove a rule added with set_rule. Returns True if one was removed."""
        found = False
        for rules in (self.config.auto_approve, self.config.confirm, self.config.blocked):
            while pattern in rules:
                rules.remove(pattern)
                found = True
        return found

    def rules(self) -> dict[str, list[str]]:
        return {
            "auto_approve": list(self.config.auto_approve),
            "confirm": list(self.config.confirm),
            "blocked": list(self.config.blocked),
        }

    def is_untrusted_source(self, tool_name: str) -> bool:
        return self.config.guard_after_untrusted and _matches(
            tool_name, self.config.untrusted_sources
        )

    def guarded(self, tool_name: str, default: Permission, resolved: Permission) -> str:
        """Why this call asks although it would run on its own: the request read something
        from outside, and the tool is one that asks first by its nature. "" otherwise."""
        taint = request_taint.get()
        if (
            not self.config.guard_after_untrusted
            or taint is None
            or not taint.sources
            or resolved is not Permission.SAFE
            or default is not Permission.CONFIRM
            or tool_name in self._session_allowed
        ):
            return ""
        return (
            f"This request just read outside content ({', '.join(taint.sources[-3:])}), "
            "which could try to talk Starlight into this. "
        )

    def allow_for_session(self, tool_name: str) -> None:
        """Stop asking for this tool until the core restarts."""
        self._session_allowed.add(tool_name)

    # -- for a while -----------------------------------------------------------------------

    def allow_for(self, pattern: str, seconds: float) -> None:
        """Let ``pattern`` run without asking for ``seconds`` (not kept over a restart)."""
        self._timed[pattern] = time.time() + seconds

    def end_timed(self, pattern: str) -> bool:
        return self._timed.pop(pattern, None) is not None

    def timed_rules(self) -> dict[str, float]:
        """The patterns allowed for a while, and until when (time.time())."""
        now = time.time()
        for pattern in [p for p, until in self._timed.items() if until <= now]:
            del self._timed[pattern]
        return dict(self._timed)

    # -- limits ----------------------------------------------------------------------------

    def _limits_for(self, tool_name: str) -> list[tuple[str, int, int]]:
        found = []
        for pattern, text in self.config.limits.items():
            parsed = parse_limit(text)
            if parsed is not None and fnmatch.fnmatchcase(tool_name, pattern):
                found.append((pattern, *parsed))
        return found

    def over_limit(self, tool_name: str) -> str:
        """Why this call asks although it may run on its own: its tools reached a limit
        (PermissionsConfig.limits). "" otherwise."""
        now = time.time()
        for pattern, count, seconds in self._limits_for(tool_name):
            runs = self._runs.setdefault(pattern, deque())
            while runs and runs[0] <= now - seconds:
                runs.popleft()
            if len(runs) >= count:
                unit = next(u for u, s in LIMIT_UNITS.items() if s == seconds)
                return (
                    f"'{pattern}' ran {len(runs)} times in the last {unit}, your limit "
                    f"({self.config.limits[pattern]}). "
                )
        return ""

    def count_run(self, tool_name: str) -> None:
        """A call ran: count it towards its limits."""
        for pattern, _count, _seconds in self._limits_for(tool_name):
            self._runs.setdefault(pattern, deque()).append(time.time())

    # -- in plain words --------------------------------------------------------------------

    def summary(self, tools: Iterable[tuple[str, Permission]]) -> dict[str, list[str]]:
        """Which of these (name, own tag) tools run on their own, ask first or never run,
        with a whole module shortened to "module.*" when all its tools are alike."""
        groups: dict[str, dict[str, list[str]]] = {}
        for name, default in tools:
            state = self.resolve(name, default).value
            module = name.split(".", 1)[0]
            groups.setdefault(module, {}).setdefault(state, []).append(name)
        out: dict[str, list[str]] = {p.value: [] for p in Permission}
        for module, states in sorted(groups.items()):
            if len(states) == 1:
                out[next(iter(states))].append(f"{module}.*")
                continue
            for state, names in states.items():
                out[state] += sorted(names)
        return out


def saved_rules(config_dir: Path) -> dict[str, str]:
    data = read_yaml(config_dir / CHAT_RULES_FILE).get("rules") or {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def save_rule(config_dir: Path, pattern: str, setting: str) -> None:
    """Remember a rule change made from the chat (setting: a Permission value or "default")."""
    rules = saved_rules(config_dir)
    rules[pattern] = setting
    write_yaml_config(
        config_dir / CHAT_RULES_FILE,
        {"rules": rules},
        "# Permission rules changed from the chat (access module), applied over\n"
        '# config/starlight.yaml. "default" means the rule was removed. Safe to edit.',
    )


def apply_saved_rules(policy: PermissionPolicy, config_dir: Path) -> None:
    for pattern, setting in saved_rules(config_dir).items():
        if setting == "default":
            policy.clear_rule(pattern)
        elif setting in {p.value for p in Permission}:
            policy.set_rule(pattern, Permission(setting))
