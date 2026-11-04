"""A terminal chat that connects to a running Starlight web backend over its WebSocket.

This lets the terminal and the browser share one assistant: the same conversation, the same
kill switch and the same permissions, because there is only one kernel (inside the web
server). The launcher uses it for "open both" - it starts the web server, opens the browser,
and runs this client in the console.

The protocol is the one the browser uses (listed in web/app.py, ``ws``): the client sends
``ask``/``reply``/``stop`` and renders ``start``/``token``/``answer``/``confirm``/``prompt``/
``notify``/``busy``/``stopped``/``error`` with the shared ChatView.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, Protocol

import httpx

from starlight import __version__
from starlight.tui import ChatView

HELP = (
    "Type to chat. `/quit` leaves this terminal (the browser and backend keep running). "
    "`/kill` stops everything, `/resume` releases the kill switch, `/clear` clears the view."
)


class Sender(Protocol):
    async def __call__(self, obj: dict[str, Any]) -> None: ...


class Receiver(Protocol):
    async def __call__(self) -> dict[str, Any]: ...


async def run_turn(text: str, send: Sender, recv: Receiver, view: Any) -> None:
    """Send one message and render the backend's replies until the turn ends. Confirmations
    and questions from the backend are answered through the view (the user), as in the
    terminal chat. Returns when the backend signals answer/stopped/error."""
    await send({"type": "ask", "text": text})
    emit = None
    streamed = False
    while True:
        message = await recv()
        kind = message.get("type")
        if kind == "start":
            emit = view.on_token()
        elif kind == "token":
            if emit is None:
                emit = view.on_token()
            streamed = True
            emit(message.get("text", ""))
        elif kind == "answer":
            if streamed:
                view.end_answer(streamed=True)
            else:
                view.answer(message.get("text", ""))
            if message.get("stopped"):
                view.info("[Stopped.]")
            return
        elif kind == "stopped":
            view.end_answer(streamed)
            view.info("[Stopped.]")
            return
        elif kind == "error":
            view.end_answer(streamed)
            view.error(message.get("text", "something went wrong"))
            return
        elif kind == "confirm":
            approved = await view.confirm(message.get("question", ""))
            await send(
                # "yes" approves: web/app.py APPROVE
                {"type": "reply", "id": message.get("id", ""), "value": "yes" if approved else "no"}
            )
        elif kind == "prompt":
            answer = await view.ask(message.get("question", ""), bool(message.get("secret")))
            await send({"type": "reply", "id": message.get("id", ""), "value": answer or ""})
        elif kind == "notify":
            view.info(f"\U0001f514 {message.get('text', '')}")
        elif kind == "busy":
            view.info("[still working on the previous message - try again in a moment]")


async def stop_turn(send: Sender, recv: Receiver, wait: float = 10) -> None:
    """After Ctrl+C: stop the answer on the backend too, and read what it still sends for
    that turn - otherwise the backend is still busy with it, and its reply would be shown
    as the answer to the next question."""
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(wait):
            await send({"type": "stop"})
            while (await recv()).get("type") not in ("answer", "stopped", "error"):
                pass


async def run_client(host: str, port: int, assume_yes: bool = False) -> int:
    """Open a terminal chat against the web backend at host:port."""
    import websockets

    view = ChatView(assume_yes=assume_yes)
    base = f"http://{host}:{port}"
    info = await _status(base)
    view.model = info.pop("model", "")  # (the status line under the banner shows it)
    view.modes = {"stopped": "note" in info}
    view.banner(__version__, info, hotkey=None)
    view.info(f"Connected to the Starlight backend at {base} - shared with the browser chat.\n")

    try:
        async with websockets.connect(f"ws://{host}:{port}/ws", open_timeout=15) as ws:

            async def send(obj: dict[str, Any]) -> None:
                await ws.send(json.dumps(obj))

            async def recv() -> dict[str, Any]:
                data: dict[str, Any] = json.loads(await ws.recv())
                return data

            while True:
                line = await view.aread()
                if not line:
                    continue
                low = line.lower()
                if low in ("/quit", "/exit"):
                    break
                if low == "/help":
                    view.help(HELP)
                    continue
                if low in ("/kill", "/resume"):
                    await _post(base, "/api/kill" if low == "/kill" else "/api/resume", view)
                    continue
                if low == "/clear":
                    print("\033[2J\033[H", end="")  # ANSI: clear the screen, cursor to the top
                    continue
                try:
                    await run_turn(line, send, recv, view)
                except asyncio.CancelledError:  # Ctrl+C: stop only this answer
                    task = asyncio.current_task()
                    if task is not None:
                        task.uncancel()
                    await stop_turn(send, recv)
    except (OSError, websockets.exceptions.WebSocketException) as exc:
        view.error(f"Lost the connection to the backend ({exc}). Is the web server running?")
        return 1
    return 0


async def _status(base: str) -> dict[str, str]:
    info: dict[str, str] = {}
    with contextlib.suppress(Exception):
        async with httpx.AsyncClient(timeout=5) as client:
            data = (await client.get(f"{base}/api/status")).json()
        info["model"] = str(data.get("model", "?"))
        info["home"] = str(data.get("home", "?"))
        if data.get("kill", {}).get("engaged"):
            info["note"] = "kill switch engaged - /resume to release"
    return info


async def _post(base: str, path: str, view: Any) -> None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(f"{base}{path}")
        if not response.is_success:  # e.g. 403: /resume is refused for a viewer that is not local
            detail = ""
            with contextlib.suppress(ValueError, AttributeError):
                detail = str(response.json().get("detail") or "")
            view.error(f"the backend refused it (HTTP {response.status_code}) {detail}".strip())
            return
        view.info("Kill switch engaged." if path.endswith("kill") else "Kill switch released.")
    except httpx.HTTPError as exc:
        view.error(f"could not reach the backend: {exc}")
