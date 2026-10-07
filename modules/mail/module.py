"""Read and send email from your own mailbox over IMAP and SMTP.

Set it up once from the chat ("set up my email ..."): the password (use an app-password, not
your main password) goes into the OS keyring, and the server settings into
config/modules/mail.yaml - no project files to edit by hand. Reading your inbox is allowed
freely; sending a message always asks first.

Uses only the Python standard library; no third-party packages.
"""

from __future__ import annotations

import asyncio
import email
import email.utils
import imaplib
import smtplib
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, Permission, ToolError, tool
from starlight.core.config import write_yaml_config

PASSWORD_KEY = "mail.password"


class Config(BaseModel):
    username: str = ""  # your email address / login
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    from_name: str = ""  # the display name on messages you send
    max_body_chars: int = Field(default=20_000, ge=1000)


class EmailError(ToolError):
    pass


class EmailModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    # -- configuration -------------------------------------------------------------------

    def _password(self) -> str | None:
        return self.ctx.vault.get(PASSWORD_KEY)

    def _require(self) -> str:
        missing = not (self.config.username and self.config.imap_host and self._password())
        if missing:
            raise EmailError(
                "email is not set up yet. Use mail.configure with your address, the IMAP and "
                "SMTP server names, and an app-password."
            )
        password = self._password()
        assert password is not None
        return password

    @tool
    async def configure(
        self,
        username: str,
        password: str,
        imap_host: str,
        smtp_host: str,
        imap_port: int = 993,
        smtp_port: int = 587,
    ) -> dict[str, Any]:
        """Set up email: store the app-password in the OS keyring and the server settings in
        the config. Use an app-password from your mail provider, not your main password.
        Asks for confirmation first."""
        if not (username and imap_host and smtp_host and password):
            raise EmailError("give your address, the IMAP and SMTP hosts, and an app-password")
        if not await self.ctx.ui.confirm(
            f"Save email settings for {username} and store its app-password in the keyring?"
        ):
            raise EmailError("the user did not confirm, so nothing was saved")
        self.ctx.vault.set(PASSWORD_KEY, password)
        self.config.username = username
        self.config.imap_host = imap_host
        self.config.smtp_host = smtp_host
        self.config.imap_port = imap_port
        self.config.smtp_port = smtp_port
        write_yaml_config(
            self.ctx.paths.module_configs / "mail.yaml",
            {
                "username": username,
                "imap_host": imap_host,
                "imap_port": imap_port,
                "smtp_host": smtp_host,
                "smtp_port": smtp_port,
                "from_name": self.config.from_name,
            },
            "# The email module (modules/email). The app-password is in the keyring, not here.",
        )
        return {
            "configured": True,
            "username": username,
            "note": "app-password stored in the keyring",
        }

    # -- reading (safe) ------------------------------------------------------------------

    @tool
    async def inbox(self, limit: int = 10) -> dict[str, Any]:
        """The most recent messages in your inbox: who from, the subject and the date. Use
        mail.read with an id to see a message's text."""
        password = self._require()
        count = max(1, min(limit, 50))
        messages = await asyncio.to_thread(self._imap_list, password, count)
        return {"count": len(messages), "messages": messages}

    @tool
    async def read(self, id: str) -> dict[str, Any]:
        """Read one message by its id (from mail.inbox): sender, subject, date and the text
        body."""
        password = self._require()
        if not id.strip():
            raise EmailError("give a message id from mail.inbox")
        message = await asyncio.to_thread(self._imap_fetch, password, id.strip())
        return message

    # -- sending (asks first) ------------------------------------------------------------

    @tool(permission=Permission.CONFIRM)
    async def send(self, to: str, subject: str, body: str) -> dict[str, Any]:
        """Send an email from your address. Asks for confirmation first. `to` is the
        recipient address, `subject` the subject line, `body` the message text."""
        password = self._require()
        if "@" not in to:
            raise EmailError("give a recipient address, e.g. 'name@example.com'")
        await asyncio.to_thread(self._smtp_send, password, to.strip(), subject, body)
        self.ctx.logger.info("email sent", to=to.strip())
        return {"sent": True, "to": to.strip(), "subject": subject}

    # -- IMAP/SMTP (blocking; run via asyncio.to_thread; stubbable in tests) --------------

    def _imap_connect(self, password: str) -> imaplib.IMAP4_SSL:
        client = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port)
        client.login(self.config.username, password)
        return client

    def _imap_list(self, password: str, count: int) -> list[dict[str, Any]]:
        try:
            client = self._imap_connect(password)
        except (imaplib.IMAP4.error, OSError) as exc:
            raise EmailError(f"could not reach the mailbox: {exc}") from exc
        try:
            client.select("INBOX", readonly=True)
            _, data = client.search(None, "ALL")
            ids = (data[0].split() if data and data[0] else [])[-count:]
            messages: list[dict[str, Any]] = []
            for raw_id in reversed(ids):
                _, fetched = client.fetch(raw_id.decode(), "(BODY.PEEK[HEADER])")
                parsed = message_from_bytes(_payload(fetched))
                messages.append(
                    {
                        "id": raw_id.decode(),
                        "from": _decode(parsed.get("From", "")),
                        "subject": _decode(parsed.get("Subject", "")),
                        "date": parsed.get("Date", ""),
                    }
                )
            return messages
        finally:
            _logout(client)

    def _imap_fetch(self, password: str, message_id: str) -> dict[str, Any]:
        try:
            client = self._imap_connect(password)
        except (imaplib.IMAP4.error, OSError) as exc:
            raise EmailError(f"could not reach the mailbox: {exc}") from exc
        try:
            client.select("INBOX", readonly=True)
            _, fetched = client.fetch(message_id, "(RFC822)")
            raw = _payload(fetched)
            if not raw:
                raise EmailError(f"no message with id '{message_id}'")
            parsed = message_from_bytes(raw)
            return {
                "id": message_id,
                "from": _decode(parsed.get("From", "")),
                "to": _decode(parsed.get("To", "")),
                "subject": _decode(parsed.get("Subject", "")),
                "date": parsed.get("Date", ""),
                "body": _body(parsed)[: self.config.max_body_chars],
            }
        finally:
            _logout(client)

    def _smtp_send(self, password: str, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        sender = self.config.username
        if self.config.from_name:
            sender = email.utils.formataddr((self.config.from_name, self.config.username))
        message["From"] = sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        try:
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(self.config.username, password)
                server.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailError(f"could not send the email: {exc}") from exc


def _payload(fetched: Any) -> bytes:
    """Pull the message bytes out of imaplib's fetch response (a list whose first element is
    usually a (envelope, body) tuple)."""
    if not fetched:
        return b""
    first = fetched[0]
    part = first[1] if isinstance(first, tuple) and len(first) > 1 else first
    return bytes(part) if isinstance(part, (bytes, bytearray)) else b""


def _decode(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, UnicodeDecodeError):
        return value


def _body(message: Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(
                part.get("Content-Disposition", "")
            ):
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return "(no plain-text part)"
    payload = message.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode(message.get_content_charset() or "utf-8", errors="replace")
    return str(message.get_payload())


def _logout(client: imaplib.IMAP4_SSL) -> None:
    import contextlib

    with contextlib.suppress(Exception):
        client.logout()
