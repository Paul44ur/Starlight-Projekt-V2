"""Undo for delete (F2): a deleted conversation waits a day in a hidden waste folder next to the
others, and ``restore`` brings it back exactly as it was."""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

import pytest

from starlight.core import backup
from starlight.core.backup import BackupSources
from starlight.core.conversations import WASTE, ConversationStore
from starlight.core.model import Message
from starlight.core.paths import Paths
from tests.test_remote import LOCAL, PHONE, client, home, pair, phone


def made(store: ConversationStore, text: str = "Plan the week") -> str:
    name = store.create(text)
    store.save(name, [Message("user", text), Message("assistant", "Here is a plan")])
    store.organize(name, pinned=True, archived=True, folder="Work")
    return name


def test_delete_moves_the_file_to_the_waste_and_hides_it(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "c")
    name = made(store)
    store.delete(name)
    assert not (store.folder / f"{name}.json").exists()
    kept = list((store.folder / WASTE).glob(f"{name}.*.json"))
    assert len(kept) == 1  # (the time of deletion is in its name)
    assert store.list(archived=True) == []
    assert store.list(search="plan", archived=True) == []  # search does not find it
    with pytest.raises(KeyError):
        store.get(name)


def test_restore_brings_back_every_byte(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "c")
    name = made(store)
    file = store.folder / f"{name}.json"
    before = json.loads(file.read_text("utf-8"))
    raw = file.read_bytes()
    store.delete(name)
    store.restore(name)
    assert file.read_bytes() == raw
    assert json.loads(file.read_text("utf-8")) == before
    got = store.get(name)
    assert got["pinned"] and got["archived"] and got["folder"] == "Work"
    assert [m["content"] for m in got["messages"]] == ["Plan the week", "Here is a plan"]
    assert list((store.folder / WASTE).glob("*")) == []


def test_restore_after_the_waste_was_emptied_or_for_nothing_is_an_error(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "c")
    name = made(store)
    with pytest.raises(KeyError):
        store.restore(name)  # never deleted
    store.delete(name)
    store.empty_waste(older_than=-1)
    with pytest.raises(KeyError):
        store.restore(name)
    with pytest.raises(KeyError):
        store.restore("../x")


def test_old_waste_goes_on_start_and_new_waste_stays(tmp_path: Path) -> None:
    folder = tmp_path / "c"
    store = ConversationStore(folder)
    old, new = made(store, "Old one"), made(store, "New one")
    store.delete(old)
    store.delete(new)
    waste = folder / WASTE
    (aged,) = waste.glob(f"{old}.*.json")
    aged.rename(waste / f"{old}.{int(time.time()) - 25 * 3600}.json")  # 25 hours ago
    (fresh,) = waste.glob(f"{new}.*.json")
    fresh.rename(waste / f"{new}.{int(time.time()) - 23 * 3600}.json")  # 23 hours ago
    ConversationStore(folder)  # the next start
    assert [p.name.split(".")[0] for p in waste.glob("*.json")] == [new]
    ConversationStore(folder).restore(new)


def test_list_sweeps_at_most_once_an_hour(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "c")
    name = made(store)
    store.delete(name)
    waste = store.folder / WASTE
    (kept,) = waste.glob("*.json")
    kept.rename(waste / f"{name}.{int(time.time()) - 30 * 3600}.json")
    store.list()
    assert len(list(waste.glob("*.json"))) == 1  # (swept a moment ago: not again yet)
    store._swept -= 2 * 3600
    store.list()
    assert list(waste.glob("*.json")) == []


def test_a_private_conversation_is_deleted_and_restored_in_memory_only(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "c")
    name = store.create("Secret", private=True)
    store.save(name, [Message("user", "hidden words")])
    store.delete(name)
    assert store.list() == [] and not store.is_private(name)
    with pytest.raises(KeyError):
        store.get(name)
    store.restore(name)
    assert store.is_private(name) and store.get(name)["messages"][0]["content"] == "hidden words"
    store.delete(name)
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert not any("hidden words" in p.read_text("utf-8") for p in files)
    assert not (store.folder / WASTE).exists()
    store._private_waste[name] = (time.monotonic() - 1000, {}, 0)  # past its short time
    with pytest.raises(KeyError):
        store.restore(name)


def test_the_routes_delete_and_restore_for_the_pc_and_a_paired_phone(tmp_path: Path) -> None:
    with client(home(tmp_path / "h")) as c:
        token = pair(c)
        name = c.post("/api/conversations", headers=LOCAL).json()["id"]
        c.patch(f"/api/conversations/{name}", headers=LOCAL, json={"pinned": True, "folder": "A"})
        assert c.post(f"/api/conversations/{name}/restore", headers=LOCAL).status_code == 404
        assert c.delete(f"/api/conversations/{name}", headers=LOCAL).json() == {"deleted": name}
        assert c.get(f"/api/conversations/{name}", headers=LOCAL).status_code == 404
        # a device that is not paired has no access, as for the other conversation routes
        assert c.post(f"/api/conversations/{name}/restore", headers=PHONE).status_code == 403
        back = c.post(f"/api/conversations/{name}/restore", headers=LOCAL)
        assert back.json() == {"id": name}
        got = c.get(f"/api/conversations/{name}", headers=LOCAL).json()
        assert got["pinned"] is True and got["folder"] == "A"
        # a paired phone may do what the other conversation routes let it do
        assert c.delete(f"/api/conversations/{name}", headers=phone(token)).status_code == 200
        restored = c.post(f"/api/conversations/{name}/restore", headers=phone(token))
        assert restored.json() == {"id": name}
        assert c.post("/api/conversations/nothing/restore", headers=LOCAL).status_code == 404
        assert c.delete("/api/conversations/nothing", headers=LOCAL).status_code == 404
        # while it is deleted the list does not have it
        c.delete(f"/api/conversations/{name}", headers=LOCAL)
        listed = c.get("/api/conversations", headers=LOCAL).json()["conversations"]
        assert name not in [x["id"] for x in listed]


def test_a_backup_leaves_the_waste_out(paths: Paths, tmp_path: Path) -> None:
    out = tmp_path.parent / f"{tmp_path.name}-out"
    paths.config_file.write_text(f"kill_switch:\n  hotkey: null\noutput:\n  folder: {out}\n")
    store = ConversationStore(paths.data / "conversations")
    keep, gone = made(store, "Kept"), made(store, "Deleted")
    store.delete(gone)
    target = tmp_path / "b" / "one.zip"
    backup.create_backup(BackupSources(paths, out), target)
    names = zipfile.ZipFile(target).namelist()
    assert f"data/conversations/{keep}.json" in names
    assert not any(WASTE in n or gone in n for n in names)
    assert (paths.data / "conversations" / WASTE).is_dir()
