"""The kernel wires the core together: config, logging, event bus, kill switch,
permissions, dispatcher, model provider, context manager, memory, shared services
(HTTP, vault, stats, user IO), the agent and the modules."""

from __future__ import annotations

import asyncio
import contextlib
from types import TracebackType
from typing import Any

import httpx

from starlight.core.agent import Agent, AgentResult
from starlight.core.config import StarlightConfig, load_config
from starlight.core.context import SUMMARY_PREFIX, ContextManager
from starlight.core.dispatcher import Confirmer, Dispatcher
from starlight.core.events import Event, EventBus
from starlight.core.http import PoliteHttp
from starlight.core.killswitch import KillSwitch
from starlight.core.logging import AuditLog, get_logger, setup_logging, shutdown_logging
from starlight.core.model import Message, ModelProvider, TokenCallback, create_provider
from starlight.core.modules import ModuleManager, Services
from starlight.core.paths import Paths
from starlight.core.permissions import PermissionPolicy
from starlight.core.stats import Stats
from starlight.core.tools import Tool
from starlight.core.ui import NoUser, UserIO
from starlight.core.vault import Vault, VaultBackend


def confirmer_for(ui: UserIO, policy: PermissionPolicy) -> Confirmer:
    """Turn the user interface into the dispatcher's confirmation callback."""

    async def confirm(tool: Tool, args: dict[str, Any]) -> bool:
        return await ui.confirm(f"Run {tool.qualified_name} with {args}?")

    return confirm


class Kernel:
    def __init__(
        self,
        paths: Paths | None = None,
        config: StarlightConfig | None = None,
        provider: ModelProvider | None = None,
        confirmer: Confirmer | None = None,
        hotkey: bool = False,
        ui: UserIO | None = None,
        vault_backend: VaultBackend | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
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
        self.stats = Stats(self.paths.stats_db)
        self.bus = EventBus()
        self.ui: UserIO = ui or NoUser()
        self.kill_switch = KillSwitch(
            self.paths.root / self.config.kill_switch.stop_file, self.audit
        )
        self.policy = PermissionPolicy(self.config.permissions)
        self.dispatcher = Dispatcher(
            self.kill_switch,
            self.policy,
            self.bus,
            self.audit,
            confirmer=confirmer or confirmer_for(self.ui, self.policy),
            timeout=self.config.tools.timeout,
            stats=self.stats,
        )
        self.provider = provider or create_provider(self.config.model)
        self.http = PoliteHttp(
            self.config.http,
            stats=self.stats,
            kill_switch=self.kill_switch,
            cache_path=self.paths.data / "web_cache.sqlite",
            transport=http_transport,  # tests pass a mock transport
        )
        self.vault = Vault(self.paths.config / "vault_index.yaml", vault_backend)

        from starlight.memory import Memory  # memory builds on the core, not the reverse

        self.memory = Memory(self.paths, self.config.memory, self.provider, self.stats)
        self.context = ContextManager(self.provider, self.config.model, self._on_summary)
        self.agent = Agent(
            self.provider,
            self.dispatcher,
            self.kill_switch,
            self.bus,
            self.config.agent,
            self.audit,
            context=self.context,
            stats=self.stats,
        )
        self.agent.enrichers.append(self.memory.enrich)
        self.services = Services(
            http=self.http, vault=self.vault, ui=self.ui, stats=self.stats, model=self.provider
        )
        self.modules = ModuleManager(
            self.paths, self.dispatcher, self.bus, self.kill_switch, self.audit, self.services
        )
        self.history: list[Message] = []
        self._hotkey = hotkey
        self._watcher: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[Any]] = set()
        self.hotkey_active = False

    def set_ui(self, ui: UserIO) -> None:
        self.ui = ui
        self.services.ui = ui
        self.dispatcher.confirmer = confirmer_for(ui, self.policy)

    async def start(self) -> None:
        self._watcher = asyncio.create_task(
            self.kill_switch.watch(self.config.kill_switch.poll_interval), name="kill-switch-watch"
        )
        if self._hotkey and self.config.kill_switch.hotkey:
            self.hotkey_active = self.kill_switch.start_hotkey(self.config.kill_switch.hotkey)
        if self.config.memory.enabled:
            for tool in self.memory.tools():
                self.dispatcher.register(tool)
            await self.memory.sync(force=True)
            self.bus.subscribe("task.completed", self._after_task, owner="memory")
        await self._warmup()
        await self.modules.load_all()
        self.audit.record(
            "core.started",
            provider=self.provider.name,
            modules=[n for n, r in self.modules.records.items() if r.instance],
        )
        self.log.info("core started", root=str(self.paths.root))
        await self.bus.publish("core.started")

    async def _warmup(self) -> None:
        warmup = getattr(self.provider, "warmup", None)
        if not self.config.model.warmup or warmup is None:
            return
        try:
            await warmup()
            self.log.info("model ready", provider=self.provider.name)
        except Exception as exc:
            self.log.warning("model warm-up failed", provider=self.provider.name, error=str(exc))

    async def _on_summary(self, summary: str) -> None:
        await self.bus.publish("conversation.summarized", {"summary": summary})
        if self.config.memory.enabled:
            await self.memory.save_summary(summary)

    async def _after_task(self, event: Event) -> None:
        """Let the model pick what to keep, in the background so the answer is not delayed."""
        if self.kill_switch.engaged:
            return

        async def extract() -> None:
            try:
                await self.memory.extract(
                    str(event.data.get("request", "")),
                    str(event.data.get("answer", "")),
                    str(event.data.get("request_id", "")),
                )
            except Exception:
                self.log.exception("memory extraction failed")

        task = asyncio.create_task(extract(), name="memory-extract")
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def ask(
        self,
        request: str,
        keep_history: bool = False,
        on_token: TokenCallback | None = None,
        images: list[bytes] | None = None,
    ) -> AgentResult:
        """Run one request through the agent loop."""
        await self.modules.sync_state()
        stream = on_token if self.config.model.stream else None
        result = await self.agent.run(
            request, self.history if keep_history else None, on_token=stream, images=images
        )
        if keep_history and not result.stopped:
            # Keep the conversation (and summaries), not the system prompt or recalled memories.
            self.history = [
                m
                for m in result.messages[1:]
                if not (m.role == "system" and not m.content.startswith(SUMMARY_PREFIX))
            ]
        return result

    async def drain(self, timeout: float = 30.0) -> None:
        """Wait for background work (memory writing) to finish."""
        if self._background:
            await asyncio.wait(list(self._background), timeout=timeout)

    async def stop(self) -> None:
        await self.bus.publish("core.stopping")
        await self.drain()
        for task in list(self._background):
            task.cancel()
        await self.modules.unload_all()
        self.kill_switch.stop_hotkey()
        if self._watcher is not None:
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher
        await self.http.aclose()
        close = getattr(self.provider, "aclose", None)
        if close is not None:
            with contextlib.suppress(Exception):
                await close()
        self.memory.close()
        self.audit.record("core.stopped")
        self.log.info("core stopped")
        self.stats.close()
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
