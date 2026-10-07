"""Permissions. Every tool is tagged safe, needs-confirmation or blocked. Config patterns
can override the tag, so you choose which actions run on their own and which ask first."""

from __future__ import annotations

import fnmatch
from contextvars import ContextVar
from enum import StrEnum
from pathlib import Path

from starlight.core.config import PermissionsConfig, read_yaml, write_yaml_config

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


class PermissionPolicy:
    def __init__(self, config: PermissionsConfig | None = None) -> None:
        self.config = config or PermissionsConfig()
        self._session_allowed: set[str] = set()

    def resolve(self, tool_name: str, default: Permission) -> Permission:
        """Effective permission. Order: blocked > tool's own BLOCKED > confirm >
        auto_approve / allowed this session > the tool's own tag."""
        if (
            _matches(tool_name, self.config.blocked)
            or default is Permission.BLOCKED
            or _matches(tool_name, list(extra_blocked.get()))
        ):
            return Permission.BLOCKED
        if _matches(tool_name, self.config.confirm) or _matches(
            tool_name, list(extra_confirm.get())
        ):
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
