"""Project folder layout. Every runtime folder hangs off one root directory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "STARLIGHT_HOME"
CONFIG_FILE = "starlight.yaml"


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def config_file(self) -> Path:
        return self.config / CONFIG_FILE

    @property
    def module_configs(self) -> Path:
        return self.config / "modules"

    @property
    def module_state_file(self) -> Path:
        return self.config / "modules_state.yaml"

    @property
    def modules(self) -> Path:
        return self.root / "modules"

    @property
    def memory(self) -> Path:
        return self.root / "memory"

    @property
    def skills(self) -> Path:
        return self.root / "skills"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def stats_db(self) -> Path:
        return self.data / "stats.db"

    def module_data(self, name: str) -> Path:
        return self.data / "modules" / name

    def ensure(self) -> None:
        for folder in (
            self.config,
            self.module_configs,
            self.modules,
            self.memory,
            self.skills,
            self.logs,
            self.data,
        ):
            folder.mkdir(parents=True, exist_ok=True)

    @classmethod
    def discover(cls, root: str | Path | None = None) -> Paths:
        """Use an explicit root, then $STARLIGHT_HOME, then the nearest folder
        upwards from the working directory that holds config/starlight.yaml."""
        if root is not None:
            return cls(Path(root).resolve())
        env = os.environ.get(ENV_HOME)
        if env:
            return cls(Path(env).resolve())
        cwd = Path.cwd().resolve()
        for candidate in (cwd, *cwd.parents):
            if (candidate / "config" / CONFIG_FILE).is_file():
                return cls(candidate)
        return cls(cwd)
