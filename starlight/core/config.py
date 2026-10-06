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


class ModelConfig(_Strict):
    provider: str = "echo"
    options: dict[str, Any] = Field(default_factory=dict)


class KillSwitchConfig(_Strict):
    hotkey: str | None = "<ctrl>+<alt>+k"
    stop_file: str = "data/STOP"
    poll_interval: float = Field(default=0.2, gt=0)


class PermissionsConfig(_Strict):
    auto_approve: list[str] = Field(default_factory=list)
    confirm: list[str] = Field(default_factory=list)
    blocked: list[str] = Field(default_factory=list)


class ToolsConfig(_Strict):
    timeout: float = Field(default=120, gt=0)


class StarlightConfig(_Strict):
    name: str = "Starlight"
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    kill_switch: KillSwitchConfig = Field(default_factory=KillSwitchConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)


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


def load_config(paths: Paths) -> StarlightConfig:
    """Load config/starlight.yaml. A missing file means all defaults."""
    try:
        return StarlightConfig.model_validate(read_yaml(paths.config_file))
    except ValidationError as exc:
        raise ConfigError(f"{paths.config_file}:\n{exc}") from exc


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
