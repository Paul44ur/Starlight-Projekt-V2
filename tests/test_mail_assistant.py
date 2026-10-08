"""The email assistant: UIDs, unread without marking anything read, a summary and a draft
from the model, replies in the same thread (asking first), and the new-mail watcher."""

from __future__ import annotations

import json
from collections.abc import Callable
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.core.events import Event
from starlight.core.vault import MemoryBackend
from tests.conftest import ScriptedProvider
from tests.test_mail import User, mod


def mail(uid: int, sender: str, subject: str, body: str, **headers: str) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "Me <me@web.de>"
    message["Subject"] = subject
    message["Date"] = "Thu, 08 Oct 2026 09:00:00 +0200"
    message["Message-ID"] = f"<m{uid}@example.org>"
    for key, value in headers.items():
        message[key.replace("_", "-")] = value
    message.set_content(body)
    return bytes(message)


class FakeIMAP:
    """Just enough of imaplib.IMAP4_SSL, with UIDs and a \\Seen flag per message."""

    def __init__(self, messages: dict[int, bytes], unseen: set[int]) -> None:
        self.messages, self.unseen = messages, unseen
        self.validity = b"777"
        self.fetches: list[str] = []
        self.readonly: bool | None = None

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.readonly = readonly
        return "OK", [str(len(self.messages)).encode()]

    def response(self, code: str) -> tuple[str, list[bytes]]:
        return code, [self.validity]

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        if command == "SEARCH":
            criteria = args[1]
            uids = sorted(self.messages)
            if criteria.startswith("UID "):
                wanted = {int(u) for u in criteria.split()[1].split(",")}
                uids = [u for u in uids if u in wanted]
            if criteria.endswith("UNSEEN"):
                uids = [u for u in uids if u in self.unseen]
            return "OK", [" ".join(str(u) for u in uids).encode()]
        assert command == "FETCH"
        uid, spec = int(args[0]), args[1]
        self.fetches.append(spec)
        if "PEEK" not in spec:
            self.unseen.discard(uid)  # a real server marks it read
        raw = self.messages.get(uid)
        if raw is None:
            return "OK", [None]
        if "HEADER" in spec:
            raw = raw.split(b"\n\n", 1)[0] + b"\n\n"
        return "OK", [(f"{uid} (UID {uid} BODY[] {{{len(raw)}}}".encode(), raw), b")"]

    def logout(self) -> None:
        pass


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("mail")
    return paths


@pytest.fixture
def inbox() -> FakeIMAP:
    return FakeIMAP(
        {
            11: mail(11, "Landlord <landlord@haus.at>", "Rent", "The rent goes up in May."),
            12: mail(12, "Shop <news@shop.example>", "50% off!!!", "Buy now."),
            13: mail(
                13,
                "Anna <anna@x.org>",
                "Re: Dinner",
                "Friday at 7?",
                References="<m1@example.org>",
                Cc="Ben <ben@x.org>, me@web.de",
            ),
        },
        unseen={12, 13},
    )


def connect(k: Kernel, imap: FakeIMAP) -> Any:
    module = mod(k)
    module.config.username = "me@web.de"
    module.config.imap_host = "imap.web.de"
    module.config.smtp_host = "smtp.web.de"
    module.ctx.vault.set("mail.password", "app-pw")
    module._imap_connect = lambda password: imap
    return module


async def call(k: Kernel, name: str, /, **args: Any) -> Any:
    result = await k.dispatcher.call(name, args)
    assert result.ok, result.error
    return result.output


async def test_inbox_and_unread_use_uids_and_mark_nothing_read(
    project: Paths, inbox: FakeIMAP
) -> None:
    async with Kernel(project, ui=User(), vault_backend=MemoryBackend()) as k:
        connect(k, inbox)
        listed = await call(k, "mail.inbox", limit=10)
        assert [m["id"] for m in listed["messages"]] == ["13", "12", "11"]
        assert [m["unread"] for m in listed["messages"]] == [True, True, False]
        unread = await call(k, "mail.unread")
        assert [m["id"] for m in unread["messages"]] == ["13", "12"]
        assert unread["messages"][0]["snippet"] == "Friday at 7?"
        read = await call(k, "mail.read", id="11")
        assert read["body"].strip() == "The rent goes up in May."
        assert inbox.readonly is True
        assert all("PEEK" in spec for spec in inbox.fetches)
        assert inbox.unseen == {12, 13}  # still unread
        bad = await k.dispatcher.call("mail.read", {"id": "1:*"})
        assert not bad.ok and "not a message id" in (bad.error or "")


