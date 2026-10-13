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
from starlight.core.toolroute import ToolRouter, active_tools

#: Adds context before the model sees a request (e.g. recalled memories). Returns text
#: for an extra system message, or None.
type Enricher = Callable[[str], Awaitable[str | None]]

# Ends a tool result that was cut to tools.max_result_chars.
CUT_RESULT = "\n[... {more} more characters not shown; ask for a part, or use a smaller limit]"

# Added to an answer the model was still writing when it reached max_tokens.
CUT_OFF = (
    "\n\n[Cut off: the answer reached the length limit (model.reserve_tokens in the "
    "config). Ask for the rest, or for a shorter answer.]"
)


# Added to an answer when the model server stopped sending in the middle of it.
STALLED = (
    "\n\n[The model stopped answering part way - the model server may be stuck or "
    "restarting. Ask again in a moment.]"
)


@dataclass
class AgentResult:
    request_id: str
    answer: str
    tool_results: list[ToolResult] = field(default_factory=list)
    steps: int = 0
    stopped: bool = False  # True if the kill switch ended the run
    failed: bool = False  # True if the model could not be reached or refused the request
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
        router: ToolRouter | None = None,
        max_result_chars: int = 12000,
    ) -> None:
        self.provider = provider
        self.router = router
        self.max_result_chars = max_result_chars
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
                    try:
                        extra = await enrich(request)
                    except (KillSwitchEngaged, asyncio.CancelledError):
                        raise
                    except Exception as exc:  # e.g. the embedding model is down
                        log.warning("context lookup skipped", error=f"{type(exc).__name__}: {exc}")
                        if self.stats is not None:
                            self.stats.record("errors", source="enricher")
                        continue
                    if extra:
                        messages.append(Message("system", extra))
                active = await self._initial_tools(request, history or [])
                token = active_tools.set(active)
                messages += [*(history or []), Message("user", request, images=images or [])]
                result.messages = messages
                try:
                    await self._loop(messages, result, on_token)
                finally:
                    active_tools.reset(token)
        except (KillSwitchEngaged, asyncio.CancelledError):
            if not self.kill_switch.engaged:  # stopped another way: Ctrl+C, shutdown
                self.audit.record("task.cancelled", request_id=request_id, steps=result.steps)
                log.info("task cancelled", steps=result.steps)
                raise
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                task.uncancel()  # the kill was handled here; let the caller carry on
            result.stopped = True
            result.answer = f"Stopped by kill switch: {self.kill_switch.reason}"
            self.audit.record("task.killed", request_id=request_id, steps=result.steps)
            await self.bus.publish("task.killed", {"request_id": request_id})
            log.warning("task stopped by kill switch", steps=result.steps)
            return result

        if result.failed:  # the model could not answer: nothing to learn from or count
            self.audit.record("task.failed", request_id=request_id, steps=result.steps)
            await self.bus.publish(
                "task.failed",
                {"request_id": request_id, "request": request, "answer": result.answer},
            )
            log.warning("task failed", steps=result.steps)  # (counted as an error above)
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
                "tool_results": list(result.tool_results),
            },
        )
        if self.stats is not None:
            self.stats.record("tasks.completed", steps=result.steps)
        log.info("task completed", steps=result.steps)
        return result

    async def _initial_tools(self, request: str, history: list[Message]) -> set[str] | None:
        """The tools this request starts with (None: all of them)."""
        if self.router is None:
            return None
        try:
            selection = await self.router.select(request, history)
        except Exception as exc:  # never let routing stop a request: send everything
            self._log.warning("tool routing failed", error=f"{type(exc).__name__}: {exc}")
            return None
        if not self.router.config.tool_routing:
            return None
        if self.stats is not None:
            self.stats.record("agent.tools_offered", len(selection.names))
        return set(selection.names)

    def _schemas(self) -> list[dict[str, Any]]:
        """Sorted by name, so the same tools give the same prompt (prefix caching)."""
        active = active_tools.get()
        tools = self.dispatcher.tools()
        if active is not None:
            tools = [t for t in tools if t.qualified_name in active]
        return [t.json_schema() for t in tools]

    def _tool_text(self, tool_result: ToolResult) -> str:
        text = tool_result.to_text()
        limit = self.max_result_chars
        if len(text) <= limit:
            return text
        return text[:limit] + CUT_RESULT.format(more=len(text) - limit)

    async def _chat(self, messages: list[Message], on_token: TokenCallback | None) -> ModelResponse:
        tools = self._schemas()
        sent = messages
        if self.context is not None:
            compacted = await self.context.compact(messages, tools)
            if compacted is not messages:
                messages[:] = compacted  # the summary replaces the old turns for good
            sent = self.context.shorten(messages, tools)  # a cut is only for this request
        if on_token is not None and getattr(self.provider, "supports_streaming", False):
            return await self.provider.chat(sent, tools, on_token=on_token)  # type: ignore[call-arg]
        return await self.provider.chat(sent, tools)

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
                if self.stats is not None:
                    self.stats.record("errors", source="model")
                result.failed = True
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status == 400:  # llama-server: the prompt does not fit the context
                    result.answer = (
                        f"The model ({self.provider.name}) turned the request down, most "
                        "likely because it is too long for its context window. Send less "
                        "(a shorter text or fewer files), or start a new conversation."
                    )
                else:
                    result.answer = (
                        f"The model ({self.provider.name}) is not available: "
                        f"{type(exc).__name__}: {exc}. Is the model server running? "
                        "Try `starlight model serve`."
                    )
                return
            self._record_usage(response, result)
            self.kill_switch.check()  # pressed while the model was writing: don't keep it
            messages.append(Message("assistant", response.content, tool_calls=response.tool_calls))
            if not response.tool_calls:
                result.answer = response.content
                note = {"length": CUT_OFF, "stalled": STALLED}.get(
                    str(response.usage.get("finish_reason"))
                )
                if note:
                    result.answer += note
                    if on_token is not None and getattr(self.provider, "supports_streaming", False):
                        on_token(note)  # streamed answers show it too
                return
            for call in response.tool_calls:
                tool_result = await self.dispatcher.call(
                    call.name, call.arguments, request_id=result.request_id
                )
                result.tool_results.append(tool_result)
                messages.append(
                    Message(
                        "tool", self._tool_text(tool_result), tool_call_id=call.id, name=call.name
                    )
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
