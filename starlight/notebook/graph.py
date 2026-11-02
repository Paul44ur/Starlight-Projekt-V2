"""How the notes link together, as a graph (docs/plan-notes-app.md, blocks F1 and F2).

``graph(index)`` gives the notes as points and their links as lines, from the notebook
index: a link counts once between two notes, whichever way it goes and however often it is
written; links to pictures, to notes that do not exist yet and to the note itself are left
out. ``around`` keeps only one note and the notes up to ``depth`` links away (the small
graph next to a note). A graph keeps at most ``limit`` points: the nearest first, then those
with the most links.
"""

from __future__ import annotations

from collections import deque
from typing import Any

LIMIT = 300
MAX_LIMIT = 3000


def graph(
    index: Any, around: str | None = None, depth: int = 1, limit: int = LIMIT
) -> dict[str, Any]:
    """{"nodes": [{id, title, folder, links, depth?}], "links": [{source, target}], "total",
    "capped"}. ``index`` is the notebook index, brought up to date by the caller."""
    notes = {info.path: info for info in index.notes()}
    resolver = index.resolver()
    pairs: set[tuple[str, str]] = set()
    for source, target in index.link_pairs():
        to = resolver.resolve(target, source)
        if to and to != source and to in notes and source in notes:
            pairs.add((source, to) if source < to else (to, source))
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
