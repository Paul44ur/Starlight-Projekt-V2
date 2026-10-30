"""Podcasts: subscribe to a feed (its RSS address), see the newest episodes of each, and play
one in your usual player. Feeds are fetched through Starlight's web client (rate limits,
size caps); nothing is downloaded until you play an episode.
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.http import HttpError
from starlight.core.jsonfile import load_json, save_json


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_podcast(xml: str) -> dict[str, Any]:
    """The show's title and its episodes (title, date, length, audio link), newest first as
    the feed lists them."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ToolError(f"that is not a podcast feed ({exc})") from None
    channel = next((e for e in root.iter() if _local(e.tag) == "channel"), None)
    if channel is None:
        raise ToolError("that is not a podcast feed (no channel in it)")
    title = next((c.text or "" for c in channel if _local(c.tag) == "title"), "").strip()
    episodes = []
    for item in channel:
        if _local(item.tag) != "item":
            continue
        fields = {_local(c.tag): c for c in item}
        enclosure = fields.get("enclosure")
        audio = enclosure.get("url", "") if enclosure is not None else ""
        if not audio:
            continue
        episodes.append({
            "title": (fields["title"].text or "").strip() if "title" in fields else "",
            "published": (fields["pubDate"].text or "").strip() if "pubDate" in fields else "",
            "length": (fields["duration"].text or "").strip() if "duration" in fields else "",
            "audio": audio,
        })  # fmt: skip
    return {"title": title, "episodes": episodes}


class PodcastsModule(Module):
    async def setup(self, ctx: ModuleContext) -> None:
        self.file = ctx.data_dir / "podcasts.json"

    def _all(self) -> dict[str, str]:
        return load_json(self.file, {})

    async def _fetch(self, url: str) -> dict[str, Any]:
        try:
            response = await self.ctx.http.get(url, respect_robots=False)
        except HttpError as exc:
            raise ToolError(f"the feed could not be fetched: {exc}") from exc
        if not response.ok:
            raise ToolError(f"the feed answered HTTP {response.status}")
        return parse_podcast(response.text)

    def _find(self, name: str) -> tuple[str, str]:
        feeds = self._all()
        wanted = name.strip().casefold()
        matches = [(t, u) for t, u in feeds.items() if wanted in t.casefold()]
        if not matches:
            raise ToolError(f"no podcast called {name}; subscribe to it first")
        return matches[0]

    @tool
    async def subscribe(self, feed_url: str) -> dict[str, Any]:
        """Subscribe to a podcast by its feed (RSS) address."""
        if not feed_url.startswith(("http://", "https://")):
            raise ToolError("give the podcast's feed address (it starts with https://)")
        show = await self._fetch(feed_url)
        feeds = self._all()
        title = show["title"] or feed_url
        feeds[title] = feed_url
        save_json(self.file, feeds)
        return {"subscribed": title, "episodes": len(show["episodes"])}

    @tool
    def unsubscribe(self, podcast: str) -> dict[str, Any]:
        """Stop following a podcast."""
        title, _ = self._find(podcast)
        feeds = self._all()
        del feeds[title]
        save_json(self.file, feeds)
        return {"unsubscribed": title}

    @tool
    def podcasts(self) -> dict[str, Any]:
        """The podcasts you follow."""
        return {"podcasts": list(self._all())}

    @tool
    async def episodes(self, podcast: str = "", count: int = 3) -> dict[str, Any]:
        """The newest episodes of one podcast, or of each you follow."""
        feeds = [self._find(podcast)] if podcast.strip() else list(self._all().items())
        if not feeds:
            raise ToolError("you follow no podcasts yet: subscribe to one with its feed address")
        out = []
        for title, url in feeds:
            try:
                show = await self._fetch(url)
            except ToolError as exc:
                out.append({"podcast": title, "error": str(exc)})
                continue
            out.append({"podcast": title, "episodes": show["episodes"][: max(1, min(count, 20))]})
        return {"podcasts": out}

    @tool
    async def play(self, podcast: str, episode: int = 1) -> dict[str, Any]:
        """Play an episode (1 = the newest) in your usual audio player."""
        title, url = self._find(podcast)
        show = await self._fetch(url)
        if not 1 <= episode <= len(show["episodes"]):
            raise ToolError(f"{title} has {len(show['episodes'])} episodes in its feed")
        chosen = show["episodes"][episode - 1]
        if self.ctx.has_tool("apps.open_url"):
            opened = await self.ctx.call("apps.open_url", {"url": chosen["audio"]})
            if not opened.ok:
                raise ToolError(f"it could not be opened: {opened.error}")
        return {"playing": chosen["title"], "podcast": title, "audio": chosen["audio"]}
