"""The log watcher: log files you name are looked at every so often, and new lines that match
(an error, a failure - or your own pattern) are sent to you on every channel. Only what is new
since the last look is read; a log that was rotated (smaller than before) is read from its
start again.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, Permission, ToolError, tool
from starlight.core.jsonfile import load_json, save_json
from starlight.core.safepaths import expand

DEFAULT_PATTERN = r"error|fail|fatal|exception|critical|panic|denied"
READ_AT_MOST = 2 * 1024 * 1024  # of new text per look: a log that suddenly grew a lot


class Config(BaseModel):
    check_every: float = Field(default=30, ge=5)
    max_lines: int = Field(default=5, ge=1, le=50)


class LogwatchModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.file = ctx.data_dir / "watches.json"
        ctx.every(self.config.check_every, self.check, name="logwatch", first_delay=10)

    def _all(self) -> dict[str, dict[str, Any]]:
        return load_json(self.file, {})

    @tool(permission=Permission.CONFIRM)
    def watch_log(self, path: str, pattern: str = "", name: str = "") -> dict[str, Any]:
        """Watch a log file: new lines matching the pattern (errors and failures if not
        said; a regular expression, case does not matter) are sent to you. Asks first."""
        file = expand(path)
        if not file.is_file():
            raise ToolError(f"{file} is not a file")
        try:
            re.compile(pattern or DEFAULT_PATTERN)
        except re.error as exc:
            raise ToolError(f"the pattern is not valid: {exc}") from None
        watches = self._all()
        label = name.strip() or file.name
        watches[label] = {"path": str(file), "pattern": pattern or DEFAULT_PATTERN,
                          "position": file.stat().st_size}  # fmt: skip
        save_json(self.file, watches)
        return {"watching": label, "path": str(file), "pattern": watches[label]["pattern"]}

    @tool
    def unwatch(self, name: str) -> dict[str, Any]:
        """Stop watching a log."""
        watches = self._all()
        if name not in watches:
            raise ToolError(f"no log watched as {name}; see logwatch.watches")
        del watches[name]
        save_json(self.file, watches)
        return {"stopped": name}

    @tool
    def watches(self) -> dict[str, Any]:
        """The logs being watched, and for what."""
        return {"watches": [{"name": n, "path": w["path"], "pattern": w["pattern"]}
                            for n, w in self._all().items()]}  # fmt: skip

    def _new_matches(self, watch: dict[str, Any]) -> tuple[list[str], int]:
        path = Path(watch["path"])
        size = path.stat().st_size
        start = int(watch.get("position", 0))
        if size < start:  # rotated or emptied: from the start
            start = 0
        start = max(start, size - READ_AT_MOST)
        with path.open("rb") as handle:
            handle.seek(start)
            data = handle.read(size - start)
        text = data.decode("utf-8", errors="replace")
        if not text.endswith("\n"):  # a line still being written: next time
            cut = text.rfind("\n") + 1
            size = start + len(text[:cut].encode("utf-8", errors="replace"))
            text = text[:cut]
        pattern = re.compile(watch["pattern"], re.IGNORECASE)
        return [ln.strip() for ln in text.splitlines() if ln.strip() and pattern.search(ln)], size

    @tool
    async def check_now(self) -> dict[str, Any]:
        """Look at the watched logs now; what matches is also sent to you."""
        return {"found": await self.check()}

    async def check(self) -> dict[str, int]:
        watches = self._all()
        found: dict[str, int] = {}
        for name, watch in watches.items():
            try:
                lines, position = self._new_matches(watch)
            except OSError as exc:
                self.ctx.logger.warning("log not readable", log=name, error=str(exc))
                continue
            watch["position"] = position
            if not lines:
                continue
            found[name] = len(lines)
            shown = "\n".join(f"  {ln[:300]}" for ln in lines[-self.config.max_lines :])
            more = (
                f" ({len(lines)} new, the last {self.config.max_lines})"
                if len(lines) > self.config.max_lines
                else ""
            )
            await self.ctx.deliver(f"{name}: new lines that match{more}:\n{shown}", "Log watcher")
        save_json(self.file, watches)
        return found
