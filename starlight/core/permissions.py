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

    def allow_for_session(self, tool_name: str) -> None:
        """Stop asking for this tool until the core restarts."""
        self._session_allowed.add(tool_name)
