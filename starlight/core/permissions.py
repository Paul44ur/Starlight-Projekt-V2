"""Permissions. Every tool is tagged safe, needs-confirmation or blocked. Config patterns
can override the tag, so you choose which actions run on their own and which ask first."""

from __future__ import annotations

import fnmatch
from enum import StrEnum

from starlight.core.config import PermissionsConfig


class Permission(StrEnum):
    SAFE = "safe"  # runs without asking
    CONFIRM = "confirm"  # asks you first (logins, input emulation, running code ...)
    BLOCKED = "blocked"  # never runs


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


class PermissionPolicy:
    def __init__(self, config: PermissionsConfig | None = None) -> None:
        self.config = config or PermissionsConfig()
        self._session_allowed: set[str] = set()

    def resolve(self, tool_name: str, default: Permission) -> Permission:
        """Effective permission. Order: blocked > tool's own BLOCKED > confirm >
        auto_approve / allowed this session > the tool's own tag."""
        if _matches(tool_name, self.config.blocked) or default is Permission.BLOCKED:
            return Permission.BLOCKED
        if _matches(tool_name, self.config.confirm):
            return Permission.CONFIRM
        if tool_name in self._session_allowed or _matches(tool_name, self.config.auto_approve):
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

    def allow_for_session(self, tool_name: str) -> None:
        """Stop asking for this tool until the core restarts."""
        self._session_allowed.add(tool_name)
