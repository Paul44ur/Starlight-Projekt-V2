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
    GET    /api/notes/search?q=              notes with these words ("phrases", -word, tag:,
                                              path:), each with where the words are
    GET    /api/notes/resolve?name=&from=    the note a link [[name]] means (404: none yet)
    GET    /api/notes/embed?name=&source=&heading=  a note or a part of it, to show inside
    GET    /api/notes/facets                 tags (nested, with counts) and property names
    GET    /api/notes/templates?lang=        the note templates (shipped ones copied the first time)
    POST   /api/notes/today                  {day, lang}: that day's journal page (made if missing)
    POST   /api/notes/memo                   {text, to, day}: a voice memo's text, kept
    POST   /api/note/property                {path, name, value, stamp}: one property (null: off)
    POST   /api/notes/welcome                {lang}: the welcome note, the first time only
    GET    /api/notes/trash                  deleted notes
    POST   /api/notes/trash/restore          {path}: bring one back
    POST   /api/notes/pins                   {path, pinned}
    POST   /api/notes/pictures?name=         the picture's bytes as the body
    GET    /api/notes/file/{path}            a picture (an SVG only as a download)
    GET    /api/note?path=                   text, stamp, properties, outline, links, backlinks
    PUT    /api/note?path=                   {text, stamp, first}: save (409 on a conflict);
                                              typed dates in tasks become ISO dates, and
                                              then the saved text comes back
    POST   /api/note                         {folder, title, text | template, lang}: a new note
    GET    /api/note/export?path=&format=    the note as one HTML file or a PDF (show: print)
    POST   /api/note/task                    {path, line, expected | id, done}: tick a task
    GET    /api/note/related?path=           related notes (by meaning, or links and tags)
    GET    /api/note/mentions?path=          lines that name the note without a link
    POST   /api/note/link-mention            {path, source, line, expected}: link one
    GET    /api/notes/tasks                  open tasks: overdue, today, upcoming, none
    GET    /api/notes/kanban                 the task board: open, doing, done
    POST   /api/notes/kanban/move            {id, to, path?}: a card to another column
    GET    /api/notes/flashcards             the flashcard decks: cards, due, new today
    GET    /api/notes/flashcards/due?deck=   the cards to study now, with their answers
    POST   /api/notes/flashcards/grade       {id, grade}: again, hard, good or easy
    POST   /api/notes/query                  {text}: a ```query block's table
    GET    /api/boards                       the .canvas boards in the Starlight folder
    GET    /api/board?path=                  a board: {board, stamp}
    PUT    /api/board?path=                  {board, stamp, first}: save (409 on a conflict)
    POST   /api/board                        {folder, title}: a new, empty board
    GET    /api/notes/graph?around=&depth=   the notes and their links (around one note;
                                              folder=, tag=, memories=true, limit=)
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
from starlight.notebook import (
    NoteConflict,
    boards,
    graph,
    markdown,
    query,
    related,
    search,
    stamp,
    tasks,
    templates,
    write_note,
)
from starlight.notebook.rename import plan as plan_rename
from starlight.notebook.rename import rename as carry_out_rename
from starlight.notebook.write import MISSING

FOLDERS = ("Notes", "Journal", "Recipes", "Clippings", "Inbox")  # always shown, in this order
ATTACHMENTS = "Notes/Attachments"
MAX_NOTE = 1024**2  # bigger than this is a document, not a note
MAX_PICTURE = 10 * 1024**2
KANBAN_DONE = 30  # the done column shows the last ones finished
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
        hits = search.search(found, q, max(1, min(limit, 100)))
        return {"hits": [h.public() for h in hits]}  # (with where the words are: marks)

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

    @app.get("/api/notes/facets")
    def facets(request: Request) -> dict[str, Any]:
        local_only(request)
        found = index()
        found.refresh()
        names = [{"name": name, "count": count} for name, count in found.property_names()]
        return {"tags": found.tag_tree(), "properties": names}

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
        lines = text.splitlines()
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
            # each task with its line as written: ticking one sends that line back
            "tasks": [
                {"line": t.line, "status": t.status, "text": t.text, "raw": lines[t.line]}
                for t in note.tasks
            ],
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
        sent = text
        with writing:
            try:
                if file.is_file():  # typed dates ("due: tomorrow") become ISO dates
                    from starlight.text import decode_text

                    before = decode_text(file.read_bytes())
                    text = markdown.iso_dates(text, dt.date.today(), before=before)
                    text = tasks.with_next_ones(text, before, dt.date.today())  # (repeating)
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
                        "yours": sent,
                    },
                ) from exc
            except OSError as exc:
                raise _bad(f"could not save {file.name}: {locked(exc)}", 409) from exc
        saved = {"path": rel(file), "stamp": written.stamp, "version": written.version}
        return {**saved, "text": text} if text != sent else saved  # (the page shows the dates)

    @app.get("/api/note/export")
    def export_note(
        request: Request, path: str, format: str = "html", show: bool = False, lang: str = ""
    ) -> Response:
        """The note as one HTML file that works on its own (``show``: opened to print) or as
        a PDF (format=pdf), to keep or to send."""
        local_only(request)
        file = note_file(path)
        from starlight.notebook import export
        from starlight.text import decode_text
        from starlight.web.export import note_html

        text = decode_text(file.read_bytes())
        front = markdown.split(text)
        when = (
            rel(file).rpartition("/")[0]
            + " · "
            + dt.datetime.fromtimestamp(file.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        )
        if format == "pdf":
            try:
                data = export.to_pdf(file.stem, front.body, root(), when=when)
            except export.ExportError as exc:
                raise _bad(str(exc), 501) from exc
            return download(data, file.stem, ".pdf", "application/pdf", show)
        shown = " · ".join(
            f"{k}: {', '.join(map(str, v)) if isinstance(v, list) else v}"
            for k, v in front.properties.items() if k not in ("aliases", "alias")
        )  # fmt: skip
        page_html = note_html(
            file.stem, front.body, when=when, properties=shown,
            pictures=export.pictures(root(), front.body), print_now=show,
            german=lang.lower().startswith("de"),
        )  # fmt: skip
        return download(page_html.encode("utf-8"), file.stem, ".html", "text/html", show)

    def download(data: bytes, title: str, suffix: str, media: str, show: bool) -> Response:
        """A file to save (or, ``show``, to open), under the note's name."""
        import unicodedata
        from urllib.parse import quote

        name = re.sub(r"[^\w\- ]+", "", title).strip()[:60] or "note"
        decomposed = unicodedata.normalize("NFKD", name.replace("ß", "ss"))
        plain = re.sub(r"[^\w\- ]+", "", decomposed, flags=re.ASCII).strip() or "note"
        charset = "; charset=utf-8" if media.startswith("text/") else ""
        return Response(
            data,
            media_type=media + charset,
            headers={
                "Content-Disposition": f"{'inline' if show else 'attachment'}; "
                f"filename=\"{plain}{suffix}\"; filename*=UTF-8''{quote(name + suffix)}"
            },
        )

    @app.post("/api/note/task")
    async def tick_task(request: Request) -> dict[str, Any]:
        """Tick a task in its note, or open it again: by its line and the line as the page
        shows it (the reading view), or by its id (the Tasks view). Refused (409) when the
        task changed meanwhile."""
        local_only(request)
        data = await body(request)
        file = note_file(str(data.get("path") or ""))
        done = data.get("done", True) is not False
        line, expected, task_id = data.get("line"), data.get("expected"), data.get("id")
        with writing:
            try:
                if isinstance(task_id, str) and task_id:
                    ticked = tasks.tick_by_id(
                        file, rel(file), task_id, history(), done=done, reason="notes.page task"
                    )
                elif isinstance(line, int) and isinstance(expected, str):
                    ticked = tasks.tick(
                        file, history(), line=line, expected=expected, done=done,
                        reason="notes.page task",
                    )  # fmt: skip
                else:
                    raise _bad("send {path, line, expected, done} or {path, id, done}")
            except NoteConflict as exc:
                raise _bad(str(exc), 409) from exc
            except OSError as exc:
                raise _bad(f"could not save {file.name}: {locked(exc)}", 409) from exc
        return {
            "path": rel(file),
            "stamp": stamp(file),
            "done": done,
            "changed": ticked.written is not None,
            "repeated": ticked.repeated,
            "next_due": ticked.next_due,
        }

    @app.get("/api/note/related")
    async def related_notes(request: Request, path: str) -> dict[str, Any]:
        """Notes like this one: by meaning (the knowledge base's docs.similar, when it has
        read the note), else by links and shared tags."""
        local_only(request)
        file = note_file(path)
        found = index()
        found.refresh()
        relative = rel(file)
        answer = await kernel().dispatcher.call("docs.similar", {"path": str(file), "limit": 15})
        notes = related.by_meaning(found, relative, answer.output) if answer.ok else []
        if notes:
            return {"related": notes, "by": "meaning"}
        return {"related": related.by_structure(found, relative), "by": "links and tags"}

    @app.get("/api/note/mentions")
    def unlinked_mentions(request: Request, path: str) -> dict[str, Any]:
        """The lines of other notes that name this one without linking to it."""
        local_only(request)
        file = note_file(path)
        found = index()
        found.refresh()
        return {"mentions": [m.public() for m in related.mentions(found, rel(file))]}

    @app.post("/api/note/link-mention")
    async def link_mention(request: Request) -> dict[str, Any]:
        """Turn one mention into a link: {path: the note named, source: the note that names
        it, line, expected: the line as shown}. 409 when the line changed."""
        local_only(request)
        data = await body(request)
        target = note_file(str(data.get("path") or ""))
        source = note_file(str(data.get("source") or ""))
        line, expected = data.get("line"), data.get("expected")
        if not isinstance(line, int) or not isinstance(expected, str):
            raise _bad("send {path, source, line, expected}")
        with writing:
            try:
                written = related.link_mention(
                    index(), history(), source=rel(source), line=line, expected=expected,
                    target=rel(target),
                )  # fmt: skip
            except NoteConflict as exc:
                raise _bad(str(exc), 409) from exc
            except OSError as exc:
                raise _bad(f"could not save {source.name}: {locked(exc)}", 409) from exc
        return {"path": rel(source), "stamp": written.stamp, "version": written.version}

    # -- boards (block F3) -----------------------------------------------------------------

    def board_file(text: str) -> Path:
        file = relative_path(root(), text, (boards.SUFFIX,))
        if not file.is_file():
            raise _bad(f"there is no board {text}", 404)
        return file

    @app.get("/api/boards")
    def list_boards(request: Request) -> dict[str, Any]:
        local_only(request)
        found = []
        for file in boards.find(root()):
            info = file.stat()
            found.append({"path": rel(file), "title": file.stem, "modified": info.st_mtime})
        return {"boards": found}

    @app.get("/api/board")
    def read_board(request: Request, path: str) -> dict[str, Any]:
        local_only(request)
        file = board_file(path)
        from starlight.text import decode_text

        current = stamp(file)
        if file.stat().st_size > boards.MAX_BYTES:
            raise _bad("this board is over 5 MB; open it in another program", 413)
        try:
            board = boards.load(decode_text(file.read_bytes()))
        except boards.BoardError as exc:
            raise _bad(str(exc), 422) from exc
        data = {"nodes": board.nodes, "edges": board.edges, **board.other}
        return {"path": rel(file), "title": file.stem, "stamp": current, "board": data}

    @app.put("/api/board")
    async def save_board(request: Request, path: str) -> dict[str, Any]:
        """{board, stamp, first}: the whole board, checked, saved with a version first;
        409 when it changed elsewhere since ``stamp``."""
        local_only(request)
        data = await body(request)
        expected = data.get("stamp")
        if not isinstance(expected, str):
            raise _bad("send {board, stamp}: the board and the stamp it was read with")
        file = relative_path(root(), path, (boards.SUFFIX,))
        try:
            text = boards.dump(boards.check(data.get("board")))
        except boards.BoardError as exc:
            raise _bad(str(exc), 422) from exc
        if len(text.encode("utf-8")) > boards.MAX_BYTES:
            raise _bad("a board can be up to 5 MB", 413)
        with writing:
            try:
                gap = 0.0 if data.get("first") else TYPING_GAP
                written = write_note(
                    file, text, "notes.page board", history(), expected=expected, min_gap=gap
                )
            except NoteConflict as exc:
                raise HTTPException(
                    status_code=409, detail={"error": str(exc), "stamp": stamp(file)}
                ) from exc
            except OSError as exc:
                raise _bad(f"could not save {file.name}: {locked(exc)}", 409) from exc
        return {"path": rel(file), "stamp": written.stamp, "version": written.version}

    @app.post("/api/board")
    async def new_board(request: Request) -> dict[str, Any]:
        """{folder, title}: a new, empty board (never over an existing one)."""
        local_only(request)
        data = await body(request)
        title = clean_name(str(data.get("title") or ""), 80) or "Board"
        base = relative_path(root(), str(data.get("folder") or "Boards"), None)
        with writing:
            file, number = base / f"{title}{boards.SUFFIX}", 2
            while file.exists():
                file, number = base / f"{title} {number}{boards.SUFFIX}", number + 1
            relative_path(root(), rel(file), (boards.SUFFIX,))
            base.mkdir(parents=True, exist_ok=True)
            written = write_note(file, boards.dump(boards.Board()), "notes.page board", history(),
                                 expected=MISSING)  # fmt: skip
        return {"path": rel(file), "title": file.stem, "stamp": written.stamp}

    @app.get("/api/notes/graph")
    def notes_graph(
        request: Request,
        around: str = "",
        depth: int = 1,
        limit: int = graph.LIMIT,
        folder: str = "",
        tag: str = "",
        memories: bool = False,
    ) -> dict[str, Any]:
        """The notes as points and their links as lines: all of them, or ``around`` one note
        (``depth`` links away); only a ``folder`` or a ``tag``; with Starlight's
        ``memories`` too. At most ``limit`` points."""
        local_only(request)
        found = index()
        found.refresh()
        centre = rel(note_file(around)) if around else None
        remembered = None
        memory = getattr(kernel(), "memory", None)
        if memories and memory is not None:
            remembered = [(e.id, e.topic, e.content) for e in memory.store.all() if not e.expired()]
        return graph.graph(found, centre, depth, limit, folder=folder, tag=tag, memories=remembered)

    @app.post("/api/notes/query")
    async def run_query(request: Request) -> dict[str, Any]:
        """A ```query block's table: {text: the block's lines}. 400 with the reason in plain
        words when it cannot be read."""
        local_only(request)
        data = await body(request)
        text = str(data.get("text") or "")
        try:
            asked = query.parse_block(text)
            found = index()
            found.refresh()
            return query.run(found, asked["where"], asked["sort"], asked["fields"], asked["limit"])
        except query.QueryError as exc:
            raise _bad(str(exc)) from exc

    @app.get("/api/notes/kanban")
    async def kanban(request: Request) -> dict[str, Any]:
        """The task board (block G2): the tasks in notes and the to-do list's own, in the
        columns open, doing and done (done: the last ones finished)."""
        local_only(request)
        found = index()
        columns: dict[str, list[dict[str, Any]]] = {"open": [], "doing": [], "done": []}
        rank = {"high": 0, "low": 2}
        finished = []
        for task in tasks.note_tasks(found):
            card = {**task.view(), "line": task.line, "kind": "note", "finished": task.done}
            if task.finished:
                finished.append(card)
            elif task.open:
                columns["doing" if task.stage == "doing" else "open"].append(card)
        listed = await kernel().dispatcher.call("todo.list", {"include_done": True})
        if listed.ok:
            for item in listed.output.get("tasks", []):
                if "path" in item:
                    continue  # (a task in a note: it is there already)
                card = {**item, "kind": "todo"}
                if item.get("done"):
                    finished.append(card)
                else:
                    columns["doing" if item.get("stage") == "doing" else "open"].append(card)

        def order(card: dict[str, Any]) -> tuple[str, int, str]:
            return (card.get("due") or "~", rank.get(card.get("priority", ""), 1),
                    card["text"].casefold())  # fmt: skip

        for name in ("open", "doing"):
            columns[name].sort(key=order)
        finished.sort(key=lambda c: c.get("finished") or "", reverse=True)
        columns["done"] = finished[:KANBAN_DONE]
        return {**columns, "todo": listed.ok}

    @app.post("/api/notes/kanban/move")
    async def kanban_move(request: Request) -> dict[str, Any]:
        """{id, to, path?}: a card to another column (open, doing, done). A task in a note
        (with its path) changes in the note; a to-do through the to-do list."""
        local_only(request)
        data = await body(request)
        task_id, to = str(data.get("id") or ""), str(data.get("to") or "")
        if to not in tasks.STAGES or not task_id:
            raise _bad("send {id, to: open, doing or done}")
        if data.get("path"):
            file = note_file(str(data["path"]))
            with writing:
                try:
                    moved = tasks.move(file, rel(file), task_id, to, history(),
                                       reason="notes.page kanban")  # fmt: skip
                except NoteConflict as exc:
                    raise _bad(str(exc), 409) from exc
            return {"moved": to, "changed": moved.written is not None}
        result = await kernel().dispatcher.call("todo.move", {"id": task_id, "to": to})
        if not result.ok:
            raise _bad(str(result.error), 409)
        return {"moved": to, "changed": True}

    # -- flashcards (block G3): through the flashcards module, so its settings count -------

    async def cards_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        tool = f"flashcards.{name}"
        if kernel().dispatcher.get(tool) is None:
            raise _bad("the flashcards module is off; turn it on in Settings", 503)
        result = await kernel().dispatcher.call(tool, args)
        if not result.ok:
            raise _bad(str(result.error), 400)
        return dict(result.output)

    @app.get("/api/notes/flashcards")
    async def card_decks(request: Request) -> dict[str, Any]:
        """The flashcard decks: cards, due today, new today."""
        local_only(request)
        return await cards_tool("decks", {})

    @app.get("/api/notes/flashcards/due")
    async def cards_due(request: Request, deck: str = "", limit: int = 200) -> dict[str, Any]:
        """The cards to study now (due first, then new), with their answers."""
        local_only(request)
        return await cards_tool("due", {"deck": deck, "limit": max(1, min(limit, 500))})

    @app.post("/api/notes/flashcards/grade")
    async def cards_grade(request: Request) -> dict[str, Any]:
        """{id, grade}: how well a card was known (again, hard, good, easy)."""
        local_only(request)
        data = await body(request)
        card, grade = str(data.get("id") or ""), str(data.get("grade") or "")
        if not card or not grade:
            raise _bad("send {id, grade: again, hard, good or easy}")
        return await cards_tool("grade", {"id": card, "grade": grade})

    @app.get("/api/notes/tasks")
    def all_tasks(request: Request) -> dict[str, Any]:
        """The open tasks in all notes: overdue, today, upcoming and without a date."""
        local_only(request)
        today = dt.date.today().isoformat()
        groups: dict[str, list[dict[str, Any]]] = {
            "overdue": [], "today": [], "upcoming": [], "none": [],
        }  # fmt: skip
        rank = {"high": 0, "low": 2}
        found = [t for t in tasks.note_tasks(index()) if t.open]
        for task in sorted(found, key=lambda t: (t.due or "~", rank.get(t.priority, 1))):
            group = (
                "none" if not task.due else "overdue" if task.due < today
                else "today" if task.due == today else "upcoming"
            )  # fmt: skip
            groups[group].append({**task.view(), "line": task.line})
        return {"date": today, "count": len(found), **groups}

    @app.post("/api/note")
    async def new_note(request: Request) -> dict[str, Any]:
        local_only(request)
        data = await body(request)
        folder = str(data.get("folder") or "Notes")
        title = clean_name(str(data.get("title") or ""), 80) or "Untitled"
        text = str(data.get("text") or "")
        base = relative_path(root(), folder, None)
        if data.get("template"):  # a new note from a template, its variables filled in
            lang = str(data.get("lang") or "")
            source = templates.find(root(), str(data["template"]), lang)
            if source is None:
                raise _bad(f"there is no template {data['template']}", 404)
            text = await templates.render(
                source.read_text("utf-8", errors="replace"),
                title=title,
                lang=lang,
                call=tool_output,
            )
        with writing:
            file, number = base / f"{title}.md", 2
            while file.exists():
                file, number = base / f"{title} {number}.md", number + 1
            relative_path(root(), rel(file))  # (the name is allowed)
            base.mkdir(parents=True, exist_ok=True)
            written = write_note(file, text, "notes.page", history(), expected=MISSING)
        return {"path": rel(file), "title": file.stem, "stamp": written.stamp}

    async def tool_output(tool: str, args: dict[str, Any]) -> Any:
        """A module's answer for a template ({{events}} ...); None when it is off."""
        k = kernel()
        if tool.split(".")[0] not in templates.FROM_MODULES_SAFE:
            return None
        result = await k.dispatcher.call(tool, args)
        return result.output if result.ok else None

    @app.get("/api/notes/templates")
    def note_templates(request: Request, lang: str = "") -> dict[str, Any]:
        local_only(request)
        return {"templates": templates.names(root(), lang)}

    @app.post("/api/notes/today")
    async def today(request: Request) -> dict[str, Any]:
        """A day's journal page (today's if no day is given), made from the Daily template by
        the journal module if it is not there yet."""
        local_only(request)
        data = await body(request)
        templates.ensure(root(), str(data.get("lang") or ""))
        result = await kernel().dispatcher.call(
            "journal.today", {"day": str(data.get("day") or "")}
        )
        if not result.ok:
            raise _bad(str(result.error or "the journal module is off (Settings)"), 409)
        file = Path(str(result.output["file"]))
        index().touch(file)
        return {"path": rel(file), "new": bool(result.output.get("new"))}

    @app.post("/api/notes/memo")
    async def memo(request: Request) -> dict[str, Any]:
        """A voice memo's text (the page recorded it and /api/transcribe wrote it down): into
        the inbox, or a journal day ({to: "journal", day})."""
        local_only(request)
        data = await body(request)
        text = str(data.get("text") or "").strip()[:4000]
        if not text:
            raise _bad("nothing was understood")
        journal = data.get("to") == "journal"
        tool, args = (
            ("journal.write", {"text": f"🎤 {text}", "day": str(data.get("day") or "")})
            if journal
            else ("notes.note", {"text": f"🎤 {text}"})
        )
        result = await kernel().dispatcher.call(tool, args)
        if not result.ok:
            raise _bad(str(result.error), 409)
        file = Path(str(result.output.get("saved") or result.output.get("file") or ""))
        if file.is_file():
            index().touch(file)
        return {"kept_in": "journal" if journal else "inbox", "text": text}

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

    @app.post("/api/note/property")
    async def note_property(request: Request) -> dict[str, Any]:
        """Set (or, with value null, remove) one property; only its own lines change. The
        stamp the page read the note with must still match (409 otherwise)."""
        local_only(request)
        data = await body(request)
        file = note_file(str(data.get("path", "")))
        name, value, expected = (
            str(data.get("name") or "").strip(),
            data.get("value"),
            data.get("stamp"),
        )
        if not isinstance(expected, str):
            raise _bad("send the stamp the note was read with")
        if not isinstance(value, (str, int, float, bool, list, type(None))):
            raise _bad("a property is text, a number, yes/no, a date or a list")
        from starlight.text import decode_text

        with writing:
            text = decode_text(file.read_bytes())
            try:
                if value is None:
                    new = markdown.remove_property(text, name)
                else:
                    if isinstance(value, list):
                        value = [str(v).strip() for v in value if str(v).strip()]
                    new = markdown.set_property(text, name, value)
                written = write_note(file, new, "notes.page property", history(), expected=expected)
            except ValueError as exc:
                raise _bad(str(exc)) from exc
            except NoteConflict as exc:
                raise _bad(str(exc), 409) from exc
            except OSError as exc:
                raise _bad(f"could not save {file.name}: {locked(exc)}", 409) from exc
        return {"stamp": written.stamp, "properties": markdown.split(new).properties}

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
