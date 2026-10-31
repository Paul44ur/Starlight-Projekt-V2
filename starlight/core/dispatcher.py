"""The one dispatcher every tool call goes through: kill switch, permissions, validation,
timeout, audit log and events."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from starlight.core.config import ConfigError
from starlight.core.events import EventBus
from starlight.core.killswitch import KillSwitch, KillSwitchEngaged
from starlight.core.logging import AuditLog, get_logger, redact
from starlight.core.permissions import (
    Permission,
    PermissionPolicy,
    confirm_reason,
    request_taint,
)
from starlight.core.stats import Stats, current_model
from starlight.core.tools import Tool, ToolError

type Confirmer = Callable[[Tool, dict[str, Any]], Awaitable[bool]]

#: Where the tools of the request being handled are reported as they run (the web chat's
#: tool timeline): {"tool", "state": running/done/failed/refused/practice, "ms", "error"}.
tool_sink: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "tool_sink", default=None
)


def _report(tool: str, state: str, **details: Any) -> None:
    sink = tool_sink.get()
    if sink is not None:
        sink({"tool": tool, "state": state, **details})


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
        stats: Stats | None = None,
    ) -> None:
        self.stats = stats
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
        why, guard = "", self.policy.guarded(name, tool.permission, permission)
        if guard:  # the injection guard: ask, although it would run on its own
            why = "injection guard"
        elif permission is Permission.SAFE and (guard := self.policy.over_limit(name)):
            why = "limit"  # it ran as often as you allow on its own
        if guard:
            permission = Permission.CONFIRM
        if permission is Permission.BLOCKED:
            self.audit.record("tool.denied", tool=name, reason="blocked", request_id=request_id)
            return self._finish(
                ToolResult(name, False, error="blocked by permissions"), request_id, "refused"
            )
        try:  # before asking you: never confirm a call that cannot run
            validated = tool.validate(args)
        except ValidationError as exc:
            return self._finish(
                ToolResult(name, False, error=f"invalid arguments: {exc.errors()}"), request_id
            )
        if self.policy.practice and tool.permission is Permission.CONFIRM:
            # Practice mode: an action that changes something is described, not done.
            self.audit.record("tool.practice", tool=name, args=redact(args), request_id=request_id)
            shown = json.dumps(redact(args), default=str, ensure_ascii=False)
            return self._finish(
                ToolResult(
                    name,
                    True,
                    output=f"Practice mode: {name} did NOT run. It would have run with {shown}. "
                    "Tell the user what would have happened; nothing was changed.",
                ),
                request_id,
                "practice",
            )
        if permission is Permission.CONFIRM:
            reason = confirm_reason.set(guard)
            try:
                approved = self.confirmer is not None and await self.confirmer(tool, args)
            finally:
                confirm_reason.reset(reason)
            if not approved:
                self.audit.record(
                    "tool.denied",
                    tool=name,
                    reason="not confirmed" + (f" ({why})" if why else ""),
                    request_id=request_id,
                )
                return self._finish(
                    ToolResult(name, False, error="not confirmed by user"), request_id, "refused"
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
        _report(name, "running")
        started = time.perf_counter()
        budget = asyncio.timeout(None if tool.own_timeout else self.timeout)
        try:
            async with budget:
                output = await tool.run(validated)
            result = ToolResult(name, True, output=output)
        except ToolError as exc:  # turned down for a reason, not a crash
            self._log.warning("tool refused", tool=name, reason=str(exc))
            result = ToolResult(name, False, error=str(exc))
        except ConfigError as exc:  # a settings file the user can fix
            self._log.warning("tool config error", tool=name, error=str(exc))
            result = ToolResult(name, False, error=f"configuration error: {exc}")
        except TimeoutError as exc:
            if budget.expired():
                error = f"timed out after {self.timeout}s"
            else:  # the tool's own limit (ssh_run, a wait_for inside it), not ours
                error = f"timed out: {exc}" if str(exc) else "timed out"
            self._log.warning("tool timed out", tool=name, error=error)
            result = ToolResult(name, False, error=error)
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
        if result.ok:
            self.policy.count_run(name)
        taint = request_taint.get()
        if result.ok and taint is not None and self.policy.is_untrusted_source(name):
            taint.sources.append(name)  # from now on in this request, the guard is up
        return self._finish(result, request_id)

    def _finish(
        self, result: ToolResult, request_id: str | None, state: str | None = None
    ) -> ToolResult:
        _report(
            result.tool,
            state or ("done" if result.ok else "failed"),
            ms=result.duration_ms,
            **({"error": str(result.error)[:200]} if result.error else {}),
        )
        if self.stats is not None:
            module = result.tool.split(".", 1)[0]
            model = {"model": current_model.get()} if current_model.get() else {}
            self.stats.record(
                "tool.calls", 1, module=module, tool=result.tool, ok=result.ok, **model
            )
            if result.duration_ms:
                self.stats.record("tool.duration_ms", result.duration_ms, tool=result.tool)
        # scripts/e2e_chat.py (tool_ran) reads "event", "tool" and "ok" of these lines.
        self.audit.record(
            "tool.result",
            tool=result.tool,
            ok=result.ok,
            error=result.error,
            duration_ms=result.duration_ms,
            request_id=request_id,
        )
        return result
