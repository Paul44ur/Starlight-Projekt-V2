"""Configuration: one global YAML file plus one YAML file per module, validated with Pydantic."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    extra_args: list[str] = Field(default_factory=list)


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


class SkillsConfig(_Strict):
    enabled: bool = True
    auto_draft: bool = True  # draft a skill when the same kind of task succeeds again
    repeat_threshold: int = Field(default=2, ge=2)
    max_in_prompt: int = Field(default=2, ge=0)  # approved skills added to a prompt
    min_score: float = Field(default=0.25, ge=-1, le=1)


class StatsConfig(_Strict):
    retention_days: int = Field(default=30, ge=1)
    gpu_sample_interval: float = Field(default=30, ge=0)  # 0 = off


class OutputConfig(_Strict):
    # Where Starlight saves files it makes for you (letters, notes, tables, code).
    # Empty: a "Starlight" folder in your Documents. Never Starlight's own folder.
    folder: str = ""


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
#   starlight.local.yaml     your own changes, never touched by any tool
OVERLAYS = ("starlight.launcher.yaml", "starlight.local.yaml")


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
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def update_local_config(paths: Paths, updates: dict[str, Any]) -> None:
    """Merge ``updates`` into config/starlight.local.yaml (your own overlay, applied over
    starlight.yaml). Only the given keys change; the rest of the file is kept."""
    overlay = paths.config / "starlight.local.yaml"
    current = read_yaml(overlay)
    merged = deep_merge(current, updates)
    write_yaml_config(
        overlay,
        merged,
        "# Your own settings, merged over config/starlight.yaml (this file wins).\n"
        "# Starlight writes here when you change settings from the chat; safe to edit by hand.",
    )


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
