"""Cleanup of the lan / login / mail / media / network / notes / notify modules: the mail
server's certificate is checked, a damaged JSON file does not break a module, the SSH
read-only check refuses commands that write files, and a few small bugs."""

from __future__ import annotations

import base64
import datetime as dt
import json
import socket
import ssl
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.core.config import read_yaml
from starlight.core.vault import MemoryBackend
from tests.test_home_devices import call, fails, instance
from tests.test_mail import User, mod
from tests.test_mail_assistant import FakeIMAP, mail

# -- mail: the server's certificate ------------------------------------------------------------


def self_signed(folder: Path) -> tuple[Path, Path]:
    """A certificate nobody trusts, the way a home mail server often has one."""
    pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mail.home.test")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = folder / "cert.pem", folder / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


class FakeMailServer:
    """A TLS server on 127.0.0.1 with a self-signed certificate, speaking just enough IMAP
    (TLS from the start) or SMTP (STARTTLS) for one login or one message."""

    def __init__(self, folder: Path, protocol: str) -> None:
        cert_file, key_file = self_signed(folder)
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(cert_file, key_file)
        self.protocol = protocol
        self.seen: list[str] = []  # the commands (SMTP: and the message) the client sent
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self) -> None:
        self.listener.settimeout(0.2)
        while not self.stop.is_set():
            try:
                conn, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            conn.settimeout(5)
            try:
                if self.protocol == "imap":
                    self.imap(conn)
                else:
                    self.smtp(conn)
            except (OSError, ssl.SSLError):
                pass  # a client that checks the certificate hangs up during the handshake
            finally:
                conn.close()

    def imap(self, conn: socket.socket) -> None:
        with self.context.wrap_socket(conn, server_side=True) as tls:
            stream = tls.makefile("rwb")
            stream.write(b"* OK ready\r\n")
            stream.flush()
            for raw in stream:
                tag, _, command = raw.decode().strip().partition(" ")
                self.seen.append(command.split()[0].upper())
                if command.upper().startswith("CAPABILITY"):
                    stream.write(b"* CAPABILITY IMAP4rev1\r\n")
                if command.upper().startswith("LOGOUT"):
                    stream.write(f"* BYE\r\n{tag} OK bye\r\n".encode())
                    stream.flush()
                    return
                stream.write(f"{tag} OK done\r\n".encode())
                stream.flush()

    def smtp(self, conn: socket.socket) -> None:
        plain = conn.makefile("rwb")
        plain.write(b"220 hello\r\n")
        plain.flush()
        for raw in plain:
            command = raw.decode().strip().upper()
            if command.startswith("EHLO"):
                plain.write(b"250-home\r\n250 STARTTLS\r\n")
            elif command == "STARTTLS":
                plain.write(b"220 go ahead\r\n")
                plain.flush()
                break
            plain.flush()
        plain.close()
        with self.context.wrap_socket(conn, server_side=True) as tls:
            stream = tls.makefile("rwb")
            for raw in stream:
                command = raw.decode().strip()
                self.seen.append(command)
                upper = command.upper()
                if upper.startswith("EHLO"):
                    stream.write(b"250-home\r\n250 AUTH PLAIN\r\n")
                elif upper.startswith("AUTH"):
                    stream.write(b"235 welcome\r\n")
                elif upper == "DATA":
                    stream.write(b"354 go on\r\n")
                    stream.flush()
                    for line in stream:
                        if line == b".\r\n":
                            break
                        self.seen.append(line.decode().rstrip())
                    stream.write(b"250 queued\r\n")
                elif upper == "QUIT":
                    stream.write(b"221 bye\r\n")
                    stream.flush()
                    return
                else:
                    stream.write(b"250 ok\r\n")
                stream.flush()

    def close(self) -> None:
        self.stop.set()
        self.thread.join(2)
        self.listener.close()


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("mail")
    return paths


