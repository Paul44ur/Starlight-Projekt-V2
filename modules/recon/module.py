"""Security reconnaissance: find out what a host exposes.

This is for testing your OWN systems, or systems you have written permission to test.
Scanning a host you do not own or control can be illegal, so every scan first asks you to
confirm you are authorized, and only a single host is scanned at a time (no ranges).

It gathers information only - it does NOT attack, exploit or flood anything:
* DNS and reverse DNS
* which TCP ports are open (a plain connect scan, bounded and gentle)
* a short service banner from each open port
* the TLS certificate on secure ports (subject, issuer, expiry)
* the HTTP response headers and server on web ports

Everything is bounded (short timeouts, a small cap on how much is read per port, a limit on
how many ports at once), so a hostile host cannot use a scan to flood this machine.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import socket
import ssl
import sys
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.config import read_yaml, write_yaml_config

from .ports import COMMON, HTTP_PORTS, TLS_PORTS

SERVERS_FILE = "servers.yaml"
MAX_BANNER = 2048  # never read more than this from one port, hostile or not


class Config(BaseModel):
    connect_timeout: float = Field(default=2.0, gt=0, le=30)
    read_timeout: float = Field(default=2.0, gt=0, le=30)
    concurrency: int = Field(default=100, ge=1, le=500)  # ports probed at once
    banner_bytes: int = Field(default=512, ge=0, le=MAX_BANNER)
    top_ports: int = Field(default=100, ge=1, le=len(COMMON))  # default breadth
    max_ports: int = Field(default=2048, ge=1, le=65535)  # hard cap per scan


class ReconModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    # -- targets and authorization -------------------------------------------------------

    def _allowed_hosts(self) -> set[str]:
        servers = read_yaml(self.ctx.paths.config / SERVERS_FILE).get("servers") or {}
        hosts = {str(s.get("host")) for s in servers.values() if isinstance(s, dict)}
        return hosts | set(servers)

    def _remember(self, name: str, host: str) -> None:
        path = self.ctx.paths.config / SERVERS_FILE
        data = read_yaml(path)
        servers = data.get("servers")
        if not isinstance(servers, dict):
            servers = {}
        servers.setdefault(name, {"host": host, "description": "added for a scan"})
        data["servers"] = servers
        write_yaml_config(
            path,
            data,
            "# Servers Starlight may touch. Edited from the chat (network.allow, scans).",
        )

    @staticmethod
    def _one_host(target: str) -> str:
        target = target.strip()
        if not target:
            raise ToolError("give one host: an IP address or a hostname")
        if "/" in target or "-" in target.split(".")[-1] or " " in target or "," in target:
            raise ToolError(
                "scan one host at a time, not a range or list. Give a single IP or hostname."
            )
        return target

    def _resolve(self, host: str) -> list[str]:
        try:
            ipaddress.ip_address(host)
            return [host]
        except ValueError:
            pass
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except OSError as exc:
            raise ToolError(f"cannot resolve '{host}': {exc}") from exc
        seen: list[str] = []
        for info in infos:
            ip = str(info[4][0])
            if ip not in seen:
                seen.append(ip)
        return seen

    async def _authorize(self, target: str, ip: str, ports_desc: str) -> None:
        """Make sure the user is allowed to scan this host before anything is sent."""
        if target in self._allowed_hosts() or ip in self._allowed_hosts():
            ok = await self.ctx.ui.confirm(
                f"Scan {target} ({ip}) now? Ports: {ports_desc}. (It is on your allow-list.)"
            )
            if not ok:
                raise ToolError("not confirmed, so nothing was scanned")
            return
        private = ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_loopback
        if private:
            question = (
                f"Scan {target} ({ip}) on your own/local network? Ports: {ports_desc}.\n"
                "  Only do this on devices you own or administer."
            )
        else:
            question = (
                f"{target} ({ip}) is a PUBLIC host. Scanning a host you do not own or have "
                "written permission to test can be illegal.\n"
                f"  Confirm you are authorized to scan {target}? Ports: {ports_desc}."
            )
        if not await self.ctx.ui.confirm(question):
            raise ToolError("not confirmed - the scan was NOT run")
        self._remember(target, ip)
        self.ctx.logger.info("scan authorized", target=target, ip=ip, public=not private)

    def _port_list(self, ports: str, full: bool) -> list[int]:
        if full:
            return list(range(1, 65536))[: self.config.max_ports]
        if not ports.strip():
            return [p for p, _ in COMMON[: self.config.top_ports]]
        chosen: list[int] = []
        for piece in ports.replace(",", " ").split():
            try:
                value = int(piece)
            except ValueError:
                raise ToolError(f"'{piece}' is not a port number") from None
            if not 1 <= value <= 65535:
                raise ToolError("ports are 1-65535")
            chosen.append(value)
        if len(chosen) > self.config.max_ports:
            raise ToolError(f"too many ports (limit {self.config.max_ports})")
        return chosen

    # -- the scan ------------------------------------------------------------------------

    @tool
    async def scan(self, target: str, ports: str = "", full: bool = False) -> dict[str, Any]:
        """Scan one host you own or are authorized to test, and report everything found:
        open TCP ports, the service and banner on each, TLS certificates, HTTP headers, and
        DNS. `target` is a single IP or hostname (not a range). `ports` is optional
        (e.g. "22 80 443" or "8000-less" not supported - list them); empty scans the common
        ports. full=true scans many more ports (slower, noisier). Asks you to confirm you
        are authorized before anything is sent, and never attacks - it only gathers info."""
        host = self._one_host(target)
        ips = self._resolve(host)
        ip = ips[0]
        wanted = self._port_list(ports, full)
        desc = "the common ports" if not ports.strip() and not full else f"{len(wanted)} ports"
        await self._authorize(host, ip, desc)
        self.ctx.kill_switch.check()

        dns = await self._dns(host, ips)
        ping = await self._ping(ip)
        open_ports = await self._scan_ports(ip, wanted)
        details = await self._probe_open(host, ip, [p for p, _ in open_ports])
        if self.ctx.stats is not None:
            self.ctx.stats.record("recon.scans", 1)
            self.ctx.stats.record("recon.open_ports", len(open_ports))
        return {
            "target": host,
            "ip": ip,
            "dns": dns,
            "reachable": ping,
            "ports_scanned": len(wanted),
            "open_ports": [
                {"port": p, "service": svc, **details.get(p, {})} for p, svc in open_ports
            ],
            "note": f"{len(open_ports)} open of {len(wanted)} scanned. Reconnaissance only.",
        }

    async def _dns(self, host: str, ips: list[str]) -> dict[str, Any]:
        reverse: dict[str, str] = {}
        for ip in ips:
            try:
                name, _, _ = await asyncio.to_thread(socket.gethostbyaddr, ip)
                reverse[ip] = name
            except OSError:
                pass
        return {"addresses": ips, "reverse": reverse}

    async def _ping(self, ip: str) -> bool:
        if sys.platform == "win32":
            cmd = ["ping", "-n", "1", "-w", "1500", ip]
        else:
            cmd = ["ping", "-c", "1", "-W", "2", ip]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            return await asyncio.wait_for(proc.wait(), timeout=5) == 0
        except (OSError, TimeoutError):
            return False

    async def _scan_ports(self, ip: str, ports: list[int]) -> list[tuple[int, str]]:
        service = {p: s for p, s in COMMON}
        semaphore = asyncio.Semaphore(self.config.concurrency)
        open_ports: list[tuple[int, str]] = []

        async def check(port: int) -> None:
            self.ctx.kill_switch.check()
            async with semaphore:
                try:
                    _, writer = await asyncio.wait_for(
                        asyncio.open_connection(ip, port), self.config.connect_timeout
                    )
                except (OSError, TimeoutError):
                    return
                writer.close()
                with contextlib.suppress(OSError, TimeoutError):
                    await asyncio.wait_for(writer.wait_closed(), 1)
                open_ports.append((port, service.get(port, "unknown")))

        await asyncio.gather(*(check(p) for p in ports))
        return sorted(open_ports)

    async def _probe_open(
        self, host: str, ip: str, open_ports: list[int]
    ) -> dict[int, dict[str, Any]]:
        semaphore = asyncio.Semaphore(min(self.config.concurrency, 20))

        async def probe(port: int) -> tuple[int, dict[str, Any]]:
            async with semaphore:
                if port in TLS_PORTS:
                    return port, await self._tls(host, ip, port)
                if port in HTTP_PORTS:
                    return port, await self._http(host, ip, port)
                return port, await self._banner(ip, port)

        results = await asyncio.gather(*(probe(p) for p in open_ports))
        return {port: data for port, data in results if data}

    async def _banner(self, ip: str, port: int) -> dict[str, Any]:
        if not self.config.banner_bytes:
            return {}
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), self.config.connect_timeout
            )
        except (OSError, TimeoutError):
            return {}
        try:
            data = await asyncio.wait_for(
                reader.read(self.config.banner_bytes), self.config.read_timeout
            )
        except (OSError, TimeoutError):
            data = b""
        finally:
            writer.close()
            with contextlib.suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 1)
        text = data.decode("latin-1", "replace").strip()
        return {"banner": text[:200]} if text else {}

    async def _http(self, host: str, ip: str, port: int) -> dict[str, Any]:
        request = (
            f"HEAD / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: StarlightRecon\r\n"
            "Accept: */*\r\nConnection: close\r\n\r\n"
        ).encode()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), self.config.connect_timeout
            )
            writer.write(request)
            await asyncio.wait_for(writer.drain(), self.config.read_timeout)
            raw = await asyncio.wait_for(reader.read(MAX_BANNER), self.config.read_timeout)
        except (OSError, TimeoutError):
            return {}
        finally:
            with contextlib.suppress(Exception):
                writer.close()
        return self._parse_http(raw)

    @staticmethod
    def _parse_http(raw: bytes) -> dict[str, Any]:
        text = raw.decode("latin-1", "replace")
        lines = text.split("\r\n")
        status = lines[0] if lines else ""
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line or ":" not in line:
                continue
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        interesting = {
            k: headers[k]
            for k in ("server", "x-powered-by", "location", "content-type", "www-authenticate")
            if k in headers
        }
        return {"http_status": status.strip(), "http": interesting} if status else {}

    async def _tls(self, host: str, ip: str, port: int) -> dict[str, Any]:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # we want to SEE the cert, not validate the chain
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port, ssl=context, server_hostname=host),
                self.config.connect_timeout,
            )
        except (OSError, TimeoutError, ssl.SSLError) as exc:
            return {"tls": f"handshake failed: {type(exc).__name__}"}
        try:
            ssl_object = writer.get_extra_info("ssl_object")
            cert = ssl_object.getpeercert() if ssl_object else None
        except (AttributeError, ValueError):
            cert = None
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), 1)
        del reader
        return {"tls": self._cert_summary(cert)}

    @staticmethod
    def _cert_summary(cert: Any) -> dict[str, Any]:
        if not cert:
            return {"note": "no certificate details (verification is off)"}

        def name(parts: Any) -> str:
            flat = {k: v for rdn in parts for k, v in rdn}
            return flat.get("commonName") or flat.get("organizationName") or ""

        summary: dict[str, Any] = {
            "subject": name(cert.get("subject", [])),
            "issuer": name(cert.get("issuer", [])),
            "expires": cert.get("notAfter"),
        }
        expires = cert.get("notAfter")
        if expires:
            try:
                when = datetime.strptime(expires, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
                summary["days_left"] = (when - datetime.now(UTC)).days
            except ValueError:
                pass
        alt = [v for typ, v in cert.get("subjectAltName", []) if typ == "DNS"]
        if alt:
            summary["alt_names"] = alt[:20]
        return summary

    @tool
    async def dns(self, host: str) -> dict[str, Any]:
        """Resolve a hostname to its IP addresses, and look up reverse DNS. Read-only, no
        confirmation needed."""
        host = self._one_host(host)
        return await self._dns(host, self._resolve(host))
