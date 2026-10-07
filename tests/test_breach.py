"""The breach module: it checks a password against Have I Been Pwned using k-anonymity
(only a hash prefix is sent) and rates strength locally. The password never leaves the PC."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.core import Kernel, Paths


def digest(password: str) -> tuple[str, str]:
    full = hashlib.sha1(password.encode()).hexdigest().upper()
    return full[:5], full[5:]


def transport(pwned_password: str, times: int = 42) -> tuple[httpx.MockTransport, list[str]]:
    prefix, suffix = digest(pwned_password)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        got_prefix = request.url.path.rsplit("/", 1)[-1]
        body = "00000000000000000000000000000000000:1\nDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEA:2\n"
        if got_prefix == prefix:
            body += f"{suffix}:{times}\n"
        return httpx.Response(200, text=body)

    return httpx.MockTransport(handler), seen


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("breach")
    return paths


async def call(k: Kernel, tool: str, **args: Any) -> Any:
    return await k.dispatcher.call(f"breach.{tool}", args)


async def test_pwned_password_is_found(project: Paths) -> None:
    trans, seen = transport("password", times=9999)
    async with Kernel(project, http_transport=trans) as k:
        result = await call(k, "password_pwned", password="password")
    assert result.output["pwned"] is True and result.output["times_seen"] == 9999
    # only the 5-char prefix was ever sent, never the full hash (k-anonymity)
    full = hashlib.sha1(b"password").hexdigest().upper()
    requests = [u for u in seen if "/range/" in u]
    assert all(full not in url.upper() for url in requests)
    assert all(url.rsplit("/range/", 1)[-1] == full[:5] for url in requests)


async def test_unknown_password_is_not_found(project: Paths) -> None:
    trans, _ = transport("password")
    async with Kernel(project, http_transport=trans) as k:
        result = await call(k, "password_pwned", password="a-very-unusual-passphrase-xyz-42")
    assert result.output["pwned"] is False and result.output["times_seen"] == 0


async def test_how_safe_rates_strength(project: Paths) -> None:
    async with Kernel(project) as k:
        weak = await call(k, "how_safe", password="123456")
        strong = await call(k, "how_safe", password="Tr0ub4dor&3-xkcd-horse")
    assert weak.output["rating"] == "weak"
    assert strong.output["rating"] == "strong"
    assert "shorter than 12 characters" in weak.output["weaknesses"]
