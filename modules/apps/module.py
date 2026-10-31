"""Open programs, files and links, and handle windows on this PC.

- `open` starts any installed program by name (from the Start menu). With `ask_first_open`
  on (the shipped config turns it off) Starlight asks the first time a program is opened;
  once you said yes it opens without asking.
- `open_file` opens a document with its usual program; `open_url` a web link in your
  browser. Programs and scripts are never opened as files.
- `windows`, `focus`, `minimize`, `maximize`, `restore` arrange what is open; `close`
  asks first and closes a window the normal way (the program may ask to save).
"""

from __future__ import annotations

import difflib
import re
import shutil
import subprocess
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, Permission, ToolError, tool
from starlight.core.jsonfile import load_json, save_json
from starlight.core.safepaths import PathGuard, asks_runnable, expand

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


def winget_table(text: str) -> list[dict[str, str]]:
    """winget's table (a header line, a line of dashes, then rows aligned under it) as
    rows of {"name", "id", "version", "available", "source"}."""
    lines = [ln.rstrip() for ln in text.replace("\r", "\n").splitlines()]
    rule = next(
        (
            i
            for i, ln in enumerate(lines)
            if ln.strip().startswith("---") and set(ln.strip()) == {"-"}
        ),
        None,
    )  # (not the "-" of winget's spinner)
    if rule is None or rule == 0:
        return []
    header = lines[rule - 1]
    names = [m.group(0) for m in re.finditer(r"\S+", header)]
    starts = [m.start() for m in re.finditer(r"\S+", header)]
    rows = []
    for line in lines[rule + 1 :]:
        if not line.strip() or re.match(r"^\d+ (upgrades?|packages?) ", line.strip()):
            continue
        cells = [
            line[a:b].strip() for a, b in zip(starts, [*starts[1:], len(line) + 1], strict=True)
        ]
        row = {key.lower(): value for key, value in zip(names, cells, strict=True)}
        if row.get("id"):
            rows.append(row)
    return rows


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
                # "note" means Notepad, not Notepad++: the shortest name is meant when it is
                # at most three characters longer than what was asked for; else ask
                if len(shortest.name) <= len(wanted) + 3:
                    return shortest
                options = ", ".join(p.name for p in group[:8])
                raise ToolError(f"'{name}' could be: {options}. Say which one.")
        close = difflib.get_close_matches(wanted, [p.name.lower() for p in programs], n=5)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise ToolError(f"no installed program called '{name}'.{hint}")

    def _allowed(self) -> set[str]:
        try:
            return {n for n in load_json(self.allowed_file, []) if isinstance(n, str)}
        except OSError:  # locked: ask again rather than fail
            return set()

    def _save_allowed(self, names: set[str]) -> None:
        save_json(self.allowed_file, sorted(names))

    @tool
    def installed(self, search: str = "", refresh: bool = False) -> dict[str, Any]:
        """Programs installed on this PC (from the Start menu). `search` filters by name."""
        programs = self._all_programs(refresh)
        wanted = search.strip().lower()
        names = [p.name for p in programs if wanted in p.name.lower()]
        return {"count": len(names), "programs": names[:200]}

    @tool
    async def open(self, name: str) -> dict[str, Any]:
        """Start an installed program by name, e.g. "Spotify", "Word", "Steam". If the
        setting ask_first_open is on, it asks the first time; after a yes that program opens
        without asking."""
        program = self._match(name)
        if self.config.ask_first_open:
            allowed = self._allowed()
            if program.name not in allowed:
                if not await self.ctx.ui.confirm(
                    f"Open {program.name}? (Next time it opens without asking.)"
                ):
                    raise ToolError("not confirmed, nothing was started")
                self._save_allowed(allowed | {program.name})
        self._guarded(self.backend.start, program)
        return {"opened": program.name}

    @tool
    def forget(self, name: str) -> dict[str, Any]:
        """Make a program ask again before Starlight opens it (when ask_first_open is on)."""
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
        if asks_runnable() and target.suffix.lower() in RUNNABLE:  # (a safety limit)
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

    # -- window layouts ------------------------------------------------------------------

    @property
    def layouts_file(self) -> Any:
        return self.ctx.data_dir / "layouts.json"

    @tool
    def save_layout(self, name: str) -> dict[str, Any]:
        """Remember where the open windows are now, as a layout ("work", "gaming" ...)."""
        name = " ".join(name.split())
        if not name:
            raise ToolError("give the layout a name")
        saved = []
        for window in self._guarded(self.backend.windows):
            try:
                place = self._guarded(self.backend.place, window)
            except ToolError:
                continue  # (a window that closed meanwhile)
            saved.append({"title": window.title, "program": window.program, "place": list(place)})
        layouts = load_json(self.layouts_file, {})
        layouts[name] = saved
        save_json(self.layouts_file, layouts)
        return {"saved": name, "windows": len(saved)}

    @tool
    def restore_layout(self, name: str) -> dict[str, Any]:
        """Put the open windows back where a layout has them (by program, and title)."""
        layouts = load_json(self.layouts_file, {})
        key = next((k for k in layouts if k.casefold() == name.strip().casefold()), None)
        if key is None:
            raise ToolError(f"no layout called {name}; saved: {', '.join(layouts) or 'none'}")
        open_now: list[Window] = list(self._guarded(self.backend.windows))
        moved: list[str] = []
        missing: list[str] = []
        for entry in layouts[key]:
            same_title = [w for w in open_now if w.title == entry["title"]]
            same_program = [
                w for w in open_now if entry["program"] and w.program == entry["program"]
            ]
            candidates = same_title or same_program
            if not candidates:
                missing.append(entry["title"])
                continue
            target = candidates[0]
            open_now.remove(target)
            self._guarded(self.backend.move, target, tuple(entry["place"]))
            moved.append(target.title)
        return {"layout": key, "moved": moved, "not_open": missing}

    @tool
    def layouts(self) -> dict[str, Any]:
        """The window layouts you saved."""
        saved = load_json(self.layouts_file, {})
        return {"layouts": {name: len(windows) for name, windows in saved.items()}}

    # -- installing programs (winget) -------------------------------------------------------

    def _winget(self, *args: str, timeout: float = 120) -> str:
        """Run winget (Windows' own package manager, part of App Installer)."""
        winget = shutil.which("winget")
        if winget is None:
            raise ToolError("installing programs needs winget (Windows 10/11, App Installer)")
        self.ctx.kill_switch.check()
        try:
            done = subprocess.run(
                [winget, *args, "--accept-source-agreements", "--disable-interactivity"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            raise ToolError("winget took too long") from None
        if done.returncode != 0 and not done.stdout.strip():
            raise ToolError(f"winget failed: {done.stderr.strip()[:300] or done.returncode}")
        return done.stdout

    @tool
    def find_program(self, text: str) -> dict[str, Any]:
        """Look for a program to install (winget's catalog): name, id, version."""
        rows = winget_table(self._winget("search", text))
        return {"found": rows[:15]}

    @tool(permission=Permission.CONFIRM, own_timeout=True)
    def install(self, id: str) -> dict[str, Any]:
        """Install a program by its winget id (from apps.find_program), quietly. Asks
        first."""
        if not re.fullmatch(r"[\w.+-]{2,100}", id.strip()):
            raise ToolError("give the program's winget id, e.g. VideoLAN.VLC")
        out = self._winget(
            "install", "--id", id.strip(), "--exact", "--silent",
            "--accept-package-agreements", timeout=1800,
        )  # fmt: skip
        self._programs = None  # the new program shows in the list next time
        return {"installed": id.strip(), "winget": out.strip().splitlines()[-3:]}

    @tool
    def updates(self) -> dict[str, Any]:
        """The installed programs that have a newer version (winget)."""
        return {"updates": winget_table(self._winget("upgrade"))}

    @tool(permission=Permission.CONFIRM, own_timeout=True)
    def update(self, id: str = "all") -> dict[str, Any]:
        """Update one program by its winget id, or all of them ("all"). Asks first."""
        args = (
            ["upgrade", "--all"]
            if id.strip() in ("", "all")
            else ["upgrade", "--id", id.strip(), "--exact"]
        )
        out = self._winget(*args, "--silent", "--accept-package-agreements", timeout=3600)
        return {"updated": id.strip() or "all", "winget": out.strip().splitlines()[-5:]}
