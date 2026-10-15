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

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from starlight.browser import Browser
from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.config import read_yaml, write_yaml_config
from starlight.core.jsonfile import load_json, save_json
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


class LoginConfig(BaseModel):
    sites: dict[str, Site] = Field(default_factory=dict)
    headless: bool = True
    timeout: float = 30.0
    respect_robots: bool = False  # your own accounts; robots.txt targets crawlers


class LoginError(ToolError):
    pass


class LoginModule(Module):
    Config = LoginConfig

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: LoginConfig = ctx.config
        self.browser = Browser(
            ctx.http, headless=self.config.headless, respect_robots=self.config.respect_robots
        )
        self.sessions = ctx.data_dir / "sessions"
        self.sessions.mkdir(parents=True, exist_ok=True)
        self.approved_file = ctx.data_dir / "approved.json"
        ctx.kill_switch.add_cleanup(self.browser.close, owner=ctx.name)

    async def shutdown(self) -> None:
        await self.browser.close()

    def _config_file(self) -> Path:
        return self.ctx.paths.module_configs / "login.yaml"

    def _live(self) -> LoginConfig:
        """Re-read the config so sites added from the chat work without a restart."""
        try:
            return LoginConfig.model_validate(read_yaml(self._config_file()))
        except ValidationError:
            return self.config

    def _site(self, name: str) -> Site:
        sites = self._live().sites
        site = sites.get(name)
        if site is None:
            known = ", ".join(sorted(sites)) or "none configured"
            raise LoginError(f"unknown site '{name}' ({known})")
        return site

    def _session(self, name: str) -> Path:
        return self.sessions / f"{name}.json"

    def _approved(self) -> set[str]:
        return set(load_json(self.approved_file, []))  # a damaged file: asked again

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
        save_json(self.approved_file, sorted(approved))
        self.ctx.logger.info("site approved for login", site=name)

    @tool
    def sites(self) -> list[dict[str, Any]]:
        """Configured sites and whether a saved session exists."""
        return [
            {"site": name, "url": s.url, "session": self._session(name).is_file()}
            for name, s in sorted(self._live().sites.items())
        ]

    LOGIN_HEADER = (
        "# Sites Starlight can log in to (Step 6). Starlight edits this from the chat\n"
        "# (login.add_site); you may also edit it by hand. Passwords are NEVER here —\n"
        "# they live in the OS keyring under login.<site>.username / .password."
    )

    def _write_sites(self, config: LoginConfig) -> None:
        data = config.model_dump(exclude_defaults=True)
        write_yaml_config(self._config_file(), data, self.LOGIN_HEADER)

    @tool
    async def add_site(
        self,
        site: str,
        url: str,
        username: str,
        password: str,
        username_selector: str = "",
        password_selector: str = "",
        success_selector: str = "",
    ) -> dict[str, Any]:
        """Set up a login for one of the user's own accounts, so they never edit files.
        site is a short name (e.g. "github"); url is the login page. The username and
        password are stored in the OS keyring, never in a file or a log. The selectors are
        optional (Starlight guesses the usual ones). Asks the user first; the password is
        never shown back."""
        name = site.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
            raise LoginError("site name: lowercase letters, digits, '-' and '_' only")
        if not url.strip().startswith(("http://", "https://")):
            raise LoginError("give the login page URL (https://...)")
        ok = await self.ctx.ui.confirm(
            f"Save a login for '{name}' at {url.strip()}?\n"
            "  The username and password go into the OS keyring (not any file). "
            "The password is hidden."
        )
        if not ok:
            raise LoginError("not confirmed, so nothing was saved")
        self.ctx.kill_switch.check()
        self.ctx.vault.set(f"login.{name}.username", username)
        self.ctx.vault.set(f"login.{name}.password", password)
        fields: dict[str, Any] = {"url": url.strip()}
        for key, value in (
            ("username_selector", username_selector),
            ("password_selector", password_selector),
            ("success_selector", success_selector),
        ):
            if value.strip():
                fields[key] = value.strip()
        config = self._live()
        config.sites[name] = Site(**fields)
        self._write_sites(config)
        self.ctx.logger.info("login site added", site=name)
        return {"added": name, "url": url.strip(), "note": "Try: log in to " + name}

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
        status, html, final, expired = await self._fetch(site, config, url)
        if expired:
            await self.login(site)  # the saved session no longer works
            status, html, final, _ = await self._fetch(site, config, url)
        text = html_to_text(html, final, max_chars)
        return {"url": final, "status": status, "title": text.title, "text": text.text}

    async def _fetch(self, site: str, config: Site, url: str) -> tuple[int | None, str, str, bool]:
        """Open a page with the saved session: (status, html, final url, whether the page
        shows the login form, so the session has expired)."""
        context = await self.browser.new_context(self._session(site))
        try:
            page = await context.new_page()
            page.set_default_timeout(self.config.timeout * 1000)
            status = await self.browser.goto(page, url)
            expired = await page.locator(config.password_selector).first.is_visible()
            return status, await page.content(), page.url, expired
        finally:
            await context.close()

    @tool
    def logout(self, site: str) -> str:
        """Forget the saved session for a site."""
        self._site(site)
        path = self._session(site)
        if path.is_file():
            path.unlink()
            return f"Session for {site} removed."
        return f"No saved session for {site}."
