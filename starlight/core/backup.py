"""Backup and restore: your conversations, notes, memory, skills and settings in one zip.

This is separate from the launcher's update snapshot (``snapshot_before_update`` in
launcher/starlight_launcher.py): that one is stdlib-only, runs before an update and has its
own rules. Do not merge the two.

The file (``starlight-backup-YYYYMMDD-HHMMSS.zip``) holds ``manifest.json`` first, then
``config/``, ``data/``, ``memory/``, ``skills/`` and ``notes/`` (the folders Notes, Journal,
Recipes, Clippings and Inbox of the output folder, which can be anywhere). Not in it: the
secrets themselves (only their names; the OS keyring is tied to this PC and user), paired
devices, the app window's profile, indexes and caches that rebuild, logs, models and the STOP
file. Also left out: ``config/starlight.launcher.yaml`` and ``starlight.remote.yaml`` (they
describe this PC: its model server and its Tailscale address; see PER_PC_CONFIG; if an older
backup holds them they are ignored on restore), and ``data/skills.git``. The skills
themselves are in the backup, their change history is not: Starlight runs ``git`` on that
folder, and a git folder from a file can carry hooks and a config that make git start
programs. The PC's own history stays as it is.

Hidden files and folders inside the note folders (a ``.git`` folder, ``.obsidian``,
Syncthing's files) are not in a backup. A restore moves them from the old folder into the new
one, so they stay where they are; if that fails the old folder is kept and named in the
outcome.

The history database (the earlier versions of notes and memory) is copied with
sqlite's backup API, so it is consistent while Starlight runs.

A restore replaces, it does not merge. It is staged first: the file is checked, unpacked into
``backups/.restore-<id>/`` and ``data/restore-pending.json`` is written. The swap runs at the
next start (``apply_pending``), before anything has the data open: first a safety copy
``backups/before-restore-<time>.zip`` of what is there now, then part by part (the old one
moved aside, the staged one moved in), rolled back if a part fails. The outcome goes to
``data/restore-result.json``.

A backup file is untrusted input. ``validate`` and ``stage_restore`` check names (relative
POSIX paths, no ``..``, drive letters, backslashes or NUL, only the allowed places, no
symlinks, no duplicates), caps on member count and sizes, and count the bytes really written,
not those the header claims. Nothing is written outside the stage folder.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import os
import platform
import re
import secrets
import shutil
import sqlite3
import stat
import sys
import uuid
import zipfile
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starlight import __version__
from starlight.core.fsutil import replace_file, retry_on_lock, write_atomic

log = logging.getLogger(__name__)

FORMAT = 1
PARTS = ("config", "data", "memory", "skills", "notes")
# The notes: every top-level folder of the output folder that the Notes index covers (not
# hidden, not in notebook.index.SKIP_TOP: the "code" folder holds programs), and the loose
# files there. The manifest lists the names it holds (notes_folders, notes_files); a restore
# swaps exactly those. Backups made earlier held these five (and, for a while, two more).
OLD_NOTE_FOLDERS = ("Notes", "Journal", "Recipes", "Clippings", "Inbox")
FLASHCARDS = "data/notebook/flashcards.json"  # when each card comes back
MANIFEST = "manifest.json"
PENDING = "restore-pending.json"
RESULT = "restore-result.json"
SAFETY_PREFIX = "before-restore-"
SAFETY_KEEP = 3
CHUNK = 1024**2

MAX_MANIFEST = 1024**2
MAX_MEMBERS = 200_000
MAX_TOTAL = 8 * 1024**3  # unpacked, all together
MAX_FILE = 2 * 1024**3  # unpacked, one file

# Sidecars of the history database must go when the database is replaced.
HISTORY = "data/history/history.sqlite"
_SIDECARS = (HISTORY + "-wal", HISTORY + "-shm")
_SINGLE_FILES = ("data/notebook/pinned.json", "data/notebook/welcomed", FLASHCARDS, HISTORY)
_DIRECTORIES = ("data/conversations", "memory", "skills")
# Config files that describe THIS PC, not a choice of the user: the launcher writes the
# first at every start (the model server), `starlight remote enable` the second (this PC's
# Tailscale address, and whether the phone may connect). They are never put in a backup, and
# if an older file holds them they are ignored: the current ones are neither replaced nor
# deleted. (The others in config.OVERLAYS, safety and local, are the user's own.)
PER_PC_CONFIG = ("starlight.launcher.yaml", "starlight.remote.yaml")
# Rebuildable, deleted after a restore so they are made again from the restored files.
_INDEXES = (
    "data/memory_index.sqlite",
    "data/notebook/index.sqlite",
)
_SKIP_SUFFIXES = (".tmp", ".part", ".lock")
_STAGE_NAME = re.compile(r"^\.restore-[0-9a-f]{12}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class BackupError(Exception):
    """The message is for the user."""


@dataclass(frozen=True)
class BackupSources:
    paths: Any  # starlight.core.paths.Paths
    notes_root: Path | None  # the output folder (holds Notes, Journal ...); None if unusable


def backups_dir(paths: Any) -> Path:
    return Path(paths.root) / "backups"


def notes_root_of(paths: Any) -> Path | None:
    """The output folder, or None when the config or the folder setting is unusable."""
    from starlight.core.config import load_config
    from starlight.core.output import resolve_output_folder

    try:
        return resolve_output_folder(load_config(paths).output.folder, Path(paths.root))
    except Exception:
        return None


def sources_for(paths: Any) -> BackupSources:
    return BackupSources(paths, notes_root_of(paths))


def backup_name(now: dt.datetime | None = None, prefix: str = "starlight-backup-") -> str:
    return f"{prefix}{(now or dt.datetime.now()):%Y%m%d-%H%M%S}.zip"


# -- what goes in --------------------------------------------------------------------------


def _hidden(part: str) -> bool:
    return part.startswith(".") or part.lower().endswith(_SKIP_SUFFIXES)


def _walk(folder: Path, arc: str) -> Iterator[tuple[str, Path]]:
    """The files under ``folder`` as (name in the zip, path). No hidden or temporary files,
    and links are never followed."""
    if not folder.is_dir() or folder.is_symlink():
        return
    for current, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not _hidden(d) and not (Path(current) / d).is_symlink())
        base = Path(current)
        for name in sorted(files):
            path = base / name
            if _hidden(name) or path.is_symlink():
                continue
            relative = path.relative_to(folder).as_posix()
            yield f"{arc}/{relative}", path


def _yaml_files(folder: Path, arc: str) -> Iterator[tuple[str, Path]]:
    if folder.is_dir():
        for path in sorted(folder.glob("*.yaml")):
            if path.name in PER_PC_CONFIG and folder.name == "config":
                continue
            if path.is_file() and not path.is_symlink():
                yield f"{arc}/{path.name}", path


def _listing(src: BackupSources) -> list[tuple[str, Path]]:
    root = Path(src.paths.root)
    found: list[tuple[str, Path]] = []
    found += _yaml_files(root / "config", "config")
    found += _yaml_files(root / "config" / "modules", "config/modules")
    for name in _SINGLE_FILES:
        if name != HISTORY and (root / name).is_file() and not (root / name).is_symlink():
            found.append((name, root / name))
    for name in _DIRECTORIES:
        found += _walk(root / name, name)
    return found


def _notes_entries(root: Path, unsafe: list[str]) -> tuple[list[str], list[str], list[Any]]:
    """The notes of the output folder ``root``: folder names, loose file names, and all the
    files as (name in the zip, path). An entry Windows could not hold (``_check_name``) is left
    out and named in ``unsafe``: a zip Starlight would refuse to read is never written."""
    from starlight.notebook.index import SKIP_TOP  # (the same rule as the index's walk)

    folders: list[str] = []
    files: list[str] = []
    members: list[Any] = []
    seen: set[str] = set()
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return folders, files, members
    for entry in entries:
        name = entry.name
        if _hidden(name) or entry.is_symlink():
            continue
        is_dir = entry.is_dir()
        if not is_dir and not entry.is_file():
            continue
        if is_dir and name.casefold() in SKIP_TOP:
            continue
        try:
            _check_name(name)
        except BackupError:
            unsafe.append(f"notes/{name}")
            continue
        if name.casefold() in seen:
            unsafe.append(f"notes/{name}")
            continue
        seen.add(name.casefold())
        if is_dir:
            folders.append(name)
            members += _walk(entry, f"notes/{name}")
        else:
            files.append(name)
            members.append((f"notes/{name}", entry))
    return folders, files, members


def human(size: int) -> str:
    if size < 1024:
        return f"{size} bytes"
    if size < 1024**2:
        return f"{size / 1024:.0f} KB"
    if size < 1024**3:
        return f"{size / 1024**2:.0f} MB"
    return f"{size / 1024**3:.1f} GB"


def _group(name: str) -> str:
    """The part of the data a file belongs to, for saying what is big."""
    parts = name.split("/")
    return "/".join(parts[:2]) if parts[0] in ("notes", "data") and len(parts) > 2 else parts[0]


def _biggest(members: list[tuple[str, int]], by_size: bool) -> str:
    totals: dict[str, int] = {}
    for name, size in members:
        key = _group(name)
        totals[key] = totals.get(key, 0) + (size if by_size else 1)
    top = sorted(totals.items(), key=lambda item: -item[1])[:3]
    return ", ".join(f"{k} ({human(v) if by_size else f'{v:,} files'})" for k, v in top)


@dataclass
class _Plan:
    members: list[tuple[str, Path]]
    notes_folders: list[str]
    notes_files: list[str]
    too_big: list[str]  # files over MAX_FILE
    unsafe: list[str]  # names the restore would refuse


def _plan(src: BackupSources) -> _Plan:
    """What goes in, with the limits a restore would enforce applied now: a backup that could
    not be restored is never written. Raises BackupError if the rest is too big or has too
    many files (naming the biggest parts)."""
    root = Path(src.paths.root)
    unsafe: list[str] = []
    folders: list[str] = []
    files: list[str] = []
    members = _listing(src)
    if src.notes_root is not None:
        folders, files, found = _notes_entries(src.notes_root, unsafe)
        members += found
    if (root / HISTORY).is_file() and not (root / HISTORY).is_symlink():
        members.append((HISTORY, root / HISTORY))
    kept: list[tuple[str, Path]] = []
    sized: list[tuple[str, int]] = []
    too_big: list[str] = []
    seen: set[str] = set()
    for name, path in members:
        try:
            _check_name(name)
        except BackupError:
            unsafe.append(name)
            continue
        if name.casefold() in seen:
            unsafe.append(name)
            continue
        seen.add(name.casefold())
        try:
            size = path.stat().st_size
        except OSError:
            size = 0  # (it is counted as unreadable when it is written)
        if size > MAX_FILE:
            too_big.append(name)
            continue
        kept.append((name, path))
        sized.append((name, size))
    total = sum(size for _, size in sized)
    if total > MAX_TOTAL:
        raise BackupError(
            f"Your data is {human(total)}, more than a backup can hold ({human(MAX_TOTAL)}). "
            f"The biggest parts: {_biggest(sized, True)}. "
            "Move or delete some of it, or copy it by hand."
        )
    if len(kept) + 1 > MAX_MEMBERS:
        raise BackupError(
            f"There are {len(kept):,} files, more than a backup can hold ({MAX_MEMBERS:,}). "
            f"The parts with most files: {_biggest(sized, False)}. "
            "Move or delete some of them, or copy them by hand."
        )
    return _Plan(kept, folders, files, too_big, unsafe)


def _names(found: list[str]) -> str:
    return ", ".join(found[:5]) + (f" and {len(found) - 5} more" if len(found) > 5 else "")


def left_out_lines(info: dict[str, Any]) -> list[str]:
    """What a backup did not take, in plain words (for the page and the command line)."""
    lines: list[str] = []
    big, unsafe = info.get("too_big") or [], info.get("unsafe") or []
    if big:
        n = info.get("too_big_count", len(big))
        lines.append(
            f"{n} file{'s' if n != 1 else ''} over {human(MAX_FILE)} "
            f"{'are' if n != 1 else 'is'} not in the backup: {_names(big)}"
        )
    if unsafe:
        n = info.get("unsafe_count", len(unsafe))
        lines.append(
            f"{n} file{'s' if n != 1 else ''} with a name that Windows cannot hold "
            f"{'are' if n != 1 else 'is'} not in the backup: {_names(unsafe)}"
        )
    unread = info.get("unreadable") or 0
    if unread:
        lines.append(
            f"{unread} file{'s' if unread != 1 else ''} could not be read and "
            f"{'are' if unread != 1 else 'is'} not in the backup."
        )
    return lines


def check_backup(src: BackupSources) -> dict[str, Any]:
    """Whether a backup can be made now, without writing it: raises BackupError if not, else
    the lines about what would be left out."""
    plan = _plan(src)
    info = {"too_big": plan.too_big[:5], "too_big_count": len(plan.too_big)}
    info |= {"unsafe": plan.unsafe[:5], "unsafe_count": len(plan.unsafe)}
    return {"left_out": left_out_lines(info)}


def _vault_keys(paths: Any) -> list[str]:
    try:
        from starlight.core.vault import Vault

        return Vault(Path(paths.config) / "vault_index.yaml").names()
    except Exception:
        return []


def _snapshot_history(source: Path, target: Path) -> None:
    """A consistent copy of the history database, also while Starlight writes to it."""
    reader = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        writer = sqlite3.connect(target)
        try:
            reader.backup(writer)
        finally:
            writer.close()
    finally:
        reader.close()


def _count(name: str) -> str | None:
    return name.split("/", 1)[0] if "/" in name else None


def create_backup(
    src: BackupSources,
    target: Path,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Write the backup to ``target`` (as ``.part`` first, renamed when complete) and return
    its manifest, with ``skipped`` (how many files are not in it), ``left_out`` (what to tell
    the user) and the first names of each kind. A file locked for good or gone meanwhile, one
    over MAX_FILE and one with a name a restore would refuse never fail the whole backup. If
    the rest is over MAX_TOTAL or MAX_MEMBERS nothing is written and BackupError says what is
    big: a backup that cannot be restored is never made."""
    root = Path(src.paths.root)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    snapshot = target.with_name(f".history-{uuid.uuid4().hex[:8]}.sqlite")
    plan = _plan(src)  # (before anything is written)
    members = plan.members
    history = root / HISTORY
    try:
        for number, (name, _path) in enumerate(members):
            if name == HISTORY:
                try:
                    retry_on_lock(lambda: _snapshot_history(history, snapshot))
                except (sqlite3.Error, OSError) as exc:
                    raise BackupError(f"The history database could not be copied: {exc}") from exc
                members[number] = (HISTORY, snapshot)
        sizes: dict[str, list[int]] = {name: [0, 0] for name in PARTS}
        for name, path in members:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            counted = sizes[_count(name) or "data"]
            counted[0] += 1
            counted[1] += size
        notes_md = sum(1 for n, _ in members if n.startswith("notes/") and n.endswith(".md"))
        manifest: dict[str, Any] = {
            "format": FORMAT,
            "starlight_version": __version__,
            "created": dt.datetime.now().isoformat(timespec="seconds"),
            "platform": sys.platform,
            "system": platform.platform(),
            "parts": {name: {"files": v[0], "bytes": v[1]} for name, v in sizes.items()},
            "notes_included": src.notes_root is not None,
            "notes_folders": plan.notes_folders,
            "notes_files": plan.notes_files,
            "flashcards": True,
            "counts": {
                "conversations": sum(1 for n, _ in members if n.startswith("data/conversations/")),
                "notes": notes_md,
                "memories": sum(1 for n, _ in members if n.startswith("memory/")),
            },
            "vault_keys": _vault_keys(src.paths),
        }
        skipped = 0
        with zipfile.ZipFile(
            part, "w", zipfile.ZIP_DEFLATED, compresslevel=6, strict_timestamps=False
        ) as zf:
            zf.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
            for number, (name, path) in enumerate(members, 1):
                if not _add(zf, name, path):
                    skipped += 1
                if progress is not None:
                    progress(number, len(members))
        replace_file(part, target)
        manifest["skipped"] = skipped + len(plan.too_big) + len(plan.unsafe)
        manifest["unreadable"] = skipped
        manifest["too_big"] = plan.too_big[:5]
        manifest["too_big_count"] = len(plan.too_big)
        manifest["unsafe"] = plan.unsafe[:5]
        manifest["unsafe_count"] = len(plan.unsafe)
        manifest["left_out"] = left_out_lines(manifest)
        return manifest
    except BackupError:
        raise
    except OSError as exc:
        raise BackupError(f"The backup could not be written: {exc}") from exc
    finally:
        with contextlib.suppress(OSError):
            part.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            snapshot.unlink(missing_ok=True)


