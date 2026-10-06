"""The one dispatcher every tool call goes through: kill switch, permissions, validation,
timeout, audit log and events."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from starlight.core.events import EventBus
from starlight.core.killswitch import KillSwitch, KillSwitchEngaged
from starlight.core.logging import AuditLog, get_logger, redact
from starlight.core.permissions import Permission, PermissionPolicy
from starlight.core.tools import Tool

type Confirmer = Callable[[Tool, dict[str, Any]], Awaitable[bool]]


@dataclass
class ToolResult:
    tool: str
    ok: bool
    output: Any = None
    error: str | None = None
    duration_ms: float = 0.0

    def to_text(self) -> str:
        """Compact form fed back to the model."""
        if self.ok:
            return (
                self.output
                if isinstance(self.output, str)
                else json.dumps(self.output, default=str)
            )
        return f"ERROR: {self.error}"


class Dispatcher:
    def __init__(
        self,
        kill_switch: KillSwitch,
        policy: PermissionPolicy,
        bus: EventBus,
        audit: AuditLog | None = None,
        confirmer: Confirmer | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.kill_switch = kill_switch
        self.policy = policy
        self.bus = bus
        self.audit = audit or AuditLog()
        self.confirmer = confirmer
        self.timeout = timeout
        self._tools: dict[str, Tool] = {}
        self._log = get_logger("dispatcher")

    # -- registry ----------------------------------------------------------------------

    def register(self, tool: Tool) -> None:
        name = tool.qualified_name
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = tool

    def unregister_module(self, module: str) -> None:
        self._tools = {n: t for n, t in self._tools.items() if t.module != module}

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def tools(self) -> list[Tool]:
        return sorted(self._tools.values(), key=lambda t: t.qualified_name)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.json_schema() for t in self.tools()]

    # -- calls -------------------------------------------------------------------------

    async def call(
        self, name: str, arguments: dict[str, Any] | None = None, request_id: str | None = None
    ) -> ToolResult:
        """Run one tool call. Raises KillSwitchEngaged if the kill switch is engaged
        before or during the call; every other failure becomes a failed ToolResult."""
        args = arguments or {}
        self.kill_switch.check()

        tool = self._tools.get(name)
        if tool is None:
            return self._finish(ToolResult(name, False, error=f"unknown tool '{name}'"), request_id)

        permission = self.policy.resolve(name, tool.permission)
        if permission is Permission.BLOCKED:
            self.audit.record("tool.denied", tool=name, reason="blocked", request_id=request_id)
            return self._finish(ToolResult(name, False, error="blocked by permissions"), request_id)
        if permission is Permission.CONFIRM:
            approved = self.confirmer is not None and await self.confirmer(tool, args)
            if not approved:
                self.audit.record(
                    "tool.denied", tool=name, reason="not confirmed", request_id=request_id
                )
                return self._finish(
                    ToolResult(name, False, error="not confirmed by user"), request_id
                )
            self.kill_switch.check()  # the question may have taken a while

        self.audit.record(
            "tool.call",
            tool=name,
            args=redact(args),
            permission=permission.value,
            request_id=request_id,
        )
        await self.bus.publish("tool.called", {"tool": name, "request_id": request_id})
        started = time.perf_counter()
        try:
            output = await asyncio.wait_for(tool.invoke(args), timeout=self.timeout)
            result = ToolResult(name, True, output=output)
        except ValidationError as exc:
            result = ToolResult(name, False, error=f"invalid arguments: {exc.errors()}")
        except TimeoutError:
            result = ToolResult(name, False, error=f"timed out after {self.timeout}s")
        except KillSwitchEngaged:
            self.audit.record("tool.killed", tool=name, request_id=request_id)
            raise
        except asyncio.CancelledError:
            if self.kill_switch.engaged:
                self.audit.record("tool.killed", tool=name, request_id=request_id)
                raise KillSwitchEngaged(self.kill_switch.reason or "kill switch") from None
            raise
        except Exception as exc:
            self._log.exception("tool failed", tool=name, module=tool.module)
            result = ToolResult(name, False, error=f"{type(exc).__name__}: {exc}")
        result.duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return self._finish(result, request_id)

    def _finish(self, result: ToolResult, request_id: str | None) -> ToolResult:
        self.audit.record(
            "tool.result",
            tool=result.tool,
            ok=result.ok,
            error=result.error,
            duration_ms=result.duration_ms,
            request_id=request_id,
        )
        return result
