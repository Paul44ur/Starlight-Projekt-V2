"""The certs module: it reads a TLS certificate and reports expiry. The TLS read is stubbed
so the test needs no network; the parsing and the servers.yaml wiring are what is checked."""

from __future__ import annotations

import ssl
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths


def not_after(days_from_now: float) -> str:
    """A notAfter string in the format ssl.getpeercert() returns."""
    return time.strftime("%b %d %H:%M:%S %Y GMT", time.gmtime(time.time() + days_from_now * 86400))


def fake_cert(days_left: float, cn: str = "example.com") -> dict[str, Any]:
    return {
        "subject": ((("commonName", cn),),),
        "issuer": ((("organizationName", "Test CA"),), (("commonName", "Test CA R3"),)),
        "notBefore": not_after(-30),
        "notAfter": not_after(days_left),
        "subjectAltName": (("DNS", cn), ("DNS", f"www.{cn}")),
    }


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("certs")
    (paths.config / "servers.yaml").write_text(
        "servers:\n"
        "  alpha:\n    host: alpha.test\n    ports: [443]\n"
        "  beta:\n    host: beta.test\n    ports: [443]\n"
    )
    return paths


def mod(k: Kernel) -> Any:
    instance = k.modules.records["certs"].instance
    assert instance is not None
    return instance


async def test_cert_reports_expiry(project: Paths) -> None:
    async with Kernel(project) as k:
        mod(k)._read_cert = lambda host, port: fake_cert(100, cn=host)
        result = await k.dispatcher.call("certs.cert", {"host": "example.com"})
    out = result.output
    assert out["subject"] == "CN=example.com"
    assert "O=Test CA" in out["issuer"]
    assert out["expired"] is False and 98 <= out["days_left"] <= 100
    assert "www.example.com" in out["san"]


async def test_expiring_filters_by_days(project: Paths) -> None:
    async with Kernel(project) as k:
        days = {"alpha.test": 10, "beta.test": 200}
        mod(k)._read_cert = lambda host, port: fake_cert(days[host], cn=host)
        result = await k.dispatcher.call("certs.expiring", {"days": 30})
    out = result.output
    hosts = [c["host"] for c in out["expiring"]]
    assert hosts == ["alpha.test"] and out["within_days"] == 30


async def test_check_all_reports_unreachable(project: Paths) -> None:
    def boom(host: str, port: int) -> dict[str, Any]:
        raise OSError("connection refused")

    async with Kernel(project) as k:
        mod(k)._read_cert = boom
        result = await k.dispatcher.call("certs.check_all", {})
    assert result.ok and all("error" in c for c in result.output["certificates"])


async def test_cert_error_becomes_tool_error(project: Paths) -> None:
    def boom(host: str, port: int) -> dict[str, Any]:
        raise ssl.SSLError("handshake failed")

    async with Kernel(project) as k:
        mod(k)._read_cert = boom
        result = await k.dispatcher.call("certs.cert", {"host": "nope.test"})
    assert not result.ok and "could not read the certificate" in str(result.error)


async def test_an_expired_certificate_is_reported_as_expired(project: Paths) -> None:
    """An expired certificate does not verify; it must still count as expiring (with its
    dates), not as an unreachable server."""

    def expired(host: str, port: int) -> dict[str, Any]:
        return {**fake_cert(-3, cn=host), "verify_error": "certificate has expired"}

    async with Kernel(project) as k:
        mod(k)._read_cert = expired
        one = await k.dispatcher.call("certs.cert", {"host": "alpha.test"})
        soon = await k.dispatcher.call("certs.expiring", {"days": 30})
    assert one.output["expired"] is True and one.output["days_left"] < 0
    assert "expired" in one.output["problem"]
    assert {c["host"] for c in soon.output["expiring"]} == {"alpha.test", "beta.test"}
    assert soon.output["unreachable"] == []
