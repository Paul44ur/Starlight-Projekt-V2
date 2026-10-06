"""Manage Starlight's own access and permissions from the chat, so you never edit files.

* set a secret (an API key, a token) straight into the OS keyring
* decide which actions run on their own, always ask, or never run
* see everything that is currently allowed: servers, login sites, secrets, permissions

Adding a server is in the network module (network.allow); adding a login is in the login
module (login.add_site). This module ties them together and shows the whole picture.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.config import read_yaml, update_local_config
from starlight.core.permissions import Permission

KEY = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
PATTERN = re.compile(r"^[a-z0-9_.][a-z0-9_.*?-]*$")


class AccessConfig(BaseModel):
    pass


class AccessModule(Module):
    Config = AccessConfig

    async def setup(self, ctx: ModuleContext) -> None:
        self.ctx = ctx

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
        update_local_config(self.ctx.paths, {"permissions": self.ctx.policy.rules()})
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
    async def clear_rule(self, pattern: str) -> dict[str, Any]:
        """Remove a permission rule set earlier, back to the action's default."""
        pattern = pattern.strip()
        if not self.ctx.policy.clear_rule(pattern):
            raise ToolError(f"no rule for '{pattern}'")
        update_local_config(self.ctx.paths, {"permissions": self.ctx.policy.rules()})
        return {"cleared": pattern}

    # -- overview ------------------------------------------------------------------------

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