def mail_module(k: Kernel, port: int) -> Any:
    module = mod(k)
    module.config.username = "me@home.test"
    module.config.imap_host = module.config.smtp_host = "127.0.0.1"
    module.config.imap_port = module.config.smtp_port = port
    module.ctx.vault.set("mail.password", "app-pw")
    return module


@pytest.fixture
def imap_server(tmp_path: Path) -> Iterator[FakeMailServer]:
    server = FakeMailServer(tmp_path, "imap")
    yield server
    server.close()


@pytest.fixture
def smtp_server(tmp_path: Path) -> Iterator[FakeMailServer]:
    server = FakeMailServer(tmp_path, "smtp")
    yield server
    server.close()


def test_the_mail_config_checks_certificates_by_default() -> None:
    from modules.mail.module import Config

    assert Config().verify_certificate is True
    shipped = read_yaml(Path(__file__).resolve().parent.parent / "config/modules/mail.yaml")
    assert shipped["verify_certificate"] is True
    assert Config.model_validate(shipped).verify_certificate is True


async def test_tls_settings_follow_verify_certificate(project: Paths) -> None:
    async with Kernel(project, ui=User(), vault_backend=MemoryBackend()) as k:
        module = mod(k)
        strict = module._tls_context()
        assert strict.verify_mode == ssl.CERT_REQUIRED and strict.check_hostname
        module.config.verify_certificate = False
        loose = module._tls_context()
        assert loose.verify_mode == ssl.CERT_NONE and not loose.check_hostname


async def test_imap_refuses_an_unknown_certificate_and_names_the_setting(
    project: Paths, imap_server: FakeMailServer
) -> None:
    async with Kernel(project, ui=User(), vault_backend=MemoryBackend()) as k:
        mail_module(k, imap_server.port)
        result = await k.dispatcher.call("mail.inbox", {})
    assert not result.ok
    error = str(result.error)
    assert "could not reach the mailbox" in error
    assert "verify_certificate: false" in error and "config/modules/mail.yaml" in error
    assert "LOGIN" not in imap_server.seen  # the password was never sent


async def test_imap_works_with_a_self_signed_certificate_when_switched_off(
    project: Paths, imap_server: FakeMailServer
) -> None:
    async with Kernel(project, ui=User(), vault_backend=MemoryBackend()) as k:
        module = mail_module(k, imap_server.port)
        module.config.verify_certificate = False
        client = module._imap_session("app-pw")
        assert client.sock.gettimeout() == 30  # a silent server cannot hang the worker forever
        client.logout()
    assert "LOGIN" in imap_server.seen and "EXAMINE" in imap_server.seen  # read-only


async def test_smtp_refuses_an_unknown_certificate_before_sending_the_password(
    project: Paths, smtp_server: FakeMailServer
) -> None:
    async with Kernel(project, ui=User(True), vault_backend=MemoryBackend()) as k:
        mail_module(k, smtp_server.port)
        result = await k.dispatcher.call(
            "mail.send", {"to": "you@example.org", "subject": "Hi", "body": "Hello"}
        )
    assert not result.ok
    error = str(result.error)
    assert "could not send the email" in error
    assert "verify_certificate: false" in error and "config/modules/mail.yaml" in error
    assert not any(c.startswith("AUTH") for c in smtp_server.seen)


async def test_smtp_works_with_a_self_signed_certificate_when_switched_off(
    project: Paths, smtp_server: FakeMailServer
) -> None:
    async with Kernel(project, ui=User(True), vault_backend=MemoryBackend()) as k:
        module = mail_module(k, smtp_server.port)
        module.config.verify_certificate = False
        result = await k.dispatcher.call(
            "mail.send", {"to": "you@example.org", "subject": "Hi", "body": "Hello"}
        )
    assert result.ok, result.error
    assert any(line == "Subject: Hi" for line in smtp_server.seen)
    password = base64.b64encode(b"\0me@home.test\0app-pw").decode()
    assert f"AUTH PLAIN {password}" in smtp_server.seen