def _add(zf: zipfile.ZipFile, name: str, path: Path) -> bool:
    """One file into the zip. False if it could not be read (it is left out entirely)."""
    try:
        handle = retry_on_lock(lambda: open(path, "rb"))  # noqa: SIM115 - closed below
    except OSError:
        return False
    with handle:
        try:
            info = zipfile.ZipInfo.from_file(path, name, strict_timestamps=False)
        except (OSError, ValueError):
            return False
        info.compress_type = zipfile.ZIP_DEFLATED
        with zf.open(info, "w", force_zip64=info.file_size >= 1024**3) as out:
            shutil.copyfileobj(handle, out, CHUNK)
    return True


def stream_file(path: Path) -> Iterator[bytes]:
    """The file in chunks; it is deleted when the stream ends or is closed."""
    try:
        with open(path, "rb") as handle:
            while chunk := handle.read(CHUNK):
                yield chunk
    finally:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


# -- checking a file -----------------------------------------------------------------------


def _bad(reason: str) -> BackupError:
    return BackupError(f"This file is not a backup Starlight can restore: {reason}")


_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{n}" for n in range(1, 10)),
        *(f"LPT{n}" for n in range(1, 10)),
    }
)


def _check_name(name: str) -> str:
    """The name as a clean relative POSIX path (without a trailing slash), or an error."""
    if not name or "\x00" in name or "\\" in name or ":" in name or name.startswith("/"):
        raise _bad(f"unsafe file name {name[:60]!r}")
    parts = name.rstrip("/").split("/")
    for part in parts:
        # (hidden names never come out of a backup: a ".git" folder could run programs)
        if part in ("", ".", "..") or part.startswith(".") or part.endswith((".", " ")):
            raise _bad(f"unsafe file name {name[:60]!r}")
        if part.split(".")[0].rstrip().upper() in _RESERVED:  # Windows device names
            raise _bad(f"unsafe file name {name[:60]!r}")
    return "/".join(parts)


