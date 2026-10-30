"""The web chat: a FastAPI app with the live assistant behind it.

It listens on this PC only (127.0.0.1). A browser connects over a WebSocket; answers are
streamed token by token, and when Starlight wants to run something that needs your OK the
question appears with Approve / Deny buttons. The same permissions and kill switch as the
terminal apply.

Pages: the chat (with saved conversations and file drop), settings (permission rules,
modules, paired devices) and memory & skills.

From your phone (through Tailscale, see starlight/web/remote.py) every request must come
from a paired device, and some things stay on the PC: changing permission rules, modules,
devices and skills, and releasing the kill switch.
"""

from __future__ import annotations

import asyncio
import contextlib
import email.policy
import html
import json
import mimetypes
import re
import time
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from starlight.core import AgentResult, Event, Kernel, Paths, ToolError
from starlight.core.activity import read_activity
from starlight.core.config import RemoteConfig, load_config
from starlight.core.dispatcher import tool_sink
from starlight.core.model import thought_sink
from starlight.core.modelpause import read_pause, resume_model
from starlight.core.modules import outside_warning
from starlight.core.output import describe_output_folder
from starlight.core.permissions import Permission, extra_blocked, extra_confirm, save_rule
from starlight.core.ui import current_ui
from starlight.web.auth import COOKIE, Device, Devices