async def test_setup_keeps_settings_made_by_hand(project: Paths) -> None:
    (project.module_configs / "mail.yaml").write_text(
        "max_body_chars: 5000\nverify_certificate: false\nnotify_from: ['@bank.de']\n"
    )
    async with Kernel(project, ui=User(True), vault_backend=MemoryBackend()) as k:
        result = await k.dispatcher.call(
            "mail.configure",
            {
                "username": "me@web.de",
                "password": "pw",
                "imap_host": "imap.web.de",
                "smtp_host": "smtp.web.de",
            },
        )
        assert result.ok, result.error
    saved = read_yaml(project.module_configs / "mail.yaml")
    assert saved["max_body_chars"] == 5000 and saved["verify_certificate"] is False
    assert saved["notify_from"] == ["@bank.de"] and saved["imap_host"] == "imap.web.de"
    assert "modules/mail" in (project.module_configs / "mail.yaml").read_text()


async def test_a_damaged_seen_file_is_a_first_look_not_an_error(project: Paths) -> None:
    imap = FakeIMAP({11: mail(11, "A <a@x.org>", "Hi", "Text")}, unseen={11})
    async with Kernel(project, ui=User(), vault_backend=MemoryBackend()) as k:
        module = mod(k)
        module.config.username, module.config.imap_host = "me@web.de", "imap.web.de"
        module.ctx.vault.set("mail.password", "pw")
        module._imap_connect = lambda password: imap
        module.seen_file.parent.mkdir(parents=True, exist_ok=True)
        module.seen_file.write_text('{"validity": "777", "uids": [')  # half-written
        assert await module.check_new() == []  # the first look only takes note
        saved = json.loads(module.seen_file.read_text())
        assert saved == {"validity": "777", "uids": ["11"]}
        assert list(module.seen_file.parent.glob("seen.unreadable-*.json"))  # kept, not lost


# -- login --------------------------------------------------------------------------------------


@pytest.fixture
def login(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("login")
    (paths.module_configs / "login.yaml").write_text(
        "sites:\n  shop:\n    url: https://x.test/in\n"
    )
    return paths


async def test_a_damaged_approved_file_asks_again_instead_of_failing(login: Paths) -> None:
    user = User(True)
    async with Kernel(login, ui=user, vault_backend=MemoryBackend()) as k:
        module = instance(k, "login")
        module.approved_file.write_text('["shop"')  # half-written
        await module._ensure_approved("shop", module._site("shop"))
        assert len(user.questions) == 1
        assert json.loads(module.approved_file.read_text()) == ["shop"]
        await module._ensure_approved("shop", module._site("shop"))
        assert len(user.questions) == 1  # approved now: not asked again


async def test_open_logs_in_again_once_when_the_session_has_expired(login: Paths) -> None:
    async with Kernel(login, ui=User(), vault_backend=MemoryBackend()) as k:
        module = instance(k, "login")
        module._session("shop").write_text("{}")
        pages = [(200, "<title>Sign in</title>Sign in", "https://x.test/in", True)]
        pages.append((200, "<title>Account</title>Balance 42", "https://x.test/acct", False))
        logins: list[str] = []

        async def fetch(site: str, config: Any, url: str) -> Any:
            return pages.pop(0)

        async def log_in(site: str) -> str:
            logins.append(site)
            return "ok"

        module._fetch, module.login = fetch, log_in
        page = await call(k, "login.open", site="shop", url="https://x.test/acct")
    assert logins == ["shop"] and page["title"] == "Account" and "Balance 42" in page["text"]


# -- network ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git log --output=/tmp/x",
        "git diff --output /tmp/x",
        "git show --output=/tmp/x HEAD",
        "/tmp/evil/cat /etc/passwd",
        "./ls",
        "/df -h",
    ],
)
def test_read_only_check_refuses_file_writes_and_unknown_programs(command: str) -> None:
    from modules.network.readonly import is_read_only

    assert not is_read_only(command)


