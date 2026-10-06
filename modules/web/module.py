"""Web search and polite scraping.

Every request goes through the core's shared HTTP client (``ctx.http``): at most 5
requests in any second across all modules, 1 per second per domain, robots.txt and
crawl-delay respected, 429/503 back off with Retry-After, and pages cached. Plain HTTP
first; a headless browser only for pages that need JavaScript.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel

from starlight.browser import Browser, BrowserUnavailable
from starlight.core import Module, ModuleContext, tool
from starlight.core.http import HttpError, RobotsDisallowed
from starlight.text import html_to_text

JS_HINTS = (
    "enable javascript",
    "requires javascript",
    "javascript is disabled",
    "you need to enable javascript",
)


class Config(BaseModel):
    search_backend: Literal["searxng", "brave"] = "searxng"
    searxng_url: str = "http://127.0.0.1:8888"  # your local SearXNG instance
    brave_key: str = "web.brave_api_key"  # vault key for the Brave Search API
    max_chars: int = 8000
    browser_fallback: bool = True  # use the headless browser when a page needs JS
    headless: bool = True


class WebModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.browser = Browser(ctx.http, headless=self.config.headless)
        ctx.kill_switch.add_cleanup(self.browser.close, owner=ctx.name)

    async def shutdown(self) -> None:
        await self.browser.close()

    # -- search ------------------------------------------------------------------------

    @tool
    async def search(self, query: str, max_results: int = 8) -> list[dict[str, str]]:
        """Search the web. Returns title, url and snippet for each result."""
        if self.config.search_backend == "brave":
            return await self._brave(query, max_results)
        return await self._searxng(query, max_results)

    async def _searxng(self, query: str, max_results: int) -> list[dict[str, str]]:
        url = self.config.searxng_url.rstrip("/") + "/search"
        response = await self.ctx.http.get(
            url, params={"q": query, "format": "json"}, respect_robots=False
        )
        if not response.ok:
            raise HttpError(f"SearXNG returned HTTP {response.status} (is JSON output enabled?)")
        results = response.json().get("results", [])[:max_results]
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
            for r in results
        ]

    async def _brave(self, query: str, max_results: int) -> list[dict[str, str]]:
        key = self.ctx.vault.require(self.config.brave_key)
        response = await self.ctx.http.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
            respect_robots=False,
        )
        if not response.ok:
            raise HttpError(f"Brave Search returned HTTP {response.status}")
        results = response.json().get("web", {}).get("results", [])[:max_results]
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("description", ""),
            }
            for r in results
        ]

    # -- fetching ----------------------------------------------------------------------

    @tool
    async def fetch(self, url: str, render_js: bool = False, max_chars: int = 0) -> dict[str, Any]:
        """Read a web page as text. Set render_js only for pages that need JavaScript."""
        limit = max_chars or self.config.max_chars
        try:
            if render_js:
                return await self._render(url, limit)
            response = await self.ctx.http.get(url)
        except RobotsDisallowed as exc:
            return {"url": url, "error": str(exc)}
        if not response.ok:
            return {"url": url, "status": response.status, "error": f"HTTP {response.status}"}
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type and content_type:
            return {
                "url": response.url,
                "status": response.status,
                "text": response.text[:limit],
                "from_cache": response.from_cache,
            }
        page = html_to_text(response.text, response.url, limit)
        if self.config.browser_fallback and self._needs_js(page.text):
            try:
                return await self._render(url, limit)
            except BrowserUnavailable:
                pass
        await self.ctx.publish("web.fetched", {"url": response.url, "status": response.status})
        return {
            "url": response.url,
            "status": response.status,
            "title": page.title,
            "text": page.text,
            "links": len(page.links),
            "from_cache": response.from_cache,
        }

    @staticmethod
    def _needs_js(text: str) -> bool:
        lowered = text.lower()
        return len(text) < 200 or any(h in lowered for h in JS_HINTS)

    async def _render(self, url: str, limit: int) -> dict[str, Any]:
        context = await self.browser.new_context()
        try:
            page = await context.new_page()
            status = await self.browser.goto(page, url)
            await page.wait_for_load_state("networkidle", timeout=15000)
            html = await page.content()
            final_url = page.url
        finally:
            await context.close()
        text = html_to_text(html, final_url, limit)
        await self.ctx.publish("web.fetched", {"url": final_url, "status": status, "js": True})
        return {
            "url": final_url,
            "status": status,
            "title": text.title,
            "text": text.text,
            "links": len(text.links),
            "rendered": True,
        }

    @tool
    async def fetch_many(self, urls: list[str], max_chars_each: int = 2000) -> list[dict[str, Any]]:
        """Read several pages (for research). Requests are spread out to stay polite."""

        async def one(url: str) -> dict[str, Any]:
            try:
                return await self.fetch(url, max_chars=max_chars_each)
            except HttpError as exc:
                return {"url": url, "error": str(exc)}

        return list(await asyncio.gather(*(one(u) for u in urls[:50])))

    @tool
    async def links(self, url: str, same_site: bool = True) -> list[str]:
        """List the links on a page."""
        response = await self.ctx.http.get(url)
        page = html_to_text(response.text, response.url)
        if not same_site:
            return page.links[:200]
        from urllib.parse import urlsplit

        host = urlsplit(response.url).netloc
        return [link for link in page.links if urlsplit(link).netloc == host][:200]
