"""Boards: cards and arrows on an endless surface (docs/plan-notes-app.md, block F3).

A board is a ``.canvas`` file in the open JSON Canvas format (jsoncanvas.org, MIT), the same
files Obsidian makes, so they can be opened there too:

    {"nodes": [...cards...], "edges": [...arrows...]}

A card ("node") has an ``id``, a ``type`` and its place and size (``x``, ``y``, ``width``,
``height``, whole numbers), and maybe a ``color`` ("1" to "6", or "#rrggbb"):

* ``text``: ``text`` in Markdown;
* ``file``: a note or picture in the Starlight folder (``file``, maybe ``subpath`` "#Heading");
* ``link``: a web page (``url``);
* ``group``: a frame around other cards (``label``, maybe a ``background`` picture).

An arrow ("edge") goes ``fromNode`` ``toNode`` (maybe ``fromSide``/``toSide``: top, right,
bottom, left; ``fromEnd``/``toEnd``: none or arrow, by default an arrowhead at the end; a
``label``; a ``color``).

Fields Starlight does not know are kept as they are, on the board, its cards and its arrows,
so nothing another program wrote is lost. ``load`` refuses what is not a board, in plain
words; ``dump`` writes it back the way Obsidian does (tabs).
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlight.core.tools import ToolError

SUFFIX = ".canvas"
MAX_BYTES = 5 * 1024**2  # a board bigger than this is not read
KINDS = ("text", "file", "link", "group")
SIDES = ("top", "right", "bottom", "left")
ENDS = ("none", "arrow")
PLACE = ("x", "y", "width", "height")
CARD = {"width": 260, "height": 140}  # a new card's size
GAP = 40


class BoardError(ToolError):
    """The file is not a board Starlight can read; the message says why."""


@dataclass
class Board:
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    other: dict[str, Any] = field(default_factory=dict)  # fields at the top Starlight keeps

    def node(self, node_id: str) -> dict[str, Any] | None:
        return next((n for n in self.nodes if n.get("id") == node_id), None)


def _number(card: dict[str, Any], name: str, where: str) -> None:
    value = card.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BoardError(f"{where} has no {name} (a number)")
    if isinstance(value, float):
        card[name] = round(value)  # (the format asks for whole numbers)


def check(data: Any) -> Board:
    """The board in ``data`` (parsed JSON), checked; BoardError when it is not one."""
    if not isinstance(data, dict):
        raise BoardError("a board is a JSON object with nodes and edges")
    nodes, edges = data.get("nodes", []), data.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise BoardError("a board's nodes and edges are lists")
    ids: set[str] = set()
    for number, node in enumerate(nodes, 1):
        where = f"card {number}"
        if not isinstance(node, dict):
            raise BoardError(f"{where} is not an object")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise BoardError(f"{where} has no id")
        if node_id in ids:
            raise BoardError(f"two cards have the id {node_id}")
        ids.add(node_id)
        if not isinstance(node.get("type"), str):
            raise BoardError(f"{where} ({node_id}) has no type")
        for name in PLACE:
            _number(node, name, f"{where} ({node_id})")
    edge_ids: set[str] = set()
    for number, edge in enumerate(edges, 1):
        where = f"arrow {number}"
        if not isinstance(edge, dict):
            raise BoardError(f"{where} is not an object")
        edge_id = edge.get("id")
        if not isinstance(edge_id, str) or not edge_id or edge_id in edge_ids:
            raise BoardError(f"{where} has no id of its own")
        edge_ids.add(edge_id)
        for end in ("fromNode", "toNode"):
            if edge.get(end) not in ids:
                raise BoardError(f"{where} ({edge_id}) leads to a card that is not there")
    other = {k: v for k, v in data.items() if k not in ("nodes", "edges")}
    return Board(nodes, edges, other)


def load(text: str) -> Board:
    """A board from a ``.canvas`` file's text (an empty file is an empty board)."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise BoardError("this board is over 5 MB; it is not read")
    if not text.strip():
        return Board()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BoardError(f"this board is not valid JSON (line {exc.lineno}: {exc.msg})") from None
    return check(data)


def dump(board: Board) -> str:
    """The board as a ``.canvas`` file's text."""
    data = {"nodes": board.nodes, "edges": board.edges, **board.other}
    return json.dumps(data, ensure_ascii=False, indent="\t") + "\n"