def _notes_names(manifest: dict[str, Any], strict: bool) -> tuple[set[str], set[str]]:
    """The note folders and loose files the manifest lists (older ones: the first five
    folders). With ``strict`` a name that is not a plain top-level name is an error."""
    from starlight.notebook.index import SKIP_TOP

    found: list[set[str]] = []
    for key, old in (("notes_folders", OLD_NOTE_FOLDERS), ("notes_files", ())):
        listed = manifest.get(key, list(old))
        if not isinstance(listed, list):
            if strict:
                raise _bad("the manifest is not valid")
            listed = []
        names: set[str] = set()
        for item in listed:
            # (never "code": a backup holds no programs, so a file that lists it is not ours
            # and must not put programs where Starlight keeps the ones it runs)
            if isinstance(item, str) and "/" not in item and item.casefold() not in SKIP_TOP:
                try:
                    names.add(_check_name(item))
                    continue
                except BackupError:
                    pass
            if strict:
                raise _bad("the manifest is not valid")
        found.append(names)
    return found[0], found[1]


def _allowed(name: str, notes: tuple[set[str], set[str]]) -> bool:
    """Only the places a backup holds; anything else (devices, logs, code) is refused."""
    parts = name.split("/")
    first = parts[0]
    if first in ("memory", "skills"):
        return True
    if first == "notes":
        folders, files = notes
        if len(parts) < 2:
            return False
        return parts[1] in folders or (len(parts) == 2 and parts[1] in files)
    if first == "config":
        if len(parts) == 2:
            return parts[1].endswith(".yaml")
        return len(parts) == 3 and parts[1] == "modules" and parts[2].endswith(".yaml")
    if first == "data":
        return name in _SINGLE_FILES or name.startswith("data/conversations/")
    return False


