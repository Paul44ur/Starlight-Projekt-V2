"""The web chat: a FastAPI app with the live assistant behind it.

It binds to localhost only. A browser connects over a WebSocket; messages are streamed
back token by token, and when Starlight wants to run something that needs confirmation the
question appears in the browser with Approve / Deny buttons. The same permissions and the
same kill switch as the terminal apply.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from starlight.core import Kernel, Paths
from starlight.core.config import load_config
from starlight.core.output import describe_output_folder
from starlight.core.ui import current_ui

STATIC = Path(__file__).with_name("static")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def allowed_request(headers: Any, extra_hosts: set[str]) -> bool:
    """Only this machine's own chat page (or a non-browser client such as the terminal) may
    talk to the backend. Browsers let any web page open a WebSocket to localhost or send it a
    plain POST, so a page you visit could otherwise ask Starlight to do things and approve its
    own confirmations. The Host must be this machine (stops DNS-rebinding tricks), and an Origin,
    which every browser sends, must be that same host."""
    host = urlsplit(f"//{headers.get('host', '')}").hostname or ""
    if host not in LOOPBACK | extra_hosts:
        return False
    origin = headers.get("origin")
    if not origin:
        return True  # not a browser (the terminal client, curl, tests)
    parts = urlsplit(origin)
    return parts.scheme in ("http", "https") and parts.netloc == headers.get("host")


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


def create_app(paths: Paths | None = None) -> Any:
    paths = paths or Paths.discover()
    state: dict[str, Any] = {}

    async def lifespan(app: Any) -> Any:
        kernel = Kernel(paths, hotkey=True)
        await kernel.start()
        state["kernel"] = kernel
        try:
            yield
        finally:
            await kernel.stop()

    app = FastAPI(title="Starlight chat", docs_url=None, redoc_url=None, lifespan=lifespan)
    extra_hosts = {load_config(paths).web.host} - {"0.0.0.0", "::"}

    def guard(request: Request) -> None:
        if not allowed_request(request.headers, extra_hosts):
            raise HTTPException(status_code=403, detail="only the local chat page may do this")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "chat.html").read_text(encoding="utf-8")

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        kernel: Kernel = state["kernel"]
        access: dict[str, Any] = {}
        record = kernel.modules.records.get("access")
        if record is not None and record.instance is not None:
            # read directly: this page polls every few seconds, and going through the
            # dispatcher would write an audit entry and a statistic each time
            with contextlib.suppress(Exception):
                read_status = getattr(record.instance, "status", None)
                if callable(read_status):
                    access = read_status()
        return {
            "model": kernel.config.model.provider,
            "home": str(paths.root),
            "saves_files": describe_output_folder(kernel.config.output.folder, paths.root),
            "kill": {
                "engaged": kernel.kill_switch.engaged,
                "reason": kernel.kill_switch.reason,
            },
            "access": access,
        }

    @app.post("/api/kill")
    def kill(request: Request) -> dict[str, Any]:
        guard(request)
        state["kernel"].kill_switch.trigger("web /kill", source="web")
        return {"engaged": True}

    @app.post("/api/resume")
    def resume(request: Request) -> dict[str, Any]:
        guard(request)
        state["kernel"].kill_switch.reset()
        return {"engaged": False}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        if not allowed_request(websocket.headers, extra_hosts):
            await websocket.close(code=1008)  # policy violation: another site's page
            return
        await websocket.accept()
        kernel: Kernel = state["kernel"]
        send_lock = asyncio.Lock()

        async def send(obj: dict[str, Any]) -> None:
            async with send_lock:
                await websocket.send_text(json.dumps(obj))

        web_ui = WebUI(send)
        ask_task: asyncio.Task[None] | None = None
        try:
            while True:
                message = json.loads(await websocket.receive_text())
                kind = message.get("type")
                if kind == "ask" and message.get("text", "").strip():
                    if ask_task is not None and not ask_task.done():
                        await send({"type": "busy"})
                        continue
                    ask_task = asyncio.create_task(
                        _run_ask(kernel, web_ui, send, message["text"].strip())
                    )
                elif kind == "reply":
                    web_ui.resolve(message.get("id", ""), message.get("value", ""))
                elif kind == "stop" and ask_task is not None and not ask_task.done():
                    ask_task.cancel()
        except WebSocketDisconnect:
            pass
        finally:
            if ask_task is not None and not ask_task.done():
                ask_task.cancel()
            web_ui.cancel_all()

    return app


async def _run_ask(kernel: Kernel, web_ui: WebUI, send: Any, text: str) -> None:
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def on_token(token: str) -> None:
        queue.put_nowait(token)

    async def pump() -> None:
        while True:
            token = await queue.get()
            if token is None:
                return
            await send({"type": "token", "text": token})

    await send({"type": "start"})
    token_ctx = current_ui.set(web_ui)
    pumper = asyncio.create_task(pump())
    try:
        result = await kernel.ask(text, keep_history=True, on_token=on_token)
        queue.put_nowait(None)
        await pumper
        await send({"type": "answer", "text": result.answer, "stopped": result.stopped})
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
        current_ui.reset(token_ctx)


def run(paths: Paths | None = None) -> None:
    import uvicorn

    from starlight.service import shutdown_file

    paths = paths or Paths.discover()
    config = load_config(paths)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(paths),
            host=config.web.host,
            port=config.web.port,
            log_level="warning",
            log_config=None,
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
