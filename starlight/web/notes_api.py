"""The web API of the Notes page (docs/plan-notes-app.md, block B3). PC only, like settings.

Paths are relative to the notebook folder (the Starlight folder in Documents) and written
with "/" ("Notes/Shopping.md"). A path must stay inside that folder: no "..", no full paths,
no hidden or Windows-reserved names, not the ``code`` folder, and a link pointing outside
is refused too. Notes are ``.md`` files; pictures (PNG, JPEG, GIF, WebP, up to 10 MB) go to
``Notes/Attachments``.

Every change goes through starlight.notebook: a version is kept first, the file is written
as a whole, and a save carries the ``stamp`` the page read the note with, so a change made
meanwhile (in another program or tab) is never overwritten: the answer is 409 with both
texts, and the page lets you choose.

    GET    /api/notes[?tag=]                 the folders and notes (pinned ones marked)
    GET    /api/notes/search?q=              notes with these words
    GET    /api/notes/resolve?name=&from=    the note a link [[name]] means (404: none yet)
    GET    /api/notes/embed?name=&source=&heading=  a note or a part of it, to show inside
    POST   /api/notes/welcome                {lang}: the welcome note, the first time only
    GET    /api/notes/trash                  deleted notes
    POST   /api/notes/trash/restore          {path}: bring one back
    POST   /api/notes/pins                   {path, pinned}
    POST   /api/notes/pictures?name=         the picture's bytes as the body
    GET    /api/notes/file/{path}            a picture (an SVG only as a download)
    GET    /api/note?path=                   text, stamp, properties, outline, links, backlinks
    PUT    /api/note?path=                   {text, stamp, first}: save (409 on a conflict)
    POST   /api/note                         {folder, title, text}: a new note
    POST   /api/note/rename                  {path, title, fix_links}: links follow
    DELETE /api/note?path=                   to the trash
    GET    /api/note/versions?path=          the kept versions
    GET    /api/note/diff?path=&version=     what changed since a version (or between two)
    POST   /api/note/restore                 {path, version}
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse

from starlight.core.fsutil import write_atomic
from starlight.core.safepaths import inside
from starlight.core.tools import ToolError
from starlight.notebook import NoteConflict, markdown, stamp, write_note
from starlight.notebook.rename import plan as plan_rename
from starlight.notebook.rename import rename as carry_out_rename
from starlight.notebook.write import MISSING

FOLDERS = ("Notes", "Journal", "Recipes", "Clippings", "Inbox")  # always shown, in this order
ATTACHMENTS = "Notes/Attachments"
MAX_NOTE = 1024**2  # bigger than this is a document, not a note
MAX_PICTURE = 10 * 1024**2
# seconds: while typing, at most one kept version per 5 minutes. The first save after
# opening the editor ("first") always keeps one, so where each editing session started stays.
TYPING_GAP = 300.0
NOTE = (".md",)
PICTURES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
SHOWN = {**PICTURES, ".svg": "image/svg+xml"}
BAD = re.compile(r'[<>:"|?*\x00-\x1f]')
RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)


def sniff(data: bytes) -> str | None:
    """The picture type the bytes really are (by their first bytes), or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _bad(detail: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def locked(exc: OSError) -> str:
    """Why a file could not be changed, in plain words."""
    reason = exc.strerror or str(exc)
    if isinstance(exc, PermissionError):
        reason += " (is it open in another program?)"
    return reason


def clean_name(name: str, limit: int = 100) -> str:
    """A file or folder name that works on Windows: no characters it refuses, no device
    names, no dots or spaces at the end."""
    name = BAD.sub("", name.replace("/", " ").replace("\\", " ")).strip().rstrip(". ")
    name = name.lstrip(".")[:limit].strip()
    return f"_{name}" if RESERVED.match(name) else name


def relative_path(root: Path, text: str, kinds: tuple[str, ...] | None = NOTE) -> Path:
    """The file ``text`` names inside the notebook folder ``root``; HTTP 400 for anything
    that could reach outside it. ``kinds``: the allowed suffixes (None: a folder)."""
    raw = text.replace("\\", "/").strip()
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or "://" in raw:
        raise _bad("give a path inside the notebook folder, such as Notes/Shopping.md")
    parts = raw.split("/")
    for part in parts:
        if part in ("", ".", "..") or part.startswith(".") or BAD.search(part):
            raise _bad(f"{text!r} is not a path inside the notebook folder")
        if part != part.rstrip(". ") or RESERVED.match(part):
            raise _bad(f"{part!r} is not a name Windows allows")
    if parts[0].casefold() == "code":
        raise _bad("the code folder holds programs, not notes")
    if kinds is not None and Path(parts[-1]).suffix.casefold() not in kinds:
        raise _bad(f"{parts[-1]!r} is not a {' or '.join(kinds)} file")
    base = root.resolve()
    path = base.joinpath(*parts)
    if not inside(path.resolve(), base):  # (a link inside that points elsewhere)
        raise _bad(f"{text!r} leads outside the notebook folder")
    return path


def welcome_note(german: bool) -> tuple[str, str]:
    """The first note: what notes can do, shown by example (itself an ordinary note)."""
    soon = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    if german:
        return (
            "Willkommen",
            f"""---
tags: [willkommen]
---
# Willkommen in deinen Notizen

Hier hältst du fest, was du behalten willst. Starlight liest mit und hilft: Frag im Chat
zum Beispiel „Was steht in meiner Einkaufsliste?“.

## So geht's
- **Verknüpfen:** Zwei eckige Klammern verbinden Notizen, z. B. [[Einkaufsliste]].
- **Schlagwörter:** Ein Rautezeichen ordnet: #ideen oder #arbeit/projekte.
- **Bilder:** Ein Bild aus dem Ordner Notes/Attachments zeigst du mit `![[foto.png]]`.

## Aufgaben
- [ ] Milch kaufen fällig: {soon}
- [x] Diese Notiz lesen

> [!tip] Tipp
> Mit **Strg+K** findest du jede Notiz schnell.

> [!note] Nichts geht verloren
> Vor jeder Änderung hebt Starlight die alte Fassung auf.
""",
        )
    return (
        "Welcome",
        f"""---
tags: [welcome]
---
# Welcome to your notes

Write down what you want to keep. Starlight reads along and helps: ask in the chat,
for example "What is on my shopping list?".

## How it works
- **Links:** Two square brackets connect notes, like [[Shopping list]].
- **Tags:** A hash sign sorts things: #ideas or #work/projects.
- **Pictures:** Show a picture from the Notes/Attachments folder with `![[photo.png]]`.

## Tasks
- [ ] Buy milk due: {soon}
- [x] Read this note

> [!tip] Tip
> Press **Ctrl+K** to find any note quickly.

> [!note] Nothing gets lost
> Before every change, Starlight keeps the earlier version.
""",
    )


def add_notes_api(
    app: FastAPI,
    kernel: Callable[[], Any],
    local_only: Callable[[Request], Any],
    body: Callable[[Request], Awaitable[dict[str, Any]]],
) -> None:
    """Add the endpoints (see the module's notes) to ``app``."""
    writing = threading.Lock()  # one change at a time (the page and a second tab)

    def index() -> Any:
        found = kernel().notebook_index
        if found is None:
            raise _bad("the Starlight folder is not usable; see output.folder", 503)
        return found

    def root() -> Path:
        return Path(index().root)

    def rel(path: Path) -> str:
        return path.relative_to(root().resolve()).as_posix()

    def note_file(text: str) -> Path:
        path = relative_path(root(), text)
        if not path.is_file():
            raise _bad(f"there is no note {text}", 404)
        return path

    def pins_file() -> Path:
        return Path(kernel().paths.data) / "notebook" / "pinned.json"

    def pinned() -> list[str]:
        try:
            data = json.loads(pins_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = data.get("pinned") if isinstance(data, dict) else None
        return [str(p) for p in items] if isinstance(items, list) else []

    def set_pins(paths: list[str]) -> None:
        pins_file().parent.mkdir(parents=True, exist_ok=True)
        unique = list(dict.fromkeys(paths))[:200]
        write_atomic(pins_file(), json.dumps({"pinned": unique}, ensure_ascii=False, indent=1))

    def history() -> Any:
        return kernel().file_history

    def audit(action: str, **fields: Any) -> None:
        kernel().audit.record(action, **fields)

    # -- the list -------------------------------------------------------------------------

    @app.get("/api/notes")
    def list_notes(request: Request, tag: str = "") -> dict[str, Any]:
        local_only(request)
        found = index()
        done = found.refresh()
        pins = pinned()
        tagged = set(found.with_tag(tag)) if tag.strip() else None
        notes = [
            {**n.public(), "pinned": n.path in pins}
            for n in found.notes()
            if tagged is None or n.path in tagged
        ]
        folders = list(FOLDERS)
        for note in notes:
            top = note["path"].split("/")[0] if "/" in note["path"] else ""
            if top and top not in folders:
                folders.append(top)
        return {"folders": folders, "notes": notes, "pinned": pins, "capped": done.capped}

    @app.get("/api/notes/search")
    def search_notes(request: Request, q: str = "", limit: int = 30) -> dict[str, Any]:
        local_only(request)
        found = index()
        found.refresh()
        hits = found.search(q, max(1, min(limit, 100)))
        return {"hits": [h.__dict__ for h in hits]}

    @app.get("/api/notes/resolve")
    def resolve(request: Request, name: str, source: str = "") -> dict[str, Any]:
        local_only(request)
        found = index()
        found.refresh()
        path = found.resolve(name, source or None)
        if path is None:
            raise _bad(f"there is no note {name} yet", 404)
        return {"path": path}

    @app.post("/api/notes/welcome")
    async def welcome(request: Request) -> dict[str, Any]:
        """The welcome note, made on the first visit to the Notes page (never again, even
        after it is deleted). In German or English, as the page speaks."""
        local_only(request)
        data = await body(request)
        flag = Path(kernel().paths.data) / "notebook" / "welcomed"
        if flag.exists():
            return {"path": None}
        german = str(data.get("lang", "")).lower().startswith("de")
        title, text = welcome_note(german)
        folder = root().resolve() / "Notes"
        file = folder / f"{title}.md"
        with writing:
            if not file.exists():
                folder.mkdir(parents=True, exist_ok=True)
                write_note(file, text, "notes.page welcome", history(), expected=MISSING)
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text("1", encoding="utf-8")
        return {"path": rel(file)}

    @app.get("/api/notes/embed")
    def embed(request: Request, name: str, source: str = "", heading: str = "") -> dict[str, Any]:
        """A note (or a part of it: "Heading" or "^block") shown inside another, or as a
        preview when the pointer rests on a link."""
        local_only(request)
        found = index()
        found.refresh()
        path = found.resolve(name, source or None)
        if path is None:
            raise _bad(f"there is no note {name} yet", 404)
        file = relative_path(root(), path)
        from starlight.text import decode_text

        text = decode_text(file.read_bytes()[:MAX_NOTE])
        part = heading.strip()
        if part.startswith("^"):
            shown = markdown.block(text, part[1:])
        elif part:
            shown = markdown.section(text, part)
        else:
            shown = markdown.split(text).body
        if shown is None:
            raise _bad(f"{file.stem} has no part {part}", 404)
        return {"path": path, "title": file.stem, "text": shown[:20000]}

    @app.get("/api/notes/trash")
    def trash(request: Request) -> dict[str, Any]:
        local_only(request)
        base = root().resolve()
        items = []
        for version in history().deleted(base):
            path = Path(version.path)
            if path.suffix.casefold() == ".md":
                items.append(
                    {"path": path.resolve().relative_to(base).as_posix(), **version.public()}
                )
        return {"notes": items}

    @app.post("/api/notes/trash/restore")
    async def restore_deleted(request: Request) -> dict[str, Any]:
        local_only(request)
        data = await body(request)
        path = relative_path(root(), str(data.get("path", "")))
        with writing:
            try:
                history().restore_deleted(path)
            except ToolError as exc:
                raise _bad(str(exc), 409) from exc
        audit("notes.page.restore_deleted", path=str(path))
        return {"restored": rel(path), "stamp": stamp(path)}

    @app.post("/api/notes/pins")
    async def pin(request: Request) -> dict[str, Any]:
        local_only(request)
        data = await body(request)
        path = rel(note_file(str(data.get("path", ""))))
        pins = [p for p in pinned() if p != path]
        if data.get("pinned", True):
            pins.insert(0, path)
        set_pins(pins)
        return {"pinned": pins}

    # -- pictures -------------------------------------------------------------------------

    @app.post("/api/notes/pictures")
    async def picture(request: Request, name: str = "picture") -> dict[str, Any]:
        local_only(request)
        declared = request.headers.get("content-length", "")
        too_big = f"a picture can be up to {MAX_PICTURE // 1024**2} MB"
        if declared.isdigit() and int(declared) > MAX_PICTURE:
            raise _bad(too_big, 413)
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_PICTURE:
                raise _bad(too_big, 413)
            chunks.append(chunk)
        data = b"".join(chunks)
        kind = sniff(data)
        if kind is None:
            raise _bad("only PNG, JPEG, GIF and WebP pictures can go into a note", 415)
        stem = clean_name(Path(name).stem, 80) or "picture"
        folder = root().resolve() / ATTACHMENTS
        with writing:
            folder.mkdir(parents=True, exist_ok=True)
            target, number = folder / f"{stem}{kind}", 2
            while target.exists():
                target, number = folder / f"{stem} ({number}){kind}", number + 1
            target.write_bytes(data)
        audit("notes.page.picture", path=str(target), bytes=len(data))
        return {"path": rel(target), "embed": f"![[{target.name}]]", "size": len(data)}

    @app.get("/api/notes/file/{path:path}")
    def notebook_file(path: str, request: Request) -> Response:
        local_only(request)
        file = relative_path(root(), path, tuple(SHOWN))
        if not file.is_file():
            raise _bad("no such picture", 404)
        media = SHOWN[file.suffix.casefold()]
        svg = media == "image/svg+xml"  # can hold scripts: never shown as a page
        return FileResponse(
            file,
            media_type=media,
            filename=file.name,
            content_disposition_type="attachment" if svg else "inline",
            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"},
        )

    # -- one note -------------------------------------------------------------------------

    @app.get("/api/note")
    def read_note(request: Request, path: str) -> dict[str, Any]:
        local_only(request)
        file = note_file(path)
        if file.stat().st_size > MAX_NOTE:
            raise _bad("this note is over 1 MB; open it in another program", 413)
        from starlight.text import decode_text

        current = stamp(file)
        text = decode_text(file.read_bytes())
        note = markdown.parse(text)
        found = index()
        found.refresh()  # (every few seconds at most: links and backlinks are current)
        found.touch(file)
        relative = rel(file)
        return {
            "path": relative,
            "title": file.stem,
            "text": text,
            "stamp": current,
            "properties": note.properties,
            "body": note.body,  # (the text after the properties block)
            "error": note.error,
            "headings": [h.__dict__ for h in note.headings],
            "tags": note.tag_names(),
            "links": found.links(relative),
            "backlinks": found.backlinks(relative),
            "pinned": relative in pinned(),
        }

    @app.put("/api/note")
    async def save_note(request: Request, path: str) -> dict[str, Any]:
        local_only(request)
        data = await body(request)
        text, expected = data.get("text"), data.get("stamp")
        if not isinstance(text, str) or not isinstance(expected, str):
            raise _bad("send {text, stamp}: the note and the stamp it was read with")
        if len(text.encode("utf-8")) > MAX_NOTE:
            raise _bad("a note can be up to 1 MB; start another one", 413)
        file = relative_path(root(), path)
        with writing:
            try:
                gap = 0.0 if data.get("first") else TYPING_GAP
                written = write_note(
                    file, text, "notes.page", history(), expected=expected, min_gap=gap
                )
            except NoteConflict as exc:
                from starlight.text import decode_text

                theirs = decode_text(file.read_bytes()) if file.is_file() else ""
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": str(exc),
                        "theirs": theirs,
                        "stamp": stamp(file),
                        "yours": text,
                    },
                ) from exc
            except OSError as exc:
                raise _bad(f"could not save {file.name}: {locked(exc)}", 409) from exc
        return {"path": rel(file), "stamp": written.stamp, "version": written.version}

    @app.post("/api/note")
    async def new_note(request: Request) -> dict[str, Any]:
        local_only(request)
        data = await body(request)
        folder = str(data.get("folder") or "Notes")
        title = clean_name(str(data.get("title") or ""), 80) or "Untitled"
        text = str(data.get("text") or "")
        base = relative_path(root(), folder, None)
        with writing:
            file, number = base / f"{title}.md", 2
            while file.exists():
                file, number = base / f"{title} {number}.md", number + 1
            relative_path(root(), rel(file))  # (the name is allowed)
            base.mkdir(parents=True, exist_ok=True)
            written = write_note(file, text, "notes.page", history(), expected=MISSING)
        return {"path": rel(file), "title": file.stem, "stamp": written.stamp}

    @app.post("/api/note/rename")
    async def rename_note(request: Request) -> dict[str, Any]:
        local_only(request)
        data = await body(request)
        file = note_file(str(data.get("path", "")))
        title = clean_name(str(data.get("title") or ""), 80)
        if not title:
            raise _bad("give the note a new title")
        target = file.with_name(f"{title}.md")
        relative_path(root(), rel(target))
        fix_links = data.get("fix_links", True) is not False  # links in other notes too
        with writing:
            if target.exists() and target.resolve() != file.resolve():
                raise _bad(f"there is already a note {title}", 409)
            try:
                planned = plan_rename(index(), file, target)
                if not fix_links:
                    planned.texts = {p: t for p, t in planned.texts.items() if p == file}
                updated = carry_out_rename(index(), history(), planned)
            except ToolError as exc:
                raise _bad(str(exc), 409) from exc
            except OSError as exc:
                raise _bad(f"could not rename {file.name}: {locked(exc)}", 409) from exc
        old, new = rel(file), rel(target)
        set_pins([new if p == old else p for p in pinned()])
        audit("notes.page.rename", path=str(file), to=str(target), links=len(updated))
        return {
            "path": new,
            "title": target.stem,
            "stamp": stamp(target),
            "updated": [rel(p) for p in updated],
        }

    @app.delete("/api/note")
    def delete_note(request: Request, path: str) -> dict[str, Any]:
        local_only(request)
        file = note_file(path)
        with writing:
            try:
                history().trash(file, "notes.page delete")
            except ToolError as exc:
                raise _bad(str(exc), 409) from exc
            except OSError as exc:
                raise _bad(f"could not delete {file.name}: {locked(exc)}", 409) from exc
        relative = rel(file)
        set_pins([p for p in pinned() if p != relative])
        audit("notes.page.delete", path=str(file))
        return {"trashed": relative}

    # -- versions -------------------------------------------------------------------------

    @app.get("/api/note/versions")
    def note_versions(request: Request, path: str) -> dict[str, Any]:
        local_only(request)
        file = relative_path(root(), path)
        return {"versions": [v.public() for v in history().versions(file)]}

    @app.get("/api/note/diff")
    def note_diff(
        request: Request, path: str, version: int, against: int | None = None
    ) -> dict[str, Any]:
        local_only(request)
        file = relative_path(root(), path)
        mine = {v.id for v in history().versions(file)}
        if version not in mine or (against is not None and against not in mine):
            raise _bad("that version is not one of this note's", 404)
        return {
            "diff": history().diff(file, version, against),
            "text": history().text(version),
        }

    @app.post("/api/note/restore")
    async def restore_note(request: Request) -> dict[str, Any]:
        local_only(request)
        data = await body(request)
        file = relative_path(root(), str(data.get("path", "")))
        try:
            version = int(data.get("version", 0))
        except (TypeError, ValueError) as exc:
            raise _bad("give the version to put back") from exc
        with writing:
            try:
                before = history().restore(file, version)
            except ToolError as exc:
                raise _bad(str(exc), 404) from exc
            except OSError as exc:
                raise _bad(f"could not put {file.name} back: {locked(exc)}", 409) from exc
        audit("notes.page.restore", path=str(file), version=version)
        return {"path": rel(file), "stamp": stamp(file), "before": before}