def _read_manifest(zf: zipfile.ZipFile) -> dict[str, Any]:
    try:
        info = zf.getinfo(MANIFEST)
    except KeyError:
        raise _bad("it has no manifest.json") from None
    if info.file_size > MAX_MANIFEST:
        raise _bad("the manifest is too big")
    try:
        with zf.open(info) as handle:
            raw = handle.read(MAX_MANIFEST + 1)
        if len(raw) > MAX_MANIFEST:
            raise _bad("the manifest is too big")
        manifest = json.loads(raw.decode("utf-8"))
    except (ValueError, zipfile.BadZipFile, zlib.error, EOFError, RuntimeError):
        raise _bad("the manifest cannot be read") from None
    if not isinstance(manifest, dict):
        raise _bad("the manifest is not valid")
    version = manifest.get("format")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise _bad("the manifest has no known format")
    if version > FORMAT:
        raise BackupError("This backup was made by a newer Starlight. Update Starlight first.")
    if not isinstance(manifest.get("parts"), dict):
        raise _bad("the manifest is not valid")
    return manifest


def _open(zip_path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError, EOFError, zlib.error, ValueError):
        raise _bad("it is not a zip file, or it is damaged") from None


def validate(zip_path: Path) -> dict[str, Any]:
    """Check a backup file without unpacking it; its manifest if it is acceptable."""
    with _open(Path(zip_path)) as zf:
        return _validate(zf)


