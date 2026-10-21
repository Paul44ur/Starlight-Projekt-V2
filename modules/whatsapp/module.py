"""WhatsApp (Step 10), with the official WhatsApp Business Cloud API.

* a webhook on this computer receives messages; a Cloudflare Tunnel exposes only the
  webhook path (see docs/setup-windows.md)
* every delivery must carry Meta's signature (X-Hub-Signature-256, your app secret)
* only messages from your own number are accepted; everything else is dropped
* ``/kill`` from your number triggers the kill switch, ``/status`` reports; other text
  becomes a task, and the answer comes back as a message
* questions while a task runs (confirmations, 2FA codes) are asked over WhatsApp;
  reply "yes" or "no"
* server alerts and new draft skills are sent to you

Secrets live in the vault: ``whatsapp.token``, ``whatsapp.app_secret``,
``whatsapp.verify_token``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import re
from collections import OrderedDict
from collections.abc import Iterator
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, Field

from starlight.core import Event, Module, ModuleContext, tool
from starlight.core.ui import current_ui

YES = {"y", "yes", "ja", "j", "ok", "okay", "sure", "allow", "approve"}
MAX_TEXT = 4000  # WhatsApp allows 4096 characters per text message
MAX_BODY = 1024**2  # Meta's deliveries are a few KB; the endpoint is public, so cap the rest


class Config(BaseModel):
    owner_number: str = ""  # your number in international format, e.g. "4917612345678"
    phone_number_id: str = ""  # from the WhatsApp Business app in the Meta dashboard
    api_version: str = "v21.0"
    graph_url: str = "https://graph.facebook.com"
    token_key: str = "whatsapp.token"
    app_secret_key: str = "whatsapp.app_secret"
    verify_token_key: str = "whatsapp.verify_token"
    host: str = "127.0.0.1"  # the tunnel connects here; never expose this port directly
    port: int = 8787
    path: str = "/webhook/whatsapp"
    reply_timeout: float = 300.0  # how long a question waits for your answer
    allow_resume: bool = False  # allow /resume from the phone
    # Events forwarded as they are. Server up/down, reminders and alerts already arrive
    # through `deliver`, so they are not listed here.
    notify: list[str] = Field(default_factory=lambda: ["skill.drafted"])
    deliver: bool = True  # reminders, alerts and briefings Starlight sends you


def digits(number: str) -> str:
    return re.sub(r"\D", "", number)


def valid_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # bytes: compare_digest raises TypeError on a non-ASCII str, and the header is untrusted
    return hmac.compare_digest(expected.encode(), header.removeprefix("sha256=").encode())


class WhatsAppUI:
    """Questions during a task go to your phone; your next message answers them."""

    def __init__(self, module: WhatsAppModule) -> None:
        self.module = module

    async def _question(self, text: str) -> str | None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self.module.pending.append(future)
        try:
            if not await self.module.send(text):
                return None  # you never saw the question; do not wait for an answer
            return await asyncio.wait_for(future, self.module.config.reply_timeout)
        except TimeoutError:
            await self.module.send("No answer, so I did not do it.")
            return None
        finally:
            if future in self.module.pending:
                self.module.pending.remove(future)

    async def confirm(self, question: str) -> bool:
        answer = await self._question(f"{question}\nReply yes or no.")
        return answer is not None and answer.strip().lower() in YES

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return await self._question(question)

    async def notify(self, text: str) -> None:
        await self.module.send(text)


class WhatsAppModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.owner = digits(self.config.owner_number)
        self.pending: list[asyncio.Future[str]] = []
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.graph = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10))
        self.ui = WhatsAppUI(self)
        self.app = self._build_app()
        self._server: Any = None
        self._server_task: asyncio.Task[None] | None = None
        self._handlers: set[asyncio.Task[Any]] = set()
        for pattern in self.config.notify:
            ctx.bus.subscribe(pattern, self._forward, owner=ctx.name, background=True)
        if self.config.deliver:
            ctx.bus.subscribe("deliver", self._deliver, owner=ctx.name, background=True)
        ctx.bus.subscribe("core.primary", self._on_primary, owner=ctx.name)
        if not (self.owner and self.config.phone_number_id):
            ctx.logger.info("whatsapp not configured; set owner_number and phone_number_id")
            return
        if ctx.services.primary:  # otherwise the running background core serves it
            await self._start_server()

    async def _on_primary(self, event: Event) -> None:
        if self.configured and self._server_task is None:
            await self._start_server()

    @property
    def configured(self) -> bool:
        return bool(self.owner and self.config.phone_number_id)

    def _port_free(self) -> bool:
        import socket

        with socket.socket() as probe:
            try:
                probe.bind((self.config.host, self.config.port))
            except OSError:
                return False
        return True

    async def _serve(self) -> None:
        try:
            await self._server.serve()
        except BaseException as exc:  # uvicorn exits the process on errors; never the core
            if isinstance(exc, asyncio.CancelledError):
                raise
            self.ctx.logger.error("webhook server stopped", error=repr(exc))

    async def _start_server(self) -> None:
        import uvicorn

        if not self._port_free():
            self.ctx.logger.warning(
                "webhook port busy; another Starlight process probably serves it",
                port=self.config.port,
            )
            return

        config = uvicorn.Config(
            self.app, host=self.config.host, port=self.config.port, log_level="warning",
            access_log=False, log_config=None,
        )  # fmt: skip

        class WebhookServer(uvicorn.Server):
            @contextlib.contextmanager
            def capture_signals(self) -> Iterator[None]:
                yield  # the core owns Ctrl+C; shutdown() stops this server

        self._server = WebhookServer(config)
        self._server_task = asyncio.create_task(self._serve(), name="whatsapp-webhook")
        self.ctx.logger.info("webhook listening", host=self.config.host, port=self.config.port)

    async def shutdown(self) -> None:
        for future in self.pending:
            future.cancel()
        if self._server is not None:
            self._server.should_exit = True
        if self._server_task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server_task, 5)
        await self.graph.aclose()

    # -- the webhook -------------------------------------------------------------------

    def _build_app(self) -> Any:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        path = self.config.path

        @app.get(path)
        async def verify(request: Request) -> Response:
            """Meta's one-time check when you register the webhook."""
            params = request.query_params
            expected = self.ctx.vault.get(self.config.verify_token_key) or ""
            token = params.get("hub.verify_token", "")
            if (
                params.get("hub.mode") == "subscribe"
                and expected
                and hmac.compare_digest(token.encode(), expected.encode())
            ):
                return Response(params.get("hub.challenge", ""), media_type="text/plain")
            return Response(status_code=403)

        @app.post(path)
        async def receive(request: Request) -> Response:
            declared = request.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > MAX_BODY:
                return Response(status_code=413)
            chunks: list[bytes] = []
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BODY:
                    return Response(status_code=413)
                chunks.append(chunk)
            body = b"".join(chunks)
            secret = self.ctx.vault.get(self.config.app_secret_key) or ""
            if not secret or not valid_signature(
                secret, body, request.headers.get("x-hub-signature-256")
            ):
                self.ctx.logger.warning("webhook delivery with a bad signature dropped")
                return Response(status_code=403)
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                return Response(status_code=400)
            for message in self._messages(payload):
                task = asyncio.create_task(self.handle(message))
                self._handlers.add(task)
                task.add_done_callback(self._handlers.discard)
            return Response(status_code=200)  # answer Meta at once; work in the background

        return app

    @staticmethod
    def _messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                found += (change.get("value") or {}).get("messages") or []
        return found

    async def handle(self, message: dict[str, Any]) -> None:
        message_id = str(message.get("id", ""))
        if message_id in self.seen:
            return  # Meta retries deliveries; act once
        self.seen[message_id] = None
        while len(self.seen) > 500:  # the latest 500 ids are plenty to catch Meta's retries
            self.seen.popitem(last=False)

        sender = digits(str(message.get("from", "")))
        if not self.owner or sender != self.owner:
            self.ctx.logger.warning("message from another number ignored")
            if self.ctx.stats is not None:
                self.ctx.stats.record("whatsapp.ignored")
            return
        if message.get("type") != "text":
            await self.send("I can only read text messages for now.")
            return
        text = str((message.get("text") or {}).get("body", "")).strip()
        if not text:
            return
        if self.ctx.stats is not None:
            self.ctx.stats.record("whatsapp.received")

        command = text.lower().split()[0] if text.startswith("/") else ""
        if command == "/kill":
            reason = text[len("/kill") :].strip() or "WhatsApp /kill"  # what follows the word
            self.ctx.kill_switch.trigger(reason, source="whatsapp")
            await self.send("Kill switch engaged. Everything is stopped.")
            return
        if command == "/status":
            switch = self.ctx.kill_switch
            state = f"STOPPED ({switch.reason})" if switch.engaged else "running"
            await self.send(f"Starlight is {state}.")
            return
        if command == "/resume":
            if not self.config.allow_resume:
                await self.send("Resume from the phone is off. Use `starlight resume` on the PC.")
                return
            self.ctx.kill_switch.reset()
            await self.send("Kill switch released.")
            return
        if self.pending:  # the answer to a question a running task asked
            future = self.pending.pop(0)
            if not future.done():
                future.set_result(text)
            return
        if self.ctx.kill_switch.engaged:
            await self.send(f"Starlight is stopped ({self.ctx.kill_switch.reason}).")
            return
        await self.ctx.publish(
            "message.received",
            {"channel": "whatsapp", "text": text, "reply": self.send, "ui": self.ui},
        )

    # -- sending -----------------------------------------------------------------------

    async def send(self, text: str) -> bool:
        """Send a text message to your number (long text is split)."""
        if not self.configured:
            self.ctx.logger.warning("whatsapp not configured; message not sent")
            return False
        token = self.ctx.vault.get(self.config.token_key)
        if not token:
            self.ctx.logger.warning("whatsapp token missing in the vault")
            return False
        url = (
            f"{self.config.graph_url.rstrip('/')}/{self.config.api_version}/"
            f"{self.config.phone_number_id}/messages"
        )
        chunks = [text[i : i + MAX_TEXT] for i in range(0, len(text), MAX_TEXT)] or [""]
        for chunk in chunks:
            response = await self.graph.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": self.owner,
                    "type": "text",
                    "text": {"body": chunk, "preview_url": False},
                },
            )
            if response.status_code >= 400:
                self.ctx.logger.error("whatsapp send failed", status=response.status_code)
                return False
        if self.ctx.stats is not None:
            self.ctx.stats.record("whatsapp.sent", len(chunks))
        return True

    async def _forward(self, event: Event) -> None:
        if not self.configured:
            return
        data = ", ".join(f"{k}: {v}" for k, v in event.data.items() if isinstance(v, str))
        await self.send(f"[{event.name}] {data}".strip())

    async def _deliver(self, event: Event) -> None:
        # Asked from WhatsApp itself? Then the message already came here as the answer.
        if not self.configured or current_ui.get() is self.ui:
            return
        await self.send(str(event.data.get("text", "")))

    @tool
    async def send_to_me(self, text: str) -> str:
        """Send a WhatsApp message to the owner's phone (only that number)."""
        if await self.send(text):
            return "Sent."
        # send() says why in the log: not set up, no token in the vault, or Meta refused it
        return (
            "Not sent: WhatsApp is not set up, its token is missing from the vault, or Meta "
            "refused the message (see the log)."
        )