@pytest.mark.parametrize(
    "command",
    [
        "/usr/bin/df -h",
        "/bin/ls -la",
        "git log --oneline -5",
        "git diff HEAD~1 --stat",
        "git log --output-indicator-new=+",
    ],
)
def test_read_only_check_still_allows_ordinary_commands(command: str) -> None:
    from modules.network.readonly import is_read_only

    assert is_read_only(command)


async def test_forgetting_an_unlisted_name_changes_nothing_even_with_any_host(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("network")
    (paths.module_configs / "network.yaml").write_text("any_host: true\n")
    listing = "servers:\n  nas:\n    host: 192.168.1.5\n"
    (paths.config / "servers.yaml").write_text(listing)
    async with Kernel(paths, ui=User(True)) as k:
        message = await fails(k, "network.forget", name="printer")
        assert "not on the list" in message
        assert (paths.config / "servers.yaml").read_text() == listing  # not even rewritten
        assert (await call(k, "network.forget", name="192.168.1.5")) == {"forgot": "nas"}
    assert read_yaml(paths.config / "servers.yaml")["servers"] == {}


# -- notes --------------------------------------------------------------------------------------


@pytest.fixture
def notes(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("notes")
    return paths


async def test_appending_to_an_unclear_title_asks_instead_of_making_a_new_note(
    notes: Paths,
) -> None:
    async with Kernel(notes) as k:
        await call(k, "notes.note", title="Car service", text="oil")
        await call(k, "notes.note", title="Car insurance", text="paid")
        message = await fails(k, "notes.append", title="car", text="tyres")
        assert "matches several notes" in message and "Car insurance" in message
        titles = {n["title"] for n in (await call(k, "notes.list"))["notes"]}
        assert titles == {"Car service", "Car insurance"}  # no stray "car" note
        added = await call(k, "notes.append", title="Boat", text="paint")  # unknown: a new note
        assert added["saved"] == "Boat"


async def test_find_stops_at_100_lines_even_inside_one_big_note(notes: Paths) -> None:
    async with Kernel(notes) as k:
        await call(k, "notes.note", title="Log", text="\n".join(["needle"] * 500))
        found = await call(k, "notes.find", text="needle")
    assert found["count"] == 100 and len(found["matches"]) == 100


# -- media --------------------------------------------------------------------------------------


async def test_now_playing_keeps_a_bar_in_the_title(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("media")
    async with Kernel(paths) as k:
        media = instance(k, "media")
        media._on_windows = lambda: False
        media._run = lambda *command: "Playing\tSome Band\tSong | Remix"
        playing = await call(k, "media.now_playing")
        assert playing == {"status": "playing", "artist": "Some Band", "title": "Song | Remix"}
        media._run = lambda *command: "Paused"  # nothing else known
        assert (await call(k, "media.now_playing"))["title"] == ""


# -- notify -------------------------------------------------------------------------------------


def test_the_notification_app_name_reaches_the_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    from modules.notify import backends

    runs: list[tuple[list[str], dict[str, str] | None]] = []
    monkeypatch.setattr(
        backends.Backend, "_run", staticmethod(lambda args, env=None: runs.append((args, env)))
    )
    backends.Backend("powershell-toast", "windows", "My Home").send("T", "M")
    args, env = runs[0]
    assert env is not None and env["STARLIGHT_NOTIFY_APP"] == "My Home"
    assert "CreateToastNotifier($env:STARLIGHT_NOTIFY_APP)" in args[-1]
    backends.Backend("notify-send", "linux", "My Home").send("T", "M", urgent=True)
    assert runs[1][0] == ["notify-send", "-a", "My Home", "-u", "critical", "T", "M"]
    assert backends.Backend("powershell-toast", "windows").app_name == "Starlight"


async def test_the_notify_module_hands_app_name_to_the_backend(
    paths: Paths, use_module: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:

    use_module("notify")
    (paths.module_configs / "notify.yaml").write_text("app_name: My Home\n")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/notify-send")
    async with Kernel(paths) as k:
        assert instance(k, "notify").backend.app_name == "My Home"
