"""Configuration: one global YAML file plus one YAML file per module, validated with Pydantic."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from starlight.core.fsutil import write_atomic
from starlight.core.paths import Paths


class ConfigError(Exception):
    """A configuration file is missing, unreadable or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoggingConfig(_Strict):
    level: str = "INFO"
    max_bytes: int = Field(default=5_000_000, gt=0)
    backups: int = Field(default=5, ge=0)


class AgentConfig(_Strict):
    max_steps: int = Field(default=8, ge=1)
    system_prompt: str = "You are Starlight, a local assistant."
    # Send the model only the tools that fit a request (faster, fewer wrong picks); the
    # model can ask for more with tools.find. Off: every tool, every time.
    tool_routing: bool = True
    max_tools: int = Field(default=40, ge=5)
    max_tool_modules: int = Field(default=6, ge=1)


REMOVED_SERVER_SETTINGS = ("draft_model_path", "draft_max", "draft_min", "gpu_layers_draft")


class ServerConfig(_Strict):
    """How `starlight model serve` starts the model server."""

    binary: str = ""  # path to llama-server(.exe) or ollama(.exe); default: from PATH
    model_path: str = ""  # .gguf file, llama.cpp only
    host: str = "127.0.0.1"
    port: int = 0  # 0: 8080 for llama.cpp, 11434 for Ollama
    gpu_layers: int = 999  # 999: every layer on the GPU
    flash_attention: bool = True
    kv_cache_type: str = "q8_0"  # quantized KV cache: longer context in the same VRAM
    parallel: int = 1
    threads: int = 0  # CPU threads for prompt processing; 0: all cores (matters on the CPU build)
    extra_args: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _drop_removed_settings(cls, data: Any) -> Any:
        """Older versions had a draft model (speculative decoding); config files an older
        Starlight.exe wrote still name it. Those settings are ignored now."""
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if k not in REMOVED_SERVER_SETTINGS}
        return data


class ModelConfig(_Strict):
    provider: str = "echo"
    options: dict[str, Any] = Field(default_factory=dict)
    context_window: int = Field(default=32768, ge=1024)
    # Tokens kept free for the answer; older turns are summarized beyond this.
    reserve_tokens: int = Field(default=4096, ge=256)
    # Recent messages never summarized away.
    keep_recent: int = Field(default=8, ge=2)
    stream: bool = True
    warmup: bool = True  # load the model into VRAM at start-up
    server: ServerConfig = Field(default_factory=ServerConfig)


class EmbedderConfig(_Strict):
    provider: str = "hash"  # hash (built-in, no model), ollama or openai_compat
    model: str = ""
    base_url: str = ""
    dimensions: int = Field(default=512, ge=16)


class MemoryConfig(_Strict):
    enabled: bool = True
    embedder: EmbedderConfig = Field(default_factory=EmbedderConfig)
    top_k: int = Field(default=5, ge=0)
    min_score: float = Field(default=0.2, ge=-1, le=1)
    merge_threshold: float = Field(default=0.92, ge=0, le=1)
    auto_extract: bool = True


class HttpConfig(_Strict):
    max_requests_per_second: int = Field(default=5, ge=1)
    per_domain_interval: float = Field(default=1.0, ge=0)
    respect_robots: bool = True
    user_agent: str = "StarlightBot/0.1 (+local assistant)"
    timeout: float = Field(default=20, gt=0)
    cache_ttl: float = Field(default=3600, ge=0)
    max_retries: int = Field(default=3, ge=0)
    backoff_base: float = Field(default=2.0, gt=0)
    max_retry_after: float = Field(default=120, ge=0)
    # Protection against a hostile or broken server flooding this machine:
    max_response_bytes: int = Field(default=10_000_000, ge=1024)  # stop reading a huge body
    max_redirects: int = Field(default=10, ge=0)  # no endless redirect chains
    max_connections: int = Field(default=20, ge=1)  # cap simultaneous connections
    max_total_seconds: float = Field(default=60, gt=0)  # hard ceiling on one request


class KillSwitchConfig(_Strict):
    hotkey: str | None = "<ctrl>+<alt>+j"
    stop_file: str = "data/STOP"
    poll_interval: float = Field(default=0.2, gt=0)


class PermissionsConfig(_Strict):
    auto_approve: list[str] = Field(default_factory=list)
    confirm: list[str] = Field(default_factory=list)
    blocked: list[str] = Field(default_factory=list)


class ToolsConfig(_Strict):
    timeout: float = Field(default=120, gt=0)
    # Longer tool results are cut before the model sees them (the log keeps all of it).
    max_result_chars: int = Field(default=12000, ge=500)


