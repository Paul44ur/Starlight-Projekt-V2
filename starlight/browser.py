"""Headless browser (Playwright) for pages that need JavaScript and for logins.

Every request the browser makes goes through the shared HTTP limits: page loads check
robots.txt and the per-domain spacing, and each request (documents, scripts, XHR) counts
against the global requests-per-second cap. Images, media and fonts are not loaded.

Install once: ``uv sync --extra browser`` then ``uv run playwright install chromium``.
Set ``STARLIGHT_CHROMIUM`` to use an already installed Chromium instead.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from starlight.core.http import PoliteHttp

if TYPE_CHECKING:
    from playwright.async_api import Browser as PWBrowser
    from playwright.async_api import BrowserContext, Page, Playwright, Route

BLOCKED_TYPES = {"image", "media", "font"}


class BrowserUnavailable(Exception):
    pass


class Browser:
    def __init__(
        self,
        http: PoliteHttp,
        headless: bool = True,
        executable_path: str | None = None,
        respect_robots: bool = True,
    ) -> None:
        self.http = http
        self.headless = headless
        self.executable_path = executable_path or os.environ.get("STARLIGHT_CHROMIUM") or None
        self.respect_robots = respect_robots
        self._playwright: Playwright | None = None
        self._browser: PWBrowser | None = None

    async def _ensure(self) -> PWBrowser:
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserUnavailable("install the 'browser' extra (playwright)") from exc
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless, executable_path=self.executable_path
        )
        return self._browser

    async def new_context(self, storage_state: Path | None = None) -> BrowserContext:
        browser = await self._ensure()
        state: Any = str(storage_state) if storage_state and storage_state.is_file() else None
        context = await browser.new_context(
            storage_state=state, user_agent=self.http.config.user_agent
        )
        await context.route("**/*", self._route)
        return context

    async def _route(self, route: Route) -> None:
        request = route.request
        if request.resource_type in BLOCKED_TYPES:
            await route.abort()
            return
        if request.url.startswith(("http://", "https://")):
            await self.http.count_request(request.url)
        await route.continue_()

    async def goto(self, page: Page, url: str) -> int | None:
        """Navigate under the polite limits. Returns the HTTP status if known."""
        await self.http.navigation_permit(url, self.respect_robots)
        response = await page.goto(url, wait_until="domcontentloaded")
        return response.status if response is not None else None

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
