"""Versioned backups of folders you choose: "back up my photos to the external drive",
"is last night's backup OK?", "restore yesterday's version of my thesis folder".

Each run makes one zip in <target>/<name>/ with a manifest of every file and its SHA-256
inside, and keeps the newest `keep` versions. `verify` re-reads a backup and checks every
file against its checksum. `restore` asks first and never overwrites anything unless you
say so. Schedule backups with an automation ("every Sunday at 3:00 run backup.run photos").
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, Permission, ToolError, tool
from starlight.core.config import write_yaml_config
from starlight.core.safepaths import expand, inside

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
MANIFEST = "starlight-backup.json"
SKIP = ["Thumbs.db", "desktop.ini", ".DS_Store", "~$*", "*.tmp"]


class BackupSet(BaseModel):
    source: str
    target: str
    keep: int = Field(default=5, ge=1, le=1000)
    exclude: list[str] = Field(default_factory=list)


class Config(BaseModel):
    sets: dict[str, BackupSet] = Field(default_factory=dict)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class BackupModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self._running: set[str] = set()

    def _set(self, name: str) -> tuple[str, BackupSet]:
        key = name.strip().lower()
        found = self.config.sets.get(key)
        if found is None:
            names = ", ".join(sorted(self.config.sets)) or "none yet"
            raise ToolError(f"no backup called '{name}' (yours: {names})")
        return key, found

    def _folder(self, key: str, plan: BackupSet) -> Path:
        return expand(plan.target) / key

    def _versions(self, key: str, plan: BackupSet) -> list[Path]:
        folder = self._folder(key, plan)
        return sorted(folder.glob(f"{key} *.zip")) if folder.is_dir() else []

    def _check_paths(self, source: Path, target: Path) -> None:
        own = self.ctx.paths.root.resolve()
        if inside(target, own):
            raise ToolError("backups can't be stored inside Starlight's own folder")
        if inside(target, source):
            raise ToolError("the backup can't go inside the folder it backs up")
        if not source.is_dir():
            raise ToolError(f"{source} is not a folder")

    def _save(self) -> None:
        write_yaml_config(
            self.ctx.paths.module_configs / "backup.yaml",
            {"sets": {k: v.model_dump() for k, v in self.config.sets.items()}},
            "# The backup module (modules/backup): your backups. Change them from the chat.",
        )

    # -- making a backup (blocking; runs in a thread) ---------------------------------------

    def _make(self, key: str, plan: BackupSet) -> dict[str, Any]:
        source, folder = expand(plan.source), self._folder(key, plan)
        self._check_paths(source, folder)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
        target = folder / f"{key} {stamp}.zip"
        partial = target.with_suffix(".partial")
        patterns = [*SKIP, *plan.exclude]
        files: list[dict[str, Any]] = []
        try:
            # strict_timestamps=False: a file dated before 1980 (zip's earliest date) is
            # stored with 1980 instead of stopping the whole backup.
            with zipfile.ZipFile(
                partial, "w", zipfile.ZIP_DEFLATED, allowZip64=True, strict_timestamps=False
            ) as archive:
                for current, dirs, names in os.walk(source):
                    here = Path(current)
                    dirs[:] = [d for d in dirs if not any(fnmatch.fnmatch(d, p) for p in patterns)]
                    for name in names:
                        if any(fnmatch.fnmatch(name, p) for p in patterns):
                            continue
                        self.ctx.kill_switch.check()
                        path = here / name
                        relative = path.relative_to(source).as_posix()
                        try:
                            digest = _hash(path)
                            archive.write(path, relative)
                        except OSError as exc:  # locked or vanished: note it, keep going
                            files.append({"path": relative, "error": str(exc)})
                            continue
                        files.append(
                            {"path": relative, "size": path.stat().st_size, "sha256": digest}
                        )
                manifest = {
                    "name": key,
                    "source": str(source),
                    "made": datetime.now().isoformat(timespec="seconds"),
                    "files": files,
                }
                archive.writestr(MANIFEST, json.dumps(manifest, indent=1, ensure_ascii=False))
            partial.replace(target)
        except BaseException:
            partial.unlink(missing_ok=True)  # never leave a half backup that looks complete
            raise
        removed = []
        versions = self._versions(key, plan)
        for old in versions[: max(0, len(versions) - plan.keep)]:
            old.unlink()
            removed.append(old.name)
        problems = [f for f in files if "error" in f]
        return {
            "backup": str(target),
            "files": len(files) - len(problems),
            "bytes": sum(f.get("size", 0) for f in files),
            "skipped": [f["path"] for f in problems][:50],
            "removed_old": removed,
        }

    def _verify(self, archive_path: Path) -> dict[str, Any]:
        bad: list[str] = []
        with zipfile.ZipFile(archive_path) as archive:
            broken = archive.testzip()
            if broken:
                bad.append(broken)
            manifest = json.loads(archive.read(MANIFEST))
            for entry in manifest["files"]:
                if "sha256" not in entry:
                    continue
                self.ctx.kill_switch.check()
                digest = hashlib.sha256()
                with archive.open(entry["path"]) as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != entry["sha256"]:
                    bad.append(entry["path"])
        return {"backup": archive_path.name, "ok": not bad, "damaged": bad[:50],
                "files": len(manifest["files"])}  # fmt: skip

    def _pick(self, key: str, plan: BackupSet, version: str) -> Path:
        versions = self._versions(key, plan)
        if not versions:
            raise ToolError(f"'{key}' has no backups yet")
        if not version.strip() or version.strip().lower() in ("latest", "newest", "last"):
            return versions[-1]
        matches = [v for v in versions if version.strip() in v.name]
        if len(matches) != 1:
            raise ToolError(f"give one version from backup.versions ({len(matches)} match)")
        return matches[0]

    # -- tools ---------------------------------------------------------------------------

    @tool
    async def add(
        self, name: str, source: str, target: str, keep: int = 5, exclude: list[str] | None = None
    ) -> dict[str, Any]:
        """Set up a backup: the `source` folder is copied into versioned zip files in the
        `target` folder (e.g. an external drive); the newest `keep` versions are kept.
        `exclude` skips names like "*.iso" or "node_modules". Asks first."""
        key = name.strip().lower()
        if not NAME.match(key):
            raise ToolError("a name is lowercase letters, digits, - or _ (max 40)")
        plan = BackupSet(source=source, target=target, keep=keep, exclude=exclude or [])
        self._check_paths(expand(source), self._folder(key, plan))
        if not await self.ctx.ui.confirm(
            f"Back up {expand(source)} to {self._folder(key, plan)}, keeping {keep} versions?"
        ):
            raise ToolError("not confirmed, nothing changed")
        self.config.sets[key] = plan
        self._save()
        return {"added": key, "source": str(expand(source)), "target": str(self._folder(key, plan))}

    @tool(name="list")
    def list_sets(self) -> dict[str, Any]:
        """Your backups: what is backed up where, and the newest version of each."""
        found = []
        for key, plan in sorted(self.config.sets.items()):
            versions = self._versions(key, plan)
            found.append(
                {
                    "name": key,
                    "source": plan.source,
                    "target": str(self._folder(key, plan)),
                    "keep": plan.keep,
                    "versions": len(versions),
                    "newest": versions[-1].name if versions else None,
                }
            )
        return {"backups": found}

    @tool(own_timeout=True)  # a big folder takes as long as it takes; the kill switch stops it
    async def run(self, name: str) -> dict[str, Any]:
        """Make a backup now (e.g. backup.run photos). The kill switch stops it cleanly."""
        key, plan = self._set(name)
        if key in self._running:
            raise ToolError(f"'{key}' is already being backed up")
        self._running.add(key)
        try:
            result = await asyncio.to_thread(self._make, key, plan)
        finally:
            self._running.discard(key)
        await self.ctx.publish("backup.done", {"name": key, "files": result["files"]})
        return result

    @tool
    def versions(self, name: str) -> dict[str, Any]:
        """The saved versions of a backup, oldest first."""
        key, plan = self._set(name)
        return {
            "name": key,
            "versions": [
                {"version": v.name, "bytes": v.stat().st_size} for v in self._versions(key, plan)
            ],
        }

    @tool
    async def verify(self, name: str, version: str = "latest") -> dict[str, Any]:
        """Check that a backup is complete and undamaged (every file against its checksum)."""
        key, plan = self._set(name)
        return await asyncio.to_thread(self._verify, self._pick(key, plan, version))

    @tool(permission=Permission.CONFIRM)
    async def restore(
        self, name: str, to: str, version: str = "latest", overwrite: bool = False
    ) -> dict[str, Any]:
        """Restore a backup version into the folder `to`. Existing files are skipped unless
        `overwrite` is true. Asks first."""
        key, plan = self._set(name)
        archive_path = self._pick(key, plan, version)
        destination = expand(to)
        if inside(destination, self.ctx.paths.root.resolve()):
            raise ToolError("Starlight's own folder is off-limits")
        return await asyncio.to_thread(self._restore, archive_path, destination, overwrite)

    def _restore(self, archive_path: Path, destination: Path, overwrite: bool) -> dict[str, Any]:
        restored = skipped = 0
        root = destination.resolve()
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if member.filename == MANIFEST or member.is_dir():
                    continue
                self.ctx.kill_switch.check()
                target = (destination / member.filename).resolve()
                if not inside(target, root):
                    raise ToolError(f"unsafe path in the backup: {member.filename}")
                if target.exists() and not overwrite:
                    skipped += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as out:
                    while chunk := source.read(1024 * 1024):
                        out.write(chunk)
                restored += 1
        return {"restored": restored, "skipped_existing": skipped, "into": str(destination),
                "from": archive_path.name}  # fmt: skip

    @tool
    def remove(self, name: str) -> dict[str, Any]:
        """Stop a backup (its saved zip files are left where they are)."""
        key, _ = self._set(name)
        del self.config.sets[key]
        self._save()
        return {"removed": key, "note": "the saved backups were not deleted"}
