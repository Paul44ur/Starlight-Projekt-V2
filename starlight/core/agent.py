"""Agent loop: receive a request, let the model plan, call tools through the dispatcher,
feed results back, answer. The kill switch is checked before every step."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from starlight.core.config import AgentConfig
from starlight.core.context import ContextManager
from starlight.core.dispatcher import Dispatcher, ToolResult
from starlight.core.events import EventBus
from starlight.core.killswitch import KillSwitch, KillSwitchEngaged
from starlight.core.logging import AuditLog, get_logger
from starlight.core.model import Message, ModelProvider, ModelResponse, TokenCallback
from starlight.core.stats import Stats

#: Adds context before the model sees a request (e.g. recalled memories). Returns text
#: for an extra system message, or None.
type Enricher = Callable[[str], Awaitable[str | None]]


@dataclass
class AgentResult:
    request_id: str
    answer: str
    tool_results: list[ToolResult] = field(default_factory=list)
    steps: int = 0
    stopped: bool = False  # True if the kill switch ended the run
    messages: list[Message] = field(default_factory=list)
    usage: list[dict[str, Any]] = field(default_factory=list)


class Agent:
    def __init__(
        self,
        provider: ModelProvider,
        dispatcher: Dispatcher,
        kill_switch: KillSwitch,
        bus: EventBus,
        config: AgentConfig | None = None,
        audit: AuditLog | None = None,
        context: ContextManager | None = None,
        stats: Stats | None = None,
    ) -> None:
        self.provider = provider
        self.dispatcher = dispatcher
        self.kill_switch = kill_switch
        self.bus = bus
        self.config = config or AgentConfig()
        self.audit = audit or AuditLog()
        self.context = context
        self.stats = stats
        self.enrichers: list[Enricher] = []
        self._log = get_logger("agent")

    async def run(
        self,
        request: str,
        history: list[Message] | None = None,
        on_token: TokenCallback | None = None,
        images: list[bytes] | None = None,
    ) -> AgentResult:
        request_id = uuid.uuid4().hex[:12]
        result = AgentResult(request_id=request_id, answer="")
        log = self._log.bind(request_id=request_id)
        self.audit.record("task.requested", request_id=request_id, request=request)
        await self.bus.publish("task.requested", {"request_id": request_id, "request": request})
        log.info("task started")

        try:
            with self.kill_switch.guard():
                self.kill_switch.check()
                messages = [Message("system", self.config.system_prompt)]
                for enrich in self.enrichers:
                    extra = await enrich(request)
                    if extra:
                        messages.append(Message("system", extra))
                messages += [*(history or []), Message("user", request, images=images or [])]
                result.messages = messages
                await self._loop(messages, result, on_token)
        except (KillSwitchEngaged, asyncio.CancelledError):
            if not self.kill_switch.engaged:
                raise  # cancelled for another reason (e.g. shutdown)
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                task.uncancel()  # the kill was handled here; let the caller carry on
            result.stopped = True
            result.answer = f"Stopped by kill switch: {self.kill_switch.reason}"
            self.audit.record("task.killed", request_id=request_id, steps=result.steps)
            await self.bus.publish("task.killed", {"request_id": request_id})
            log.warning("task stopped by kill switch", steps=result.steps)
            return result

        self.audit.record(
            "task.completed",
            request_id=request_id,
            steps=result.steps,
            tool_calls=len(result.tool_results),
        )
        await self.bus.publish(
            "task.completed",
            {
                "request_id": request_id,
                "request": request,
                "steps": result.steps,
                "answer": result.answer,
                "tools": [r.tool for r in result.tool_results],
            },
        )
        if self.stats is not None:
            self.stats.record("tasks.completed", steps=result.steps)
        log.info("task completed", steps=result.steps)
        return result

    async def _chat(self, messages: list[Message], on_token: TokenCallback | None) -> ModelResponse:
        tools = self.dispatcher.schemas()  # same order every time: prompt prefix caching
        if self.context is not None:
            fitted = await self.context.fit(messages, tools)
            if fitted is not messages:
                messages[:] = fitted
        if on_token is not None and getattr(self.provider, "supports_streaming", False):
            return await self.provider.chat(messages, tools, on_token=on_token)  # type: ignore[call-arg]
        return await self.provider.chat(messages, tools)

    async def _loop(
        self, messages: list[Message], result: AgentResult, on_token: TokenCallback | None
    ) -> None:
        for _ in range(self.config.max_steps):
            self.kill_switch.check()
            result.steps += 1
            try:
                response = await self._chat(messages, on_token)
            except (KillSwitchEngaged, asyncio.CancelledError):
                raise
            except Exception as exc:  # model server down, bad response ...
                self._log.exception("model call failed", provider=self.provider.name)
                result.answer = (
                    f"The model ({self.provider.name}) is not available: "
                    f"{type(exc).__name__}: {exc}. Is the model server running? "
                    "Try `starlight model serve`."
                )
                return
            self._record_usage(response, result)
            messages.append(Message("assistant", response.content, tool_calls=response.tool_calls))
            if not response.tool_calls:
                result.answer = response.content
                return
            for call in response.tool_calls:
                tool_result = await self.dispatcher.call(
                    call.name, call.arguments, request_id=result.request_id
                )
                result.tool_results.append(tool_result)
                messages.append(
                    Message("tool", tool_result.to_text(), tool_call_id=call.id, name=call.name)
                )
        result.answer = f"Stopped after {self.config.max_steps} steps without a final answer."

    def _record_usage(self, response: ModelResponse, result: AgentResult) -> None:
        if not response.usage:
            return
        result.usage.append(response.usage)
        if self.stats is None:
            return
        model = str(response.usage.get("model", self.provider.name))
        for key in ("tokens_per_second", "prompt_tokens", "completion_tokens", "duration_s"):
            value = response.usage.get(key)
            if isinstance(value, int | float):
                self.stats.record(f"model.{key}", value, model=model)
