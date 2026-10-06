"""Module system. A capability is a folder in ``modules/``:

    modules/hello/
        module.yaml     manifest: name, version, description, permissions, dependencies
        module.py       a Module subclass with @tool methods
        __init__.py     (optional)

The manager discovers folders at start-up, checks dependencies, imports each module,
runs ``setup()``, registers its tools with the dispatcher and subscribes ``on_event()``.
A crash in a module is caught and logged; it never takes down the core.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.machinery
import importlib.util
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from starlight.core.config import ConfigError, load_module_config, read_yaml
from starlight.core.dispatcher import Dispatcher
from starlight.core.events import Event, EventBus
from starlight.core.http import PoliteHttp
from starlight.core.killswitch import KillSwitch
from starlight.core.logging import AuditLog, get_logger, module_logger
from starlight.core.model import ModelProvider
from starlight.core.output import resolve_output_folder
from starlight.core.paths import Paths
from starlight.core.permissions import Permission
from starlight.core.stats import Stats
from starlight.core.tools import Tool, collect_tools
from starlight.core.ui import NoUser, UserIO
from starlight.core.vault import Vault

MANIFEST = "module.yaml"
PACKAGE_ROOT = "starlight_modules"
NAME_PATTERN = r"^[a-z][a-z0-9_]*$"


# Programs a module starts for you (e.g. Notepad) keep running when the module reloads or
# Starlight stops; their handles live here, outside any module, for the whole process.
_KEPT: list[subprocess.Popen[bytes]] = []


def keep_running(process: subprocess.Popen[bytes]) -> None:
    _KEPT[:] = [p for p in _KEPT if p.poll() is None] + [process]


class ModuleManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=NAME_PATTERN)
    version: str = "0.1.0"
    description: str = ""
    entrypoint: str = Field(default="module:Module", pattern=r"^[\w.]+:\w+$")
    permissions: list[Permission] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    enabled_by_default: bool = True


@dataclass
class Services:
    """Shared core services handed to every module."""

    http: PoliteHttp | None = None  # every web request goes through this (rate limits)
    vault: Vault | None = None  # secrets
    ui: UserIO = field(default_factory=NoUser)  # confirmations and questions to you
    stats: Stats | None = None  # metrics for the statistics viewer
    model: ModelProvider | None = None  # the main model (e.g. for vision or summaries)
    # Model for a role such as "code" or "vision" (falls back to the main model).
    models: Callable[[str], ModelProvider] | None = None
    skills: Any = None  # the skill manager (Step 9)
    output_dir: Path | None = None  # where files made for the user go (output.folder)
    # This process runs the background parts (webhook, watchers). False while another
    # Starlight core does; the event "core.primary" announces a takeover.
    primary: bool = True


@dataclass
class ModuleContext:
    """What a module gets from the core. Modules talk to each other only through ``bus``."""

    name: str
    config: Any
    logger: structlog.stdlib.BoundLogger
    bus: EventBus
    kill_switch: KillSwitch
    paths: Paths
    data_dir: Path
    services: Services = field(default_factory=Services)

    @property
    def http(self) -> PoliteHttp:
        if self.services.http is None:
            raise RuntimeError("no HTTP client configured")
        return self.services.http

    @property
    def vault(self) -> Vault:
        if self.services.vault is None:
            raise RuntimeError("no vault configured")
        return self.services.vault

    @property
    def ui(self) -> UserIO:
        return self.services.ui

    @property
    def stats(self) -> Stats | None:
        return self.services.stats

    @property
    def model(self) -> ModelProvider | None:
        return self.services.model

    @property
    def output_dir(self) -> Path:
        """Where to save files made for the user: ``output.folder``, by default the
        "Starlight" folder in their Documents. Created when something is first saved."""
        if self.services.output_dir is not None:
            return self.services.output_dir
        return resolve_output_folder("", self.paths.root)

    def model_for(self, role: str) -> ModelProvider | None:
        """The model the core routes this kind of work to (``roles:`` in the config)."""
        if self.services.models is not None:
            return self.services.models(role)
        return self.services.model

    async def publish(self, event: str, data: dict[str, Any] | None = None) -> None:
        await self.bus.publish(event, data, source=self.name)


class Module:
    """Base class for modules. Override what you need."""

    #: Pydantic model for config/modules/<name>.yaml (optional)
    Config: ClassVar[type[BaseModel] | None] = None
    #: Event name patterns delivered to on_event(), e.g. ["task.completed", "web.*"]
    subscriptions: ClassVar[list[str]] = []

    manifest: ModuleManifest
    ctx: ModuleContext

    async def setup(self, ctx: ModuleContext) -> None:
        """Called once after loading."""

    def tools(self) -> list[Tool]:
        """Functions the AI may call. Default: every method marked with @tool."""
        return collect_tools(self, self.manifest.name)

    async def on_event(self, event: Event) -> None:
        """Called for events matching ``subscriptions``."""

    async def shutdown(self) -> None:
        """Called on unload, disable, reload and core shutdown."""


class ModuleStatus(StrEnum):
    LOADED = "loaded"
    DISABLED = "disabled"
    FAILED = "failed"


@dataclass
class ModuleRecord:
    manifest: ModuleManifest
    path: Path
    status: ModuleStatus = ModuleStatus.DISABLED
    instance: Module | None = None
    error: str | None = None
    tools: list[str] = field(default_factory=list)


class ModuleState:
    """Which modules you enabled or disabled, in config/modules_state.yaml."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> tuple[set[str], set[str]]:
        raw = read_yaml(self.path)
        return set(raw.get("enabled") or []), set(raw.get("disabled") or [])

    def is_enabled(self, manifest: ModuleManifest) -> bool:
        enabled, disabled = self._read()
        if manifest.name in disabled:
            return False
        return manifest.name in enabled or manifest.enabled_by_default

    def set(self, name: str, enabled: bool) -> None:
        on, off = self._read()
        if enabled:
            on.add(name)
            off.discard(name)
        else:
            off.add(name)
            on.discard(name)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            "# Written by `starlight modules enable/disable`.\n"
            + yaml.safe_dump({"enabled": sorted(on), "disabled": sorted(off)}),
            encoding="utf-8",
        )