class SkillsConfig(_Strict):
    enabled: bool = True
    auto_draft: bool = True  # draft a skill when the same kind of task succeeds again
    repeat_threshold: int = Field(default=2, ge=2)
    max_in_prompt: int = Field(default=2, ge=0)  # approved skills added to a prompt
    min_score: float = Field(default=0.25, ge=-1, le=1)


class StatsConfig(_Strict):
    retention_days: int = Field(default=30, ge=1)
    gpu_sample_interval: float = Field(default=30, ge=0)  # 0 = off


class RemoteConfig(_Strict):
    """Using the web chat from your other devices (through Tailscale). Off by default;
    `starlight remote enable` sets it up in config/starlight.remote.yaml."""

    enabled: bool = False
    # Host names the chat answers to besides this PC, e.g. "my-pc.tail1234.ts.net".
    hosts: list[str] = Field(default_factory=list)
    # From another device these always ask on that device first, even if they run on
    # their own at the PC. Empty: the phone asks exactly when the PC asks. For example
    # ["desktop.*", "mail.send"] brings the extra questions back.
    confirm: list[str] = Field(default_factory=list)
    # From another device these never run: they would change what Starlight may do (or
    # set up actions that later run on the PC), which stays a decision made at the PC.
    blocked: list[str] = Field(
        default_factory=lambda: [
            "access.allow_action", "access.ask_action", "access.block_action",
            "access.clear_rule", "access.set_secret", "access.forget_secret",
            "automations.add", "automations.use_template", "automations.enable",
            "automations.allow", "network.allow", "login.add_site", "commands.add",
            "docs.add_folder", "home.connect", "backup.add",
            "commands.shell",  # a shell could rewrite Starlight's own settings
        ]
    )  # fmt: skip


class WebConfig(_Strict):
    host: str = "127.0.0.1"  # local only; the chat can run actions, keep it off the network
    port: int = 8700
    remote: RemoteConfig = Field(default_factory=RemoteConfig)


class OutputConfig(_Strict):
    # Where Starlight saves files it makes for you (letters, notes, tables, code).
    # Empty: a "Starlight" folder in your Documents. Never Starlight's own folder.
    folder: str = ""
    # True: new files may be saved in any folder without asking (replacing or adding to an
    # existing file outside the Starlight folder, and reading one, still ask). Starlight's
    # own folder stays off-limits either way.
    write_anywhere: bool = False


class ViewerConfig(_Strict):
    host: str = "127.0.0.1"
    port: int = 8765
    refresh_seconds: int = Field(default=5, ge=1)


class StarlightConfig(_Strict):
    name: str = "Starlight"
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    # Specialised models by role, e.g. {code: ..., vision: ...}. A role without an entry
    # uses the main model.
    roles: dict[str, ModelConfig] = Field(default_factory=dict)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    kill_switch: KillSwitchConfig = Field(default_factory=KillSwitchConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    stats: StatsConfig = Field(default_factory=StatsConfig)
    viewer: ViewerConfig = Field(default_factory=ViewerConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    return data


# Merged over config/starlight.yaml, in this order (later wins):
#   starlight.launcher.yaml  written by Starlight.exe (model server, model file)
#   starlight.remote.yaml    written by `starlight remote enable/disable` (Tailscale access)
#   starlight.local.yaml     your own changes, never touched by any tool
OVERLAYS = ("starlight.launcher.yaml", "starlight.remote.yaml", "starlight.local.yaml")


def deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Nested mappings merge key by key; anything else in ``extra`` replaces ``base``."""
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def write_yaml_config(path: Path, data: dict[str, Any], header: str = "") -> None:
    """Write a config file as a comment header plus YAML. Used when Starlight edits its own
    config from the chat, so the user never has to open the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(data, sort_keys=True, allow_unicode=True, default_flow_style=False)
    text = (header.rstrip() + "\n\n" if header else "") + body
    write_atomic(path, text)


def load_config(paths: Paths) -> StarlightConfig:
    """Load config/starlight.yaml plus its overlays. Missing files mean defaults."""
    data = read_yaml(paths.config_file)
    used = [paths.config_file]
    for name in OVERLAYS:
        overlay = paths.config / name
        if overlay.is_file():
            data = deep_merge(data, read_yaml(overlay))
            used.append(overlay)
    try:
        return StarlightConfig.model_validate(data)
    except ValidationError as exc:
        files = " + ".join(str(p) for p in used)
        raise ConfigError(f"{files}:\n{exc}") from exc


def load_module_config[T: BaseModel](
    paths: Paths, module: str, schema: type[T] | None
) -> T | dict[str, Any]:
    """Load config/modules/<module>.yaml, validated against the module's schema if it has one."""
    path = paths.module_configs / f"{module}.yaml"
    raw = read_yaml(path)
    if schema is None:
        return raw
    try:
        return schema.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{path}:\n{exc}") from exc
