"""The kernel wires the core together: config, logging, event bus, kill switch,
permissions, dispatcher, model provider, context manager, memory, shared services
(HTTP, vault, stats, user IO), the agent and the modules."""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from types import TracebackType
from typing import Any

import httpx

from starlight.core.agent import Agent, AgentResult, private_request, request_conversation
from starlight.core.config import ConfigError, ModelConfig, StarlightConfig, load_config
from starlight.core.context import SUMMARY_PREFIX, ContextManager
from starlight.core.conversations import ConversationStore
from starlight.core.corelock import CoreLock
from starlight.core.dispatcher import Confirmer, Dispatcher
from starlight.core.events import Event, EventBus
from starlight.core.focus import Focus, finish
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
from starlight.core.modelpause import pause_note
from starlight.core.modules import ModuleManager, Services
from starlight.core.output import resolve_output_folder
from starlight.core.paths import Paths
from starlight.core.permissions import PermissionPolicy, apply_saved_rules, confirm_reason
from starlight.core.situation import Situation
from starlight.core.stats import Stats
from starlight.core.toolroute import ToolRouter
from starlight.core.tools import Tool
from starlight.core.ui import ContextualUI, NoUser, UserIO, current_ui
from starlight.core.vault import Vault, VaultBackend


def confirmer_for(ui: UserIO) -> Confirmer:
    """Turn the user interface into the dispatcher's confirmation callback."""

    async def confirm(tool: Tool, args: dict[str, Any]) -> bool:
        return await ui.confirm(question_for(tool, args))

    return confirm


def question_for(tool: Tool, args: dict[str, Any]) -> str:
    """The confirmation question, with the reason when it is not the tool's usual one."""
    return f"{confirm_reason.get()}Run {tool.qualified_name} with {args}?"