def discover(modules_dir: Path) -> tuple[dict[str, tuple[ModuleManifest, Path]], dict[str, str]]:
    """Find module folders. Folders starting with '_' or '.' (like _template) are skipped.
    Returns (found modules, errors by folder name)."""
    found: dict[str, tuple[ModuleManifest, Path]] = {}
    errors: dict[str, str] = {}
    if not modules_dir.is_dir():
        return found, errors
    for folder in sorted(modules_dir.iterdir()):
        if not folder.is_dir() or folder.name.startswith(("_", ".")):
            continue
        manifest_file = folder / MANIFEST
        if not manifest_file.is_file():
            continue
        try:
            manifest = ModuleManifest.model_validate(read_yaml(manifest_file))
        except (ValidationError, ConfigError) as exc:
            errors[folder.name] = f"invalid {MANIFEST}: {exc}"
            continue
        if manifest.name != folder.name:
            errors[folder.name] = f"manifest name '{manifest.name}' must match the folder name"
            continue
        found[manifest.name] = (manifest, folder)
    return found, errors


def _ensure_package(name: str, path: Path | None) -> None:
    """Register a package for a module folder so ``import_module`` and relative imports work."""
    if name in sys.modules:
        return
    init = path / "__init__.py" if path else None
    locations = [str(path)] if path else []
    spec: importlib.machinery.ModuleSpec | None
    if init is not None and init.is_file():
        spec = importlib.util.spec_from_file_location(
            name, init, submodule_search_locations=locations
        )
    else:
        spec = importlib.machinery.ModuleSpec(name, None, is_package=True)
        spec.submodule_search_locations = locations
    assert spec is not None
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    if spec.loader is not None:
        try:
            spec.loader.exec_module(package)
        except BaseException:
            del sys.modules[name]
            raise