async def test_summary_and_draft_come_from_the_model(project: Paths, inbox: FakeIMAP) -> None:
    provider = ScriptedProvider(
        "Important:\n- Anna: dinner Friday at 7, answer her\nProbably spam:\n- Shop: 50% off",
        "Hi Anna, Friday at 7 works for me. See you then!",
    )
    async with Kernel(project, ui=User(), vault_backend=MemoryBackend(), provider=provider) as k:
        connect(k, inbox)
        summary = await call(k, "mail.summary")
        assert summary["count"] == 2 and "Probably spam" in summary["summary"]
        prompt = provider.calls[0][0].content  # type: ignore[attr-defined]
        assert "[13] from Anna <anna@x.org>" in prompt and "Friday at 7?" in prompt
        draft = await call(k, "mail.draft_reply", id="13", instructions="say yes")
        assert draft["to"] == "Anna <anna@x.org>"
        assert draft["subject"] == "Re: Dinner"  # not "Re: Re: Dinner"
        assert "Friday at 7 works" in draft["body"] and "Nothing was sent" in draft["note"]
        assert "say yes" in provider.calls[1][0].content  # type: ignore[attr-defined]


async def test_reply_asks_first_and_stays_in_the_thread(project: Paths, inbox: FakeIMAP) -> None:
    sent: list[tuple[Any, ...]] = []
    async with Kernel(project, ui=User(False, True), vault_backend=MemoryBackend()) as k:
        module = connect(k, inbox)
        module._smtp_send = lambda *args: sent.append(args)
        denied = await k.dispatcher.call("mail.reply", {"id": "13", "body": "Yes!"})
        assert not denied.ok and sent == []
        await call(k, "mail.reply", id="13", body="Yes!", reply_all=True)
    _, to, subject, body, headers = sent[0]
    assert to == "Anna <anna@x.org>, ben@x.org"  # everyone but me
    assert subject == "Re: Dinner" and body == "Yes!"
    assert headers["In-Reply-To"] == "<m13@example.org>"
    assert headers["References"] == "<m1@example.org> <m13@example.org>"


async def test_new_mail_is_published_once_and_announced(project: Paths, inbox: FakeIMAP) -> None:
    (project.module_configs / "mail.yaml").write_text("watch: false\nnotify_from: ['@haus.at']\n")
    async with Kernel(project, ui=User(), vault_backend=MemoryBackend()) as k:
        connect(k, inbox)
        module = mod(k)
        published: list[Event] = []
        delivered: list[str] = []

        async def on_new(event: Event) -> None:
            published.append(event)

        async def on_deliver(event: Event) -> None:
            delivered.append(event.data["text"])

        k.bus.subscribe("mail.new", on_new, owner="t")
        k.bus.subscribe("deliver", on_deliver, owner="t")
        assert await module.check_new() == []  # the first look only takes note
        inbox.messages[14] = mail(14, "Landlord <landlord@haus.at>", "Keys", "Keys are ready.")
        inbox.messages[15] = mail(15, "Shop <news@shop.example>", "More deals", "Buy.")
        inbox.unseen |= {14, 15}
        fresh = await module.check_new()
        assert [m["id"] for m in fresh] == ["14", "15"]
        assert await module.check_new() == []  # never twice
        await k.drain()
        assert [e.data["subject"] for e in published] == ["Keys", "More deals"]
        assert published[0].source == "mail"
        assert delivered == ["Mail: New mail from Landlord <landlord@haus.at>: Keys"]
        saved = json.loads(module.seen_file.read_text())
        assert saved["validity"] == "777" and "14" in saved["uids"]
        inbox.validity = b"778"  # the mailbox was rebuilt: UIDs mean something else now
        inbox.messages[16] = mail(16, "Landlord <landlord@haus.at>", "Again", "Hi")
        inbox.unseen.add(16)
        assert await module.check_new() == []  # starts over quietly


async def test_reply_to_a_name_with_a_comma(project: Paths, inbox: FakeIMAP) -> None:
    inbox.messages[20] = mail(
        20,
        "=?iso-8859-1?Q?M=FCller=2C_Hans?= <hans@x.de>",
        "Termin",
        "Passt Dienstag?",
        Cc="=?utf-8?Q?Schmidt=2C_Eva?= <eva@x.de>, me@web.de",
    )
    sent: list[tuple[Any, ...]] = []
    async with Kernel(project, ui=User(), vault_backend=MemoryBackend()) as k:
        module = connect(k, inbox)
        module._smtp_send = lambda *args: sent.append(args)
        draft = await call(k, "mail.draft_reply", id="20")
        assert "hans@x.de" in draft["to"] and "Müller, Hans" in draft["to"]
        await call(k, "mail.reply", id="20", body="Ja!", reply_all=True)
    to = sent[0][1]
    from email.utils import getaddresses

    assert [a for _, a in getaddresses([to])] == ["hans@x.de", "eva@x.de"]
