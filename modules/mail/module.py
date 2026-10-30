"""Read and send email from your own mailbox over IMAP and SMTP.

Set it up once from the chat ("set up my email ..."): the password (use an app-password, not
your main password) goes into the OS keyring, and the server settings into
config/modules/mail.yaml - no project files to edit by hand. Reading your inbox is allowed
freely and never marks anything as read; sending or replying asks first (unless you
auto-approved ``mail.send`` / ``mail.reply`` under ``permissions`` in config/starlight.yaml).

Beyond reading and sending, it helps with the inbox: what is unread, a summary sorted into
important / can wait / probably spam (by your local model), a draft reply you can change
before it goes out, and replies that stay in the same thread. With ``watch: true`` it
checks for new mail every few minutes, publishes ``mail.new`` (automations can react) and
tells you at once about mail from the senders in ``notify_from``.

Messages are named by their IMAP UID, which stays the same across sessions.

The server's TLS certificate is checked on every connection. A home server with a
self-signed certificate fails with an error that names the ``verify_certificate`` setting
in config/modules/mail.yaml; set it to ``false`` to accept such a certificate anyway.

Uses only the Python standard library; no third-party packages.
"""

from __future__ import annotations

import asyncio
import email
import email.utils
import imaplib
import re
import smtplib
import ssl
from email import message_from_bytes
from email.header import Header, decode_header
from email.message import EmailMessage, Message
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, Permission, ToolError, tool
from starlight.core.config import write_yaml_config
from starlight.core.jsonfile import load_json, save_json
from starlight.core.model import Message as ChatMessage

PASSWORD_KEY = "mail.password"
CONNECT_TIMEOUT = 30  # seconds, for IMAP and SMTP alike


class Config(BaseModel):
    username: str = ""  # your email address / login
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    from_name: str = ""  # the display name on messages you send
    max_body_chars: int = Field(default=20_000, ge=1000)
    watch: bool = False  # check for new mail in the background
    check_minutes: int = Field(default=5, ge=1)
    # Tell me at once about new mail from these (addresses or parts of them, e.g. "@bank.de").
    notify_from: list[str] = Field(default_factory=list)
    # Check the mail server's TLS certificate (IMAP and SMTP). Set this to false only for your
    # own server with a self-signed certificate: the connection stays encrypted, but anyone
    # on the network could then pose as the server and read the password and your mail.
    verify_certificate: bool = True


SUMMARY_PROMPT = """\
Sort these unread emails for a busy person. Write three short sections - Important,
Can wait, Probably spam - with one line per email: sender, what it is about, and what (if
anything) they need to do. Leave out empty sections. Do not invent anything.

{mails}"""

DRAFT_PROMPT = """\
Write a reply to the email below, as {me}. {instructions}
Write only the body of the reply (no subject line), in the language of the email unless
the instructions say otherwise. Keep it short and polite. Do not invent facts, dates or
promises; leave a [placeholder] where you would need something you do not know.

From: {sender}
Subject: {subject}

{body}"""


class EmailError(ToolError):
    pass


class EmailModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.seen_file = ctx.data_dir / "seen.json"
        if self.config.watch:
            ctx.every(self.config.check_minutes * 60, self.check_new, "mail-watch", first_delay=30)

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
            self.config.model_dump(),  # every setting, so hand-made ones are kept
            "# The email module (modules/mail). The app-password is in the keyring, not here.",
        )
        return {
            "configured": True,
            "username": username,
            "note": "app-password stored in the keyring",
        }

    # -- reading (safe) ------------------------------------------------------------------

    @tool
    async def inbox(self, limit: int = 10) -> dict[str, Any]:
        """The most recent messages in your inbox: who from, the subject, the date and
        whether it is unread. Use mail.read with an id to see a message's text."""
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
        return await asyncio.to_thread(self._imap_fetch, password, id.strip())

    @tool
    async def unread(self, limit: int = 15) -> dict[str, Any]:
        """Unread messages, newest first: sender, subject, date and the first lines. Does
        not mark anything as read."""
        password = self._require()
        count = max(1, min(limit, 50))
        messages = await asyncio.to_thread(self._imap_list, password, count, True)
        return {"count": len(messages), "messages": messages}

    @tool
    async def summary(self, limit: int = 20) -> dict[str, Any]:
        """A short overview of unread mail, sorted into important, can wait and probably
        spam. Nothing is marked as read."""
        password = self._require()
        count = max(1, min(limit, 40))
        messages = await asyncio.to_thread(self._imap_list, password, count, True)
        if not messages:
            return {"count": 0, "summary": "No unread mail."}
        lines = [
            f"- [{m['id']}] from {m['from']}, subject: {m['subject']}. {m.get('snippet', '')}"
            for m in messages
        ]
        text = await self._write(SUMMARY_PROMPT.replace("{mails}", "\n".join(lines)))
        return {"count": len(messages), "summary": text}

    @tool
    async def draft_reply(self, id: str, instructions: str = "") -> dict[str, Any]:
        """Draft a reply to a message (by its id) and show it; nothing is sent. Use
        mail.reply to send it, changed or as it is. `instructions`: what the reply should
        say, e.g. "accept, but only after 5pm"."""
        password = self._require()
        original = await asyncio.to_thread(self._imap_fetch, password, id.strip())
        prompt = (
            DRAFT_PROMPT.replace("{me}", self.config.from_name or self.config.username)
            .replace("{instructions}", f"What to say: {instructions}" if instructions else "")
            .replace("{sender}", original["from"])
            .replace("{subject}", original["subject"])
            .replace("{body}", original["body"][:6000])
        )
        draft = await self._write(prompt)
        return {
            "to": original["reply_target"],
            "subject": _reply_subject(original["subject"]),
            "body": draft,
            "note": "Nothing was sent. Send it with mail.reply.",
        }

    async def _write(self, prompt: str) -> str:
        model = self.ctx.model_for("summary")
        if model is None:
            raise EmailError("no model is available to write this")
        response = await model.chat([ChatMessage("user", prompt)], [])
        return response.content.strip()

    # -- sending (asks first unless auto-approved) ---------------------------------------

    @tool(permission=Permission.CONFIRM)
    async def send(self, to: str, subject: str, body: str) -> dict[str, Any]:
        """Send an email from your address (asks for confirmation first unless it was
        auto-approved). `to` is the recipient address, `subject` the subject line, `body`
        the message text."""
        password = self._require()
        if "@" not in to:
            raise EmailError("give a recipient address, e.g. 'name@example.com'")
        await asyncio.to_thread(self._smtp_send, password, to.strip(), subject, body)
        self.ctx.logger.info("email sent", to=to.strip())
        return {"sent": True, "to": to.strip(), "subject": subject}

    @tool(permission=Permission.CONFIRM)
    async def reply(self, id: str, body: str, reply_all: bool = False) -> dict[str, Any]:
        """Reply to a message (by its id), in the same thread (asks for confirmation first
        unless it was auto-approved). `body` is the text of the reply."""
        password = self._require()
        if not body.strip():
            raise EmailError("what should the reply say?")
        original = await asyncio.to_thread(self._imap_fetch, password, id.strip())
        to = original["reply_target"]
        if reply_all:
            skip = {self.config.username.lower(), *(a.lower() for a in _raw_addresses([to]))}
            others = [a for a in original["recipients"] if a.lower() not in skip]
            to = ", ".join(dict.fromkeys([to, *others]))
        headers = {}
        if original.get("message_id"):
            headers["In-Reply-To"] = original["message_id"]
            headers["References"] = " ".join(
                p for p in (original.get("references", ""), original["message_id"]) if p
            )
        subject = _reply_subject(original["subject"])
        await asyncio.to_thread(self._smtp_send, password, to, subject, body, headers)
        self.ctx.logger.info("email reply sent", uid=id.strip())
        return {"sent": True, "to": to, "subject": subject, "in_reply_to": id.strip()}

    # -- new mail (background) -----------------------------------------------------------

    async def check_new(self) -> list[dict[str, Any]]:
        """Look for unread mail not seen before; publish mail.new for each one, and tell
        the user about mail from senders in notify_from. The first look only takes note
        of what is there."""
        if not (self.config.username and self.config.imap_host and self._password()):
            return []
        password = self._require()
        validity, unseen = await asyncio.to_thread(self._imap_unseen, password)
        state = load_json(self.seen_file, {})  # {} (no file yet) is the first look
        known = set(state.get("uids", [])) if state.get("validity") == validity else None
        fresh = [] if known is None else [m for m in unseen if m["id"] not in known]
        remembered = sorted({*(known or set()), *(m["id"] for m in unseen)}, key=int)[-2000:]
        save_json(self.seen_file, {"validity": validity, "uids": remembered})
        for message in fresh:
            await self.ctx.publish("mail.new", dict(message))
            sender = message["from"].lower()
            if any(n.strip().lower() in sender for n in self.config.notify_from if n.strip()):
                await self.ctx.deliver(
                    f"New mail from {message['from']}: {message['subject']}", title="Mail"
                )
        return fresh

    # -- IMAP/SMTP (blocking; run via asyncio.to_thread; stubbable in tests) --------------

    def _tls_context(self) -> ssl.SSLContext:
        """The TLS settings for IMAP and SMTP: the server's certificate is checked unless
        verify_certificate is off."""
        context = ssl.create_default_context()
        if not self.config.verify_certificate:
            context.check_hostname = False  # before verify_mode: the order matters
            context.verify_mode = ssl.CERT_NONE
        return context

    def _imap_connect(self, password: str) -> imaplib.IMAP4_SSL:
        client = imaplib.IMAP4_SSL(
            self.config.imap_host,
            self.config.imap_port,
            ssl_context=self._tls_context(),
            timeout=CONNECT_TIMEOUT,
        )
        client.login(self.config.username, password)
        return client

    def _imap_session(self, password: str) -> imaplib.IMAP4_SSL:
        try:
            client = self._imap_connect(password)
            client.select("INBOX", readonly=True)  # read-only: nothing is marked as read
        except ssl.SSLCertVerificationError as exc:
            raise EmailError(f"could not reach the mailbox: {_certificate_problem(exc)}") from exc
        except (imaplib.IMAP4.error, OSError) as exc:
            raise EmailError(f"could not reach the mailbox: {exc}") from exc
        return client

    @staticmethod
    def _uids(client: imaplib.IMAP4_SSL, criteria: str) -> list[str]:
        _, data = client.uid("SEARCH", None, criteria)  # type: ignore[arg-type]
        return [u.decode() for u in (data[0].split() if data and data[0] else [])]

    @staticmethod
    def _flags(client: imaplib.IMAP4_SSL, uids: list[str]) -> set[str]:
        """Which of these UIDs are unread."""
        if not uids:
            return set()
        _, data = client.uid("SEARCH", None, f"UID {','.join(uids)} UNSEEN")  # type: ignore[arg-type]
        return {u.decode() for u in (data[0].split() if data and data[0] else [])}

    def _imap_list(self, password: str, count: int, unseen: bool = False) -> list[dict[str, Any]]:
        client = self._imap_session(password)
        try:
            uids = self._uids(client, "UNSEEN" if unseen else "ALL")[-count:]
            unread = set(uids) if unseen else self._flags(client, uids)
            messages: list[dict[str, Any]] = []
            for uid in reversed(uids):
                # PEEK: reading never sets the "seen" flag. Unread mail: the start of the
                # message too (headers and the first text), for a snippet.
                spec = "(BODY.PEEK[]<0.65536>)" if unseen else "(BODY.PEEK[HEADER])"
                _, fetched = client.uid("FETCH", uid, spec)
                parsed = message_from_bytes(_payload(fetched))
                item = {
                    "id": uid,
                    "from": _decode(parsed.get("From", "")),
                    "subject": _decode(parsed.get("Subject", "")),
                    "date": parsed.get("Date", ""),
                    "unread": uid in unread,
                }
                if unseen:
                    item["snippet"] = " ".join(_body(parsed).split())[:240]
                messages.append(item)
            return messages
        finally:
            _logout(client)

    def _imap_unseen(self, password: str) -> tuple[str, list[dict[str, Any]]]:
        """(UIDVALIDITY, the newest unread messages' headers)."""
        client = self._imap_session(password)
        try:
            _, data = client.response("UIDVALIDITY")
            validity = data[0].decode() if data and isinstance(data[0], bytes) else ""
            messages = []
            for uid in self._uids(client, "UNSEEN")[-50:]:
                _, fetched = client.uid(
                    "FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
                )
                parsed = message_from_bytes(_payload(fetched))
                messages.append(
                    {
                        "id": uid,
                        "from": _decode(parsed.get("From", "")),
                        "subject": _decode(parsed.get("Subject", "")),
                        "date": parsed.get("Date", ""),
                    }
                )
            return validity, messages
        finally:
            _logout(client)

    def _imap_fetch(self, password: str, message_id: str) -> dict[str, Any]:
        if not message_id.isdigit():
            raise EmailError(f"'{message_id}' is not a message id (see mail.inbox)")
        client = self._imap_session(password)
        try:
            _, fetched = client.uid("FETCH", message_id, "(BODY.PEEK[])")
            raw = _payload(fetched)
            if not raw:
                raise EmailError(f"no message with id '{message_id}'")
            parsed = message_from_bytes(raw)
            return {
                "id": message_id,
                "from": _decode(parsed.get("From", "")),
                "to": _decode(parsed.get("To", "")),
                "cc": _decode(parsed.get("Cc", "")),
                "reply_to": _decode(parsed.get("Reply-To", "")),
                # Where a reply goes, and who else got it: from the raw headers, so a name
                # like "Müller, Hans" (encoded on the wire) is never split at its comma.
                "reply_target": _reply_target(parsed),
                "recipients": _raw_addresses(parsed.get_all("To", []), parsed.get_all("Cc", [])),
                "subject": _decode(parsed.get("Subject", "")),
                "date": parsed.get("Date", ""),
                "message_id": str(parsed.get("Message-ID", "")).strip(),
                "references": " ".join(str(parsed.get("References", "")).split()),
                "body": _body(parsed)[: self.config.max_body_chars],
            }
        finally:
            _logout(client)

    def _smtp_send(
        self,
        password: str,
        to: str,
        subject: str,
        body: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        message = EmailMessage()
        sender = self.config.username
        if self.config.from_name:
            sender = email.utils.formataddr((self.config.from_name, self.config.username))
        message["From"] = sender
        message["To"] = to
        message["Subject"] = subject
        for key, value in (headers or {}).items():
            message[key] = value
        message.set_content(body)
        try:
            with smtplib.SMTP(
                self.config.smtp_host, self.config.smtp_port, timeout=CONNECT_TIMEOUT
            ) as server:
                server.starttls(context=self._tls_context())
                server.login(self.config.username, password)
                server.send_message(message)
        except ssl.SSLCertVerificationError as exc:
            raise EmailError(f"could not send the email: {_certificate_problem(exc)}") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailError(f"could not send the email: {exc}") from exc


def _certificate_problem(exc: ssl.SSLCertVerificationError) -> str:
    return (
        f"the server's certificate was not accepted ({exc.verify_message or exc}). If this is "
        "your own server with a self-signed certificate, set `verify_certificate: false` in "
        "config/modules/mail.yaml (the connection stays encrypted, but the server is no "
        "longer checked); otherwise do not send your password to it."
    )


def _payload(fetched: Any) -> bytes:
    """Pull the message bytes out of imaplib's fetch response (a list whose first element is
    usually a (envelope, body) tuple)."""
    if not fetched:
        return b""
    first = fetched[0]
    part = first[1] if isinstance(first, tuple) and len(first) > 1 else first
    return bytes(part) if isinstance(part, (bytes, bytearray)) else b""


def _reply_subject(subject: str) -> str:
    return subject if re.match(r"(?i)\s*(re|aw|sv)\s*:", subject) else f"Re: {subject}"


def _raw_addresses(*fields: list[Any]) -> list[str]:
    """The bare addresses in raw (still encoded) header values."""
    values = [_raw(v) for field in fields for v in field]
    return [address for _, address in email.utils.getaddresses(values) if "@" in address]


def _raw(value: Any) -> str:
    """A header value as text to find addresses in: encoded words left as they are (they
    are decoded per name), raw 8-bit bytes (RFC 6532: "Jürgen Müller") read as UTF-8."""
    if isinstance(value, Header):  # how the parser hands over a header with raw 8-bit bytes
        return "".join(
            chunk if isinstance(chunk, str) else _bytes_text(chunk, charset)
            for chunk, charset in decode_header(value)
        )
    return str(value)


def _reply_target(parsed: Message) -> str:
    """Reply-To, else From, as "Name <address>" with the name decoded."""
    raw = parsed.get_all("Reply-To") or parsed.get_all("From") or []
    pairs = [(n, a) for n, a in email.utils.getaddresses([_raw(v) for v in raw]) if "@" in a]
    return ", ".join(_named(_decode(n), a) for n, a in pairs)


def _named(name: str, address: str) -> str:
    """'"Müller, Hans" <hans@x.de>': readable, and still one address (quoted name)."""
    name = " ".join(name.split())
    if not name:
        return address
    if not re.search(r'[][\\()<>@,:;".]', name):  # nothing that needs quoting
        return f"{name} <{address}>"
    quoted = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{quoted}" <{address}>'


def _decode(value: Any) -> str:
    """A header as one line of text: encoded words decoded, folding (line breaks a long
    header is wrapped with) removed, so it can be shown and reused in a reply. Headers
    sent as raw UTF-8 (RFC 6532: "From: Jürgen Müller") are read as UTF-8 too."""
    try:
        parts = [
            chunk if isinstance(chunk, str) else _bytes_text(chunk, charset)
            for chunk, charset in decode_header(value)
        ]
        text = "".join(parts)
    except (ValueError, TypeError, AttributeError):
        text = str(value)
    return " ".join(text.split())


def _bytes_text(data: bytes, charset: str | None) -> str:
    if charset in (None, "unknown-8bit"):  # raw 8-bit: UTF-8 these days, else Windows-1252
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("cp1252", errors="replace")
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def _text_of(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return str(part.get_payload())
    try:
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    except LookupError:  # a charset Python does not know, e.g. "unknown-8bit"
        return payload.decode("utf-8", errors="replace")


def _body(message: Message) -> str:
    """The message's text: its plain-text part, else its HTML part as text."""
    if not message.is_multipart():
        text = _text_of(message)
        if message.get_content_type() == "text/html":
            from starlight.text import html_to_text

            text = html_to_text(text).text
        return text
    html = None
    for part in _own_parts(message):
        if part.get_content_type() == "text/plain":
            return _text_of(part)
        if part.get_content_type() == "text/html" and html is None:
            html = part
    if html is not None:
        from starlight.text import html_to_text

        return html_to_text(_text_of(html)).text
    return "(no text part)"


def _own_parts(message: Message) -> Any:
    """The message's own parts, in order - not those inside an attachment or an attached
    (forwarded) mail, whose text is not this mail's text."""
    queue = [message]
    while queue:
        part = queue.pop(0)
        if part is not message and (
            part.get_content_disposition() == "attachment"
            or part.get_content_type() == "message/rfc822"
        ):
            continue
        if part.is_multipart():
            queue[0:0] = [p for p in part.get_payload() if isinstance(p, Message)]
            continue
        yield part


def _logout(client: imaplib.IMAP4_SSL) -> None:
    import contextlib

    with contextlib.suppress(Exception):
        client.logout()