STATIC = Path(__file__).with_name("static")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
LOOPBACK_PEERS = {"127.0.0.1", "::1"}
MAX_UNPACKED = 200 * 1024**2  # a Word/Excel file may unpack to at most this much
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",  # no other page may show the chat in a frame (clickjacking)
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
FORWARDED = ("x-forwarded-for", "x-forwarded-host", "forwarded", "tailscale-user-login")
MAX_UPLOAD = 20 * 1024**2
UPLOAD_KEEP = 3600  # seconds an uploaded file waits to be sent with a message
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
TEXTS = {".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".html", ".htm"}


def _hostname(value: str) -> str:
    return (urlsplit(f"//{value}").hostname or "").lower()


def allowed_request(headers: Any, peer: str | None) -> bool:
    """Only this machine's own chat page (or a non-browser client such as the terminal) may
    talk to the backend as the local user. Browsers let any web page open a WebSocket to
    localhost or send it a plain POST, so a page you visit could otherwise ask Starlight to
    do things and approve its own confirmations. The Host must be this machine (stops
    DNS-rebinding tricks), and an Origin, which every browser sends, must be that same host.
    The connection itself must come from this PC, and a request that came through a proxy
    (Tailscale) is never local. Pages are never shown inside another site's frame."""
    if peer not in LOOPBACK_PEERS:
        return False
    if any(name in headers for name in FORWARDED):
        return False
    host = _hostname(headers.get("host", ""))
    if host not in LOOPBACK:
        return False
    origin = headers.get("origin")
    if not origin:
        return True  # not a browser (the terminal client, curl, tests)
    parts = urlsplit(origin)
    return parts.scheme in ("http", "https") and parts.netloc == headers.get("host")


@dataclass
class Viewer:
    """Who is asking: the local user, or a paired device from elsewhere."""

    local: bool
    device: Device | None = None


class Gate:
    """Decides, for each request, whether it is the local user, a paired device, or nobody."""

    def __init__(self, devices: Devices, remote: RemoteConfig) -> None:
        self.devices = devices
        self.remote = remote

    def remote_host(self, headers: Any) -> str | None:
        """The address a request was sent to, if it is one of your Tailscale addresses."""
        if not self.remote.enabled:
            return None
        name = _hostname(headers.get("x-forwarded-host") or headers.get("host", ""))
        return name if name in {h.lower() for h in self.remote.hosts} else None

    @staticmethod
    def client(headers: Any, peer: str | None) -> str:
        """Who is knocking, for counting wrong pairing codes: the Tailscale user, else the
        address Tailscale saw (it replaces any X-Forwarded-For a client sends)."""
        login = headers.get("tailscale-user-login", "").strip()
        if login:
            return f"user:{login}"
        forwarded = headers.get("x-forwarded-for", "")
        return forwarded.split(",")[-1].strip() or peer or "?"

    def viewer(self, headers: Any, cookies: dict[str, str], peer: str | None) -> Viewer | None:
        if allowed_request(headers, peer):
            return Viewer(local=True)
        host = self.remote_host(headers)
        if host is None:
            return None
        origin = headers.get("origin")
        if origin and _hostname(urlsplit(origin).netloc) != host:
            return None  # another site's page
        device = self.devices.check(cookies.get(COOKIE), self.client(headers, peer))
        return Viewer(local=False, device=device) if device else None


class WebUI:
    """Carries confirmations, questions and notices to one browser over its socket."""

    def __init__(self, send: Any) -> None:
        self._send = send
        self._pending: dict[str, asyncio.Future[str]] = {}

    def resolve(self, reply_id: str, value: str) -> None:
        future = self._pending.get(reply_id)
        if future is not None and not future.done():
            future.set_result(value)

    def cancel_all(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.cancel()

    async def _ask_browser(self, payload: dict[str, Any]) -> str:
        reply_id = uuid.uuid4().hex
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[reply_id] = future
        await self._send({**payload, "id": reply_id})
        try:
            return await future
        finally:
            self._pending.pop(reply_id, None)

    async def confirm(self, question: str) -> bool:
        return await self._ask_browser({"type": "confirm", "question": question}) == "yes"

    async def ask(self, question: str, secret: bool = False) -> str | None:
        answer = await self._ask_browser({"type": "prompt", "question": question, "secret": secret})
        return answer.strip() or None

    async def notify(self, text: str) -> None:
        await self._send({"type": "notify", "text": text})


@dataclass
class Upload:
    name: str
    data: bytes
    added: float
    text: str | None = None  # a document's text, read once when it was added


def extract_text(name: str, data: bytes) -> str:
    """Text of a document dropped into the chat (text, HTML, PDF, Word, Excel)."""
    import tempfile

    suffix = Path(name).suffix.lower()
    if suffix in TEXTS:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:  # older Windows files and German Excel CSVs
            text = data.decode("cp1252", errors="replace")
        if suffix in {".html", ".htm"}:
            from starlight.text import html_to_text

            text = html_to_text(text).text
        return text
    if suffix not in {".pdf", ".docx", ".xlsx"}:
        raise ValueError(f"{name}: only pictures, text, PDF, Word and Excel files can be added")
    if suffix in {".docx", ".xlsx"}:
        import io
        import zipfile

        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                unpacked = sum(info.file_size for info in archive.infolist())
        except zipfile.BadZipFile as exc:
            raise ValueError(f"{name} is not a valid {suffix} file") from exc
        if unpacked > MAX_UNPACKED:
            raise ValueError(f"{name} unpacks to more than 200 MB; it can't be added")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"upload{suffix}"
        path.write_bytes(data)
        try:
            if suffix == ".pdf":
                from pypdf import PdfReader

                return "\n\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)
            if suffix == ".docx":
                import docx

                return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
            import openpyxl

            book = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            try:
                return "\n".join(
                    " | ".join("" if v is None else str(v) for v in row)
                    for sheet in book.worksheets
                    for row in sheet.iter_rows(values_only=True, max_row=2000)
                )
            finally:
                book.close()
        except ImportError as exc:
            extra = "pdf" if suffix == ".pdf" else "office"
            raise ValueError(f"reading {suffix} files needs the '{extra}' extra") from exc


def parse_form(content_type: str, data: bytes) -> tuple[dict[str, str], list[tuple[str, bytes]]]:
    """A posted form (multipart or urlencoded) -> (its text fields, its files as (name,
    bytes)). The standard library's email parser reads multipart without extra packages."""
    if content_type.startswith("application/x-www-form-urlencoded"):
        return {k: v for k, v in parse_qsl(data.decode("utf-8", "replace"))}, []
    if not content_type.startswith("multipart/form-data"):
        return {}, []
    head = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    message = BytesParser(policy=email.policy.HTTP).parsebytes(head + data)
    fields: dict[str, str] = {}
    files: list[tuple[str, bytes]] = []
    for part in message.iter_parts() if message.is_multipart() else []:
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        if not isinstance(name, str) or not isinstance(payload, bytes):
            continue
        filename = part.get_filename()
        if filename:
            if payload:
                files.append((filename, payload))
        else:
            fields[name] = payload.decode(part.get_content_charset() or "utf-8", "replace")
    return fields, files


def first_sentence(text: str, limit: int = 160) -> str:
    """The first sentence of a tool's description (they wrap over several lines), cut to
    ``limit`` characters."""
    paragraph = " ".join(text.strip().split("\n\n", 1)[0].split())
    found = re.match(r"(.+?[.!?])(?=\s+[A-Z(\"']|$)", paragraph)  # not "e.g. the"
    sentence = found.group(1) if found else paragraph
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"


def create_app(paths: Paths | None = None) -> Any:
    paths = paths or Paths.discover()
    state: dict[str, Any] = {}
    clients: dict[WebUI, Callable[[dict[str, Any]], Awaitable[None]]] = {}
    uploads: dict[str, Upload] = {}

    async def broadcast(event: Event) -> None:
        """Reminders, alerts and briefings appear in every open chat page."""
        for web_ui, send in list(clients.items()):
            if current_ui.get() is web_ui:
                continue  # asked from this page: it already got the message
            with contextlib.suppress(Exception):
                await send({"type": "notify", "text": str(event.data.get("text", ""))})

    async def lifespan(app: Any) -> Any:
        kernel = Kernel(paths, hotkey=True)
        await kernel.start()
        kernel.bus.subscribe("deliver", broadcast, owner="web", background=True)
        state["kernel"] = kernel
        app.state.kernel = kernel  # for tests and tooling
        try:
            yield
        finally:
            await kernel.stop()

    app = FastAPI(title="Starlight chat", docs_url=None, redoc_url=None, lifespan=lifespan)
    config = load_config(paths)
    devices = Devices(paths.data / "devices")
    gate = Gate(devices, config.web.remote)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    def kernel() -> Kernel:
        found: Kernel = state["kernel"]
        return found

    def viewer(request: Request) -> Viewer:
        peer = request.client.host if request.client else None
        found = gate.viewer(request.headers, dict(request.cookies), peer)
        if found is None:
            raise HTTPException(status_code=403, detail="pair this device first")
        return found

    def local_only(request: Request) -> Viewer:
        found = viewer(request)
        if not found.local:
            raise HTTPException(status_code=403, detail="this can only be changed on the PC")
        return found

    async def body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="send JSON") from exc
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="send a JSON object")
        return data

    def page(name: str) -> str:
        return (STATIC / name).read_text(encoding="utf-8")

    # -- pages ---------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Any:
        peer = request.client.host if request.client else None
        if gate.viewer(request.headers, dict(request.cookies), peer) is None:
            if gate.remote_host(request.headers):
                return RedirectResponse("/pair", status_code=303)
            raise HTTPException(status_code=403, detail="not available here")
        return page("chat.html")

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request) -> str:
        viewer(request)
        return page("settings.html")

    @app.get("/memory", response_class=HTMLResponse)
    def memory_page(request: Request) -> str:
        viewer(request)
        return page("memory.html")

    @app.get("/docs/file")
    def knowledge_file(path: str, request: Request) -> Response:
        """A file from your knowledge base (docs module), so a source in an answer opens
        in the browser (a PDF at its page). Only files the knowledge base indexed."""
        viewer(request)
        record = kernel().modules.records.get("docs")
        docs = record.instance if record is not None else None
        indexed = getattr(docs, "indexed", None)
        if not callable(indexed) or not indexed(path):
            raise HTTPException(status_code=404, detail="not in your knowledge base")
        file = Path(path)
        if not file.is_file():
            raise HTTPException(status_code=404, detail="the file is gone")
        media = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        inline = media in ("application/pdf", "text/plain") or media.startswith("image/")
        return FileResponse(
            file,
            media_type=media,
            filename=file.name,
            content_disposition_type="inline" if inline else "attachment",
        )

    @app.get("/activity", response_class=HTMLResponse)
    def activity_page(request: Request) -> str:
        viewer(request)
        return page("activity.html")

    @app.get("/automations", response_class=HTMLResponse)
    def automations_page(request: Request) -> str:
        viewer(request)
        return page("automations.html")

    @app.get("/pair", response_class=HTMLResponse)
    def pair_page(request: Request) -> Any:
        if gate.remote_host(request.headers) is None:
            return RedirectResponse("/", status_code=303)
        return page("pair.html")

    @app.get("/manifest.webmanifest")
    def manifest() -> Response:
        return Response(page("manifest.webmanifest"), media_type="application/manifest+json")

    @app.get("/style.css")
    def style() -> Response:
        return Response(page("style.css"), media_type="text/css")

    @app.get("/common.js")
    def common() -> Response:
        return Response(page("common.js"), media_type="text/javascript")

    @app.get("/i18n.js")
    def i18n() -> Response:
        return Response(page("i18n.js"), media_type="text/javascript")

    @app.get("/markdown.js")
    def markdown() -> Response:
        return Response(page("markdown.js"), media_type="text/javascript")

    @app.get("/icon.svg")
    def icon() -> Response:
        return Response(page("icon.svg"), media_type="image/svg+xml")

    @app.get("/kill-icon.svg")
    def kill_icon() -> Response:
        return Response(page("kill-icon.svg"), media_type="image/svg+xml")

    @app.get("/kill.webmanifest")
    def kill_manifest() -> Response:
        return Response(page("kill.webmanifest"), media_type="application/manifest+json")

    @app.get("/kill", response_class=HTMLResponse)
    def kill_page(request: Request) -> Any:
        peer = request.client.host if request.client else None
        if gate.viewer(request.headers, dict(request.cookies), peer) is None:
            if gate.remote_host(request.headers):
                return RedirectResponse("/pair", status_code=303)
            raise HTTPException(status_code=403, detail="not available here")
        return page("kill.html")

    @app.get("/sw.js")
    def service_worker() -> Response:
        # Keeps the chat page's shell on the phone for when the PC is off (see sw.js).
        return Response(
            page("sw.js"), media_type="text/javascript", headers={"Cache-Control": "no-cache"}
        )

    # -- pairing and devices -------------------------------------------------------------

    @app.post("/api/pair")
    async def pair(request: Request) -> Response:
        host = gate.remote_host(request.headers)
        if host is None:
            raise HTTPException(status_code=404, detail="pairing is for your other devices")
        origin = request.headers.get("origin")
        if origin and _hostname(urlsplit(origin).netloc) != host:
            raise HTTPException(status_code=403, detail="wrong page")
        data = await body(request)
        client = gate.client(request.headers, request.client.host if request.client else None)
        name = str(data.get("name", "")).strip() or "Phone"
        token = devices.pair(str(data.get("code", "")), name, client)
        kernel().audit.record("web.pair", ok=token is not None, client=client)
        if token is None:
            raise HTTPException(status_code=403, detail="wrong or expired code (or too many tries)")
        await kernel().deliver(f"A new device was paired with Starlight: {name[:60]}", urgent=True)
        response = JSONResponse({"paired": True})
        response.set_cookie(
            COOKIE, token, max_age=365 * 86400, httponly=True, secure=True, samesite="strict"
        )
        return response

    @app.post("/api/pair/code")
    def pair_code(request: Request) -> dict[str, Any]:
        local_only(request)
        return {"code": devices.new_code(), "valid_seconds": 300}

    @app.get("/api/devices")
    def list_devices(request: Request) -> dict[str, Any]:
        local_only(request)
        remote = config.web.remote
        return {
            "devices": devices.list(),
            "remote": {"enabled": remote.enabled, "hosts": remote.hosts},
        }

    @app.delete("/api/devices/{device_id}")
    def revoke_device(device_id: str, request: Request) -> dict[str, Any]:
        local_only(request)
        if not devices.revoke(device_id):
            raise HTTPException(status_code=404, detail="no such device")
        kernel().audit.record("web.revoke", device=device_id)
        return {"revoked": device_id}

    # -- status and kill switch ------------------------------------------------------------

    @app.get("/api/status")
    async def status(request: Request) -> dict[str, Any]:
        who = viewer(request)
        k = kernel()
        access: dict[str, Any] = {}
        record = k.modules.records.get("access")
        if who.local and record is not None and record.instance is not None:
            # read directly: this page polls every few seconds, and going through the
            # dispatcher would write an audit entry and a statistic each time
            with contextlib.suppress(Exception):
                read_status = getattr(record.instance, "status", None)
                if callable(read_status):
                    access = read_status()
        pause = read_pause(paths)  # game mode: the model is off the graphics card
        waiting = 0
        queue = k.modules.records.get("approvals")
        if queue is not None and queue.instance is not None:
            waiting_list: Any = queue.instance
            with contextlib.suppress(Exception):  # (read directly too, like access above)
                waiting = len(waiting_list.items())
        return {
            "model": k.config.model.provider,
            "home": str(paths.root) if who.local else None,
            "saves_files": describe_output_folder(k.config.output.folder, paths.root),
            "kill": {"engaged": k.kill_switch.engaged, "reason": k.kill_switch.reason},
            "practice": k.policy.practice,
            "paused": None if pause is None else str(pause.get("reason") or "paused"),
            "access": access,
            "local": who.local,
            "device": who.device.name if who.device else None,
            "waiting": waiting,  # things in the approval queue
            "wake_url": config.web.remote.wake_url or None,  # for "Wake the PC" when it is off
        }

    @app.post("/api/kill")
    def kill(request: Request) -> dict[str, Any]:
        who = viewer(request)
        kernel().kill_switch.trigger("web /kill", source="web" if who.local else "web-remote")
        return {"engaged": True}

    @app.get("/api/activity")
    def activity(
        request: Request, kind: str = "", search: str = "", limit: int = 300
    ) -> dict[str, Any]:
        """What Starlight did (logs/audit.log in plain words), newest first."""
        viewer(request)
        return {"entries": read_activity(paths.logs, max(1, min(limit, 2000)), kind, search)}

    @app.post("/api/practice")
    async def practice(request: Request) -> dict[str, Any]:
        """Practice mode on or off. Off only at the PC: it makes actions real again."""
        on = (await body(request)).get("on") is True
        if not on:
            local_only(request)
        k = kernel()
        k.policy.practice = on
        k.audit.record("web.practice", on=on)
        return {"practice": on}

    @app.post("/api/resume")
    def resume(request: Request) -> dict[str, Any]:
        local_only(request)
        kernel().kill_switch.reset()
        return {"engaged": False}

    @app.post("/api/model/resume")
    def resume_paused_model(request: Request) -> dict[str, Any]:
        """End a game-mode pause: the launcher brings the model back."""
        viewer(request)
        resumed = resume_model(paths)
        if resumed:
            kernel().audit.record("model.resumed", source="web")
        return {"resumed": resumed}

    # -- conversations -------------------------------------------------------------------

    @app.get("/api/conversations")
    def conversations(request: Request, search: str = "", archived: bool = False) -> dict[str, Any]:
        viewer(request)
        store = kernel().conversations
        return {"current": store.current, "conversations": store.list(search, archived=archived)}

    @app.post("/api/conversations/{conversation}/branch")
    async def branch_conversation(conversation: str, request: Request) -> dict[str, Any]:
        """A new conversation with this one's first ``turns`` exchanges, to take it
        another way from there."""
        viewer(request)
        turns = (await body(request)).get("turns")
        if not isinstance(turns, int):
            raise HTTPException(status_code=400, detail="give turns: how many exchanges")
        try:
            return {"id": kernel().conversations.branch(conversation, turns)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="no such conversation or message") from exc

    @app.post("/api/conversations")
    async def new_conversation(request: Request) -> dict[str, Any]:
        """A new conversation; {"private": true}: never saved, nothing learned from it."""
        viewer(request)
        body: Any = {}
        with contextlib.suppress(ValueError):  # no body: an ordinary conversation
            body = await request.json()
        private = isinstance(body, dict) and body.get("private") is True
        return {"id": kernel().new_conversation(private=private), "private": private}

    @app.get("/api/conversations/{conversation}")
    def get_conversation(conversation: str, request: Request) -> dict[str, Any]:
        viewer(request)
        try:
            return kernel().conversations.get(conversation)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="no such conversation") from exc

    @app.get("/api/conversations/{conversation}/export")
    def export_conversation(
        conversation: str, request: Request, format: str = "md", show: bool = False
    ) -> Response:
        """The conversation as a Markdown file, or (format=html) a page that works on its
        own, to keep or to print as a PDF. ``show``: open it in the browser (and print)
        instead of downloading it."""
        viewer(request)
        store = kernel().conversations
        try:
            data = store.get(conversation)
            title = data["title"]
            text = store.markdown(conversation) if format != "html" else ""
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="no such conversation") from exc
        if format == "html":
            text = export_html(data, page("markdown.js"), print_now=show)
        name = re.sub(r"[^\w\- ]+", "", title).strip()[:60] or "conversation"
        ascii_name = unicodedata.normalize("NFKD", name)  # "ü" -> "u" for old browsers
        plain = re.sub(r"[^\w\- ]+", "", ascii_name, flags=re.ASCII).strip() or "conversation"
        suffix, media = (".html", "text/html") if format == "html" else (".md", "text/markdown")
        disposition = "inline" if show else "attachment"
        return Response(
            text,
            media_type=f"{media}; charset=utf-8",
            headers={
                "Content-Disposition": f'{disposition}; filename="{plain}{suffix}"; '
                f"filename*=UTF-8''{quote(name + suffix)}"
            },
        )

    @app.patch("/api/conversations/{conversation}")
    async def change_conversation(conversation: str, request: Request) -> dict[str, Any]:
        """Rename it ({"title"}), or pin, archive or file it ({"pinned", "archived",
        "folder"})."""
        viewer(request)
        data = await body(request)
        store = kernel().conversations
        changed: dict[str, Any] = {"id": conversation}
        try:
            if "title" in data:
                title = str(data.get("title") or "").strip()
                store.rename(conversation, title)
                changed["title"] = title
            flags = {k: data[k] for k in ("pinned", "archived") if isinstance(data.get(k), bool)}
            folder = data.get("folder")
            if flags or isinstance(folder, str):
                changed.update(
                    store.organize(
                        conversation, folder=folder if isinstance(folder, str) else None, **flags
                    )
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="no such conversation") from exc
        return changed

    @app.delete("/api/conversations/{conversation}")
    def delete_conversation(conversation: str, request: Request) -> dict[str, Any]:
        viewer(request)
        try:
            kernel().forget_conversation(conversation)
        except (KeyError, OSError) as exc:
            raise HTTPException(status_code=404, detail="no such conversation") from exc
        return {"deleted": conversation}

    # -- files dropped into the chat -------------------------------------------------------

    async def limited_body(request: Request) -> bytes:
        """The request body, refusing anything over 20 MB before it is all in memory."""
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_UPLOAD:
            raise HTTPException(status_code=413, detail="files can be up to 20 MB")
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_UPLOAD:
                raise HTTPException(status_code=413, detail="files can be up to 20 MB")
            chunks.append(chunk)
        return b"".join(chunks)

    @app.post("/api/upload")
    async def upload(request: Request) -> dict[str, Any]:
        viewer(request)
        name = Path(unquote(request.headers.get("x-filename", "file"))).name[:120] or "file"
        data = await limited_body(request)
        if not data:
            raise HTTPException(status_code=400, detail="the file is empty")
        return await keep_upload(name, data)

    async def keep_upload(name: str, data: bytes) -> dict[str, Any]:
        """Keep a file until the message it goes with is sent (its text, for a document)."""
        now = time.time()
        for key in [k for k, u in uploads.items() if now - u.added > UPLOAD_KEEP]:
            uploads.pop(key, None)
        while len(uploads) >= 20:
            uploads.pop(next(iter(uploads)))
        kind = "image" if Path(name).suffix.lower() in IMAGES else "document"
        text: str | None = None
        if kind == "document":
            try:
                text = await asyncio.to_thread(extract_text, name, data)
            except Exception as exc:
                raise HTTPException(status_code=415, detail=str(exc)) from exc
        upload_id = uuid.uuid4().hex
        uploads[upload_id] = Upload(name, b"" if text is not None else data, now, text)
        return {"id": upload_id, "name": name, "kind": kind, "size": len(data)}

    @app.post("/share")
    async def share(request: Request) -> Any:
        """The phone's share menu ("Share to Starlight"): a link, some text or a file
        lands in the chat box, ready to send with what you want done with it."""
        headers = dict(request.headers)
        if headers.get("origin") in (None, "null") and headers.get("sec-fetch-site") == "none":
            # The share menu: the browser itself sends this (no page did, so it has no
            # origin of its own). Another site's page cannot send "none", and never has the
            # pairing cookie (SameSite=Strict); everything else is checked as usual.
            headers.pop("origin", None)
        peer = request.client.host if request.client else None
        if gate.viewer(headers, dict(request.cookies), peer) is None:
            raise HTTPException(status_code=403, detail="pair this device first")
        data = await limited_body(request)
        fields, files = parse_form(request.headers.get("content-type", ""), data)
        kept = []
        for name, content in files[:5]:
            with contextlib.suppress(HTTPException):  # a file it cannot read is left out
                kept.append(await keep_upload(Path(name).name[:120] or "file", content))
        words = [fields.get(k, "").strip() for k in ("title", "text", "url")]
        text = "\n".join(dict.fromkeys(w for w in words if w))[:4000]
        query = {"share": text} if text else {}
        if kept:
            query["files"] = json.dumps([{"id": f["id"], "name": f["name"]} for f in kept])
        return RedirectResponse("/?" + urlencode(query), status_code=303)

    def attach(text: str, ids: list[Any]) -> tuple[str, list[bytes]]:
        images: list[bytes] = []
        parts = [text]
        for upload_id in ids[:10]:
            item = uploads.pop(str(upload_id), None)
            if item is None:
                continue
            if Path(item.name).suffix.lower() in IMAGES:
                images.append(item.data)
                continue
            content = item.text or ""
            parts.append(f"\n\n[Attached file: {item.name}]\n{content[:60000]}")
        return "".join(parts), images

    # -- voice ----------------------------------------------------------------------------

    def voice() -> Any:
        record = kernel().modules.records.get("voice")
        if record is None or record.instance is None:
            raise HTTPException(status_code=404, detail="the voice module is off")
        return record.instance

    @app.post("/api/transcribe")
    async def transcribe(request: Request) -> dict[str, Any]:
        viewer(request)
        audio = await limited_body(request)
        if not audio:
            raise HTTPException(status_code=400, detail="send a short WAV recording")
        try:
            text = await voice().transcribe_audio(audio)
        except ToolError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"text": text}

    @app.post("/api/speak")
    async def speak(request: Request) -> Response:
        viewer(request)
        data = await body(request)
        try:
            audio = await voice().synthesize_audio(
                str(data.get("text", "")), str(data.get("language", "")), str(data.get("voice", ""))
            )
        except ToolError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return Response(audio, media_type="audio/wav")

    @app.get("/api/voices")
    def voices(request: Request) -> dict[str, Any]:
        viewer(request)
        listed: dict[str, Any] = voice().voice_list()
        return listed

    @app.post("/api/voices/{action}")
    async def voice_setting(action: str, request: Request) -> dict[str, Any]:
        local_only(request)  # a download, a setting, the PC's microphone: at the PC
        data = await body(request)
        speech = voice()
        try:
            if action == "choose":
                done: dict[str, Any] = speech.choose_voice(str(data.get("name", "")))
            elif action == "install":
                done = await speech.install_voice(str(data.get("name", "")))
            elif action == "wake":
                done = await speech.wake_word(bool(data.get("on")))
            else:
                raise HTTPException(status_code=404, detail="choose, install or wake")
        except ToolError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        kernel().audit.record("web.voice", step=action, name=str(data.get("name", "")))
        return done

    # -- automations and the approval queue -------------------------------------------------

    def module_instance(name: str, label: str) -> Any:
        record = kernel().modules.records.get(name)
        if record is None or record.instance is None:
            raise HTTPException(status_code=404, detail=f"the {label} module is off")
        return record.instance

    @app.get("/api/automations")
    def automations(request: Request) -> dict[str, Any]:
        viewer(request)
        rules = module_instance("automations", "automations")
        return {
            "rules": rules.list_rules()["automations"],
            "templates": rules.template_rules(),
            "events": rules.events(),
            "tools": sorted(
                [
                    {
                        "name": t.qualified_name,
                        "tag": t.permission.value,
                        "description": first_sentence(t.description),
                    }
                    for t in kernel().dispatcher.tools()
                ],
                key=lambda t: t["name"],
            ),
        }

    @app.post("/api/automations/check")
    async def check_automation(request: Request) -> dict[str, Any]:
        local_only(request)
        rule = await body(request)
        try:
            checked: dict[str, Any] = module_instance("automations", "automations").check_rule(rule)
        except ToolError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return checked

    @app.post("/api/automations")
    async def save_automation(request: Request) -> dict[str, Any]:
        local_only(request)  # changing rules stays on the PC, like in the chat
        rule = await body(request)
        try:
            saved: dict[str, Any] = await module_instance(
                "automations", "automations"
            ).save_from_page(rule)
        except ToolError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        kernel().audit.record("web.automation", name=saved.get("saved"), rule=saved.get("rule"))
        return saved

    @app.post("/api/automations/{name}/{action}")
    async def automation_action(name: str, action: str, request: Request) -> dict[str, Any]:
        local_only(request)
        if action not in {"enable", "disable", "remove", "run"}:
            raise HTTPException(status_code=400, detail="enable, disable, remove or run")
        try:
            done: dict[str, Any] = await module_instance("automations", "automations").page_action(
                name, action
            )
        except ToolError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        kernel().audit.record("web.automation", name=name, step=action)
        return done

    @app.get("/api/approvals")
    def approvals(request: Request) -> dict[str, Any]:
        viewer(request)
        record = kernel().modules.records.get("approvals")
        if record is None or record.instance is None:
            return {"count": 0, "items": [], "off": True}
        queue: Any = record.instance
        listed: dict[str, Any] = queue.pending()
        return listed

    @app.post("/api/approvals/{item}")
    async def approval_action(item: str, request: Request) -> dict[str, Any]:
        who = viewer(request)
        action = str((await body(request)).get("action", ""))
        queue = module_instance("approvals", "approvals")
        if action == "dismiss":
            try:
                gone: dict[str, Any] = queue.dismiss(item)
            except ToolError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return gone
        if action != "approve":
            raise HTTPException(status_code=400, detail="approve or dismiss")
        # From the phone, what is blocked there stays blocked; the click is the yes.
        block_ctx = extra_blocked.set(() if who.local else tuple(config.web.remote.blocked))
        try:
            done: dict[str, Any] = await queue.run_item(item)
        except ToolError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            extra_blocked.reset(block_ctx)
        return done

    # -- settings: permissions and modules ---------------------------------------------------

    @app.get("/api/permissions")
    def permissions(request: Request) -> dict[str, Any]:
        viewer(request)
        k = kernel()
        tools = [
            {
                "name": t.qualified_name,
                "description": first_sentence(t.description),
                "tag": t.permission.value,
                "now": k.policy.resolve(t.qualified_name, t.permission).value,
            }
            for t in k.dispatcher.tools()
        ]
        now = time.time()
        return {
            "rules": k.policy.rules(),
            "tools": tools,
            "summary": k.policy.summary(
                (t.qualified_name, t.permission) for t in k.dispatcher.tools()
            ),
            "limits": dict(k.policy.config.limits),
            "timed": {
                p: max(1, round((until - now) / 60)) for p, until in k.policy.timed_rules().items()
            },
            "practice": k.policy.practice,
        }

    @app.post("/api/permissions")
    async def set_permission(request: Request) -> dict[str, Any]:
        local_only(request)
        data = await body(request)
        pattern = str(data.get("pattern", "")).strip()
        setting = str(data.get("setting", ""))
        if not pattern or setting not in {"safe", "confirm", "blocked", "default"}:
            raise HTTPException(
                status_code=400, detail="give a pattern and safe, confirm, blocked or default"
            )
        k = kernel()
        if setting == "default":
            k.policy.clear_rule(pattern)
        else:
            k.policy.set_rule(pattern, Permission(setting))
        save_rule(paths.config, pattern, setting)
        k.audit.record("web.permission", pattern=pattern, setting=setting)
        return {"rules": k.policy.rules()}

    @app.get("/api/modules")
    def modules(request: Request) -> dict[str, Any]:
        viewer(request)
        records = kernel().modules.records
        return {
            "modules": [
                {
                    "name": n,
                    "status": r.status.value,
                    "description": r.manifest.description,
                    "error": r.error,
                    "tools": len(r.tools),
                    "outside": outside_warning(n),  # did not come with Starlight
                }
                for n, r in sorted(records.items())
            ]
        }

    @app.post("/api/modules/{name}")
    async def toggle_module(name: str, request: Request) -> dict[str, Any]:
        local_only(request)
        enabled = bool((await body(request)).get("enabled"))
        manager = kernel().modules
        if name not in manager.records:
            raise HTTPException(status_code=404, detail="no such module")
        if enabled:
            record = await manager.enable(name)
            return {"name": name, "status": record.status.value, "error": record.error}
        await manager.disable(name)
        return {"name": name, "status": "disabled"}

    # -- memory and skills -------------------------------------------------------------------

    @app.get("/api/memory")
    def memories(request: Request, search: str = "") -> dict[str, Any]:
        viewer(request)
        wanted = search.strip().lower()
        entries = [
            {
                "id": e.id,
                "topic": e.topic,
                "content": e.content,
                "date": str(e.date),
                "source": e.source,
                "tags": e.tags,
                "expires": e.expires,
                "expired": e.expired(),
            }
            for e in kernel().memory.store.all()
            if not wanted or wanted in e.text.lower()
        ]
        entries.sort(key=lambda e: str(e["date"]), reverse=True)
        return {"count": len(entries), "memories": entries}

    @app.post("/api/conversations/{conversation}/forget-learned")
    async def forget_learned(conversation: str, request: Request) -> dict[str, Any]:
        """Delete the memories learned only in this conversation."""
        viewer(request)
        if not re.fullmatch(r"[a-f0-9]{12}", conversation):
            raise HTTPException(status_code=400, detail="bad id")
        k = kernel()
        result = await k.memory.forget_conversation(conversation)
        k.audit.record("memory.forget_conversation", conversation=conversation,
                       forgotten=len(result["forgotten"]))  # fmt: skip
        return result

    @app.put("/api/memory/{entry_id:path}")
    async def edit_memory(entry_id: str, request: Request) -> dict[str, Any]:
        viewer(request)
        data = await body(request)
        memory = kernel().memory
        try:
            entry = memory.store.get(entry_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="bad id") from exc
        if entry is None:
            raise HTTPException(status_code=404, detail="no such memory")
        content = str(data.get("content", entry.content)).strip()
        if not content:
            raise HTTPException(status_code=400, detail="a memory can't be empty")
        entry.content = content
        entry.topic = str(data.get("topic", entry.topic)).strip() or entry.topic
        memory.store.write(entry)
        await memory.sync(force=True)
        return {"saved": entry.id}

    @app.delete("/api/memory/{entry_id:path}")
    async def delete_memory(entry_id: str, request: Request) -> dict[str, Any]:
        viewer(request)
        try:
            removed = await kernel().memory.forget(entry_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="bad id") from exc
        if not removed:
            raise HTTPException(status_code=404, detail="no such memory")
        return {"deleted": entry_id}

    @app.get("/api/skills/{name}/export")
    def export_skill(name: str, request: Request) -> Response:
        """One skill as a file, to keep or give to another Starlight."""
        viewer(request)
        try:
            data = kernel().skills.export_data(name)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="no such skill") from exc
        file = f"{data['skill']['name']}.skill.json"
        return Response(
            json.dumps(data, indent=2, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{file}"'},
        )

    @app.post("/api/skills/import")
    async def import_skill(request: Request) -> dict[str, Any]:
        """Add a skill from a file: always as a draft, used only after you approve it."""
        local_only(request)
        data = await body(request)
        try:
            skill = kernel().skills.import_data(data, source="imported in the web chat")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        kernel().audit.record("skill.imported", skill=skill.name, version=skill.version)
        return {"name": skill.name, "version": skill.version, "status": skill.status}

    @app.get("/api/skills")
    def skills(request: Request) -> dict[str, Any]:
        viewer(request)
        return {
            "skills": [
                {
                    "name": s.name,
                    "status": s.status,
                    "version": s.version,
                    "description": s.description,
                    "instructions": s.instructions,
                    "updated": s.updated,
                }
                for s in kernel().skills.all()
            ]
        }

    @app.post("/api/skills/{name}/{action}")
    def skill_action(name: str, action: str, request: Request) -> dict[str, Any]:
        local_only(request)
        manager = kernel().skills
        actions: dict[str, Callable[[str], Any]] = {
            "approve": manager.approve,
            "reject": manager.reject,
            "rollback": manager.rollback,
        }
        if action not in actions:
            raise HTTPException(status_code=400, detail="approve, reject or rollback")
        try:
            skill = actions[action](name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"name": skill.name, "status": skill.status, "version": skill.version}

    @app.delete("/api/skills/{name}")
    def delete_skill(name: str, request: Request) -> dict[str, Any]:
        local_only(request)
        try:
            kernel().skills.delete(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": name}

    # -- the chat socket ---------------------------------------------------------------------

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        peer = websocket.client.host if websocket.client else None
        who = gate.viewer(websocket.headers, dict(websocket.cookies), peer)
        if who is None:
            await websocket.close(code=1008)  # policy violation: another site, or not paired
            return
        await websocket.accept()
        k = kernel()
        send_lock = asyncio.Lock()

        async def send(obj: dict[str, Any]) -> None:
            async with send_lock:
                await websocket.send_text(json.dumps(obj))

        web_ui = WebUI(send)
        clients[web_ui] = send
        ask_task: asyncio.Task[None] | None = None
        confirm_more = () if who.local else tuple(config.web.remote.confirm)
        block_more = () if who.local else tuple(config.web.remote.blocked)
        try:
            while True:
                message = json.loads(await websocket.receive_text())
                kind = message.get("type")
                if kind == "ask" and str(message.get("text", "")).strip():
                    if ask_task is not None and not ask_task.done():
                        await send({"type": "busy"})
                        continue
                    text, images = attach(
                        str(message["text"]).strip(), list(message.get("attachments") or [])
                    )
                    ask_task = asyncio.create_task(
                        _run_ask(
                            k,
                            web_ui,
                            send,
                            text,
                            images,
                            message.get("conversation") or None,
                            confirm_more,
                            block_more,
                        )
                    )
                elif kind == "regenerate":
                    if ask_task is not None and not ask_task.done():
                        await send({"type": "busy"})
                        continue
                    ask_task = asyncio.create_task(
                        _run_ask(
                            k,
                            web_ui,
                            send,
                            str(message.get("question") or ""),
                            None,
                            message.get("conversation") or None,
                            confirm_more,
                            block_more,
                            regenerate=True,
                        )
                    )
                elif kind == "edit" and str(message.get("text", "")).strip():
                    if ask_task is not None and not ask_task.done():
                        await send({"type": "busy"})
                        continue
                    ask_task = asyncio.create_task(
                        _run_ask(
                            k,
                            web_ui,
                            send,
                            str(message.get("question") or ""),
                            None,
                            message.get("conversation") or None,
                            confirm_more,
                            block_more,
                            regenerate=True,
                            replacement=str(message["text"]).strip(),
                        )
                    )
                elif kind == "reply":
                    web_ui.resolve(message.get("id", ""), message.get("value", ""))
                elif kind == "stop" and ask_task is not None and not ask_task.done():
                    ask_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await ask_task
                    if ask_task.cancelled():  # stopped before it began: nothing said so yet
                        await send({"type": "stopped"})
        except WebSocketDisconnect:
            pass
        finally:
            clients.pop(web_ui, None)
            if ask_task is not None and not ask_task.done():
                ask_task.cancel()
            web_ui.cancel_all()

    return app


def answer_stats(usage: list[dict[str, Any]]) -> dict[str, Any]:
    """How long the model took for an answer, and how fast it wrote (over all steps)."""
    seconds = sum(float(u.get("duration_s") or 0) for u in usage)
    tokens = sum(int(u.get("completion_tokens") or 0) for u in usage)
    speeds = [float(u["tokens_per_second"]) for u in usage if u.get("tokens_per_second")]
    stats: dict[str, Any] = {"seconds": round(seconds, 1), "tokens": tokens}
    if speeds:
        stats["tokens_per_second"] = round(speeds[-1], 1)
    return stats


EXPORT_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>__TITLE__</title>
<style>
  body { font: 15px/1.55 system-ui, "Segoe UI", sans-serif; color: #1f2328; background: #fff;
         max-width: 820px; margin: 24px auto; padding: 0 16px; }
  h1 { font-size: 22px; margin-bottom: 2px; } .when { color: #5a6372; font-size: 13px; }
  .msg { border: 1px solid #d8dce3; border-radius: 10px; padding: 10px 14px; margin: 12px 0;
         break-inside: avoid; }
  .msg.you { background: #eef4ff; } .who { font-size: 11px; text-transform: uppercase;
         letter-spacing: .05em; color: #5a6372; margin-bottom: 4px; }
  .you .body { white-space: pre-wrap; }
  pre { background: #f6f7f9; border-radius: 6px; padding: 8px 10px; overflow-x: auto; }
  code { font-family: ui-monospace, Consolas, monospace; font-size: .92em; }
  .md-code-bar { font-size: 12px; color: #5a6372; } .md-code-bar button { display: none; }
  table { border-collapse: collapse; } th, td { border: 1px solid #d8dce3; padding: 3px 7px; }
  blockquote { border-left: 3px solid #d8dce3; margin: 0; padding-left: 10px; color: #5a6372; }
  .tok-k { color: #8250df; } .tok-s { color: #116329; } .tok-n { color: #b35900; }
  .tok-c { color: #6e7781; font-style: italic; } .tok-f { color: #0550ae; }
  .tok-v { color: #953800; }
  .math { font-family: "Cambria Math", "STIX Two Math", Georgia, serif; }
  .math-block { display: block; text-align: center; margin: .4em 0; }
  .bar { position: sticky; top: 0; background: #fff; padding: 6px 0; text-align: right; }
  @media print { .bar { display: none; } body { margin: 0 auto; } }
</style></head><body>
<div class="bar"><button onclick="print()">Print or save as PDF</button></div>
<h1 id="title"></h1><div class="when" id="when"></div><div id="log"></div>
<script type="application/json" id="data">__DATA__</script>
<script>
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text; return e; }
function toast() {}
</script>
<script>__MARKDOWN__</script>
<script>
const data = JSON.parse(document.getElementById("data").textContent);
document.getElementById("title").textContent = data.title;
document.getElementById("when").textContent = (data.created || "").slice(0, 16).replace("T", " ");
for (const m of data.messages) {
  const box = el("div", "msg " + (m.role === "user" ? "you" : "bot"));
  box.appendChild(el("div", "who", m.role === "user" ? "You" : "Starlight"));
  const body = box.appendChild(el("div", "body"));
  if (m.role === "user") body.textContent = m.content; else renderMarkdown(body, m.content);
  document.getElementById("log").appendChild(box);
}
if (data.print) setTimeout(() => print(), 300);
</script></body></html>
"""


def export_html(data: dict[str, Any], markdown_js: str, print_now: bool = False) -> str:
    """A conversation as one HTML file that shows it on its own (no server, no network):
    the answers drawn from their Markdown by the chat's own safe renderer."""
    payload = {
        "title": data.get("title") or "Conversation",
        "created": data.get("created") or "",
        "messages": [
            {"role": m.get("role"), "content": str(m.get("content", ""))}
            for m in data.get("messages", [])
        ],
        "print": print_now,
    }
    # "</" would end the script element early: a message cannot close it
    embedded = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    parts = {
        "__TITLE__": html.escape(str(payload["title"])),
        "__MARKDOWN__": markdown_js.replace("</script", "<\\/script"),
        "__DATA__": embedded,
    }  # in one pass: a title or message that says "__DATA__" stays as it is
    return re.sub("__(?:TITLE|MARKDOWN|DATA)__", lambda m: parts[m.group(0)], EXPORT_PAGE)


async def _run_ask(
    kernel: Kernel,
    web_ui: WebUI,
    send: Any,
    text: str,
    images: list[bytes] | None = None,
    conversation: str | None = None,
    confirm_more: tuple[str, ...] = (),
    block_more: tuple[str, ...] = (),
    regenerate: bool = False,
    replacement: str | None = None,
) -> None:
    """One request from the browser. ``regenerate``: ask the conversation's last question
    again (``text``, if given, is the question the page means) and replace its answer; if
    the new answer is stopped or fails, the old one stays. ``replacement``: ask this
    instead of that question (you edited it)."""
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def on_token(token: str) -> None:
        queue.put_nowait({"type": "token", "text": token})

    def on_thought(text: str) -> None:  # the model's thinking, for the thoughts box
        queue.put_nowait({"type": "thought", "text": text})

    def on_tool(report: dict[str, Any]) -> None:  # a tool starts or ends: the timeline
        queue.put_nowait({"type": "tool", **report})

    async def pump() -> None:
        while True:
            item = await queue.get()
            if item is None:
                return
            await send(item)

    def answer(result: AgentResult) -> dict[str, Any]:
        return {
            "type": "answer",
            "text": result.answer,
            "stopped": result.stopped,
            "failed": result.failed,  # stopped or failed: not kept in the conversation
            "conversation": name,
            "stats": answer_stats(result.usage),
        }

    name = conversation or kernel.conversations.current
    try:
        kernel.conversations.messages(name)
    except KeyError:
        await send({"type": "error", "text": "that conversation no longer exists"})
        return
    token_ctx = current_ui.set(web_ui)
    thought_ctx = thought_sink.set(on_thought)
    tool_ctx = tool_sink.set(on_tool)
    confirm_ctx = extra_confirm.set(confirm_more)
    block_ctx = extra_blocked.set(block_more)
    pumper: asyncio.Task[None] | None = None
    result: AgentResult | None = None
    taken = kept = False
    try:
        if regenerate:
            try:
                question = await kernel.take_back(name, expected=text or None)
            except ValueError as exc:  # the page is behind: another window asked since
                await send({"type": "error", "text": str(exc)})
                return
            if question is None:
                await send({"type": "error", "text": "nothing to ask again"})
                return
            taken = True
            text, images = question.content, question.images  # the pictures go again too
            if replacement:
                text = replacement
        await send({"type": "start", "conversation": name})
        pumper = asyncio.create_task(pump())
        result = await kernel.ask(
            text, keep_history=True, on_token=on_token, images=images or None, conversation=name
        )
        kept = not (result.stopped or result.failed)
        queue.put_nowait(None)
        await pumper
        await send(answer(result))
    except asyncio.CancelledError:
        queue.put_nowait(None)
        if pumper is not None:
            # Stopped while the last words were still being sent: the pump was cancelled
            # with us, which is no reason not to tell the page.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pumper
        # Already answered (and kept) when the stop came: say so, it is in the conversation.
        await send(answer(result) if result is not None else {"type": "stopped"})
    except Exception as exc:  # surface any failure to the browser
        queue.put_nowait(None)
        if pumper is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pumper
        await send({"type": "error", "text": f"{type(exc).__name__}: {exc}"})
    finally:
        if taken and not kept:
            with contextlib.suppress(Exception):
                await kernel.put_back(name)
        extra_blocked.reset(block_ctx)
        extra_confirm.reset(confirm_ctx)
        tool_sink.reset(tool_ctx)
        thought_sink.reset(thought_ctx)
        current_ui.reset(token_ctx)


def run(paths: Paths | None = None) -> None:
    import uvicorn

    paths = paths or Paths.discover()
    config = load_config(paths)
    if config.web.host not in LOOPBACK:
        raise SystemExit(
            f"web.host is {config.web.host}: the chat must listen on this PC only "
            "(127.0.0.1). To use it from your phone, use `starlight remote enable` (Tailscale)."
        )
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(paths),
            host=config.web.host,
            port=config.web.port,
            log_level="warning",
            log_config=None,
            proxy_headers=False,  # who is remote is decided from the headers, not by uvicorn
        )
    )
    asyncio.run(server.serve())
