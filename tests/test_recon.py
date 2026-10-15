"""The recon module: it finds what a host exposes, only with authorization, only one host,
and bounded so a hostile host cannot flood this machine. It never attacks."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.core.vault import MemoryBackend


class User:
    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.questions: list[str] = []

    async def confirm(self, question: str) -> bool:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else False

    async def ask(self, q: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("recon")
    (paths.config / "servers.yaml").write_text("servers: {}\n")
    return paths


def recon(k: Kernel) -> Any:
    module = k.modules.records["recon"].instance
    assert module is not None
    return module


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def serve(banner: bytes, size: int = 0) -> asyncio.AbstractServer:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(banner or (b"X" * size))
        with contextlib.suppress(Exception):
            await writer.drain()
            await asyncio.sleep(0.2)
            writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


def port_of(server: asyncio.AbstractServer) -> int:
    return int(server.sockets[0].getsockname()[1])


async def test_scan_finds_open_ports_and_banners_on_localhost(project: Paths) -> None:
    server = await serve(b"SSH-2.0-OpenSSH_9.6\r\n")
    open_port = port_of(server)
    closed = free_port()  # bound then released: nothing listens here now
    async with server, Kernel(project, ui=User(True), vault_backend=MemoryBackend()) as k:
        result = await recon(k).scan("127.0.0.1", ports=f"{open_port} {closed}")
    found = {p["port"]: p for p in result["open_ports"]}
    assert open_port in found and closed not in found  # the closed port is not reported
    assert "OpenSSH" in found[open_port].get("banner", "")
    assert result["ip"] == "127.0.0.1" and result["ports_scanned"] == 2


async def test_a_hostile_host_cannot_flood_the_scanner(project: Paths) -> None:
    """A port that pours out data must not hang or fill memory: the read is capped."""
    server = await serve(b"", size=50_000_000)  # 50 MB waiting to be read
    async with server, Kernel(project, ui=User(True), vault_backend=MemoryBackend()) as k:
        started = time.monotonic()
        result = await recon(k).scan("127.0.0.1", ports=str(port_of(server)))
        elapsed = time.monotonic() - started
    [entry] = result["open_ports"]
    assert len(entry.get("banner", "")) <= 200  # only a little was read, not 50 MB
    assert elapsed < 10  # bounded by timeouts, did not stall


async def test_public_host_needs_authorization_and_is_remembered(project: Paths) -> None:
    async with Kernel(project, ui=User(False), vault_backend=MemoryBackend()) as k:
        refused = await k.dispatcher.call("recon.scan", {"target": "8.8.8.8", "ports": "80"})
        assert not refused.ok and "NOT run" in str(refused.error)  # refused without a yes
    assert "8.8.8.8" not in (project.config / "servers.yaml").read_text()

    user = User(True)
    async with Kernel(project, ui=user, vault_backend=MemoryBackend()) as k:
        module = recon(k)
        module._scan_ports = _no_ports  # don't reach the internet from a test
        module._probe_open = _no_details
        module._ping = _no_ping
        await module.scan("8.8.8.8", ports="80")
    assert any("PUBLIC host" in q for q in user.questions)  # it warned about legality
    # remembered for the next scan, but not added to the network tools' allow-list
    assert "8.8.8.8" not in (project.config / "servers.yaml").read_text()

    again = User(True)
    async with Kernel(project, ui=again, vault_backend=MemoryBackend()) as k:
        module = recon(k)
        module._scan_ports = _no_ports
        module._probe_open = _no_details
        module._ping = _no_ping
        await module.scan("8.8.8.8", ports="80")
    assert "allowed this host before" in again.questions[0]  # asked, no legal warning again


async def test_only_one_host_no_ranges(project: Paths) -> None:
    async with Kernel(project, ui=User(True), vault_backend=MemoryBackend()) as k:
        for bad in ("10.0.0.0/24", "10.0.0.1-50", "10.0.0.1 10.0.0.2", "a,b"):
            result = await k.dispatcher.call("recon.scan", {"target": bad})
            assert not result.ok and "one host" in str(result.error).lower(), bad
        for name in ("my-nas", "router-1", "nas-2.home.lan"):  # ordinary names with a dash
            assert recon(k)._one_host(name) == name


async def test_http_and_cert_parsing_are_read_only(project: Paths) -> None:
    async with Kernel(project, ui=User(), vault_backend=MemoryBackend()) as k:
        module = recon(k)
    http = module._parse_http(
        b"HTTP/1.1 200 OK\r\nServer: nginx/1.25\r\nContent-Type: text/html\r\n\r\n"
    )
    assert http["http"]["server"] == "nginx/1.25" and "200 OK" in http["http_status"]
    cert = module._cert_summary(
        {
            "subject": [[("commonName", "example.com")]],
            "issuer": [[("organizationName", "Let's Encrypt")]],
            "notAfter": "Jan  1 00:00:00 2099 GMT",
            "subjectAltName": [("DNS", "example.com"), ("DNS", "www.example.com")],
        }
    )
    assert cert["subject"] == "example.com" and cert["issuer"] == "Let's Encrypt"
    assert cert["days_left"] > 0 and "www.example.com" in cert["alt_names"]


async def _no_ports(ip: str, ports: list[int]) -> list[tuple[int, str]]:
    return []


async def _no_details(host: str, ip: str, ports: list[int]) -> dict[int, dict[str, Any]]:
    return {}


async def _no_ping(ip: str) -> bool:
    return False
