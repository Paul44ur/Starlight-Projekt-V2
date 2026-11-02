"""How the notes link together, as a graph (docs/plan-notes-app.md, blocks F1 and F2).

``graph(index)`` gives the notes as points and their links as lines, from the notebook
index: a link counts once between two notes, whichever way it goes and however often it is
written; links to pictures, to notes that do not exist yet and to the note itself are left
out. ``around`` keeps only one note and the notes up to ``depth`` links away (the small
graph next to a note), ``folder`` and ``tag`` keep some of them, and ``memories`` adds
Starlight's memories (the whole graph's "Memories" switch). A graph keeps at most ``limit``
points: the nearest first, then those with the most links.
"""

from __future__ import annotations

from collections import deque
from typing import Any

LIMIT = 300
MAX_LIMIT = 3000


class _Point:
    """A memory shown as a point: what the graph needs of a note."""

    def __init__(self, title: str, folder: str) -> None:
        self.title, self.folder = title, folder


MEMORY = "memory:"  # the id of a memory's point: "memory:" and the memory's id
MEMORIES = "Memories"  # their "folder"


def graph(
    index: Any,
    around: str | None = None,
    depth: int = 1,
    limit: int = LIMIT,
    *,
    folder: str = "",
    tag: str = "",
    memories: list[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """{"nodes": [{id, title, folder, links, depth?}], "links": [{source, target}], "total",
    "capped"}. ``index`` is the notebook index, brought up to date by the caller. ``folder``
    and ``tag`` keep only the notes in that folder (or below) or with that tag (or one below
    it). ``memories`` (id, topic, text) adds Starlight's memories as points too, linked to
    the notes and memories their [[links]] name."""
    from starlight.notebook.markdown import parse

    notes: dict[str, Any] = {info.path: info for info in index.notes()}
    if folder.strip("/"):
        wanted = folder.strip("/").casefold()
        notes = {
            p: i for p, i in notes.items() if (i.folder.casefold() + "/").startswith(wanted + "/")
        }
    if tag.strip():
        tagged = set(index.with_tag(tag))
        notes = {p: i for p, i in notes.items() if p in tagged}
    resolver = index.resolver()
    pairs: set[tuple[str, str]] = set()

    def join(source: str, to: str | None) -> None:
        if to and to != source and to in notes and source in notes:
            pairs.add((source, to) if source < to else (to, source))

    for source, target in index.link_pairs():
        join(source, resolver.resolve(target, source))
    if memories is not None:
        by_name: dict[str, str] = {}
        for memory_id, topic, _ in memories:
            point = MEMORY + memory_id
            notes[point] = _Point(topic or memory_id, MEMORIES)
            by_name.setdefault(memory_id.casefold(), point)
            by_name.setdefault(topic.casefold(), point)
        for memory_id, _, text in memories:
            for link in parse(text).links:
                to = by_name.get(link.target.casefold()) or resolver.resolve(link.target)
                join(MEMORY + memory_id, to)
    neighbours: dict[str, set[str]] = {}
    for a, b in pairs:
        neighbours.setdefault(a, set()).add(b)
        neighbours.setdefault(b, set()).add(a)
    limit = max(1, min(limit, MAX_LIMIT))
    distance: dict[str, int] = {}
    if around is not None:
        if around not in notes:
            return {"nodes": [], "links": [], "total": 0, "capped": False}
        distance[around] = 0
        queue = deque([around])
        while queue:
            here = queue.popleft()
            if distance[here] >= max(1, min(depth, 3)):
                continue
            for other in sorted(neighbours.get(here, ())):
                if other not in distance:
                    distance[other] = distance[here] + 1
                    queue.append(other)
        found = list(distance)
    else:
        found = list(notes)

    def rank(path: str) -> tuple[int, int, str]:
        return (distance.get(path, 0), -len(neighbours.get(path, ())), path.casefold())

    kept = sorted(found, key=rank)[:limit]
    keep = set(kept)
    nodes = []
    for path in sorted(kept, key=str.casefold):
        info = notes[path]
        node: dict[str, Any] = {
            "id": path, "title": info.title, "folder": info.folder,
            "links": len(neighbours.get(path, ())),
        }  # fmt: skip
        if around is not None:
            node["depth"] = distance[path]
        nodes.append(node)
    links = [{"source": a, "target": b} for a, b in sorted(pairs) if a in keep and b in keep]
    return {"nodes": nodes, "links": links, "total": len(found), "capped": len(found) > limit}