def _validate(zf: zipfile.ZipFile) -> dict[str, Any]:
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise _bad("it has too many files")
    manifest = _read_manifest(zf)
    notes = _notes_names(manifest, strict=True)
    seen: set[str] = set()
    total = 0
    for info in infos:
        name = _check_name(info.filename)
        key = name.casefold()  # Windows would treat "A" and "a" as one file
        if key in seen:
            raise _bad(f"{name!r} is in it twice")
        seen.add(key)
        if (info.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
            raise _bad("it contains a link")
        if name == MANIFEST:
            continue
        if not _allowed(name, notes):
            raise _bad(f"{name!r} is not a place a backup may hold")
        if info.is_dir():
            continue
        if info.file_size > MAX_FILE:
            raise _bad("a file in it is too big")
        total += info.file_size
        if total > MAX_TOTAL:
            raise _bad("it is too big")
    return manifest


# -- staging a restore ---------------------------------------------------------------------


def _extract(zf: zipfile.ZipFile, stage: Path) -> None:
    """Unpack into ``stage``. Sizes are counted as the bytes are written."""
    base = stage.resolve()
    total = 0
    for info in zf.infolist():
        name = _check_name(info.filename)
        target = (base / name).resolve()
        if not target.is_relative_to(base):
            raise _bad(f"unsafe file name {name[:60]!r}")
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with zf.open(info) as source, open(target, "xb") as out:
            while chunk := source.read(CHUNK):
                written += len(chunk)
                total += len(chunk)
                if written > MAX_FILE or total > MAX_TOTAL:
                    raise _bad("it unpacks to more than it says")
                out.write(chunk)


def pending_file(paths: Any) -> Path:
    return Path(paths.data) / PENDING


def result_file(paths: Any) -> Path:
    return Path(paths.data) / RESULT


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def summary(manifest: dict[str, Any]) -> dict[str, Any]:
    """What the page shows about a backup."""
    counts = manifest.get("counts")
    return {
        "created": manifest.get("created"),
        "starlight_version": manifest.get("starlight_version"),
        "platform": manifest.get("platform"),
        "counts": counts if isinstance(counts, dict) else {},
        "vault_keys": manifest.get("vault_keys")
        if isinstance(manifest.get("vault_keys"), list)
        else [],
    }


def stage_restore(paths: Any, zip_path: Path) -> dict[str, Any]:
    """Check the file, unpack it next to the data and note that it is to be applied at the
    next start. A restore staged earlier is replaced. Returns the manifest."""
    zip_path = Path(zip_path)
    folder = backups_dir(paths)
    folder.mkdir(parents=True, exist_ok=True)
    with _open(zip_path) as zf:
        manifest = _validate(zf)
        need = sum(i.file_size for i in zf.infolist())
        free = shutil.disk_usage(folder).free
        if free < need:
            raise BackupError("There is not enough free space on the disk to unpack the backup.")
        cancel_pending(paths)
        stage_id = uuid.uuid4().hex[:12]
        stage = folder / f".restore-{stage_id}"
        try:
            _extract(zf, stage)
        except BackupError:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError):
            shutil.rmtree(stage, ignore_errors=True)
            raise _bad("it is damaged") from None
        except OSError as exc:
            shutil.rmtree(stage, ignore_errors=True)
            raise BackupError(f"The backup could not be unpacked: {exc}") from exc
    note = {
        "version": 1,
        "id": stage_id,
        "staged": dt.datetime.now().isoformat(timespec="seconds"),
        "stage": stage.name,
        "manifest": manifest,
    }
    try:
        write_atomic(pending_file(paths), json.dumps(note, indent=2, ensure_ascii=False))
    except OSError as exc:
        shutil.rmtree(stage, ignore_errors=True)
        raise BackupError(f"The restore could not be saved: {exc}") from exc
    return manifest