def new_id(board: Board) -> str:
    """An id no card or arrow on the board has (16 hex characters, as Obsidian makes them)."""
    taken = {n.get("id") for n in board.nodes} | {e.get("id") for e in board.edges}
    while True:
        made = secrets.token_hex(8)
        if made not in taken:
            return made


def add_card(
    board: Board,
    kind: str,
    value: str,
    *,
    label: str = "",
    color: str = "",
    near: str = "",
) -> dict[str, Any]:
    """Put a new card on the board, to the right of the others (or below the card ``near``,
    with an arrow from it, named ``label``). ``kind``: text (``value`` is the text), file (a
    path in the Starlight folder), link (a web address) or group (``value`` is its label)."""
    if kind not in KINDS:
        raise BoardError(f"a card is text, file, link or group, not {kind}")
    if not value.strip() and kind != "group":
        raise BoardError("what should the card show?")
    if kind == "link" and not value.strip().lower().startswith(("http://", "https://")):
        raise BoardError("a link card needs a web address (http:// or https://)")
    card: dict[str, Any] = {"id": new_id(board), "type": kind}
    anchor = board.node(near) if near else None
    if near and anchor is None:
        raise BoardError(f"there is no card {near} on this board")
    if anchor is not None:
        card["x"], card["y"] = anchor["x"], anchor["y"] + anchor["height"] + GAP
    elif board.nodes:
        card["x"] = max(n["x"] + n["width"] for n in board.nodes) + GAP
        card["y"] = min(n["y"] for n in board.nodes)
    else:
        card["x"], card["y"] = 0, 0
    card.update(CARD)
    if kind == "text":
        card["text"] = value.strip()
    elif kind == "file":
        card["file"] = value.strip().replace("\\", "/").lstrip("/")
    elif kind == "link":
        card["url"] = value.strip()
    else:
        card["label"] = value.strip()
    if color:
        card["color"] = color
    board.nodes.append(card)
    if anchor is not None:  # an arrow from the card it was put under
        arrow = {"id": new_id(board), "fromNode": anchor["id"], "fromSide": "bottom",
                 "toNode": card["id"], "toSide": "top"}  # fmt: skip
        if label:
            arrow["label"] = label
        board.edges.append(arrow)
    return card


def _inside(card: dict[str, Any], group: dict[str, Any]) -> bool:
    return bool(
        group["x"] <= card["x"] and group["y"] <= card["y"]
        and card["x"] + card["width"] <= group["x"] + group["width"]
        and card["y"] + card["height"] <= group["y"] + group["height"]
    )  # fmt: skip


def find(root: Path, limit: int = 2000) -> list[Path]:
    """The boards in the Starlight folder ``root`` (not in hidden folders or the code
    folder), A to Z by path, at most ``limit``."""
    found: list[Path] = []
    for folder, dirs, names in os.walk(root):
        here = Path(folder)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (
            here == root and d.casefold() == "code"))  # fmt: skip
        for name in sorted(names):
            if name.casefold().endswith(SUFFIX) and not name.startswith("."):
                found.append(here / name)
                if len(found) >= limit:
                    return found
    return found


def describe(card: dict[str, Any]) -> str:
    """A card in a few words: its text, note, address or label."""
    kind = card.get("type")
    if kind == "text":
        return " ".join(str(card.get("text", "")).split())[:300]
    if kind == "file":
        return str(card.get("file", "")) + str(card.get("subpath", ""))
    if kind == "link":
        return str(card.get("url", ""))
    if kind == "group":
        return str(card.get("label", "")) or "(a group)"
    return f"({kind})"


def summary(board: Board) -> dict[str, Any]:
    """What is on the board, for the assistant: the cards (with the group each is in) and
    the arrows between them, in words."""
    groups = [n for n in board.nodes if n.get("type") == "group"]
    cards = []
    for card in sorted(board.nodes, key=lambda n: (n["y"], n["x"])):
        item: dict[str, Any] = {"id": card["id"], "type": card.get("type"), "says": describe(card)}
        inside = [g for g in groups if g is not card and _inside(card, g)]
        if inside:
            item["in"] = describe(min(inside, key=lambda g: g["width"] * g["height"]))
        cards.append(item)
    arrows = []
    for edge in board.edges:
        start, end = board.node(edge["fromNode"]), board.node(edge["toNode"])
        if start is None or end is None:
            continue
        arrow = {"from": describe(start), "to": describe(end)}
        if edge.get("label"):
            arrow["label"] = edge["label"]
        arrows.append(arrow)
    return {"cards": cards, "arrows": arrows}
