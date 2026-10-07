"""Check TLS/SSL certificates: who they are for, who issued them, and when they expire.

Read-only: it opens a TLS connection, reads the certificate the server presents, and closes
it again. ``check_all`` and ``expiring`` go through the servers you allowed in
config/servers.yaml (the same list the network module uses).
"""

from __future__ import annotations

import asyncio
import ssl
import time
from typing import Any

import yaml
from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool

SERVERS_FILE = "servers.yaml"


class Config(BaseModel):
    timeout: float = Field(default=10, gt=0, le=60)
    default_warn_days: int = Field(default=30, ge=1)


class CertsModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    # -- reading a certificate (blocking; run off the event loop) -------------------------

    def _read_cert(self, host: str, port: int) -> dict[str, Any]:
        context = ssl.create_default_context()
        with (
            __import__("socket").create_connection(
                (host, port), timeout=self.config.timeout
            ) as sock,
            context.wrap_socket(sock, server_hostname=host) as tls,
        ):
            cert = tls.getpeercert()
        return cert or {}

    def _describe(self, host: str, port: int, raw: dict[str, Any]) -> dict[str, Any]:
        not_after = raw.get("notAfter")
        not_before = raw.get("notBefore")
        result: dict[str, Any] = {
            "host": host,
            "port": port,
            "subject": _name(raw.get("subject")),
            "issuer": _name(raw.get("issuer")),
            "valid_from": not_before,
            "valid_until": not_after,
            "san": [value for kind, value in raw.get("subjectAltName", ()) if kind == "DNS"],
        }
        if not_after:
            try:
                expiry = ssl.cert_time_to_seconds(not_after)
            except ValueError:
                return result
            days_left = int((expiry - time.time()) // 86400)
            result["days_left"] = days_left
            result["expired"] = days_left < 0
        return result

    async def _check(self, host: str, port: int) -> dict[str, Any]:
        host = host.strip()
        if not host:
            raise ToolError("give a host name, e.g. 'example.com'")
        try:
            raw = await asyncio.to_thread(self._read_cert, host, port)
        except ssl.SSLCertVerificationError as exc:
            return {
                "host": host,
                "port": port,
                "error": f"certificate did not verify: {exc.verify_message or exc}",
            }
        except (OSError, ssl.SSLError) as exc:
            return {"host": host, "port": port, "error": f"could not read the certificate: {exc}"}
        return self._describe(host, port, raw)

    # -- servers.yaml --------------------------------------------------------------------

    def _configured_hosts(self) -> list[str]:
        path = self.ctx.paths.config / SERVERS_FILE
        if not path.exists():
            return []
        try:
            data = yaml.safe_load(path.read_text("utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ToolError(f"config/servers.yaml is not valid YAML: {exc}") from exc
        servers = (data.get("servers") or {}) if isinstance(data, dict) else {}
        hosts: list[str] = []
        for entry in servers.values():
            if isinstance(entry, dict) and entry.get("host"):
                hosts.append(str(entry["host"]))
        return hosts

    # -- tools ---------------------------------------------------------------------------

    @tool
    async def cert(self, host: str, port: int = 443) -> dict[str, Any]:
        """Read the TLS certificate a server presents: who it is for, who issued it, and how
        many days until it expires. Works for any host (reading a certificate is harmless)."""
        result = await self._check(host, port)
        if "error" in result:
            raise ToolError(result["error"])
        return result

    @tool
    async def check_all(self) -> dict[str, Any]:
        """Check the certificate of every server in config/servers.yaml (on port 443)."""
        hosts = self._configured_hosts()
        if not hosts:
            raise ToolError("no servers are configured in config/servers.yaml")
        results = await asyncio.gather(*(self._check(h, 443) for h in hosts))
        return {"checked": len(results), "certificates": list(results)}

    @tool
    async def expiring(self, days: int = 0) -> dict[str, Any]:
        """Which of your servers' certificates expire within `days` days (default 30)."""
        within = days or self.config.default_warn_days
        hosts = self._configured_hosts()
        if not hosts:
            raise ToolError("no servers are configured in config/servers.yaml")
        results = await asyncio.gather(*(self._check(h, 443) for h in hosts))
        soon = [
            r for r in results if isinstance(r.get("days_left"), int) and r["days_left"] <= within
        ]
        errors = [r for r in results if "error" in r]
        return {"within_days": within, "expiring": soon, "unreachable": errors}


def _name(pairs: Any) -> str:
    """Flatten the nested tuples ssl returns for subject/issuer into "CN=..., O=...".
    Keeps commonName and organizationName, which are the useful parts."""
    if not pairs:
        return ""
    wanted = {"commonName": "CN", "organizationName": "O"}
    parts: list[str] = []
    for rdn in pairs:
        for key, value in rdn:
            if key in wanted:
                parts.append(f"{wanted[key]}={value}")
    return ", ".join(parts)
