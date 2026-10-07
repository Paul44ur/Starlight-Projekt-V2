"""The news module: it reads RSS and Atom feeds through the shared web client, with no key."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.core import Kernel, Paths

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Example News</title>
  <item>
    <title>AMD launches a new GPU</title>
    <link>https://example.com/a</link>
    <pubDate>Mon, 06 Oct 2026 10:00:00 GMT</pubDate>
    <source url="https://ex.com">Example</source>
  </item>
  <item>
    <title>Second story</title>
    <link>https://example.com/b</link>
  </item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Atom headline</title>
    <link href="https://example.com/atom"/>
    <updated>2026-10-06T10:00:00Z</updated>
  </entry>
</feed>"""


def transport(body: str = RSS) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=body, headers={"content-type": "application/xml"})

    return httpx.MockTransport(handler)


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("news")
    return paths


async def call(k: Kernel, tool: str, **args: Any) -> Any:
    return await k.dispatcher.call(f"news.{tool}", args)


async def test_search_a_topic(project: Paths) -> None:
    async with Kernel(project, http_transport=transport()) as k:
        result = await call(k, "headlines", topic="AMD GPUs", limit=5)
    out = result.output
    assert out["topic"] == "AMD GPUs" and out["count"] == 2
    assert out["headlines"][0]["title"] == "AMD launches a new GPU"
    assert out["headlines"][0]["source"] == "Example"
    assert out["headlines"][0]["link"] == "https://example.com/a"


async def test_atom_feed_parses(project: Paths) -> None:
    async with Kernel(project, http_transport=transport(ATOM)) as k:
        result = await call(k, "headlines", topic="anything")
    assert result.output["headlines"][0]["title"] == "Atom headline"
    assert result.output["headlines"][0]["link"] == "https://example.com/atom"


async def test_add_feed_persists(project: Paths) -> None:
    async with Kernel(project, http_transport=transport()) as k:
        result = await call(k, "add_feed", url="https://example.com/rss")
        assert result.ok and result.output["added"]
        listed = await call(k, "feeds")
        assert "https://example.com/rss" in listed.output["feeds"]
    saved = (project.module_configs / "news.yaml").read_text()
    assert "https://example.com/rss" in saved


async def test_add_feed_rejects_non_url(project: Paths) -> None:
    async with Kernel(project, http_transport=transport()) as k:
        result = await call(k, "add_feed", url="not a url")
    assert not result.ok and "http://" in str(result.error)
