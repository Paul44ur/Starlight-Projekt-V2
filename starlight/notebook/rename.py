"""Renaming a note so that no link breaks (docs/plan-notes-app.md, block C1).

Every [[link]] that means the note (by its name, a folder in the link, or an alias that now
points elsewhere) is rewritten to the new name, in every note; each note that changes keeps
its earlier version first (write_note). ``plan`` says what would change without changing
anything, so the assistant can ask first; ``rename`` does it. Flashcards in a deck named
after the note keep their progress (flashcards.renamed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlight.notebook.history import HistoryError
from starlight.notebook.links import clean, key, retarget
from starlight.notebook.markdown import wikilink_spans
from starlight.notebook.write import NoteConflict, stamp, write_note

MAX_LINKED = 500  # notes one rename may change


@dataclass
class RenamePlan:
    old: Path
    new: Path
    texts: dict[Path, str] = field(default_factory=dict)  # note -> its text with links fixed
    links: int = 0  # how many links change
    stamps: dict[Path, str] = field(default_factory=dict)  # note -> its stamp when read

    def others(self) -> list[Path]:
        """The other notes whose links change (not the renamed note itself)."""
        return [p for p in self.texts if p != self.old]


def _text(path: Path) -> str:
    from starlight.text import decode_text

    return decode_text(path.read_bytes())


def plan(index: Any, old: Path, new: Path) -> RenamePlan:
    """What renaming ``old`` to ``new`` would change (nothing is written)."""
    if new.exists() and new.resolve() != old.resolve():
        raise HistoryError(f"there is already a note {new.stem}")
    index.refresh(force=True)
    root = Path(index.root).resolve()
    old_relative = old.resolve().relative_to(root).as_posix()
    resolver = index.resolver()
    sources = {b["from"] for b in index.backlinks(old_relative)} | {old_relative}
    if len(sources) > MAX_LINKED:
        raise HistoryError(f"more than {MAX_LINKED} notes link to {old.stem}; rename it elsewhere")
    result = RenamePlan(old, new)
    names = {key(old.stem), key(old.name)}
    for source in sorted(sources):
        path = root / source
        before = stamp(path)  # (first: a change while reading shows too)
        try:
            text = _text(path)
        except OSError:
            continue
        pieces, last, changed = [], 0, 0
        for start, end, inside, embed in wikilink_spans(text):
            target = inside.split("|", 1)[0].split("#", 1)[0].rstrip("\\").strip()
            if not target or key(clean(target).rpartition("/")[2]) not in names:
                continue  # (a link by an alias still works: the alias goes along)
            if resolver.resolve(target, source) != old_relative:
                continue
            link = ("!" if embed else "") + "[[" + retarget(inside, new.stem) + "]]"
            pieces += [text[last:start], link]
            last, changed = end, changed + 1
        if changed:
            result.texts[path] = "".join(pieces) + text[last:]
            result.stamps[path] = before
            result.links += changed
    return result


def rename(index: Any, history: Any, planned: RenamePlan) -> list[Path]:
    """Carry out a plan: rename the note, then fix the links (a version of each note first).
    Nothing happens when a note to change was changed since the plan read it (the assistant
    asks first, and the Notes page may save meanwhile). Returns the other notes that changed."""
    old, new = planned.old, planned.new
    if new.exists() and new.resolve() != old.resolve():
        raise HistoryError(f"there is already a note {new.stem}")
    for path, before in planned.stamps.items():
        if stamp(path) != before:
            raise NoteConflict(
                f"{path.stem} was changed meanwhile, so nothing was renamed; try it again"
            )
    history.save(old, "rename")
    old.rename(new)
    history.moved(old, new)  # (its earlier versions go along)
    history.changed(old)
    history.changed(new)
    for path, text in planned.texts.items():
        target = new if path == old else path
        # (a rename keeps the time and size, so the stamp is the same under the new name)
        write_note(target, text, f"links to {new.stem}", history,
                   expected=planned.stamps.get(path))  # fmt: skip
    if getattr(index, "db_path", None) is not None:  # (flashcards of a deck named after it)
        from starlight.notebook import flashcards

        flashcards.renamed(index, old.stem, new)
    return planned.others()
