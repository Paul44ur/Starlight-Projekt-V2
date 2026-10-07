"""The kernel wires the core together: config, logging, event bus, kill switch,
permissions, dispatcher, model provider, context manager, memory, shared services
(HTTP, vault, stats, user IO), the agent and the modules."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from types import TracebackType
from typing import Any

import httpx

from starlight.core.agent import Agent, AgentResult
from starlight.core.config import ConfigError, StarlightConfig, load_config
from starlight.core.context import SUMMARY_PREFIX, ContextManager
from starlight.core.corelock import CoreLock
from starlight.core.dispatcher import Confirmer, Dispatcher
from starlight.core.events import Event, EventBus
from starlight.core.http import PoliteHttp
from starlight.core.killswitch import KillSwitch
from starlight.core.logging import (
    AuditLog,
    add_secret_values,
    get_logger,
    setup_logging,
    shutdown_logging,
)
from starlight.core.model import Message, ModelProvider, TokenCallback, create_provider
from starlight.core.modules import ModuleManager, Services
from starlight.core.output import resolve_output_folder
from starlight.core.paths import Paths
from starlight.core.permissions import PermissionPolicy, apply_saved_rules
from starlight.core.stats import Stats
from starlight.core.tools import Tool
from starlight.core.ui import ContextualUI, NoUser, UserIO, current_ui
from starlight.core.vault import Vault, VaultBackend


def confirmer_for(ui: UserIO, policy: PermissionPolicy) -> Confirmer:
    """Turn the user interface into the dispatcher's confirmation callback."""

    async def confirm(tool: Tool, args: dict[str, Any]) -> bool:
        return await ui.confirm(f"Run {tool.qualified_name} with {args}?")

    return confirm


def channel_confirmer(fallback: Confirmer) -> Confirmer:
    """Requests from another channel (WhatsApp) are confirmed there; terminal requests use
    the terminal's own confirmation (with 'always')."""

    async def confirm(tool: Tool, args: dict[str, Any]) -> bool:
        ui = current_ui.get()
        if ui is not None:
            return await ui.confirm(f"Run {tool.qualified_name} with {args}?")
        return await fallback(tool, args)

    return confirm


