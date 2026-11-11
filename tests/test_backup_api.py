"""The backup routes (starlight/web/backup_api.py): PC only, streamed, size-capped."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from starlight.core import Paths, backup
from starlight.web import backup_api
from tests.test_remote import LOCAL, PHONE, client, home, pair, phone


def site(tmp_path: Path) -> Path:
    root = home(tmp_path / "h")
    out = tmp_path / "out"
    with (root / "config" / "starlight.yaml").open("a", encoding="utf-8") as config:
        config.write(f"output:\n  folder: {out.as_posix()}\n")
    (root / "memory").mkdir()
    (root / "memory" / "m1.md").write_text("likes tea")
    (root / "data" / "conversations").mkdir(parents=True)
    (root / "data" / "conversations" / "a.json").write_text("chat")
    return root


def a_backup(root: Path, tmp_path: Path) -> Path:
    target = tmp_path / "mine.zip"
    backup.create_backup(backup.BackupSources(Paths(root), None), target)
    return target


def test_every_route_refuses_a_paired_phone_and_works_at_the_pc(tmp_path: Path) -> None:
    root = site(tmp_path)
    data = a_backup(root, tmp_path).read_bytes()
    with client(root) as c:
        token = pair(c)
        calls = (
            ("get", "/api/backup", {}),
            ("get", "/api/backup/download", {}),
            ("post", "/api/backup/inspect", {"content": data}),
            ("post", "/api/backup/restore", {"json": {"token": "x" * 20}}),
            ("delete", "/api/backup/pending", {}),
        )
        for method, url, extra in calls:
            assert getattr(c, method)(url, headers=PHONE, **extra).status_code == 403, url
            seen = getattr(c, method)(url, headers=phone(token), **extra)
            assert seen.status_code == 403, url
        assert c.get("/api/backup", headers=LOCAL).json() == {"pending": None, "last": None}
        assert c.delete("/api/backup/pending", headers=LOCAL).json() == {"cancelled": False}


def test_the_download_is_a_valid_streamed_backup(tmp_path: Path) -> None:
    root = site(tmp_path)
    with client(root) as c:
        response = c.get("/api/backup/download", headers=LOCAL)
        assert response.status_code == 200
        disposition = response.headers["content-disposition"]
        assert disposition.startswith('attachment; filename="starlight-backup-')
        assert disposition.endswith('.zip"')
        assert response.headers["content-type"] == "application/zip"
        assert int(response.headers["content-length"]) == len(response.content)
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            assert zf.namelist()[0] == "manifest.json"
            assert zf.read("memory/m1.md") == b"likes tea"
            assert json.loads(zf.read("manifest.json"))["counts"]["memories"] == 1
        # the temporary file is gone
        assert not list((root / "backups").glob(".download-*"))


def test_inspect_stage_and_cancel(tmp_path: Path) -> None:
    root = site(tmp_path)
    data = a_backup(root, tmp_path).read_bytes()
    (root / "memory" / "m1.md").write_text("changed")
    with client(root) as c:
        found = c.post("/api/backup/inspect", headers=LOCAL, content=data)
        assert found.status_code == 200, found.text
        token = found.json()["token"]
        assert found.json()["manifest"]["counts"]["memories"] == 1
        assert (root / "backups" / f".upload-{token}.zip").is_file()
        staged = c.post("/api/backup/restore", headers=LOCAL, json={"token": token})
        assert staged.json() == {"staged": True, "restart_required": True}
        assert (root / "memory" / "m1.md").read_text() == "changed"  # not applied now
        state = c.get("/api/backup", headers=LOCAL).json()
        assert state["pending"]["counts"]["memories"] == 1
        assert not (root / "backups" / f".upload-{token}.zip").exists()
        assert c.delete("/api/backup/pending", headers=LOCAL).json() == {"cancelled": True}
        assert c.get("/api/backup", headers=LOCAL).json()["pending"] is None
        assert not list((root / "backups").glob(".restore-*"))


def test_a_bad_file_or_token_is_refused(tmp_path: Path) -> None:
    root = site(tmp_path)
    with client(root) as c:
        bad = c.post("/api/backup/inspect", headers=LOCAL, content=b"not a zip")
        assert bad.status_code == 400 and "not a backup" in bad.json()["detail"]
        assert not list((root / "backups").glob(".upload-*"))
        for token in ("nope", "x" * 20, "../../config/starlight", 5, None):
            gone = c.post("/api/backup/restore", headers=LOCAL, json={"token": token})
            assert gone.status_code == 404, token


def test_an_upload_over_the_cap_gives_413(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = site(tmp_path)
    data = a_backup(root, tmp_path).read_bytes()
    monkeypatch.setattr(backup_api, "MAX_BACKUP_UPLOAD", len(data) - 1)
    with client(root) as c:
        declared = c.post("/api/backup/inspect", headers=LOCAL, content=data)
        assert declared.status_code == 413
        # no declared length (a chunked body): the real count stops it
        chunked = c.post(
            "/api/backup/inspect",
            headers=LOCAL,
            content=(data[i : i + 50] for i in range(0, len(data), 50)),
        )
        assert chunked.status_code == 413
        assert not list((root / "backups").glob(".upload-*"))


def test_the_check_route_tells_what_would_be_left_out_or_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = site(tmp_path)
    (root / "memory" / "big.bin").write_bytes(b"x" * 12000)
    with client(root) as c:
        assert c.get("/api/backup/check", headers=PHONE).status_code == 403
        assert c.get("/api/backup/check", headers=LOCAL).json() == {"left_out": []}
        monkeypatch.setattr(backup, "MAX_FILE", 10000)
        found = c.get("/api/backup/check", headers=LOCAL).json()
        assert found["left_out"] == ["1 file over 10 KB is not in the backup: memory/big.bin"]
        monkeypatch.setattr(backup, "MAX_TOTAL", 10)
        refused = c.get("/api/backup/check", headers=LOCAL)
        assert refused.status_code == 400
        assert "more than a backup can hold" in refused.json()["detail"]
