"""The cve module: it queries the NVD REST API through the shared web client, no key."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.core import Kernel, Paths

LOG4SHELL = {
    "vulnerabilities": [
        {
            "cve": {
                "id": "CVE-2021-44228",
                "published": "2021-12-10T10:15:09.143",
                "lastModified": "2023-11-07T03:39:43.337",
                "descriptions": [
                    {"lang": "en", "value": "Apache Log4j2 JNDI features (Log4Shell) RCE."},
                    {"lang": "es", "value": "(spanish)"},
                ],
                "metrics": {
                    "cvssMetricV31": [{"cvssData": {"baseScore": 10.0, "baseSeverity": "CRITICAL"}}]
                },
                "references": [{"url": "https://logging.apache.org/log4j/"}],
            }
        }
    ]
}

SEARCH = {
    "totalResults": 1,
    "vulnerabilities": [
        {
            "cve": {
                "id": "CVE-2022-0001",
                "published": "2022-01-01T00:00:00.000",
                "descriptions": [{"lang": "en", "value": "An OpenSSL issue."}],
                "metrics": {
                    "cvssMetricV30": [{"cvssData": {"baseScore": 7.5, "baseSeverity": "HIGH"}}]
                },
            }
        }
    ],
}


def transport(body: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("cve")
    return paths


async def call(k: Kernel, tool: str, **args: Any) -> Any:
    return await k.dispatcher.call(f"cve.{tool}", args)


async def test_lookup(project: Paths) -> None:
    async with Kernel(project, http_transport=transport(LOG4SHELL)) as k:
        result = await call(k, "lookup", cve_id="cve-2021-44228")
    out = result.output
    assert out["id"] == "CVE-2021-44228" and out["severity"] == "CRITICAL" and out["score"] == 10.0
    assert "Log4j2" in out["summary"]
    assert out["references"] == ["https://logging.apache.org/log4j/"]


async def test_lookup_rejects_bad_id(project: Paths) -> None:
    async with Kernel(project, http_transport=transport({"vulnerabilities": []})) as k:
        result = await call(k, "lookup", cve_id="not-a-cve")
    assert not result.ok and "CVE id" in str(result.error)


async def test_lookup_missing(project: Paths) -> None:
    async with Kernel(project, http_transport=transport({"vulnerabilities": []})) as k:
        result = await call(k, "lookup", cve_id="CVE-2000-9999")
    assert not result.ok and "no record found" in str(result.error)


async def test_search(project: Paths) -> None:
    async with Kernel(project, http_transport=transport(SEARCH)) as k:
        result = await call(k, "search", product="openssl", version="3.0.0")
    out = result.output
    assert out["total"] == 1 and out["results"][0]["id"] == "CVE-2022-0001"
    assert out["results"][0]["severity"] == "HIGH"