class Kernel:
    takeover_interval = 10.0  # seconds between tries to take over the background parts

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
        self.ui = ContextualUI(ui or NoUser())
        self.kill_switch = KillSwitch(
            self.paths.root / self.config.kill_switch.stop_file, self.audit
        )
        self.policy = PermissionPolicy(self.config.permissions)
        apply_saved_rules(self.policy, self.paths.config)  # rules changed from the chat
        self.dispatcher = Dispatcher(
            self.kill_switch,
            self.policy,
            self.bus,
            self.audit,
            confirmer=channel_confirmer(confirmer or confirmer_for(self.ui.default, self.policy)),
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

        from starlight.memory import Memory  # memory and skills build on the core
        from starlight.skills import SkillManager

        self.memory = Memory(self.paths, self.config.memory, self.provider, self.stats)
        self._role_models: dict[str, ModelProvider] = {}
        self.skills = SkillManager(
            self.paths,
            self.config.skills,
            getattr(self.provider, "summarizer", self.provider),
            self.memory.index.embedder,
            self.stats,
        )
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
        self.agent.enrichers.append(self.skills.enrich)
        # A bad output.folder must not stop all of Starlight: only saving files fails,
        # with this message, and the problem is logged at start.
        output_dir: Path | None = None
        output_error: str | None = None
        try:
            output_dir = resolve_output_folder(self.config.output.folder, self.paths.root)
        except ConfigError as exc:
            output_error = str(exc)
        self.core_lock = CoreLock(self.paths.data / "core.lock")
        self.services = Services(
            primary=False,
            http=self.http,
            vault=self.vault,
            ui=self.ui,
            stats=self.stats,
            model=self.provider,
            models=self.model_for,
            skills=self.skills,
            output_dir=output_dir,
            output_error=output_error,
            policy=self.policy,
        )
        self.modules = ModuleManager(
            self.paths, self.dispatcher, self.bus, self.kill_switch, self.audit, self.services
        )
        self.history: list[Message] = []
        # One conversation may be shared by several front-ends (--both: browser + terminal);
        # requests that continue it take turns so no exchange is lost from the history.
        self._history_lock = asyncio.Lock()
        self._hotkey = hotkey
        self._watcher: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[Any]] = set()
        self.hotkey_active = False

    def set_ui(self, ui: UserIO) -> None:
        """The default interface (the terminal); other channels set theirs per request."""
        self.ui.default = ui
        self.dispatcher.confirmer = channel_confirmer(confirmer_for(ui, self.policy))

    def set_confirmer(self, confirmer: Confirmer) -> None:
        """Replace the terminal's tool confirmation (other channels still ask themselves)."""
        self.dispatcher.confirmer = channel_confirmer(confirmer)

    def model_for(self, role: str) -> ModelProvider:
        """The model for a kind of work (``roles:`` in the config), else the main model."""
        config = self.config.roles.get(role)
        if config is None:
            return self.provider
        if role not in self._role_models:
            self._role_models[role] = create_provider(config)
        return self._role_models[role]

    async def start(self) -> None:
        # First: is this the process that runs the webhook, watchers and heartbeat?
        self.services.primary = self.core_lock.acquire()
        if not self.services.primary:
            self.log.info("another Starlight core runs the webhook and watchers")
        self._watcher = asyncio.create_task(
            self.kill_switch.watch(self.config.kill_switch.poll_interval), name="kill-switch-watch"
        )
        if self._hotkey and self.config.kill_switch.hotkey:
            self.hotkey_active = self.kill_switch.start_hotkey(self.config.kill_switch.hotkey)
        if self.config.memory.enabled:
            for tool in self.memory.tools():
                self.dispatcher.register(tool)
            try:
                await self.memory.sync(force=True)
            except Exception as exc:  # e.g. the embedding model is not running yet
                self.log.warning(
                    "memory index not ready; recall is off until the embedder answers",
                    error=f"{type(exc).__name__}: {exc}",
                )
            self.bus.subscribe("task.completed", self._after_task, owner="memory")
        if self.config.skills.enabled:
            for tool in self.skills.tools():
                self.dispatcher.register(tool)
            self.bus.subscribe("task.completed", self._learn_skill, owner="skills")
        self.bus.subscribe("message.received", self._on_message, owner="core")
        self.stats.prune(self.config.stats.retention_days)
        self._spawn(self._heartbeat(), "gpu-heartbeat")
        self._protect_secrets()
        await self._warmup()
        await self.modules.load_all()
        self.audit.record(
            "core.started",
            provider=self.provider.name,
            modules=[n for n, r in self.modules.records.items() if r.instance],
        )
        self.log.info("core started", root=str(self.paths.root))
        if self.services.output_error:
            self.log.error(
                "output.folder is not usable; saving files will fail",
                reason=self.services.output_error,
            )
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
            try:
                await self.memory.save_summary(summary)
            except Exception:  # the summary still goes into the prompt
                self.log.exception("could not save the summary to memory")

    async def _after_task(self, event: Event) -> None:
        """Let the model pick what to keep, in the background so the answer is not delayed."""
        if self.kill_switch.engaged or getattr(self.provider, "stand_in", False):
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

        self._spawn(extract(), "memory-extract")

    def _spawn(self, coro: Any, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    async def _learn_skill(self, event: Event) -> None:
        if self.kill_switch.engaged or getattr(self.provider, "stand_in", False):
            return

        async def learn() -> None:
            try:
                skill = await self.skills.on_task(event.data)
            except Exception:
                self.log.exception("skill drafting failed")
                return
            if skill is not None:
                await self.bus.publish("skill.drafted", {"skill": skill.name})
                await self.ui.notify(
                    f"New draft skill '{skill.name}'. Review it with "
                    f"`starlight skills show {skill.name}` and approve with "
                    f"`starlight skills approve {skill.name}`."
                )

        self._spawn(learn(), "skill-draft")

    async def _on_message(self, event: Event) -> None:
        """A request from another channel (e.g. WhatsApp): run it with that channel's
        interface and send the answer back through ``reply``."""
        text = str(event.data.get("text", "")).strip()
        reply = event.data.get("reply")
        ui = event.data.get("ui")
        if not text or reply is None:
            return

        async def handle() -> None:
            token = current_ui.set(ui)
            try:
                result = await self.ask(text)
                await reply(result.answer)
            except Exception:
                self.log.exception("message handling failed", source=event.source)
                self.stats.record("errors", source=event.source)
                await reply("Sorry, that failed. The details are in the Starlight logs.")
            finally:
                current_ui.reset(token)

        self._spawn(handle(), f"message-{event.source}")

    def _protect_secrets(self) -> None:
        """Mask the vault's values wherever they would appear in a log line."""
        try:
            values = [self.vault.get(key) for key in self.vault.names()]
        except Exception as exc:  # no keyring on this machine
            self.log.warning("vault unavailable", error=str(exc))
            return
        add_secret_values(v for v in values if v)

    async def _heartbeat(self) -> None:
        """The primary core tells the viewer it is running and samples GPU use and VRAM.
        A secondary core waits and takes over when the primary one exits."""
        from starlight.models.bench import gpu_status

        interval = self.config.stats.gpu_sample_interval
        while True:
            if not self.services.primary:
                if not self.core_lock.acquire():
                    await asyncio.sleep(self.takeover_interval)
                    continue
                self.services.primary = True
                self.log.info("this process now runs the background parts")
                await self.bus.publish("core.primary")
            self.stats.record("core.heartbeat")
            status = None
            if interval > 0:
                try:
                    status = await asyncio.to_thread(gpu_status)
                except Exception:
                    status = None
            if status:
                if status.get("used_bytes") is not None:
                    self.stats.record("gpu.vram_used_bytes", status["used_bytes"])
                if status.get("total_bytes"):
                    self.stats.record("gpu.vram_total_bytes", status["total_bytes"])
                if status.get("utilization") is not None:
                    self.stats.record("gpu.utilization_percent", status["utilization"])
            await asyncio.sleep(min(interval, 30) if interval > 0 else 30)

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
        if not keep_history:
            return await self.agent.run(request, None, on_token=stream, images=images)
        async with self._history_lock:
            return await self._ask_in_conversation(request, stream, images)

    async def _ask_in_conversation(
        self, request: str, stream: TokenCallback | None, images: list[bytes] | None
    ) -> AgentResult:
        result = await self.agent.run(request, self.history, on_token=stream, images=images)
        if not result.stopped:
            # Keep the conversation (and summaries), not the system prompt or recalled memories.
            self.history = [
                m
                for m in result.messages[1:]
                if not (m.role == "system" and not m.content.startswith(SUMMARY_PREFIX))
            ]
        return result

    async def drain(self, timeout: float = 30.0) -> None:
        """Wait for background work (memory writing, skill drafting) to finish."""
        pending = [t for t in self._background if not t.get_name().startswith("gpu-")]
        if pending:
            await asyncio.wait(pending, timeout=timeout)

    async def stop(self) -> None:
        await self.bus.publish("core.stopping")
        await self.drain(timeout=10)
        for task in list(self._background):
            task.cancel()
        for provider in self._role_models.values():
            close_role = getattr(provider, "aclose", None)
            if close_role is not None:
                with contextlib.suppress(Exception):
                    await close_role()
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
        if self.services.primary:
            self.stats.record("core.stopped")  # the viewer shows "not running" at once
        self.core_lock.release()
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
