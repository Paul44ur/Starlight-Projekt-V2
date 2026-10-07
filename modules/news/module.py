"""Headlines and topic search from free news feeds (RSS/Atom) - no account, no API key.

By default it searches Google News for a topic; with no topic it reads the feeds you have
added (or Google News top stories). Requests go through Starlight's shared, rate-limited web
client. It only reads feeds; it never signs in anywhere.
"""

from __future__ import annotations

import urllib.parse
from typing import Any
from xml.etree import ElementTree

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.config import write_yaml_config
from starlight.core.http import HttpError

GOOGLE_SEARCH = "https://news.google.com/rss/search"
GOOGLE_TOP = "https://news.google.com/rss"


class Config(BaseModel):
    feeds: list[str] = Field(default_factory=list)  # extra RSS/Atom feed URLs you follow
    region: str = "en-US"  # Google News language-region, e.g. "en-GB", "de-DE"
    max_items: int = Field(default=10, ge=1, le=50)


class NewsModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    def _config_file(self) -> Any:
        return self.ctx.paths.module_configs / "news.yaml"

    def _region_params(self) -> str:
        lang, _, country = self.config.region.partition("-")
        country = country or lang.upper()
        return f"hl={self.config.region}&gl={country}&ceid={country}:{lang}"

    @tool
    async def headlines(self, topic: str = "", limit: int = 0) -> dict[str, Any]:
        """Recent news. Give a `topic` (e.g. "AMD GPUs", "Vienna weather") to search, or
        leave it empty for your followed feeds / top stories. Returns titles, sources,
        times and links."""
        count = min(limit or self.config.max_items, 50)
        if topic.strip():
            query = urllib.parse.quote(topic.strip())
            url = f"{GOOGLE_SEARCH}?q={query}&{self._region_params()}"
            items = await self._read_feed(url)
            return {"topic": topic.strip(), "count": len(items[:count]), "headlines": items[:count]}
        sources = self.config.feeds or [f"{GOOGLE_TOP}?{self._region_params()}"]
        collected: list[dict[str, Any]] = []
        for feed in sources:
            try:
                collected.extend(await self._read_feed(feed))
            except ToolError:
                continue
            if len(collected) >= count:
                break
        if not collected:
            raise ToolError("could not read any news feed right now")
        return {"count": len(collected[:count]), "headlines": collected[:count]}

    @tool
    def feeds(self) -> dict[str, Any]:
        """The news feeds you follow (used when you ask for headlines with no topic)."""
        return {"feeds": list(self.config.feeds), "region": self.config.region}

    @tool
    async def add_feed(self, url: str) -> dict[str, Any]:
        """Follow an RSS/Atom feed, so "the news" with no topic includes it. Give the feed's
        URL (often ending in /rss, /feed or .xml)."""
        url = url.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ToolError("give a feed URL starting with http:// or https://")
        items = await self._read_feed(url)  # verify it parses before saving
        if url in self.config.feeds:
            return {"added": False, "note": "that feed is already followed"}
        self.config.feeds.append(url)
        write_yaml_config(
            self._config_file(),
            {
                "feeds": self.config.feeds,
                "region": self.config.region,
                "max_items": self.config.max_items,
            },
            "# The news module (modules/news). Added with news.add_feed.",
        )
        return {"added": True, "url": url, "sample": items[0]["title"] if items else None}

    # -- feed reading --------------------------------------------------------------------

    async def _read_feed(self, url: str) -> list[dict[str, Any]]:
        try:
            response = await self.ctx.http.get(url, respect_robots=False)
        except HttpError as exc:
            raise ToolError(f"could not reach the news feed: {exc}") from exc
        if not response.ok:
            raise ToolError(f"the news feed returned HTTP {response.status}")
        return parse_feed(response.text)


def _tag(element: ElementTree.Element) -> str:
    """The local tag name, without any XML namespace."""
    return element.tag.rsplit("}", 1)[-1]


def _find_text(item: ElementTree.Element, name: str) -> str:
    for child in item:
        if _tag(child) == name and child.text:
            return child.text.strip()
    return ""


def _find_link(item: ElementTree.Element) -> str:
    # RSS: <link>url</link>; Atom: <link href="url"/>
    for child in item:
        if _tag(child) == "link":
            if child.text and child.text.strip():
                return child.text.strip()
            href = child.get("href")
            if href:
                return href.strip()
    return ""


def parse_feed(xml: str) -> list[dict[str, Any]]:
    """Parse an RSS or Atom feed into a list of headline dicts."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ToolError(f"that did not look like a news feed ({exc})") from None
    items: list[dict[str, Any]] = []
    for element in root.iter():
        if _tag(element) not in ("item", "entry"):
            continue
        title = _find_text(element, "title")
        if not title:
            continue
        when = (
            _find_text(element, "pubDate")
            or _find_text(element, "published")
            or _find_text(element, "updated")
        )
        source = _find_text(element, "source")
        items.append(
            {
                "title": title,
                "source": source or None,
                "published": when or None,
                "link": _find_link(element),
            }
        )
    return items