def read_pending(paths: Any) -> dict[str, Any] | None:
    """The staged restore (id, staged, manifest), or None; a damaged file counts as none."""
    note = _read_json(pending_file(paths))
    if note is None or not isinstance(note.get("manifest"), dict):
        return None
    return note


def cancel_pending(paths: Any) -> bool:
    """Drop a staged restore. True if there was one."""
    found = pending_file(paths).exists()
    note = _read_json(pending_file(paths))
    name = note.get("stage") if note else None
    if isinstance(name, str) and _STAGE_NAME.match(name):
        shutil.rmtree(backups_dir(paths) / name, ignore_errors=True)
    with contextlib.suppress(OSError):
        pending_file(paths).unlink(missing_ok=True)
    return found


def last_result(paths: Any) -> dict[str, Any] | None:
    return _read_json(result_file(paths))


def new_token() -> str:
    return secrets.token_urlsafe(18)


def drop_old_uploads(paths: Any, seconds: float = 1800) -> None:
    """Uploads that were never restored (the user said no) do not stay on the disk."""
    now = dt.datetime.now().timestamp()
    for path in backups_dir(paths).glob(".upload-*.zip"):
        with contextlib.suppress(OSError):
            if now - path.stat().st_mtime > seconds:
                path.unlink()


def upload_path(paths: Any, token: str) -> Path | None:
    """The file an upload was saved as, for a token we handed out (None for any other text)."""
    if not _TOKEN.match(token):
        return None
    path = backups_dir(paths) / f".upload-{token}.zip"
    return path if path.is_file() else None


# -- applying at start ---------------------------------------------------------------------


@dataclass
class _Swap:
    target: Path
    aside: Path | None
    source: Path | None
    moved_in: bool = False


def _units(
    paths: Any, notes_root: Path | None, stage: Path, manifest: dict[str, Any]
) -> list[tuple[Path, Path]]:
    """(staged path, target) for every part that is swapped, a staged path that may not
    exist (the backup has none: the current one is then moved away)."""
    root = Path(paths.root)
    units: list[tuple[Path, Path]] = []
    for name in (*_DIRECTORIES, *_SINGLE_FILES, *_SIDECARS):
        if name == FLASHCARDS and manifest.get("flashcards") is not True:
            continue  # an older backup has none: the current schedule is not thrown away
        units.append((stage / name, root / name))
    for folder, arc in (
        (root / "config", "config"),
        (root / "config" / "modules", "config/modules"),
    ):
        names = {p.name for p in folder.glob("*.yaml")} if folder.is_dir() else set()
        staged = stage / arc
        if staged.is_dir():
            names |= {p.name for p in staged.glob("*.yaml")}
        if arc == "config":
            names -= set(PER_PC_CONFIG)  # this PC's own: never replaced, never removed
        units += [(staged / n, folder / n) for n in sorted(names)]
    if notes_root is not None and manifest.get("notes_included") is True:
        folders, files = _notes_names(manifest, strict=False)
        for note_name in sorted(folders | files):  # (every other entry is left alone)
            units.append((stage / "notes" / note_name, notes_root / note_name))
    return units


