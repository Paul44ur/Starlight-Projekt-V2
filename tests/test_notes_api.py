"""Block B3 of the notes plan (docs/plan-notes-app.md): the Notes page's web API
(starlight/web/notes_api.py). PC only; paths stay inside the notebook folder; saving never
overwrites a change made meanwhile."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO
from tests.test_remote import LOCAL, client, home, pair, phone

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def notebook(documents: Path) -> Path:
    root = documents / "Starlight"
    (root / "Notes").mkdir(parents=True)
    (root / "Journal").mkdir()
    (root / "Notes" / "Shopping.md").write_text(
        "---\ntags: [home]\n---\n# Shopping\n- [ ] milk\nSee [[Plan]].\n", encoding="utf-8"
    )
    (root / "Notes" / "Plan.md").write_text("Back to [[Shopping]].\n", encoding="utf-8")
    (root / "Journal" / "2026-11-01.md").write_text("A day.\n", encoding="utf-8")
    return root


@pytest.fixture
def api(tmp_path: Path, notebook: Path) -> Iterator[Any]:
    root = home(tmp_path / "h")
    shutil.copytree(REPO / "modules" / "notes", root / "modules" / "notes")
    with client(root) as c:
        yield c


def get(c: Any, url: str, **params: Any) -> Any:
    response = c.get(url, params=params, headers=LOCAL)
    assert response.status_code == 200, response.text
    return response.json()


def send(c: Any, method: str, url: str, status: int = 200, **kwargs: Any) -> Any:
    response = c.request(method, url, headers=LOCAL, **kwargs)
    assert response.status_code == status, response.text
    return response.json()


def test_the_list_and_pinned_notes(api: Any) -> None:
    listed = get(api, "/api/notes")
    assert listed["folders"][:5] == ["Notes", "Journal", "Recipes", "Clippings", "Inbox"]
    paths = [n["path"] for n in listed["notes"]]
    assert paths == ["Journal/2026-11-01.md", "Notes/Plan.md", "Notes/Shopping.md"]
    assert not any(n["pinned"] for n in listed["notes"]) and listed["capped"] is False
    send(api, "POST", "/api/notes/pins", json={"path": "Notes/Plan.md"})
    pins = send(api, "POST", "/api/notes/pins", json={"path": "Notes/Shopping.md"})["pinned"]
    assert pins == ["Notes/Shopping.md", "Notes/Plan.md"]  # the newest first
    send(api, "POST", "/api/notes/pins", json={"path": "Notes/Plan.md", "pinned": False})
    assert get(api, "/api/notes")["pinned"] == ["Notes/Shopping.md"]
    send(api, "POST", "/api/notes/pins", 404, json={"path": "Notes/Nothing.md"})
    hits = get(api, "/api/notes/search", q="milk")["hits"]
    assert [h["path"] for h in hits] == ["Notes/Shopping.md"]


def test_reading_and_saving_with_a_stamp(api: Any, notebook: Path) -> None:
    note = get(api, "/api/note", path="Notes/Shopping.md")
    assert note["title"] == "Shopping" and note["properties"] == {"tags": ["home"]}
    assert note["headings"] == [{"level": 1, "text": "Shopping", "line": 3}]
    assert note["tags"] == ["home"] and note["links"][0]["to"] == "Notes/Plan.md"
    assert [b["from"] for b in note["backlinks"]] == ["Notes/Plan.md"]
    saved = send(
        api,
        "PUT",
        "/api/note",
        params={"path": "Notes/Shopping.md"},
        json={"text": "# Shopping\n- [ ] milk\n- [ ] eggs\n", "stamp": note["stamp"]},
    )
    assert saved["stamp"] != note["stamp"] and saved["version"]
    file = notebook / "Notes" / "Shopping.md"
    assert "eggs" in file.read_text("utf-8")
    again = send(
        api,
        "PUT",
        "/api/note",
        params={"path": "Notes/Shopping.md"},
        json={"text": "# Shopping\n- [ ] eggs\n", "stamp": saved["stamp"]},
    )
    versions = get(api, "/api/note/versions", path="Notes/Shopping.md")["versions"]
    assert len(versions) == 1  # typing: one version per 5 minutes, not one per save
    # someone else changed it meanwhile: refused, with both texts
    file.write_text("Changed in Notepad\n", encoding="utf-8")
    conflict = api.put(
        "/api/note",
        params={"path": "Notes/Shopping.md"},
        headers=LOCAL,
        json={"text": "mine", "stamp": again["stamp"]},
    )
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["theirs"] == "Changed in Notepad\n" and detail["yours"] == "mine"
    assert file.read_text("utf-8") == "Changed in Notepad\n"  # not overwritten
    send(
        api,
        "PUT",
        "/api/note",
        params={"path": "Notes/Shopping.md"},
        json={"text": "mine", "stamp": detail["stamp"]},
    )
    assert file.read_text("utf-8") == "mine"
    send(api, "PUT", "/api/note", 400, params={"path": "Notes/Shopping.md"}, json={"text": "x"})
    big = {"text": "x" * (1024**2 + 1), "stamp": "missing"}
    send(api, "PUT", "/api/note", 413, params={"path": "Notes/Big.md"}, json=big)


def test_new_rename_delete_and_the_trash(api: Any, notebook: Path) -> None:
    made = send(api, "POST", "/api/note", json={"folder": "Inbox", "title": "Ide:as?", "text": "x"})
    assert made["path"] == "Inbox/Ideas.md" and (notebook / "Inbox" / "Ideas.md").is_file()
    second = send(api, "POST", "/api/note", json={"folder": "Inbox", "title": "Ideas"})
    assert second["path"] == "Inbox/Ideas 2.md"
    untitled = send(api, "POST", "/api/note", json={"title": "  "})
    assert untitled["path"] == "Notes/Untitled.md"
    send(api, "POST", "/api/note", 400, json={"folder": "../Elsewhere", "title": "x"})

    send(api, "POST", "/api/notes/pins", json={"path": "Notes/Shopping.md"})
    renamed = send(
        api, "POST", "/api/note/rename", json={"path": "Notes/Shopping.md", "title": "Groceries"}
    )
    assert (
        renamed["path"] == "Notes/Groceries.md"
        and not (notebook / "Notes" / "Shopping.md").exists()
    )
    assert get(api, "/api/notes")["pinned"] == ["Notes/Groceries.md"]  # the pin follows
    send(api, "POST", "/api/note/rename", 409, json={"path": "Notes/Groceries.md", "title": "Plan"})

    send(api, "DELETE", "/api/note", params={"path": "Notes/Groceries.md"})
    assert not (notebook / "Notes" / "Groceries.md").exists()
    assert get(api, "/api/notes")["pinned"] == []
    trash = get(api, "/api/notes/trash")["notes"]
    assert [t["path"] for t in trash] == ["Notes/Groceries.md"]
    send(api, "POST", "/api/notes/trash/restore", json={"path": "Notes/Groceries.md"})
    assert "milk" in (notebook / "Notes" / "Groceries.md").read_text("utf-8")
    send(api, "POST", "/api/notes/trash/restore", 409, json={"path": "Notes/Groceries.md"})
    send(api, "DELETE", "/api/note", 404, params={"path": "Notes/Nothing.md"})


def test_versions_differences_and_restore(api: Any, notebook: Path) -> None:
    note = get(api, "/api/note", path="Notes/Plan.md")
    send(
        api,
        "PUT",
        "/api/note",
        params={"path": "Notes/Plan.md"},
        json={"text": "A new plan.\n", "stamp": note["stamp"]},
    )
    [version] = get(api, "/api/note/versions", path="Notes/Plan.md")["versions"]
    diff = get(api, "/api/note/diff", path="Notes/Plan.md", version=version["id"])
    assert "-Back to [[Shopping]]." in diff["diff"] and "+A new plan." in diff["diff"]
    assert diff["text"] == "Back to [[Shopping]].\n"
    restored = send(
        api, "POST", "/api/note/restore", json={"path": "Notes/Plan.md", "version": version["id"]}
    )
    assert (notebook / "Notes" / "Plan.md").read_text("utf-8") == "Back to [[Shopping]].\n"
    assert restored["before"]  # the text before the restore was kept too
    # a version of another note is not this note's
    other = get(api, "/api/note/versions", path="Notes/Plan.md")["versions"][-1]["id"]
    api_other = api.get(
        "/api/note/diff", params={"path": "Notes/Shopping.md", "version": other}, headers=LOCAL
    )
    assert api_other.status_code == 404
    send(
        api, "POST", "/api/note/restore", 404, json={"path": "Notes/Shopping.md", "version": other}
    )


@pytest.mark.parametrize(
    "path",
    [
        "../outside.md",
        "Notes/../../outside.md",
        "/etc/passwd.md",
        "C:/Windows/win.md",
        "C:\\Windows\\win.md",
        ".hidden/x.md",
        "code/x.md",
        "Notes/CON.md",
        "Notes/x.md.",
        "Notes/x.txt",
        "Notes/a:b.md",
        "file://x.md",
        "",
    ],
)
def test_paths_stay_inside_the_notebook(api: Any, path: str) -> None:
    assert api.get("/api/note", params={"path": path}, headers=LOCAL).status_code == 400
    put = api.put(
        "/api/note", params={"path": path}, headers=LOCAL, json={"text": "x", "stamp": "missing"}
    )
    assert put.status_code == 400


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need extra rights on Windows")
def test_a_link_out_of_the_notebook_is_refused(api: Any, notebook: Path, tmp_path: Path) -> None:
    starlight_own = tmp_path / "h"  # Starlight's own folder (settings, data)
    (notebook / "Notes" / "own").symlink_to(starlight_own, target_is_directory=True)
    (notebook / "Notes" / "outside.md").symlink_to(starlight_own / "config" / "starlight.yaml")
    for path in ("Notes/own/config/x.md", "Notes/outside.md"):
        response = api.get("/api/note", params={"path": path}, headers=LOCAL)
        assert response.status_code == 400 and "outside" in response.text


def test_only_the_pc_can_use_the_notes_api(tmp_path: Path, notebook: Path) -> None:
    root = home(tmp_path / "h")
    with client(root) as c:
        token = pair(c)
        for method, url, kwargs in (
            ("GET", "/api/notes", {}),
            ("GET", "/api/note", {"params": {"path": "Notes/Shopping.md"}}),
            (
                "PUT",
                "/api/note",
                {"params": {"path": "Notes/Shopping.md"}, "json": {"text": "x", "stamp": "y"}},
            ),
            ("DELETE", "/api/note", {"params": {"path": "Notes/Shopping.md"}}),
            ("POST", "/api/notes/pictures", {"content": PNG}),
            ("GET", "/api/notes/file/Notes/Attachments/x.png", {}),
        ):
            response = c.request(method, url, headers=phone(token), **kwargs)
            assert response.status_code == 403, (method, url)
    assert "milk" in (notebook / "Notes" / "Shopping.md").read_text("utf-8")


def test_pictures(api: Any, notebook: Path) -> None:
    added = send(
        api, "POST", "/api/notes/pictures", params={"name": "../Holiday photo.jpg"}, content=PNG
    )
    assert added["path"] == "Notes/Attachments/Holiday photo.png"  # its real type; inside
    assert added["embed"] == "![[Holiday photo.png]]"
    again = send(api, "POST", "/api/notes/pictures", params={"name": "Holiday photo"}, content=PNG)
    assert again["path"] == "Notes/Attachments/Holiday photo (2).png"
    shown = api.get(f"/api/notes/file/{added['path']}", headers=LOCAL)
    assert shown.status_code == 200 and shown.content == PNG
    assert shown.headers["content-type"] == "image/png"
    assert shown.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in shown.headers["content-security-policy"]
    send(api, "POST", "/api/notes/pictures", 415, content=b"<script>alert(1)</script>")
    send(api, "POST", "/api/notes/pictures", 413, content=PNG + b"\x00" * (10 * 1024**2))
    (notebook / "Notes" / "Attachments" / "drawing.svg").write_text("<svg onload='x()'/>")
    svg = api.get("/api/notes/file/Notes/Attachments/drawing.svg", headers=LOCAL)
    assert svg.status_code == 200 and svg.headers["content-disposition"].startswith("attachment")
    assert api.get("/api/notes/file/Notes/Shopping.md", headers=LOCAL).status_code == 400
    assert api.get("/api/notes/file/Notes/Attachments/none.png", headers=LOCAL).status_code == 404


def test_a_file_open_elsewhere_gets_a_plain_answer(
    api: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import starlight.notebook.write as write

    def locked(*args: Any, **kwargs: Any) -> None:
        raise PermissionError(13, "Permission denied")

    note = get(api, "/api/note", path="Notes/Plan.md")
    monkeypatch.setattr(write, "write_atomic_bytes", locked)
    response = api.put(
        "/api/note",
        params={"path": "Notes/Plan.md"},
        headers=LOCAL,
        json={"text": "new", "stamp": note["stamp"]},
    )
    assert response.status_code == 409 and "another program" in response.json()["detail"]
    assert (notebook / "Notes" / "Plan.md").read_text("utf-8") == "Back to [[Shopping]].\n"


def test_tag_filter_link_resolution_and_the_welcome_note(api: Any, notebook: Path) -> None:
    tagged = get(api, "/api/notes", tag="HOME")["notes"]
    assert [n["path"] for n in tagged] == ["Notes/Shopping.md"]
    assert get(api, "/api/notes/resolve", name="plan", source="Notes/Shopping.md") == {
        "path": "Notes/Plan.md"
    }
    assert (
        api.get("/api/notes/resolve", params={"name": "Nowhere"}, headers=LOCAL).status_code == 404
    )
    note = get(api, "/api/note", path="Notes/Shopping.md")
    assert note["body"] == "# Shopping\n- [ ] milk\nSee [[Plan]].\n"  # without the properties
    made = send(api, "POST", "/api/notes/welcome", json={"lang": "de-DE"})
    assert made["path"] == "Notes/Willkommen.md"
    text = (notebook / "Notes" / "Willkommen.md").read_text("utf-8")
    assert "# Willkommen in deinen Notizen" in text and "- [ ] Milch kaufen fällig: " in text
    (notebook / "Notes" / "Willkommen.md").unlink()
    assert send(api, "POST", "/api/notes/welcome", json={"lang": "en"})["path"] is None  # once
    assert not (notebook / "Notes" / "Welcome.md").exists()


def test_each_editing_session_keeps_where_it_started(api: Any, notebook: Path) -> None:
    def put(text: str, stamp: str, first: bool = False) -> str:
        body = {"text": text, "stamp": stamp, "first": first}
        return str(
            send(api, "PUT", "/api/note", params={"path": "Notes/Plan.md"}, json=body)["stamp"]
        )

    stamp = get(api, "/api/note", path="Notes/Plan.md")["stamp"]
    stamp = put("one", stamp, first=True)  # a session starts: the text before it is kept
    stamp = put("two", stamp)  # typing on: no new version within 5 minutes
    stamp = put("three", stamp, first=True)  # the next session: "two" is kept
    versions = get(api, "/api/note/versions", path="Notes/Plan.md")["versions"]
    texts = [
        get(api, "/api/note/diff", path="Notes/Plan.md", version=v["id"])["text"] for v in versions
    ]
    assert texts == ["two", "Back to [[Shopping]].\n"]
