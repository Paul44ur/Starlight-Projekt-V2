"""Open programs, files and links, and handle windows on this PC.

- `open` starts any installed program by name (from the Start menu). The first time a
  program is opened Starlight asks; once you said yes it opens without asking.
- `open_file` opens a document with its usual program; `open_url` a web link in your
  browser. Programs and scripts are never opened as files.
- `windows`, `focus`, `minimize`, `maximize`, `restore` arrange what is open; `close`
  asks first and closes a window the normal way (the program may ask to save).
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, Permission, ToolError, tool
from starlight.core.safepaths import PathGuard, expand

from .backends import AppsUnavailable, Backend, Program, Window, detect

# Never "opened" as a document: that would run them.
RUNNABLE = {
    ".exe", ".bat", ".cmd", ".com", ".msi", ".msp", ".ps1", ".psm1", ".vbs", ".vbe", ".js",
    ".jse", ".wsf", ".wsh", ".scr", ".pif", ".lnk", ".reg", ".cpl", ".hta", ".jar", ".sh",
    ".py", ".pyw", ".appimage", ".desktop", ".url", ".inf", ".sys", ".dll",
}  # fmt: skip


class Config(BaseModel):
    extra_folders: list[str] = Field(default_factory=list)
    ask_first_open: bool = True  # ask the first time each program is opened


class AppsModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.allowed_file = ctx.data_dir / "allowed.json"
        self._backend: Backend | None = None
        self._programs: list[Program] | None = None

    @property
    def backend(self) -> Backend:
        if self._backend is None:
            try:
                self._backend = detect()
            except Exception as exc:
                raise ToolError(f"programs can't be controlled here: {exc}") from exc
        return self._backend

    def _guarded(self, action: Any, *args: Any) -> Any:
        self.ctx.kill_switch.check()
        try:
            return action(*args)
        except AppsUnavailable as exc:
            raise ToolError(str(exc)) from exc
        except OSError as exc:
            raise ToolError(f"that did not work: {exc}") from exc

    # -- programs ------------------------------------------------------------------------

    def _all_programs(self, refresh: bool = False) -> list[Program]:
        if self._programs is None or refresh:
            extra = [expand(f) for f in self.config.extra_folders]
            unique: dict[str, Program] = {}
            for program in self._guarded(self.backend.programs, extra):
                unique.setdefault(program.name.lower(), program)
            self._programs = sorted(unique.values(), key=lambda p: p.name.lower())
        return self._programs

    def _match(self, name: str) -> Program:
        wanted = name.strip().lower()
        if not wanted:
            raise ToolError("which program?")
        programs = self._all_programs()
        exact = [p for p in programs if p.name.lower() == wanted]
        starts = [p for p in programs if p.name.lower().startswith(wanted)]
        contains = [p for p in programs if wanted in p.name.lower()]
        for group in (exact, starts, contains):
            if len(group) == 1:
                return group[0]
            if group:
                shortest = min(group, key=lambda p: len(p.name))
                if group is exact or len(shortest.name) <= len(wanted) + 3:
                    return shortest
                options = ", ".join(p.name for p in group[:8])
                raise ToolError(f"'{name}' could be: {options}. Say which one.")
        close = difflib.get_close_matches(wanted, [p.name.lower() for p in programs], n=5)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise ToolError(f"no installed program called '{name}'.{hint}")

    def _allowed(self) -> set[str]:
        try:
            return set(json.loads(self.allowed_file.read_text("utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            return set()

    def _save_allowed(self, names: set[str]) -> None:
        self.allowed_file.write_text(json.dumps(sorted(names), indent=2), encoding="utf-8")

    @tool
    def installed(self, search: str = "", refresh: bool = False) -> dict[str, Any]:
        """Programs installed on this PC (from the Start menu). `search` filters by name."""
        programs = self._all_programs(refresh)
        wanted = search.strip().lower()
        names = [p.name for p in programs if wanted in p.name.lower()]
        return {"count": len(names), "programs": names[:200]}

    @tool
    async def open(self, name: str) -> dict[str, Any]:
        """Start an installed program by name, e.g. "Spotify", "Word", "Steam". The first
        time asks; after a yes that program opens without asking."""
        program = self._match(name)
        allowed = self._allowed()
        if self.config.ask_first_open and program.name not in allowed:
            if not await self.ctx.ui.confirm(
                f"Open {program.name}? (Next time it opens without asking.)"
            ):
                raise ToolError("not confirmed, nothing was started")
            self._save_allowed(allowed | {program.name})
        self._guarded(self.backend.start, program)
        return {"opened": program.name}

    @tool
    def forget(self, name: str) -> dict[str, Any]:
        """Make a program ask again before Starlight opens it."""
        allowed = self._allowed()
        match = next((a for a in allowed if a.lower() == name.strip().lower()), None)
        if match is None:
            raise ToolError(f"'{name}' is not on the list of programs that open without asking")
        self._save_allowed(allowed - {match})
        return {"forgotten": match}

    @tool
    async def open_file(self, path: str) -> dict[str, Any]:
        """Open a document, picture, video or folder with its usual program. A name is
        looked up in your Starlight folder; a file elsewhere asks first. Programs and
        scripts are never opened this way."""
        guard = PathGuard(self.ctx.paths.root, self.ctx.output_dir)
        found = guard.resolve(path)
        target = found.path
        if not target.exists():
            raise ToolError(f"{target} does not exist")
        if target.suffix.lower() in RUNNABLE:
            raise ToolError(f"{target.name} is a program or script; use apps.open for programs")
        if not found.free and not await self.ctx.ui.confirm(f"Open {target}?"):
            raise ToolError("not confirmed")
        self._guarded(self.backend.open, str(target))
        return {"opened": str(target)}

    @tool
    def open_url(self, url: str) -> dict[str, Any]:
        """Open a web page in your browser (https:// or http:// only)."""
        link = url.strip()
        if not re.fullmatch(r"https?://[^\s\"'<>]+", link):
            raise ToolError("give a web address starting with https://")
        self._guarded(self.backend.open, link)
        return {"opened": link}

    # -- windows -------------------------------------------------------------------------

    def _window(self, title: str) -> Window:
        wanted = title.strip().lower()
        if not wanted:
            raise ToolError("which window? give part of its title or program name")
        windows: list[Window] = self._guarded(self.backend.windows)
        hits = [w for w in windows if wanted in w.title.lower() or wanted in w.program.lower()]
        if not hits:
            raise ToolError(f"no open window matches '{title}'; see apps.windows")
        if len({w.title for w in hits}) > 1 and not any(w.title.lower() == wanted for w in hits):
            titles = "; ".join(w.title for w in hits[:6])
            raise ToolError(f"'{title}' matches several windows: {titles}. Be more specific.")
        return next((w for w in hits if w.title.lower() == wanted), hits[0])

    @tool
    def windows(self) -> dict[str, Any]:
        """The windows that are open right now (title and program)."""
        found = self._guarded(self.backend.windows)
        return {
            "count": len(found),
            "windows": [{"title": w.title, "program": w.program} for w in found],
        }

    @tool
    def focus(self, title: str) -> dict[str, Any]:
        """Bring a window to the front (by part of its title or program name)."""
        window = self._window(title)
        self._guarded(self.backend.focus, window)
        return {"focused": window.title}

    @tool
    def minimize(self, title: str) -> dict[str, Any]:
        """Minimize a window."""
        window = self._window(title)
        self._guarded(self.backend.show, window, "minimize")
        return {"minimized": window.title}

    @tool
    def maximize(self, title: str) -> dict[str, Any]:
        """Maximize a window."""
        window = self._window(title)
        self._guarded(self.backend.show, window, "maximize")
        return {"maximized": window.title}

    @tool
    def restore(self, title: str) -> dict[str, Any]:
        """Bring a minimized or maximized window back to its normal size."""
        window = self._window(title)
        self._guarded(self.backend.show, window, "restore")
        return {"restored": window.title}

    @tool(permission=Permission.CONFIRM)
    def close(self, title: str) -> dict[str, Any]:
        """Close a window the normal way (the program may ask to save first). Asks first."""
        window = self._window(title)
        self._guarded(self.backend.close, window)
        return {"closed": window.title}