def _gone(path: Path) -> bool:
    return not path.exists() and not path.is_symlink()


def _aside(target: Path, stage_id: str) -> Path:
    candidate = target.with_name(f".{target.name}.old-{stage_id}")
    number = 1
    while not _gone(candidate):
        number += 1
        candidate = target.with_name(f".{target.name}.old-{stage_id}-{number}")
    return candidate


def _drop(path: Path) -> None:
    with contextlib.suppress(OSError):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def _same_drive(a: Path, b: Path) -> bool:
    for path in (b, b.parent):
        if path.exists():
            with contextlib.suppress(OSError):
                return os.stat(a).st_dev == os.stat(path).st_dev
            break
    return True


def _hidden_entries(folder: Path) -> list[Path]:
    """The hidden files and folders (names starting with ".") under ``folder``, at any depth;
    a hidden folder is one entry, not looked into. A backup holds none of them."""
    found: list[Path] = []
    try:
        entries = sorted(folder.iterdir())
    except OSError:
        return found
    for entry in entries:
        if entry.name.startswith("."):
            found.append(entry)
        elif entry.is_dir() and not entry.is_symlink():
            found += _hidden_entries(entry)
    return found


def _keep_hidden(old: Path, new: Path) -> bool:
    """Move the hidden entries of the replaced folder ``old`` (a ".git" folder, ".obsidian",
    Syncthing's files ...) to the same place in ``new``. True if all are moved and ``old`` can
    go; False if one could not be (it already exists there, or is locked): ``old`` stays."""
    try:
        hidden = _hidden_entries(old)
        if hidden:
            new.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    complete = True
    for entry in hidden:
        place = new / entry.relative_to(old)
        try:
            if not _gone(place):
                complete = False  # the new one wins; the old stays where it is
                continue
            place.parent.mkdir(parents=True, exist_ok=True)
            replace_file(entry, place)
        except OSError as exc:
            log.error("restore: could not keep %s: %s", entry, exc)
            complete = False
    return complete


def _swap(
    units: list[tuple[Path, Path]], stage_id: str, keep_hidden: frozenset[Path] = frozenset()
) -> list[Path]:
    """Put the staged parts in place; roll back the parts done so far if one fails."""
    done: list[_Swap] = []
    copies: list[Path] = []
    try:
        plan: list[tuple[Path, Path | None]] = []
        for staged, target in units:
            if not staged.exists():
                plan.append((target, None))
            elif _same_drive(staged, target):
                plan.append((target, staged))
            else:  # another drive cannot be renamed into: copy next to the target first
                target.parent.mkdir(parents=True, exist_ok=True)
                copy = target.with_name(f".{target.name}.new-{stage_id}")
                _drop(copy)
                if staged.is_dir():
                    shutil.copytree(staged, copy)
                else:
                    shutil.copy2(staged, copy)
                copies.append(copy)
                plan.append((target, copy))
        for target, source in plan:
            if _gone(target) and source is None:
                continue
            swap = _Swap(target, None, source)
            if not _gone(target):
                aside = _aside(target, stage_id)
                replace_file(target, aside)  # (if this fails, nothing of it is to undo)
                swap.aside = aside
            done.append(swap)
            if source is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                replace_file(source, target)
                swap.moved_in = True
    except Exception as exc:
        problems = _roll_back(done)
        for copy in copies:
            _drop(copy)
        message = (
            f"Replacing {exc.filename or 'a file'} failed: {exc}"
            if isinstance(exc, OSError)
            else f"The restore failed: {exc}"
        )
        if isinstance(exc, PermissionError):
            message += " Close other programs that use the Starlight folder and try again."
        if problems:
            message += f" Some parts could not be put back: {'; '.join(problems)}"
        raise BackupError(message) from exc
    kept: list[Path] = []
    for swap in done:
        if swap.aside is None:
            continue
        if swap.target in keep_hidden and not _keep_hidden(swap.aside, swap.target):
            kept.append(swap.aside)  # (the data is restored; only these files are not moved)
            continue
        _drop(swap.aside)
    return kept


def _roll_back(done: list[_Swap]) -> list[str]:
    problems: list[str] = []
    for swap in reversed(done):
        try:
            if swap.moved_in and swap.source is not None:
                replace_file(swap.target, swap.source)
            if swap.aside is not None:
                replace_file(swap.aside, swap.target)
        except OSError as exc:
            problems.append(f"{swap.target.name} ({exc})")
            log.error("restore: could not put %s back: %s", swap.target, exc)
    return problems


