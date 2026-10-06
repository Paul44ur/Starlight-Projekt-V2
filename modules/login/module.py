"""Log in to your own accounts.

* credentials come from the vault (``starlight vault set login.<site>.username`` and
  ``login.<site>.password``) and are never logged, returned or shown to the model
* a headless browser fills the form and saves the session cookies, so later visits skip
  the login
* 2FA codes are asked from you, never bypassed
* each site needs your confirmation the first time

Configure sites in ``config/modules/login.yaml``:

    sites:
      github:
        url: https://github.com/login
        username_selector: "#login_field"
        password_selector: "#password"
        submit_selector: "input[type=submit]"
        success_selector: "meta[name=user-login]:not([content=''])"
        otp_selector: "#app_totp"
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from starlight.browser import Browser
from starlight.core import Module, ModuleContext, tool
from starlight.text import html_to_text


class Site(BaseModel):
    url: str
    username_selector: str = (
        "input[type=email], input[name=username], input[name=login], input[name=email]"
    )
    password_selector: str = "input[type=password]"
    submit_selector: str = "button[type=submit], input[type=submit]"
    success_selector: str | None = None  # present only when logged in
    success_url_contains: str | None = None
    otp_selector: str | None = None  # the 2FA code field, if the site uses one
    otp_submit_selector: str | None = None
    username_key: str = ""  # vault key, default login.<site>.username
    password_key: str = ""  # vault key, default login.<site>.password


class Config(BaseModel):
    sites: dict[str, Site] = Field(default_factory=dict)
    headless: bool = True
    timeout: float = 30.0
    respect_robots: bool = False  # your own accounts; robots.txt targets crawlers


class LoginError(Exception):
    pass


class LoginModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.browser = Browser(
            ctx.http, headless=self.config.headless, respect_robots=self.config.respect_robots
        )
        self.sessions = ctx.data_dir / "sessions"
        self.sessions.mkdir(parents=True, exist_ok=True)
        self.approved_file = ctx.data_dir / "approved.json"
        ctx.kill_switch.add_cleanup(self.browser.close, owner=ctx.name)

    async def shutdown(self) -> None:
        await self.browser.close()

    def _site(self, name: str) -> Site:
        site = self.config.sites.get(name)
        if site is None:
            known = ", ".join(sorted(self.config.sites)) or "none configured"
            raise LoginError(f"unknown site '{name}' ({known})")
        return site

    def _session(self, name: str) -> Path:
        return self.sessions / f"{name}.json"

    def _approved(self) -> set[str]:
        if not self.approved_file.is_file():
            return set()
        return set(json.loads(self.approved_file.read_text(encoding="utf-8")))

    async def _ensure_approved(self, name: str, site: Site) -> None:
        approved = self._approved()
        if name in approved:
            return
        ok = await self.ctx.ui.confirm(
            f"Allow Starlight to log in to '{name}' ({site.url}) with your saved credentials?"
        )
        if not ok:
            raise LoginError(f"login to '{name}' not approved")
        approved.add(name)
        self.approved_file.write_text(json.dumps(sorted(approved)), encoding="utf-8")
        self.ctx.logger.info("site approved for login", site=name)

    @tool
    def sites(self) -> list[dict[str, Any]]:
        """Configured sites and whether a saved session exists."""
        return [
            {"site": name, "url": s.url, "session": self._session(name).is_file()}
            for name, s in sorted(self.config.sites.items())
        ]

    @tool
    async def login(self, site: str) -> str:
        """Log in to one of your configured sites and save the session."""
        config = self._site(site)
        await self._ensure_approved(site, config)
        vault = self.ctx.vault
        username = vault.require(config.username_key or f"login.{site}.username")
        password = vault.require(config.password_key or f"login.{site}.password")
        timeout_ms = self.config.timeout * 1000

        context = await self.browser.new_context()
        try:
            page = await context.new_page()
            page.set_default_timeout(timeout_ms)
            await self.browser.goto(page, config.url)
            await page.locator(config.username_selector).first.fill(username)
            await page.locator(config.password_selector).first.fill(password)
            await page.locator(config.submit_selector).first.click()
            await page.wait_for_load_state("domcontentloaded")

            if config.otp_selector and await self._visible(page, config.otp_selector):
                code = await self.ctx.ui.ask(f"Enter the 2FA code for {site}")
                if not code:
                    raise LoginError("no 2FA code given")
                await page.locator(config.otp_selector).first.fill(code.strip())
                if config.otp_submit_selector:
                    await page.locator(config.otp_submit_selector).first.click()
                else:
                    await page.keyboard.press("Enter")
                await page.wait_for_load_state("domcontentloaded")

            if not await self._logged_in(page, config):
                raise LoginError(f"login to '{site}' did not succeed (check credentials)")
            await context.storage_state(path=str(self._session(site)))
        finally:
            await context.close()
        self.ctx.logger.info("logged in", site=site)
        await self.ctx.publish("login.succeeded", {"site": site})
        return f"Logged in to {site}; session saved."

    async def _visible(self, page: Any, selector: str) -> bool:
        try:
            await page.locator(selector).first.wait_for(state="visible", timeout=5000)
            return True
        except Exception:
            return False

    async def _logged_in(self, page: Any, site: Site) -> bool:
        if site.success_url_contains and site.success_url_contains in page.url:
            return True
        if site.success_selector:
            return await self._visible(page, site.success_selector)
        # Without a success marker: logged in if the password field is gone.
        return not await page.locator(site.password_selector).first.is_visible()

    @tool
    async def open(self, site: str, url: str, max_chars: int = 8000) -> dict[str, Any]:
        """Read a page of one of your sites using the saved session (logs in if needed)."""
        config = self._site(site)
        if not self._session(site).is_file():
            await self.login(site)
        for attempt in range(2):
            context = await self.browser.new_context(self._session(site))
            try:
                page = await context.new_page()
                page.set_default_timeout(self.config.timeout * 1000)
                status = await self.browser.goto(page, url)
                expired = await page.locator(config.password_selector).first.is_visible()
                html, final = await page.content(), page.url
            finally:
                await context.close()
            if expired and attempt == 0:
                await self.login(site)  # the saved session no longer works
                continue
            text = html_to_text(html, final, max_chars)
            return {"url": final, "status": status, "title": text.title, "text": text.text}
        raise LoginError(f"could not open {url} as '{site}'")

    @tool
    def logout(self, site: str) -> str:
        """Forget the saved session for a site."""
        self._site(site)
        path = self._session(site)
        if path.is_file():
            path.unlink()
            return f"Session for {site} removed."
        return f"No saved session for {site}."
