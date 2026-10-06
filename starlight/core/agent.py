"""Agent loop: receive a request, let the model plan, call tools through the dispatcher,
feed results back, answer. The kill switch is checked before every step."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from starlight.core.config import AgentConfig
from starlight.core.dispatcher import Dispatcher, ToolResult
from starlight.core.events import EventBus
from starlight.core.killswitch import KillSwitch, KillSwitchEngaged
from starlight.core.logging import AuditLog, get_logger
from starlight.core.model import Message, ModelProvider


@dataclass
class AgentResult:
    request_id: str
    answer: str
    tool_results: list[ToolResult] = field(default_factory=list)
    steps: int = 0
    stopped: bool = False  # True if the kill switch ended the run
    messages: list[Message] = field(default_factory=list)


class Agent:
    def __init__(
        self,
        provider: ModelProvider,
        dispatcher: Dispatcher,
        kill_switch: KillSwitch,
        bus: EventBus,
        config: AgentConfig | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.provider = provider
        self.dispatcher = dispatcher
        self.kill_switch = kill_switch
        self.bus = bus
        self.config = config or AgentConfig()
        self.audit = audit or AuditLog()
        self._log = get_logger("agent")

    async def run(self, request: str, history: list[Message] | None = None) -> AgentResult:
        request_id = uuid.uuid4().hex[:12]
        result = AgentResult(request_id=request_id, answer="")
        log = self._log.bind(request_id=request_id)
        messages = [
            Message("system", self.config.system_prompt),
            *(history or []),
            Message("user", request),
        ]
        result.messages = messages
        self.audit.record("task.requested", request_id=request_id, request=request)
        await self.bus.publish("task.requested", {"request_id": request_id, "request": request})
        log.info("task started")

        try:
            with self.kill_switch.guard():
                await self._loop(messages, result)
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
            {"request_id": request_id, "steps": result.steps, "answer": result.answer},
        )
        log.info("task completed", steps=result.steps)
        return result

    async def _loop(self, messages: list[Message], result: AgentResult) -> None:
        for _ in range(self.config.max_steps):
            self.kill_switch.check()
            result.steps += 1
            response = await self.provider.chat(messages, self.dispatcher.schemas())
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
