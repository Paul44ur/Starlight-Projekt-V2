"""The web API of backup and restore (starlight/core/backup.py). PC only, like settings.

    GET    /api/backup           {pending, last}: a restore waiting for the next start, and
                                 the outcome of the last one
    GET    /api/backup/check     {left_out}: what a backup would leave out (400: it cannot be made)
    GET    /api/backup/download  a new backup as a file (streamed; never held in memory)
    POST   /api/backup/inspect   the zip as the raw body: checked, kept, {token, manifest}
    POST   /api/backup/restore   {token}: unpacked and staged; applied at the next start
    DELETE /api/backup/pending   cancel a staged restore

Nothing here replaces data: a restore is applied when Starlight starts next, before anything
has the data open.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from starlight.core import backup

MAX_BACKUP_UPLOAD = 8 * 1024**3  # its own cap: a backup is far bigger than a chat file
TOO_BIG = "this file is too big for a backup"


def add_backup_api(
    app: FastAPI,
    kernel: Callable[[], Any],
    local_only: Callable[[Request], Any],
    body: Callable[[Request], Awaitable[dict[str, Any]]],
) -> None:
    def paths() -> Any:
        return kernel().paths

    @app.get("/api/backup")
    async def backup_state(request: Request) -> dict[str, Any]:
        local_only(request)
        pending = backup.read_pending(paths())
        return {
            "pending": (
                {**backup.summary(pending["manifest"]), "staged": pending.get("staged")}
                if pending
                else None
            ),
            "last": backup.last_result(paths()),
        }

    @app.get("/api/backup/check")
    async def backup_check(request: Request) -> dict[str, Any]:
        """Before the download: can a backup be made, and what would it leave out?"""
        local_only(request)
        sources = backup.BackupSources(paths(), kernel().services.output_dir)
        try:
            return await run_in_threadpool(backup.check_backup, sources)
        except backup.BackupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/backup/download")
    async def backup_download(request: Request) -> Response:
        local_only(request)
        folder = backup.backups_dir(paths())
        temp = folder / f".download-{uuid.uuid4().hex[:12]}.zip"
        sources = backup.BackupSources(paths(), kernel().services.output_dir)
        try:
            await run_in_threadpool(backup.create_backup, sources, temp)
        except backup.BackupError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return StreamingResponse(
            backup.stream_file(temp),  # it deletes the file when the stream ends
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{backup.backup_name()}"',
                "Content-Length": str(temp.stat().st_size),
            },
            background=BackgroundTask(lambda: temp.unlink(missing_ok=True)),
        )

    @app.post("/api/backup/inspect")
    async def backup_inspect(request: Request) -> dict[str, Any]:
        local_only(request)
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BACKUP_UPLOAD:
            raise HTTPException(status_code=413, detail=TOO_BIG)
        folder = backup.backups_dir(paths())
        folder.mkdir(parents=True, exist_ok=True)
        backup.drop_old_uploads(paths())
        token = backup.new_token()
        target = folder / f".upload-{token}.zip"
        size = 0
        try:
            with open(target, "xb") as out:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_BACKUP_UPLOAD:  # (the declared length may lie)
                        raise HTTPException(status_code=413, detail=TOO_BIG)
                    await run_in_threadpool(out.write, chunk)
            manifest = await run_in_threadpool(backup.validate, target)
        except backup.BackupError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except BaseException:
            with contextlib.suppress(OSError):
                target.unlink(missing_ok=True)
            raise
        return {"token": token, "manifest": backup.summary(manifest)}

    @app.post("/api/backup/restore")
    async def backup_restore(request: Request) -> dict[str, Any]:
        local_only(request)
        data = await body(request)
        token = data.get("token")
        found: Path | None = backup.upload_path(paths(), token) if isinstance(token, str) else None
        if found is None:
            raise HTTPException(status_code=404, detail="that backup is gone; choose it again")
        try:
            await run_in_threadpool(backup.stage_restore, paths(), found)
        except backup.BackupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            with contextlib.suppress(OSError):
                found.unlink(missing_ok=True)
        return {"staged": True, "restart_required": True}

    @app.delete("/api/backup/pending")
    async def backup_cancel(request: Request) -> dict[str, Any]:
        local_only(request)
        cancelled = await run_in_threadpool(backup.cancel_pending, paths())
        return {"cancelled": cancelled}
