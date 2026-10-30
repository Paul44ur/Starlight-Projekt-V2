"""Manage Starlight's own access and permissions from the chat, so you never edit files.

* set a secret (an API key, a token) straight into the OS keyring
* decide which actions run on their own, always ask, or never run
* see everything that is currently allowed: servers, login sites, secrets, permissions

Adding a server is in the network module (network.allow); adding a login is in the login
module (login.add_site). This module ties them together and shows the whole picture.
"""

from __future__ import annotations

import re
import time
from typing import Any

from starlight.core import Module, ToolError, tool
from starlight.core.config import read_yaml
from starlight.core.permissions import Permission, save_rule

KEY = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
PATTERN = re.compile(r"^[a-z0-9_.][a-z0-9_.*?-]*$")


class AccessModule(Module):
    # -- secrets -------------------------------------------------------------------------

    @tool
    async def set_secret(self, key: str, secret: str) -> dict[str, Any]:
        """Store a secret (an API key, token or password) in the OS keyring under `key`
        (e.g. "web.brave_api_key"). Use this so the user never has to run a command or open
        a file. The value is hidden: it is never shown back, logged or put in a prompt.
        Asks first."""
        key = key.strip().lower()
        if not KEY.match(key):
            raise ToolError("key: lowercase letters, digits, '.', '_' and '-' (e.g. api.key)")
        if not secret:
            raise ToolError("give the secret value")
        if not await self.ctx.ui.confirm(f"Store a secret under '{key}'? (the value stays hidden)"):
            raise ToolError("not confirmed, so nothing was stored")
        self.ctx.kill_switch.check()
        self.ctx.vault.set(key, secret)
        self.ctx.logger.info("secret set", key=key)
        return {"stored": key, "note": "saved in the OS keyring; the value is not shown"}

    @tool
    async def forget_secret(self, key: str) -> dict[str, Any]:
        """Delete a secret from the OS keyring by its key name."""
        key = key.strip().lower()
        if key not in self.ctx.vault.names():
            raise ToolError(f"no secret named '{key}'")
        if not await self.ctx.ui.confirm(f"Delete the secret '{key}'?"):
            raise ToolError("not confirmed")
        self.ctx.vault.delete(key)
        return {"deleted": key}

    # -- permissions ---------------------------------------------------------------------

    async def _set_rule(
        self, pattern: str, permission: Permission, question: str
    ) -> dict[str, Any]:
        pattern = pattern.strip()
        if not PATTERN.match(pattern):
            raise ToolError("give a tool name or pattern, e.g. 'network.ping' or 'recon.*'")
        if not await self.ctx.ui.confirm(question):
            raise ToolError("not confirmed, so the rule was not changed")
        self.ctx.kill_switch.check()
        self.ctx.policy.set_rule(pattern, permission)
        save_rule(self.ctx.paths.config, pattern, permission.value)
        self.ctx.logger.info("permission rule set", pattern=pattern, permission=permission.value)
        return {"rule": pattern, "permission": permission.value}

    @tool
    async def allow_action(self, pattern: str) -> dict[str, Any]:
        """Let an action run without asking every time (e.g. "network.ping" or "recon.*").
        Risky, so it asks once now and remembers the choice. Reconnaissance and reaching a
        host still only work on allow-listed targets."""
        return await self._set_rule(
            pattern, Permission.SAFE, f"Let '{pattern}' run from now on WITHOUT asking each time?"
        )

    @tool
    async def ask_action(self, pattern: str) -> dict[str, Any]:
        """Make an action ask for confirmation every time (undo allow_action)."""
        return await self._set_rule(
            pattern, Permission.CONFIRM, f"Make '{pattern}' ask for confirmation every time?"
        )

    @tool
    async def block_action(self, pattern: str) -> dict[str, Any]:
        """Forbid an action completely until you unblock it."""
        return await self._set_rule(
            pattern, Permission.BLOCKED, f"Forbid '{pattern}' completely (never run it)?"
        )

    @tool
    async def allow_action_for(self, pattern: str, minutes: int = 30) -> dict[str, Any]:
        """Let an action run without asking for a while (e.g. "desktop.*" for 30 minutes),
        then it asks again. Not kept over a restart. Asks once now."""
        pattern = pattern.strip()
        if not PATTERN.match(pattern):
            raise ToolError("give a tool name or pattern, e.g. 'network.ping' or 'recon.*'")
        minutes = max(1, min(int(minutes), 24 * 60))
        question = f"Let '{pattern}' run WITHOUT asking for the next {minutes} minutes?"
        if not await self.ctx.ui.confirm(question):
            raise ToolError("not confirmed, so nothing changed")
        self.ctx.kill_switch.check()
        self.ctx.policy.allow_for(pattern, minutes * 60)
        self.ctx.logger.info("permission for a while", pattern=pattern, minutes=minutes)
        return {"rule": pattern, "runs_without_asking_for_minutes": minutes}

    @tool
    async def practice_mode(self, on: bool = True) -> dict[str, Any]:
        """Practice mode: actions that change something (sending mail, clicking, running
        code, commands ...) are only described, not done; looking things up still works.
        Turning it off asks first."""
        leaving = not on and self.ctx.policy.practice
        if leaving and not await self.ctx.ui.confirm(
            "Leave practice mode, so actions run for real?"
        ):
            raise ToolError("not confirmed, so practice mode stays on")
        self.ctx.policy.practice = on
        self.ctx.logger.info("practice mode", on=on)
        return {"practice_mode": on}

    @tool
    def rules_in_plain_words(self) -> dict[str, Any]:
        """What Starlight may do right now, in three lists: runs without asking, asks
        first, never runs (a whole module shown as "module.*"), plus limits, actions
        allowed for a while and practice mode."""
        lookup, names = self.ctx.services.tool_lookup, self.ctx.services.tool_names
        tools = [
            (name, found.permission)
            for name in (names() if names is not None else [])
            if lookup is not None and (found := lookup(name)) is not None
        ]
        policy = self.ctx.policy
        summary = policy.summary(tools)
        now = time.time()
        return {
            "runs without asking": summary["safe"],
            "asks first": summary["confirm"],
            "never runs": summary["blocked"],
            "limits": dict(policy.config.limits),
            "allowed for a while": {
                p: f"{max(1, round((until - now) / 60))} more minutes"
                for p, until in policy.timed_rules().items()
            },
            "practice mode": policy.practice,
        }

    @tool
    async def clear_rule(self, pattern: str) -> dict[str, Any]:
        """Remove a permission rule set earlier, back to the action's default. Removing a
        block or an always-ask rule asks first, since the action may then run."""
        pattern = pattern.strip()
        if self.ctx.policy.end_timed(pattern):  # allowed for a while: it asks again now
            return {"cleared": pattern}
        rules = self.ctx.policy.rules()
        if not any(pattern in listed for listed in rules.values()):
            raise ToolError(f"no rule for '{pattern}'")
        loosening = pattern in rules["blocked"] or pattern in rules["confirm"]
        if loosening:
            was = "blocked" if pattern in rules["blocked"] else "set to always ask"
            question = f"'{pattern}' is {was}. Remove that rule, so it may run again?"
            if not await self.ctx.ui.confirm(question):
                raise ToolError("not confirmed, so the rule was kept")
        self.ctx.kill_switch.check()
        self.ctx.policy.clear_rule(pattern)
        save_rule(self.ctx.paths.config, pattern, "default")
        return {"cleared": pattern}

    # -- overview ------------------------------------------------------------------------

    @tool
    def recent_errors(self, count: int = 10) -> dict[str, Any]:
        """Show Starlight's own recent errors and warnings in plain language (what went
        wrong, where, and the underlying cause), so you can see problems from the chat."""
        from starlight.core.logging import recent_errors

        errors = recent_errors(self.ctx.paths.logs, limit=max(1, min(count, 100)))
        return {"count": len(errors), "errors": errors}

    @tool
    def status(self) -> dict[str, Any]:
        """Everything Starlight is currently allowed to do and reach: servers, login sites,
        stored secrets (names only) and the permission rules."""
        config = self.ctx.paths.config
        servers = read_yaml(config / "servers.yaml").get("servers") or {}
        logins = read_yaml(config / "modules" / "login.yaml").get("sites") or {}
        return {
            "servers": sorted(servers),
            "login_sites": sorted(logins),
            "secrets": self.ctx.vault.names(),  # names only, never values
            "permissions": self.ctx.policy.rules(),
        }
