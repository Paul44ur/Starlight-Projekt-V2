"""The kernel wires the core together: config, logging, event bus, kill switch,
permissions, dispatcher, model provider, agent and modules."""

from __future__ import annotations

import asyncio
import contextlib
from types import TracebackType

from starlight.core.agent import Agent, AgentResult
from starlight.core.config import StarlightConfig, load_config
from starlight.core.dispatcher import Confirmer, Dispatcher
from starlight.core.events import EventBus
from starlight.core.killswitch import KillSwitch
from starlight.core.logging import AuditLog, get_logger, setup_logging, shutdown_logging
from starlight.core.model import Message, ModelProvider, create_provider
from starlight.core.modules import ModuleManager
from starlight.core.paths import Paths
from starlight.core.permissions import PermissionPolicy


class Kernel:
    def __init__(
        self,
        paths: Paths | None = None,
        config: StarlightConfig | None = None,
        provider: ModelProvider | None = None,
        confirmer: Confirmer | None = None,
        hotkey: bool = False,
    ) -> None:
        self.paths = paths or Paths.discover()
        self.config = config or load_config(self.paths)
        self.paths.ensure()
        setup_logging(
            self.paths.logs,
            self.config.logging.level,
            self.config.logging.max_bytes,
            self.config.logging.backups,
        )
        self.log = get_logger("kernel")
        self.audit = AuditLog()
        self.bus = EventBus()
        self.kill_switch = KillSwitch(
            self.paths.root / self.config.kill_switch.stop_file, self.audit
        )
        self.policy = PermissionPolicy(self.config.permissions)
        self.dispatcher = Dispatcher(
            self.kill_switch,
            self.policy,
            self.bus,
            self.audit,
            confirmer=confirmer,
            timeout=self.config.tools.timeout,
        )
        self.provider = provider or create_provider(self.config.model)
        self.agent = Agent(
            self.provider,
            self.dispatcher,
            self.kill_switch,
            self.bus,
            self.config.agent,
            self.audit,
        )
        self.modules = ModuleManager(
            self.paths, self.dispatcher, self.bus, self.kill_switch, self.audit
        )
        self.history: list[Message] = []
        self._hotkey = hotkey
        self._watcher: asyncio.Task[None] | None = None
        self.hotkey_active = False

    async def start(self) -> None:
        self._watcher = asyncio.create_task(
            self.kill_switch.watch(self.config.kill_switch.poll_interval), name="kill-switch-watch"
        )
        if self._hotkey and self.config.kill_switch.hotkey:
            self.hotkey_active = self.kill_switch.start_hotkey(self.config.kill_switch.hotkey)
        await self.modules.load_all()
        self.audit.record(
            "core.started",
            provider=self.provider.name,
            modules=[n for n, r in self.modules.records.items() if r.instance],
        )
        self.log.info("core started", root=str(self.paths.root))
        await self.bus.publish("core.started")

    async def ask(self, request: str, keep_history: bool = False) -> AgentResult:
        """Run one request through the agent loop."""
        await self.modules.sync_state()
        result = await self.agent.run(request, self.history if keep_history else None)
        if keep_history and not result.stopped:
            self.history = result.messages[1:]  # drop the system prompt
        return result

    async def stop(self) -> None:
        await self.bus.publish("core.stopping")
        await self.modules.unload_all()
        self.kill_switch.stop_hotkey()
        if self._watcher is not None:
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher
        self.audit.record("core.stopped")
        self.log.info("core stopped")
        shutdown_logging()

    async def __aenter__(self) -> Kernel:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()
