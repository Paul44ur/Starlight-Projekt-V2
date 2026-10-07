"""Your smart home through Home Assistant: "turn off the living room lights", "is the front
door closed?", "set the bedroom to 40 %", "start the movie scene".

Lights, switches, fans, media players and scenes (the `free_domains` setting) are switched
without asking; anything else - locks, alarms, covers, heating - asks first. The access
token is kept in the keyring. Watched entities announce changes as `home.changed` events,
so automations can react ("when the front door opens while I'm away, tell me").
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, Permission, ToolError, tool
from starlight.core.config import write_yaml_config

TOKEN_KEY = "home.token"
ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


class Config(BaseModel):
    url: str = ""
    free_domains: list[str] = Field(
        default_factory=lambda: ["light", "switch", "fan", "media_player", "scene", "input_boolean"]
    )
    watch: list[str] = Field(default_factory=list)
    watch_interval: float = Field(default=30, ge=5)


class HomeModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.transport: httpx.AsyncBaseTransport | None = None  # tests put a fake HA here
        self._last: dict[str, str] = {}
        if self.config.watch:
            ctx.every(self.config.watch_interval, self.check_watched, "watch", first_delay=0)

    # -- talking to Home Assistant ----------------------------------------------------------

    async def _api(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        self.ctx.kill_switch.check()
        if not self.config.url:
            raise ToolError(
                "Home Assistant is not set up yet: give me its address and a long-lived "
                "access token (home.connect)"
            )
        token = self.ctx.vault.get(TOKEN_KEY)
        if not token:
            raise ToolError(
                "the Home Assistant token is missing; set it up again with home.connect"
            )
        async with httpx.AsyncClient(
            base_url=self.config.url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(15, connect=5),
            transport=self.transport,
        ) as client:
            try:
                response = await client.request(method, f"/api{path}", json=body)
            except httpx.HTTPError as exc:
                raise ToolError(f"could not reach Home Assistant: {exc}") from exc
        if response.status_code == 401:
            raise ToolError("Home Assistant refused the token (401); create a new one")
        if response.status_code == 404:
            raise ToolError("Home Assistant does not know that (404)")
        if response.status_code >= 400:
            raise ToolError(
                f"Home Assistant answered {response.status_code}: {response.text[:200]}"
            )
        return response.json() if response.content else None

    @staticmethod
    def _summary(state: dict[str, Any]) -> dict[str, Any]:
        attributes = state.get("attributes") or {}
        found: dict[str, Any] = {
            "entity": state.get("entity_id"),
            "name": attributes.get("friendly_name", state.get("entity_id")),
            "state": state.get("state"),
        }
        unit = attributes.get("unit_of_measurement")
        if unit:
            found["state"] = f"{state.get('state')} {unit}"
        if "brightness" in attributes and attributes["brightness"] is not None:
            found["brightness"] = f"{round(int(attributes['brightness']) * 100 / 255)}%"
        if "current_temperature" in attributes:
            found["temperature"] = attributes["current_temperature"]
        return found

    async def _resolve(self, name: str) -> str:
        """An entity id from an id or a friendly name ("living room lamp")."""
        wanted = name.strip().lower()
        if ENTITY.match(wanted):
            return wanted
        states = await self._api("GET", "/states")
        matches = [
            s["entity_id"]
            for s in states
            if wanted in str((s.get("attributes") or {}).get("friendly_name", "")).lower()
            or wanted.replace(" ", "_") in s["entity_id"]
        ]
        if len(matches) == 1:
            return str(matches[0])
        if not matches:
            raise ToolError(f"nothing called '{name}' in Home Assistant; see home.states")
        exact = [
            m for m in matches
            if str(next(s for s in states if s["entity_id"] == m).get("attributes", {})
                   .get("friendly_name", "")).lower() == wanted
        ]  # fmt: skip
        if len(exact) == 1:
            return str(exact[0])
        raise ToolError(f"'{name}' could be: {', '.join(matches[:8])}. Say which one.")

    async def _allowed(self, entity: str, action: str) -> None:
        """Switching a lock, alarm, cover, heating ... asks first."""
        domain = entity.split(".", 1)[0]
        if domain in self.config.free_domains:
            return
        if not await self.ctx.ui.confirm(f"{action} {entity} in your home?"):
            raise ToolError("not confirmed, nothing was changed")

    async def _service(self, domain: str, service: str, data: dict[str, Any]) -> Any:
        return await self._api("POST", f"/services/{domain}/{service}", data)

    # -- tools ---------------------------------------------------------------------------

    @tool
    async def connect(self, url: str, token: str) -> dict[str, Any]:
        """Set up Home Assistant: its address (e.g. http://homeassistant.local:8123) and a
        long-lived access token (HA profile -> Security). The token goes into the keyring.
        Asks first."""
        address = url.strip().rstrip("/")
        if not re.match(r"https?://", address):
            raise ToolError("give the address with http:// or https://")
        if not token.strip():
            raise ToolError("give a long-lived access token")
        if not await self.ctx.ui.confirm(f"Connect to Home Assistant at {address}?"):
            raise ToolError("not confirmed, nothing was saved")
        self.ctx.vault.set(TOKEN_KEY, token.strip())
        self.config.url = address
        write_yaml_config(
            self.ctx.paths.module_configs / "home.yaml",
            self.config.model_dump(),
            "# The home module (modules/home). The token is in the keyring (home.token).",
        )
        info = await self._api("GET", "/")
        return {"connected": address, "message": (info or {}).get("message", "")}

    @tool
    async def states(self, filter: str = "", domain: str = "") -> dict[str, Any]:
        """Devices and sensors in Home Assistant and their state. `filter` matches names
        ("kitchen"), `domain` a kind ("light", "sensor", "binary_sensor", "climate")."""
        wanted, kind = filter.strip().lower(), domain.strip().lower()
        found = [
            self._summary(s)
            for s in await self._api("GET", "/states")
            if (not kind or s["entity_id"].startswith(kind + "."))
            and (
                not wanted
                or wanted in s["entity_id"]
                or wanted in str((s.get("attributes") or {}).get("friendly_name", "")).lower()
            )
        ]
        return {"count": len(found), "entities": found[:200]}

    @tool
    async def get(self, entity: str) -> dict[str, Any]:
        """The state of one device or sensor (by id or name)."""
        resolved = await self._resolve(entity)
        return self._summary(await self._api("GET", f"/states/{resolved}"))

    async def _switch(self, entity: str, service: str, label: str) -> dict[str, Any]:
        resolved = await self._resolve(entity)
        await self._allowed(resolved, label)
        domain = resolved.split(".", 1)[0]
        target = "homeassistant" if domain not in ("scene", "script", "automation") else domain
        await self._service(target, service, {"entity_id": resolved})
        return {"done": service, "entity": resolved}

    @tool
    async def turn_on(self, entity: str) -> dict[str, Any]:
        """Switch something on (a light, plug, fan ...). Locks, alarms, covers and heating
        ask first."""
        return await self._switch(entity, "turn_on", "Turn on")

    @tool
    async def turn_off(self, entity: str) -> dict[str, Any]:
        """Switch something off."""
        return await self._switch(entity, "turn_off", "Turn off")

    @tool
    async def toggle(self, entity: str) -> dict[str, Any]:
        """Switch something on if it is off, off if it is on."""
        return await self._switch(entity, "toggle", "Toggle")

    @tool
    async def set(self, entity: str, value: float) -> dict[str, Any]:
        """Set a level: a light's brightness or a media player's volume (0-100 %), a
        thermostat's temperature, a cover's position (0-100 %), or a number helper."""
        resolved = await self._resolve(entity)
        domain = resolved.split(".", 1)[0]
        level = max(0.0, min(100.0, value))
        plans: dict[str, tuple[str, str, dict[str, Any]]] = {
            "light": ("light", "turn_on", {"brightness_pct": level}),
            "media_player": ("media_player", "volume_set", {"volume_level": level / 100}),
            "climate": ("climate", "set_temperature", {"temperature": value}),
            "cover": ("cover", "set_cover_position", {"position": int(level)}),
            "input_number": ("input_number", "set_value", {"value": value}),
            "number": ("number", "set_value", {"value": value}),
        }
        if domain not in plans:
            raise ToolError(f"{resolved} has no level to set")
        await self._allowed(resolved, f"Set to {value:g}:")
        service_domain, service, data = plans[domain]
        await self._service(service_domain, service, {"entity_id": resolved, **data})
        return {"set": resolved, "value": value}

    @tool
    async def scenes(self) -> dict[str, Any]:
        """The scenes you can start (e.g. "Movie night")."""
        return await self.states(domain="scene")

    @tool(permission=Permission.CONFIRM)
    async def call_service(
        self, domain: str, service: str, entity: str = "", data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Any other Home Assistant action (domain and service as in HA, e.g. "vacuum",
        "start"). Asks first."""
        if not re.fullmatch(r"[a-z_]+", domain) or not re.fullmatch(r"[a-z_]+", service):
            raise ToolError("domain and service are lowercase names like 'vacuum' and 'start'")
        payload = dict(data or {})
        if entity.strip():
            payload["entity_id"] = await self._resolve(entity)
        result = await self._service(domain, service, payload)
        return {"called": f"{domain}.{service}", "changed": len(result or [])}

    # -- watching ------------------------------------------------------------------------

    async def check_watched(self) -> list[dict[str, str]]:
        changes: list[dict[str, str]] = []
        for entity in self.config.watch:
            try:
                state = str((await self._api("GET", f"/states/{entity}")).get("state"))
            except ToolError as exc:
                self.ctx.logger.warning("watch failed", entity=entity, error=str(exc))
                continue
            before = self._last.get(entity)
            self._last[entity] = state
            if before is not None and before != state:
                change = {"entity": entity, "old": before, "new": state}
                changes.append(change)
                await self.ctx.publish("home.changed", change)
        return changes