def prune_backups(folder: Path, prefix: str, keep: int = SAFETY_KEEP) -> None:
    """Keep the newest ``keep`` zips whose name starts with ``prefix`` (the names hold the
    time, so the name order is the time order)."""
    found = sorted(p for p in folder.glob(f"{prefix}*.zip") if p.is_file())
    for old in found[: max(0, len(found) - keep)]:
        with contextlib.suppress(OSError):
            old.unlink()


def _write_result(paths: Any, outcome: dict[str, Any]) -> None:
    outcome["at"] = dt.datetime.now().isoformat(timespec="seconds")
    with contextlib.suppress(OSError):
        write_atomic(result_file(paths), json.dumps(outcome, indent=2, ensure_ascii=False))


def clean_leftovers(paths: Any) -> None:
    """Old uploads, downloads and stages nobody refers to any more."""
    folder = backups_dir(paths)
    if not folder.is_dir():
        return
    note = _read_json(pending_file(paths))
    keep = note.get("stage") if note else None
    for path in folder.glob(".*"):
        young = False
        with contextlib.suppress(OSError):
            young = (dt.datetime.now().timestamp() - path.stat().st_mtime) < 3600
        if young:
            continue
        if path.name.startswith((".upload-", ".download-", ".history-")) or (
            _STAGE_NAME.match(path.name) and path.name != keep
        ):
            _drop(path)


def apply_pending(paths: Any, notes_root: Path | str | None = "auto") -> dict[str, Any] | None:
    """At start, before a Kernel is built: carry out a staged restore. Returns the outcome
    (also written to ``data/restore-result.json``), or None if nothing was pending or another
    Starlight is running. Never raises: a damaged pending file only gets recorded."""
    try:
        if not pending_file(paths).exists():
            clean_leftovers(paths)
            return None
        from starlight.core.corelock import core_running

        if core_running(Path(paths.data) / "core.lock"):
            return None  # the data is in use: it stays staged until the next start
        outcome = _apply(paths, notes_root)
    except Exception as exc:
        log.exception("restore failed unexpectedly")
        outcome = {"ok": False, "reason": f"The restore failed: {exc}"}
        _write_result(paths, outcome)
    # Whatever happened, the staged restore is used up.
    with contextlib.suppress(Exception):
        cancel_pending(paths)
        clean_leftovers(paths)
    return outcome


def _apply(paths: Any, notes_root: Path | str | None) -> dict[str, Any]:
    note = read_pending(paths)
    name = note.get("stage") if note else None
    if note is None or not isinstance(name, str) or not _STAGE_NAME.match(name):
        outcome = {"ok": False, "reason": "The restore file was damaged, so nothing was changed."}
        _write_result(paths, outcome)
        return outcome
    stage = backups_dir(paths) / name
    manifest = note["manifest"]
    if not stage.is_dir():
        outcome = {"ok": False, "reason": "The unpacked backup is gone; nothing was changed."}
        _write_result(paths, outcome)
        return outcome
    root = notes_root_of(paths) if notes_root == "auto" else notes_root
    if manifest.get("notes_included") is True and root is None:
        outcome = {
            "ok": False,
            "reason": "The folder for your notes is not usable (see output.folder), "
            "so nothing was changed.",
        }
        _write_result(paths, outcome)
        return outcome
    assert root is None or isinstance(root, Path)
    safety_name = backup_name(prefix=SAFETY_PREFIX)
    safety = backups_dir(paths) / safety_name
    try:
        create_backup(BackupSources(paths, root), safety)
    except BackupError as exc:
        outcome = {
            "ok": False,
            "reason": f"A copy of what you have now could not be saved first ({exc}), "
            "so nothing was changed.",
        }
        _write_result(paths, outcome)
        return outcome
    try:
        units = _units(paths, root, stage, manifest)
        folders = frozenset(
            target for _, target in units if root is not None and target.parent == root
        )
        kept = _swap(units, str(note.get("id") or "x"), folders)
    except BackupError as exc:
        outcome = {"ok": False, "reason": str(exc), "safety": safety_name}
        _write_result(paths, outcome)
        return outcome
    for relative in _INDEXES:
        for suffix in ("", "-wal", "-shm", "-journal"):
            with contextlib.suppress(OSError):
                (Path(paths.root) / (relative + suffix)).unlink(missing_ok=True)
    prune_backups(backups_dir(paths), SAFETY_PREFIX)
    outcome = {
        "ok": True,
        "safety": safety_name,
        "backup_created": manifest.get("created"),
        "starlight_version": manifest.get("starlight_version"),
    }
    if kept:
        where = ", ".join(str(path) for path in kept)
        outcome["reason"] = f"Some hidden files of your old notes are still in {where}."
    _write_result(paths, outcome)
    return outcome