def channel_confirmer(fallback: Confirmer) -> Confirmer:
    """Requests from another channel (WhatsApp) are confirmed there; terminal requests use
    the terminal's own confirmation (with 'always')."""

    async def confirm(tool: Tool, args: dict[str, Any]) -> bool:
        ui = current_ui.get()
        if ui is not None:
            return await ui.confirm(question_for(tool, args))
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
            confirmer=channel_confirmer(confirmer or confirmer_for(self.ui.default)),
            timeout=self.config.tools.timeout,
            stats=self.stats,
        )
        self.vault = Vault(self.paths.config / "vault_index.yaml", vault_backend)
        self.provider = provider or create_provider(self._with_token(self.config.model))
        self.http = PoliteHttp(
            self.config.http,
            stats=self.stats,
            kill_switch=self.kill_switch,
            cache_path=self.paths.data / "web_cache.sqlite",
            transport=http_transport,  # tests pass a mock transport
        )

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
        self.router = ToolRouter(
            self.dispatcher, self.config.agent, self._describe_module, self.memory.index.embedder
        )
        self.agent = Agent(
            self.provider,
            self.dispatcher,
            self.kill_switch,
            self.bus,
            self.config.agent,
            self.audit,
            context=self.context,
            stats=self.stats,
            router=self.router,
            max_result_chars=self.config.tools.max_result_chars,
            pause_note=lambda: pause_note(self.paths),  # game mode (modelpause.py)
        )
        self.situation = Situation(lambda: self.services.output_dir, self._profile)
        self.agent.enrichers.append(self.situation.enrich)
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
        self.focus = Focus(self.paths.data / "focus.json")
        self.services = Services(
            primary=False,
            http=self.http,
            vault=self.vault,
            ui=self.ui,
            stats=self.stats,
            model=self.provider,
            models=self.model_for,
            output_dir=output_dir,
            output_error=output_error,
            write_anywhere=self.config.output.write_anywhere,
            policy=self.policy,
            deliver=self.deliver,
            embedder=self.memory.index.embedder,
            call=self.dispatcher.call,
            tool_names=lambda: [t.qualified_name for t in self.dispatcher.tools()],
            tool_lookup=self.dispatcher.get,
            run_request=self._run_request,
            profile=self._profile,
            focus=self.focus,
        )
        self.modules = ModuleManager(
            self.paths, self.dispatcher, self.bus, self.kill_switch, self.audit, self.services
        )
        # Saved conversations (data/conversations). One may be shared by several front-ends
        # (--both: browser + terminal); requests that continue the same conversation take
        # turns so no exchange is lost from it.
        self.conversations = ConversationStore(self.paths.data / "conversations")
        self._histories: dict[str, list[Message]] = {}
        self._taken_back: dict[str, tuple[int, list[Message]]] = {}  # see take_back
        self._stamps: dict[str, tuple[int, int] | None] = {}  # file versions of _histories
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self._housekeeping_at = 0.0
        self._hotkey = hotkey
        self._watcher: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[Any]] = set()
        self.hotkey_active = False

    async def _refresh_router(self, event: Event) -> None:
        self.router.refresh()

    def _profile(self) -> dict[str, str]:
        """The user's profile (memory/profile.md); empty with memory turned off."""
        return self.memory.profile.read() if self.config.memory.enabled else {}

    def _describe_module(self, name: str) -> tuple[str, list[str]]:
        """A module's description and keywords, for the tool router."""
        record = self.modules.records.get(name)
        if record is None:
            return {
                "memory": (
                    "Long-term memory: remember, recall and forget facts; the user's profile",
                    ["remember", "know", "profile", "forget", "tidy", "duplicates", "weißt"],
                ),
                "skills": ("Approved skills: how-tos learned from earlier tasks", ["skill"]),
            }.get(name, ("", []))
        return record.manifest.description, record.manifest.keywords

    def set_ui(self, ui: UserIO) -> None:
        """The default interface (the terminal); other channels set theirs per request."""
        self.ui.default = ui
        self.dispatcher.confirmer = channel_confirmer(confirmer_for(ui))

    def set_confirmer(self, confirmer: Confirmer) -> None:
        """Replace the terminal's tool confirmation (other channels still ask themselves)."""
        self.dispatcher.confirmer = channel_confirmer(confirmer)

    def model_for(self, role: str) -> ModelProvider:
        """The model for a kind of work (``roles:`` in the config), else the main model."""
        config = self.config.roles.get(role)
        if config is None:
            return self.provider
        if role not in self._role_models:
            self._role_models[role] = create_provider(self._with_token(config))
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
        self.dispatcher.register(self.router.find_tool())
        for event in ("module.loaded", "module.unloaded"):  # keeps tools.find's list current
            self.bus.subscribe(event, self._refresh_router, owner="core")
        self.bus.subscribe("message.received", self._on_message, owner="core")
        await self._housekeeping()
        self._spawn(self._heartbeat(), "gpu-heartbeat")
        self._protect_secrets()
        await self._warmup()
        await self.modules.load_all()
        self._spawn(self.router.warm(), "tool-index")
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
        """Let the model pick what to keep, in the background so the answer is not delayed.
        Nothing is kept from a private conversation."""
        if (
            self.kill_switch.engaged
            or getattr(self.provider, "stand_in", False)
            or event.data.get("private")
        ):
            return

        async def extract() -> None:
            try:
                await self.memory.extract(
                    str(event.data.get("request", "")),
                    str(event.data.get("answer", "")),
                    str(event.data.get("request_id", "")),
                    conversation=str(event.data.get("conversation") or ""),
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
        if (
            self.kill_switch.engaged
            or getattr(self.provider, "stand_in", False)
            or event.data.get("private")
        ):
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

    def _with_token(self, config: ModelConfig) -> ModelConfig:
        """An LM Studio model with the API token it may ask for: the environment variable
        LM_API_TOKEN, else the vault's lmstudio.token (masked in the logs either way)."""
        if config.provider != "lmstudio" or config.options.get("api_key"):
            return config
        import os

        from starlight.models.lmstudio import TOKEN_ENV, TOKEN_KEY

        token = os.environ.get(TOKEN_ENV, "").strip()
        if not token:
            try:
                token = (self.vault.get(TOKEN_KEY) or "").strip()
            except Exception:  # no keyring on this machine: LM Studio will say what it needs
                token = ""
        if not token:
            return config
        add_secret_values([token])
        return config.model_copy(update={"options": {**config.options, "api_key": token}})

    def _protect_secrets(self) -> None:
        """Mask the vault's values wherever they would appear in a log line."""
        try:
            values = [self.vault.get(key) for key in self.vault.names()]
        except Exception as exc:  # no keyring on this machine
            self.log.warning("vault unavailable", error=str(exc))
            return
        add_secret_values(v for v in values if v)

    HOUSEKEEPING_EVERY = 6 * 3600.0

    async def _housekeeping(self) -> None:
        """Keep what grows from growing forever: old statistics, cached web pages and old
        conversation summaries. At start, and every few hours while the core runs."""
        self._housekeeping_at = time.monotonic()
        steps: list[tuple[str, Any]] = [
            ("statistics", lambda: self.stats.maintain(self.config.stats.retention_days)),
            ("web cache", self.http.cache.purge),
        ]
        for name, step in steps:
            try:
                await asyncio.to_thread(step)
            except Exception:
                self.log.exception("housekeeping step failed", step=name)
        if self.config.memory.enabled:
            for name, prune in (
                ("summaries", self.memory.prune_summaries),
                ("expired memories", self.memory.prune_expired),
            ):
                try:
                    await prune()
                except Exception:
                    self.log.exception("housekeeping step failed", step=name)

    async def _heartbeat(self) -> None:
        """The primary core tells the viewer it is running and samples GPU use and VRAM.
        A secondary core waits and takes over when the primary one exits."""
        from starlight.models.bench import gpu_status

        interval = self.config.stats.gpu_sample_interval
        sampled_at = float("-inf")
        while True:
            try:
                if not self.services.primary:
                    if not self.core_lock.acquire():
                        await asyncio.sleep(self.takeover_interval)
                        continue
                    self.services.primary = True
                    self.log.info("this process now runs the background parts")
                    await self.bus.publish("core.primary")
                self.stats.record("core.heartbeat")
                if time.monotonic() - self._housekeeping_at >= self.HOUSEKEEPING_EVERY:
                    await self._housekeeping()
                if self.focus.expired():  # focus time is over: deliver what was held
                    try:
                        await finish(self.focus, self.deliver)
                    except Exception:
                        self.log.exception("could not end focus mode")
                status = None
                # The beat stays at 30 s at most (the viewer calls the core "not running"
                # after 120 s); the GPU is sampled every ``interval``, a bit early is fine.
                if interval > 0 and time.monotonic() - sampled_at >= interval - 0.5:
                    sampled_at = time.monotonic()
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
            except Exception:  # one bad beat must not end the heartbeat for good
                self.log.exception("heartbeat failed")
            await asyncio.sleep(min(interval, 30) if interval > 0 else 30)

    async def ask(
        self,
        request: str,
        keep_history: bool = False,
        on_token: TokenCallback | None = None,
        images: list[bytes] | None = None,
        conversation: str | None = None,
    ) -> AgentResult:
        """Run one request through the agent loop. With ``keep_history`` it continues a
        saved conversation: ``conversation`` (an id) or the current one."""
        await self.modules.sync_state()
        stream = on_token if self.config.model.stream else None
        if not keep_history:
            return await self.agent.run(request, None, on_token=stream, images=images)
        name = conversation or self.conversations.current
        lock = self._conversation_locks.setdefault(name, asyncio.Lock())
        async with lock:
            return await self._ask_in_conversation(name, request, stream, images)

    def _history_of(self, conversation: str) -> list[Message]:
        """The conversation's messages: kept in memory (with the pictures sent in it), and
        read again when another Starlight program saved to it since."""
        stamp = self.conversations.stamp(conversation)
        if conversation not in self._histories or self._stamps.get(conversation) != stamp:
            self._histories[conversation] = self.conversations.messages(conversation)
            self._stamps[conversation] = stamp
        return self._histories[conversation]

    def _keep(self, conversation: str, messages: list[Message]) -> None:
        """Make ``messages`` the conversation, in memory and on disk."""
        self._histories.pop(conversation, None)  # (re-inserted last: the newest in the order)
        self._histories[conversation] = messages
        self.conversations.save(conversation, messages)
        self._stamps[conversation] = self.conversations.stamp(conversation)
        self._evict_old_histories()

    MAX_CACHED_HISTORIES = 20

    def _evict_old_histories(self) -> None:
        """Only the most recently used conversations stay in memory (with their pictures);
        any other is read from its file when it is opened again."""
        for name in list(self._histories):
            if len(self._histories) <= self.MAX_CACHED_HISTORIES:
                break
            if name not in self._taken_back:  # a Regenerate in progress keeps its state
                del self._histories[name]
                self._stamps.pop(name, None)

    async def _ask_in_conversation(
        self,
        conversation: str,
        request: str,
        stream: TokenCallback | None,
        images: list[bytes] | None,
    ) -> AgentResult:
        history = self._history_of(conversation)
        private = private_request.set(self.conversations.is_private(conversation))
        named = request_conversation.set(conversation)
        try:
            result = await self.agent.run(request, history, on_token=stream, images=images)
        finally:
            request_conversation.reset(named)
            private_request.reset(private)
        if not (result.stopped or result.failed):  # a failed turn is not kept: retrying works
            # Keep the conversation (and summaries), not the system prompt or recalled memories.
            kept = [
                m
                for m in result.messages[1:]
                if not (m.role == "system" and not m.content.startswith(SUMMARY_PREFIX))
            ]
            try:
                self._keep(conversation, kept)
            except (OSError, KeyError):  # deleted meanwhile, or the disk is full
                self.log.exception("could not save the conversation", conversation=conversation)
            self._taken_back.pop(conversation, None)  # a Regenerate that worked: nothing to restore
        return result

    @property
    def history(self) -> list[Message]:
        """The current conversation's messages."""
        return self._history_of(self.conversations.current)

    async def take_back(
        self, conversation: str | None = None, expected: str | None = None
    ) -> Message | None:
        """Set the last exchange (your last message and everything after it) aside, to ask
        that message again; returns it (with any pictures), or None if there is none. Only
        in memory: the file keeps the old exchange until a new answer is saved, so a crash
        or a stop loses nothing, and `put_back` restores it. ``expected``: the question the
        caller means; if the last one is a different one (the page is behind, or another
        window asked something since), ValueError."""
        name = conversation or self.conversations.current
        lock = self._conversation_locks.setdefault(name, asyncio.Lock())
        async with lock:
            history = self._history_of(name)
            last = next(
                (i for i in range(len(history) - 1, -1, -1) if history[i].role == "user"), -1
            )
            if last < 0:
                return None
            if expected is not None and not history[last].content.startswith(expected.strip()):
                raise ValueError("the conversation changed since; open it again to see it")
            self._histories[name] = history[:last]
            self._taken_back[name] = (last, history[last:])
            return history[last]

    async def put_back(self, conversation: str | None = None) -> None:
        """Restore what `take_back` set aside, when asking again was stopped or failed and
        so kept nothing. Does nothing if the conversation has moved on since."""
        name = conversation or self.conversations.current
        lock = self._conversation_locks.setdefault(name, asyncio.Lock())
        async with lock:
            length, removed = self._taken_back.pop(name, (-1, []))
            history = self._history_of(name)
            if removed and len(history) == length:
                self._histories[name] = [*history, *removed]

    def new_conversation(self, title: str = "", private: bool = False) -> str:
        """Start a fresh conversation and make it the current one. A private one is never
        saved, nothing is learned from it, and it ends when Starlight stops."""
        return self.conversations.create(title, private=private)

    def forget_conversation(self, conversation: str) -> None:
        self.conversations.delete(conversation)
        self._histories.pop(conversation, None)
        self._stamps.pop(conversation, None)
        self._taken_back.pop(conversation, None)
        self._conversation_locks.pop(conversation, None)

    async def _run_request(self, text: str) -> str:
        return (await self.ask(text)).answer

    async def deliver(
        self, text: str, title: str = "", source: str = "core", urgent: bool = False
    ) -> None:
        """Tell the user something now (a reminder, an alert, a briefing): on the terminal
        or the channel in use, and - through the "deliver" event - in every open web chat,
        as a desktop notification and on WhatsApp, when those are set up. In focus mode
        it is held for the digest at the end, unless it is ``urgent``."""
        if not urgent and self.focus.active():
            self.focus.hold(text, title, source)
            self.stats.record("deliver.held", source=source)
            return
        message = f"{title}: {text}" if title and not text.startswith(title) else text
        with contextlib.suppress(Exception):
            await self.ui.notify(message)
        self.stats.record("deliver", source=source)
        await self.bus.publish("deliver", {"text": message, "title": title}, source=source)

    async def drain(self, timeout: float = 30.0) -> None:
        """Wait for background work (memory writing, skill drafting) to finish."""
        pending = [t for t in self._background if not t.get_name().startswith("gpu-")]
        if pending:
            await asyncio.wait(pending, timeout=timeout)
        await self.bus.settle(timeout)

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
