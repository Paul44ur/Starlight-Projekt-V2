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
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from starlight.core import Event, Kernel, Paths, ToolError
from starlight.core.config import RemoteConfig, load_config
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
        text = data.decode("utf-8", errors="replace")
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

    @app.get("/icon.svg")
    def icon() -> Response:
        return Response(page("icon.svg"), media_type="image/svg+xml")

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
        await kernel().deliver(f"A new device was paired with Starlight: {name[:60]}")
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
        return {
            "model": k.config.model.provider,
            "home": str(paths.root) if who.local else None,
            "saves_files": describe_output_folder(k.config.output.folder, paths.root),
            "kill": {"engaged": k.kill_switch.engaged, "reason": k.kill_switch.reason},
            "access": access,
            "local": who.local,
            "device": who.device.name if who.device else None,
        }

    @app.post("/api/kill")
    def kill(request: Request) -> dict[str, Any]:
        who = viewer(request)
        kernel().kill_switch.trigger("web /kill", source="web" if who.local else "web-remote")
        return {"engaged": True}

    @app.post("/api/resume")
    def resume(request: Request) -> dict[str, Any]:
        local_only(request)
        kernel().kill_switch.reset()
        return {"engaged": False}

    # -- conversations -------------------------------------------------------------------

    @app.get("/api/conversations")
    def conversations(request: Request, search: str = "") -> dict[str, Any]:
        viewer(request)
        store = kernel().conversations
        return {"current": store.current, "conversations": store.list(search)}

    @app.post("/api/conversations")
    def new_conversation(request: Request) -> dict[str, Any]:
        viewer(request)
        return {"id": kernel().new_conversation()}

    @app.get("/api/conversations/{conversation}")
    def get_conversation(conversation: str, request: Request) -> dict[str, Any]:
        viewer(request)
        try:
            return kernel().conversations.get(conversation)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="no such conversation") from exc

    @app.patch("/api/conversations/{conversation}")
    async def rename_conversation(conversation: str, request: Request) -> dict[str, Any]:
        viewer(request)
        title = str((await body(request)).get("title", "")).strip()
        try:
            kernel().conversations.rename(conversation, title)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="no such conversation") from exc
        return {"id": conversation, "title": title}

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

    async def attach(text: str, ids: list[Any]) -> tuple[str, list[bytes]]:
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
                str(data.get("text", "")), str(data.get("language", ""))
            )
        except ToolError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return Response(audio, media_type="audio/wav")

    # -- settings: permissions and modules ---------------------------------------------------

    @app.get("/api/permissions")
    def permissions(request: Request) -> dict[str, Any]:
        viewer(request)
        k = kernel()
        tools = [
            {
                "name": t.qualified_name,
                "tag": t.permission.value,
                "now": k.policy.resolve(t.qualified_name, t.permission).value,
            }
            for t in k.dispatcher.tools()
        ]
        return {"rules": k.policy.rules(), "tools": tools}

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
            }
            for e in kernel().memory.store.all()
            if not wanted or wanted in e.text.lower()
        ]
        entries.sort(key=lambda e: str(e["date"]), reverse=True)
        return {"count": len(entries), "memories": entries}

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
                    try:
                        text, images = await attach(
                            str(message["text"]).strip(), list(message.get("attachments") or [])
                        )
                    except ValueError as exc:
                        await send({"type": "error", "text": str(exc)})
                        continue
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
                elif kind == "reply":
                    web_ui.resolve(message.get("id", ""), message.get("value", ""))
                elif kind == "stop" and ask_task is not None and not ask_task.done():
                    ask_task.cancel()
        except WebSocketDisconnect:
            pass
        finally:
            clients.pop(web_ui, None)
            if ask_task is not None and not ask_task.done():
                ask_task.cancel()
            web_ui.cancel_all()

    return app


async def _run_ask(
    kernel: Kernel,
    web_ui: WebUI,
    send: Any,
    text: str,
    images: list[bytes] | None = None,
    conversation: str | None = None,
    confirm_more: tuple[str, ...] = (),
    block_more: tuple[str, ...] = (),
) -> None:
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def on_token(token: str) -> None:
        queue.put_nowait(token)

    async def pump() -> None:
        while True:
            token = await queue.get()
            if token is None:
                return
            await send({"type": "token", "text": token})

    name = conversation or kernel.conversations.current
    try:
        kernel.conversations.messages(name)
    except KeyError:
        await send({"type": "error", "text": "that conversation no longer exists"})
        return
    await send({"type": "start", "conversation": name})
    token_ctx = current_ui.set(web_ui)
    confirm_ctx = extra_confirm.set(confirm_more)
    block_ctx = extra_blocked.set(block_more)
    pumper = asyncio.create_task(pump())
    try:
        result = await kernel.ask(
            text, keep_history=True, on_token=on_token, images=images or None, conversation=name
        )
        queue.put_nowait(None)
        await pumper
        await send(
            {
                "type": "answer",
                "text": result.answer,
                "stopped": result.stopped,
                "conversation": name,
            }
        )
    except asyncio.CancelledError:
        queue.put_nowait(None)
        with contextlib.suppress(Exception):
            await pumper
        await send({"type": "stopped"})
    except Exception as exc:  # surface any failure to the browser
        queue.put_nowait(None)
        with contextlib.suppress(Exception):
            await pumper
        await send({"type": "error", "text": f"{type(exc).__name__}: {exc}"})
    finally:
        extra_blocked.reset(block_ctx)
        extra_confirm.reset(confirm_ctx)
        current_ui.reset(token_ctx)


def run(paths: Paths | None = None) -> None:
    import uvicorn

    from starlight.service import shutdown_file

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
    request = shutdown_file(paths.data, "web")
    request.unlink(missing_ok=True)

    async def watch_for_stop() -> None:
        while not server.should_exit:
            if request.exists():
                request.unlink(missing_ok=True)
                server.should_exit = True
            await asyncio.sleep(1)

    async def main() -> None:
        watcher = asyncio.create_task(watch_for_stop())
        try:
            await server.serve()
        finally:
            watcher.cancel()

    asyncio.run(main())