def import_module_class(manifest: ModuleManifest, path: Path) -> type[Module]:
    package = f"{PACKAGE_ROOT}.{manifest.name}"
    _forget(package)
    # Cached bytecode is keyed on mtime (1 s) and size, so a quick edit can be missed on
    # hot-reload. Module folders are small; always compile from source.
    for cache in path.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    importlib.invalidate_caches()
    _ensure_package(PACKAGE_ROOT, None)
    _ensure_package(package, path)
    file_part, class_name = manifest.entrypoint.split(":")
    py_module = importlib.import_module(f"{package}.{file_part}")
    cls = getattr(py_module, class_name, None)
    if not (isinstance(cls, type) and issubclass(cls, Module)):
        raise TypeError(f"{manifest.entrypoint} is not a Module subclass")
    return cls


def _forget(package: str) -> None:
    for key in [k for k in sys.modules if k == package or k.startswith(package + ".")]:
        del sys.modules[key]


class ModuleManager:
    def __init__(
        self,
        paths: Paths,
        dispatcher: Dispatcher,
        bus: EventBus,
        kill_switch: KillSwitch,
        audit: AuditLog | None = None,
        services: Services | None = None,
    ) -> None:
        self.services = services or Services()
        self.paths = paths
        self.dispatcher = dispatcher
        self.bus = bus
        self.kill_switch = kill_switch
        self.audit = audit or AuditLog()
        self.state = ModuleState(paths.module_state_file)
        self.records: dict[str, ModuleRecord] = {}
        self.errors: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._log = get_logger("modules")

    def rescan(self) -> None:
        found, self.errors = discover(self.paths.modules)
        for name, error in self.errors.items():
            self._log.error("module skipped", module=name, error=error)
        for name, (manifest, path) in found.items():
            record = self.records.get(name)
            if record is None:
                self.records[name] = ModuleRecord(manifest, path)
            elif record.status is not ModuleStatus.LOADED:
                record.manifest, record.path = manifest, path
        for name in [n for n in self.records if n not in found]:
            if self.records[name].status is not ModuleStatus.LOADED:
                del self.records[name]

    def load_order(self) -> list[str]:
        """Dependencies first. Cycles are reported as failures, not loaded."""
        order: list[str] = []
        visiting: set[str] = set()

        def visit(name: str, chain: tuple[str, ...]) -> None:
            if name in order or name not in self.records:
                return
            if name in visiting:
                self.records[name].error = "dependency cycle: " + " -> ".join((*chain, name))
                return
            visiting.add(name)
            for dep in self.records[name].manifest.dependencies:
                visit(dep, (*chain, name))
            visiting.discard(name)
            order.append(name)

        for name in sorted(self.records):
            visit(name, ())
        return order

    async def load_all(self) -> None:
        async with self._lock:
            self.rescan()
            for name in self.load_order():
                record = self.records[name]
                if record.status is ModuleStatus.LOADED:
                    continue
                if self.state.is_enabled(record.manifest):
                    await self._load(name)

    async def sync_state(self) -> None:
        """Apply enable/disable changes made from another process (`starlight modules ...`)."""
        async with self._lock:
            self.rescan()
            for name in self.load_order():
                record = self.records[name]
                wanted = self.state.is_enabled(record.manifest)
                if wanted and record.status is not ModuleStatus.LOADED:
                    await self._load(name)
                elif not wanted and record.status is ModuleStatus.LOADED:
                    await self._unload(name)

    # Public entry points take the lock: two requests at once (terminal and WhatsApp)
    # must never load or unload the same module twice.

    async def load(self, name: str) -> ModuleRecord:
        async with self._lock:
            return await self._load(name)

    async def unload(self, name: str) -> None:
        async with self._lock:
            await self._unload(name)

    async def _load(self, name: str) -> ModuleRecord:
        record = self.records.get(name)
        if record is None:
            self.rescan()
            record = self.records.get(name)
            if record is None:
                raise KeyError(f"no module named '{name}' in {self.paths.modules}")
        if record.status is ModuleStatus.LOADED:
            return record
        if record.error and record.error.startswith("dependency cycle"):
            return self._fail(record, record.error)

        missing = [
            dep
            for dep in record.manifest.dependencies
            if self.records.get(dep) is None or self.records[dep].status is not ModuleStatus.LOADED
        ]
        if missing:
            return self._fail(record, f"missing dependencies: {', '.join(missing)}")

        log = module_logger(name)
        try:
            cls = import_module_class(record.manifest, record.path)
            instance = cls()
            instance.manifest = record.manifest
            config = load_module_config(self.paths, name, cls.Config)
            data_dir = self.paths.module_data(name)
            data_dir.mkdir(parents=True, exist_ok=True)
            instance.ctx = ModuleContext(
                name, config, log, self.bus, self.kill_switch, self.paths, data_dir, self.services
            )
            await instance.setup(instance.ctx)
            tools = instance.tools()
            for tool in tools:
                if tool.module != name:
                    raise ValueError(f"tool {tool.qualified_name} does not belong to '{name}'")
                self.dispatcher.register(tool)
        except Exception as exc:
            self.dispatcher.unregister_module(name)
            log.exception("module failed to load")
            return self._fail(record, f"{type(exc).__name__}: {exc}")

        for pattern in cls.subscriptions:
            self.bus.subscribe(pattern, instance.on_event, owner=name)
        record.instance = instance
        record.status = ModuleStatus.LOADED
        record.error = None
        record.tools = [t.qualified_name for t in tools]
        self.audit.record("module.loaded", module=name, version=record.manifest.version)
        self._log.info("module loaded", module=name, tools=record.tools)
        await self.bus.publish("module.loaded", {"module": name})
        return record

    def _fail(self, record: ModuleRecord, error: str) -> ModuleRecord:
        record.status = ModuleStatus.FAILED
        record.instance = None
        record.error = error
        record.tools = []
        _forget(f"{PACKAGE_ROOT}.{record.manifest.name}")
        self._log.error("module not loaded", module=record.manifest.name, error=error)
        return record

    async def _unload(self, name: str) -> None:
        record = self.records.get(name)
        if record is None or record.status is not ModuleStatus.LOADED:
            return
        for other in self.dependents(name):
            await self._unload(other)
        self.dispatcher.unregister_module(name)
        self.bus.unsubscribe_owner(name)
        self.kill_switch.remove_cleanups(name)
        if record.instance is not None:
            try:
                await record.instance.shutdown()
            except Exception:
                module_logger(name).exception("module shutdown failed")
        record.instance = None
        record.status = ModuleStatus.DISABLED
        record.tools = []
        _forget(f"{PACKAGE_ROOT}.{name}")
        self.audit.record("module.unloaded", module=name)
        self._log.info("module unloaded", module=name)
        await self.bus.publish("module.unloaded", {"module": name})

    async def reload(self, name: str) -> ModuleRecord:
        """Hot-reload: unload, re-read manifest and code, load again."""
        async with self._lock:
            await self._unload(name)
            self.rescan()
            return await self._load(name)

    async def enable(self, name: str) -> ModuleRecord:
        async with self._lock:
            self.state.set(name, True)
            return await self._load(name)

    async def disable(self, name: str) -> None:
        async with self._lock:
            self.state.set(name, False)
            await self._unload(name)

    def dependents(self, name: str) -> list[str]:
        return [
            n
            for n, r in self.records.items()
            if name in r.manifest.dependencies and r.status is ModuleStatus.LOADED
        ]

    async def unload_all(self) -> None:
        async with self._lock:
            for name in reversed(self.load_order()):
                with contextlib.suppress(Exception):
                    await self._unload(name)
