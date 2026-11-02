"""Block F3 of the notes plan (docs/plan-notes-app.md): boards as .canvas files (JSON Canvas):
the format read and written back without losing anything, checks with plain answers, tools
for the assistant, and the API with stamps, versions and safe paths."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from tests.test_home_devices import call, fails
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_remote import LOCAL

# every kind of card and arrow the JSON Canvas spec has, and fields of other programs
SAMPLE = {
    "nodes": [
        {"id": "g1", "type": "group", "x": -40, "y": -40, "width": 700, "height": 400,
         "label": "Ideas", "background": "Notes/Attachments/sky.png",
         "backgroundStyle": "cover", "color": "4"},
        {"id": "t1", "type": "text", "x": 0, "y": 0, "width": 250, "height": 60,
         "text": "# Plan\nBuy **seeds**", "color": "#ff8800"},
        {"id": "f1", "type": "file", "x": 300, "y": 0, "width": 300, "height": 200,
         "file": "Notes/Garden.md", "subpath": "#Roses"},
        {"id": "l1", "type": "link", "x": 0, "y": 500, "width": 300, "height": 100,
         "url": "https://example.org/roses", "styleAttributes": {"shape": "pill"}},
    ],
    "edges": [
        {"id": "e1", "fromNode": "t1", "fromSide": "right", "fromEnd": "none", "toNode": "f1",
         "toSide": "left", "toEnd": "arrow", "color": "2", "label": "see"},
        {"id": "e2", "fromNode": "f1", "toNode": "l1"},
    ],
    "metadata": {"version": "1.0-1.0", "frontmatter": {}},
}  # fmt: skip


def test_the_spec_examples_round_trip() -> None:
    from starlight.notebook import boards

    text = json.dumps(SAMPLE)
    board = boards.load(text)
    assert [n["type"] for n in board.nodes] == ["group", "text", "file", "link"]
    assert board.other == {"metadata": {"version": "1.0-1.0", "frontmatter": {}}}
    written = boards.dump(board)
    assert json.loads(written) == SAMPLE  # nothing lost: not even other programs' fields
    assert written.startswith('{\n\t"nodes": [\n\t\t{\n\t\t\t"id": "g1"')  # (tabs, as Obsidian)
    assert boards.dump(boards.load(written)) == written
    assert boards.load("") == boards.Board()  # an empty file is an empty board
    floats = boards.load('{"nodes": [{"id": "a", "type": "text", "x": 1.6, "y": 0, '
                         '"width": 10, "height": 10, "text": "x"}]}')  # fmt: skip
    assert floats.nodes[0]["x"] == 2  # (whole numbers)


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("[1, 2]", "a JSON object"),
        ("{nodes: []}", "not valid JSON (line 1"),
        ('{"nodes": {}}', "lists"),
        ('{"nodes": [{"type": "text", "x": 0, "y": 0, "width": 1, "height": 1}]}', "no id"),
        ('{"nodes": [{"id": "a", "x": 0, "y": 0, "width": 1, "height": 1}]}', "no type"),
        ('{"nodes": [{"id": "a", "type": "text", "x": "0", "y": 0, "width": 1, "height": 1}]}',
         "no x"),
        ('{"nodes": [{"id": "a", "type": "text", "x": 0, "y": 0, "width": 1, "height": 1},'
         ' {"id": "a", "type": "text", "x": 0, "y": 0, "width": 1, "height": 1}]}', "two cards"),
        ('{"nodes": [], "edges": [{"id": "e", "fromNode": "a", "toNode": "b"}]}',
         "card that is not there"),
    ],
)  # fmt: skip
def test_what_is_not_a_board_gets_a_plain_answer(text: str, says: str) -> None:
    from starlight.notebook import boards

    with pytest.raises(boards.BoardError, match=says.replace("(", r"\(")):
        boards.load(text)


def test_adding_cards_and_what_the_assistant_reads() -> None:
    from starlight.notebook import boards

    board = boards.load(json.dumps(SAMPLE))
    card = boards.add_card(board, "text", "Water on Sunday", near="t1", label="then")
    assert (card["x"], card["y"], card["width"], card["height"]) == (0, 100, 260, 140)
    assert board.edges[-1] == {
        "id": board.edges[-1]["id"], "fromNode": "t1", "fromSide": "bottom",
        "toNode": card["id"], "toSide": "top", "label": "then",
    }  # fmt: skip
    beside = boards.add_card(board, "link", "https://example.org/x")
    assert beside["x"] == 700 and beside["y"] == -40  # (to the right of everything)
    ids = [n["id"] for n in board.nodes] + [e["id"] for e in board.edges]
    assert len(set(ids)) == len(ids) and all(len(i) == 16 for i in ids[4:6])
    with pytest.raises(boards.BoardError, match="web address"):
        boards.add_card(board, "link", "javascript:alert(1)")
    with pytest.raises(boards.BoardError, match="text, file, link or group"):
        boards.add_card(board, "video", "x")
    told = boards.summary(board)
    texts = {c["id"]: c for c in told["cards"]}
    assert texts["t1"]["says"] == "# Plan Buy **seeds**" and texts["t1"]["in"] == "Ideas"
    assert texts["f1"]["says"] == "Notes/Garden.md#Roses"
    assert {"from": "# Plan Buy **seeds**", "to": "Notes/Garden.md#Roses", "label": "see"} in (
        told["arrows"]
    )


@pytest.fixture
def boards_project(paths: Paths, use_module: Callable[[str], Path], documents: Path) -> Path:
    for name in ("boards", "notes"):
        use_module(name)
    root = documents / "Starlight"
    (root / "Notes").mkdir(parents=True)
    (root / "Notes" / "Garden.md").write_text("# Garden\nRoses.\n", "utf-8")
    return root


async def test_the_tools_for_the_assistant(paths: Paths, boards_project: Path) -> None:
    async with Kernel(paths) as k:
        assert (await call(k, "boards.list"))["count"] == 0
        made = await call(k, "boards.add_card", board="Project", text="Find a gardener")
        assert made["path"] == "Boards/Project.canvas" and made["changed"]["new"] is True
        await call(k, "boards.add_card", board="project", note="Garden")
        await call(k, "boards.add_card", board="Project", url="https://example.org/roses",
                   under="gardener", label="maybe")  # fmt: skip
        read = await call(k, "boards.read", board="Proj")
        assert [c["says"] for c in read["cards"]] == [
            "Find a gardener", "Notes/Garden.md", "https://example.org/roses",
        ]  # fmt: skip
        assert read["arrows"] == [
            {"from": "Find a gardener", "to": "https://example.org/roses", "label": "maybe"}
        ]
        listed = await call(k, "boards.list")
        assert listed["boards"][0]["cards"] == 3 and listed["boards"][0]["name"] == "Project"
        assert "no note called" in await fails(k, "boards.add_card", board="Project", note="Nope")
        assert "one of text, note" in await fails(k, "boards.add_card", board="Project")
        assert "no board called" in await fails(k, "boards.read", board="Holiday")
        board = boards_project / "Boards" / "Project.canvas"
        assert len(json.loads(board.read_text("utf-8"))["nodes"]) == 3
    from starlight.notebook.history import History

    kept = History(paths.data / "history" / "history.sqlite").versions(board)
    assert len(kept) == 2  # (a version before each change after the first)


def test_the_board_api(app: Any, notebook: Path) -> None:
    made = app.post("/api/board", json={"title": "Plan"}, headers=LOCAL).json()
    assert made["path"] == "Boards/Plan.canvas"
    again = app.post("/api/board", json={"title": "Plan"}, headers=LOCAL).json()
    assert again["path"] == "Boards/Plan 2.canvas"  # (never over an existing board)
    read = app.get("/api/board", params={"path": made["path"]}, headers=LOCAL).json()
    assert read["board"] == {"nodes": [], "edges": []}
    board = {**SAMPLE, "nodes": SAMPLE["nodes"][1:2], "edges": []}
    body = {"board": board, "stamp": read["stamp"], "first": True}
    saved = app.put("/api/board", params={"path": made["path"]}, json=body, headers=LOCAL)
    assert saved.status_code == 200 and saved.json()["version"] is not None
    file = notebook / "Boards" / "Plan.canvas"
    assert json.loads(file.read_text("utf-8"))["metadata"]["version"] == "1.0-1.0"
    stale = app.put("/api/board", params={"path": made["path"]}, json=body, headers=LOCAL)
    assert stale.status_code == 409  # (the stamp is old: nothing is overwritten)
    broken = {"board": {"nodes": [{"id": "x"}]}, "stamp": saved.json()["stamp"]}
    refused = app.put("/api/board", params={"path": made["path"]}, json=broken, headers=LOCAL)
    assert refused.status_code == 422 and "no type" in refused.json()["detail"]
    listed = app.get("/api/boards", headers=LOCAL).json()["boards"]
    assert [b["path"] for b in listed] == ["Boards/Plan 2.canvas", "Boards/Plan.canvas"]
    file.write_text("{oops", "utf-8")
    unreadable = app.get("/api/board", params={"path": made["path"]}, headers=LOCAL)
    assert unreadable.status_code == 422 and "not valid JSON" in unreadable.json()["detail"]


@pytest.mark.parametrize(
    "path",
    ["../secret.canvas", "/etc/x.canvas", "C:/Windows/x.canvas", "Notes/Shopping.md",
     ".hidden/x.canvas", "code/x.canvas", "Boards/con.canvas"],
)  # fmt: skip
def test_board_paths_stay_inside_the_starlight_folder(app: Any, notebook: Path, path: str) -> None:
    read = app.get("/api/board", params={"path": path}, headers=LOCAL)
    assert read.status_code == 400
    body = {"board": {"nodes": [], "edges": []}, "stamp": "missing"}
    assert app.put("/api/board", params={"path": path}, json=body, headers=LOCAL).status_code == 400
