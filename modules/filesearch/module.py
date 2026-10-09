"""Find files on this PC: by name, by what's inside them, and what changed recently.

Read-only. It looks only inside folders you allow - by default your Documents, Desktop,
Downloads and home folder, plus any extra ``roots`` you set in the config. Starlight's own
folder (settings, data, memory, logs) is never searched. Nothing is opened, changed or
deleted; this module only tells you where things are.
"""

from __future__ import annotations

import fnmatch
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.output import known_folders
from starlight.core.safepaths import expand, inside

# Folders not worth searching: huge, and never what the user means.
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", "site-packages", ".cache", "$RECYCLE.BIN", "AppData",
}  # fmt: skip
# Treat these as text for content search; everything else is skipped by grep.
_TEXT_SUFFIXES = {
    ".txt", ".md", ".rst", ".log", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".html", ".htm", ".xml", ".py", ".js", ".ts", ".java",
    ".c", ".h", ".cpp", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".bat", ".ps1",
    ".sql", ".tex", ".srt", ".env",
}  # fmt: skip


class Config(BaseModel):
    roots: list[str] = Field(default_factory=list)  # extra folders to allow, e.g. ["D:/Work"]
    include_default_folders: bool = True  # also allow Documents, Desktop, Downloads, home
    max_files_scanned: int = Field(default=50_000, ge=100)  # safety cap per search
    max_hits: int = Field(default=100, ge=1, le=1000)
    max_grep_mb: float = Field(default=5, gt=0)  # largest file grep will read


class FileSearchModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.star_root = ctx.paths.root.resolve()
        allowed: list[Path] = []
        if self.config.include_default_folders:
            allowed.extend(known_folders().values())
            allowed.append(Path.home())
        allowed.extend(Path(os.path.expandvars(r)).expanduser() for r in self.config.roots)
        seen: set[Path] = set()
        self.allowed: list[Path] = []
        for folder in allowed:
            try:
                resolved = folder.resolve()
            except OSError:
                continue
            if resolved in seen or self._in_starlight(resolved):
                continue
            seen.add(resolved)
            self.allowed.append(resolved)
        ctx.logger.info("ready", roots=[str(p) for p in self.allowed])

    # -- path safety ---------------------------------------------------------------------

    def _in_starlight(self, path: Path) -> bool:
        return inside(path, self.star_root)

    def _roots(self, root: str) -> list[Path]:
        if not root.strip():
            live = [p for p in self.allowed if p.is_dir()]
            if not live:
                raise ToolError("no searchable folders are configured or present")
            return live
        given = expand(root)
        if self._in_starlight(given):
            raise ToolError("Starlight's own folder is not searchable")
        if not any(given == a or given.is_relative_to(a) for a in self.allowed):
            allowed = ", ".join(str(p) for p in self.allowed) or "(none)"
            raise ToolError(
                f"{given} is outside the allowed folders. Allowed: {allowed}. "
                "Add it under 'roots' in config/modules/filesearch.yaml to search it."
            )
        if not given.is_dir():
            raise ToolError(f"{given} is not a folder that exists")
        return [given]

    def _walk(self, roots: list[Path]) -> Any:
        """Yield files under the roots, skipping heavy/irrelevant folders and Starlight's."""
        scanned = 0
        walked: list[Path] = []  # roots inside one another (home and Documents): once each
        for base in roots:
            if any(base.is_relative_to(done) for done in walked):
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                here = Path(dirpath)
                if self._in_starlight(here) or (here != base and here in walked):
                    dirnames[:] = []
                    continue
                dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
                for filename in filenames:
                    scanned += 1
                    if scanned > self.config.max_files_scanned:
                        return
                    yield here / filename
            walked.append(base)

    @staticmethod
    def _info(path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {
            "path": str(path),
            "name": path.name,
            "size": _human(stat.st_size),
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        }

    # -- tools (all read-only) -----------------------------------------------------------

    @tool
    def find(self, name_pattern: str, root: str = "") -> dict[str, Any]:
        """Find files by name. `name_pattern` can be a wildcard like "*.pdf", "report*", or
        part of a name like "invoice". Searches the allowed folders, or just `root` if you
        give one. Newest matches first."""
        pattern = name_pattern.strip()
        if not pattern:
            raise ToolError("give a name or pattern, e.g. '*.pdf' or 'invoice'")
        glob = pattern if any(c in pattern for c in "*?[") else f"*{pattern}*"
        glob_lower = glob.lower()
        hits: list[tuple[float, dict[str, Any]]] = []
        for file in self._walk(self._roots(root)):
            if fnmatch.fnmatch(file.name.lower(), glob_lower):
                try:
                    hits.append((file.stat().st_mtime, self._info(file)))
                except OSError:
                    continue
                if len(hits) > self.config.max_hits * 4:
                    break
        hits.sort(key=lambda h: h[0], reverse=True)
        shown = [info for _, info in hits[: self.config.max_hits]]
        result: dict[str, Any] = {"pattern": glob, "count": len(shown), "files": shown}
        if len(hits) > len(shown):
            result["note"] = f"showing the newest {len(shown)}"
        return result

    @tool
    def grep(self, text: str, root: str = "", max_hits: int = 0) -> dict[str, Any]:
        """Search inside text files for a phrase. Returns the files and the matching lines.
        Only readable text files are searched (documents, code, notes, CSVs); big and
        binary files are skipped. Give `root` to search one folder."""
        needle = text.strip()
        if not needle:
            raise ToolError("give some text to search for")
        limit = max_hits or self.config.max_hits
        needle_lower = needle.lower()
        cap = int(self.config.max_grep_mb * 1024**2)
        matches: list[dict[str, Any]] = []
        files_with_hits = 0
        for file in self._walk(self._roots(root)):
            if file.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            try:
                if file.stat().st_size > cap:
                    continue
                found = self._scan_file(file, needle_lower)
            except OSError:
                continue
            if found:
                files_with_hits += 1
                for lineno, line in found:
                    matches.append({"path": str(file), "line": lineno, "text": line})
                    if len(matches) >= limit:
                        return {
                            "text": needle,
                            "files": files_with_hits,
                            "matches": matches,
                            "note": f"stopped at {limit} matches",
                        }
        return {"text": needle, "files": files_with_hits, "matches": matches}

    @staticmethod
    def _scan_file(file: Path, needle_lower: str) -> list[tuple[int, str]]:
        """Matching lines of a text file in whatever encoding Windows wrote it: UTF-8,
        UTF-16 (PowerShell 5 `>`, many logs) or Windows-1252 (Excel's CSV export)."""
        with file.open("rb") as handle:
            head = handle.read(4)
        if head.startswith((b"\xff\xfe", b"\xfe\xff")):
            encoding = "utf-16"
        elif head.startswith(b"\xef\xbb\xbf"):
            encoding = "utf-8-sig"
        else:
            encoding = "utf-8"
        try:
            return _matching_lines(file, needle_lower, encoding, "strict")
        except UnicodeDecodeError:
            fallback = "cp1252" if encoding.startswith("utf-8") else encoding
            return _matching_lines(file, needle_lower, fallback, "replace")

    @tool
    def info(self, path: str) -> dict[str, Any]:
        """Details about one file or folder: size, when it changed, and whether it exists."""
        given = Path(os.path.expandvars(path.strip())).expanduser().resolve()
        if self._in_starlight(given):
            raise ToolError("Starlight's own folder is off-limits")
        if not any(given == a or given.is_relative_to(a) for a in self.allowed):
            raise ToolError(f"{given} is outside the allowed folders")
        if not given.exists():
            raise ToolError(f"{given} does not exist")
        if given.is_dir():
            try:
                count = sum(1 for _ in given.iterdir())
            except OSError:
                count = 0
            return {"path": str(given), "type": "folder", "items": count}
        return {"type": "file", **self._info(given)}

    @tool
    def recent(self, root: str = "", days: int = 7) -> dict[str, Any]:
        """Files changed in the last `days` days (default 7), newest first."""
        if days < 1:
            raise ToolError("days must be at least 1")
        cutoff = time.time() - days * 86400
        hits: list[tuple[float, dict[str, Any]]] = []
        for file in self._walk(self._roots(root)):
            try:
                mtime = file.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                hits.append((mtime, self._info(file)))
                if len(hits) > self.config.max_hits * 4:
                    break
        hits.sort(key=lambda h: h[0], reverse=True)
        shown = [info for _, info in hits[: self.config.max_hits]]
        return {"days": days, "count": len(shown), "files": shown}


def _human(size: int) -> str:
    if size < 1024:
        return f"{size} bytes"
    if size < 1024**2:
        return f"{size / 1024:.1f} KB"
    if size < 1024**3:
        return f"{size / 1024**2:.1f} MB"
    return f"{size / 1024**3:.1f} GB"


def _matching_lines(
    file: Path, needle_lower: str, encoding: str, errors: str
) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    with file.open("r", encoding=encoding, errors=errors) as handle:
        for lineno, line in enumerate(handle, 1):
            if needle_lower in line.lower():
                found.append((lineno, line.strip()[:200]))
                if len(found) >= 20:  # per-file cap
                    break
    return found
