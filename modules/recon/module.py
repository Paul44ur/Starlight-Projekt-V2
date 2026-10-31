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
import json
import re
import socket
import sys
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.config import read_yaml
from starlight.core.tlsinfo import decode_der, unverified_context

from .ports import COMMON, HTTP_PORTS, TLS_PORTS

SERVERS_FILE = "servers.yaml"
AUTHORIZED_FILE = "authorized.json"  # hosts the user authorized for scans
MAX_BANNER = 2048  # never read more than this from one port, hostile or not
# A ping round-trip time: "time=0.4 ms", "time<1ms", "Zeit=3ms" (German Windows).
_PING_TIME = re.compile(r"(?:time|zeit)\s*[=<]\s*[\d.,]+\s*ms", re.IGNORECASE)


def echo_reply(returncode: int | None, output: str) -> bool:
    """Whether ping got a real answer. Windows' ping also exits with 0 when a router
    answers "Destination host unreachable"; only a real echo reply carries a TTL (or a
    time). The same check as the network module's (modules do not import each other)."""
    return returncode == 0 and ("ttl=" in output.lower() or _PING_TIME.search(output) is not None)


class Config(BaseModel):
    connect_timeout: float = Field(default=2.0, gt=0, le=30)
    read_timeout: float = Field(default=2.0, gt=0, le=30)
    concurrency: int = Field(default=100, ge=1, le=500)  # ports probed at once
    banner_bytes: int = Field(default=512, ge=0, le=MAX_BANNER)
    top_ports: int = Field(default=100, ge=1, le=len(COMMON))  # default breadth
    max_ports: int = Field(default=2048, ge=1, le=65535)  # hard cap per scan
    # A host you allowed before asks again before each scan (Settings -> Safety limits).
    # A host not allowed yet always asks, a public one with a warning.
    ask_again_for_known: bool = True


class ReconModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    # -- targets and authorization -------------------------------------------------------

    def _allowed_hosts(self) -> set[str]:
        servers = read_yaml(self.ctx.paths.config / SERVERS_FILE).get("servers") or {}
        hosts = {str(s.get("host")) for s in servers.values() if isinstance(s, dict)}
        return hosts | set(servers)

    # Hosts the user authorized for scanning are kept in recon's own data, not in
    # servers.yaml: authorizing a scan must not also open the host to the network tools.
    def _authorized(self) -> set[str]:
        path = self.ctx.data_dir / AUTHORIZED_FILE
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return set()
        return {str(h) for h in data} if isinstance(data, list) else set()

    def _remember(self, *hosts: str) -> None:
        known = self._authorized() | set(hosts)
        path = self.ctx.data_dir / AUTHORIZED_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(known), indent=2), encoding="utf-8")

    @staticmethod
    def _one_host(target: str) -> str:
        target = target.strip()
        if not target:
            raise ToolError("give one host: an IP address or a hostname")
        # An nmap-style range in the last part, like "192.168.1.1-20"
        numeric_range = re.fullmatch(r"\d+-\d+", target.split(".")[-1]) is not None
        if "/" in target or numeric_range or " " in target or "," in target:
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
        known = self._allowed_hosts() | self._authorized()
        if target in known or ip in known:
            if not self.config.ask_again_for_known:
                return
            ok = await self.ctx.ui.confirm(
                f"Scan {target} ({ip}) now? Ports: {ports_desc}. (You allowed this host before.)"
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
        if full:  # ports 1 to max_ports (2048 shipped), not all 65535; max_ports is <= 65535
            return list(range(1, self.config.max_ports + 1))
        if not ports.strip():
            return [p for p, _ in COMMON[: self.config.top_ports]]
        chosen: dict[int, None] = {}  # a port named twice is scanned once
        for piece in ports.replace(",", " ").split():
            try:
                value = int(piece)
            except ValueError:
                raise ToolError(f"'{piece}' is not a port number") from None
            if not 1 <= value <= 65535:
                raise ToolError("ports are 1-65535")
            chosen[value] = None
        if len(chosen) > self.config.max_ports:
            raise ToolError(f"too many ports (limit {self.config.max_ports})")
        return list(chosen)

    # -- the scan ------------------------------------------------------------------------

    @tool
    async def scan(self, target: str, ports: str = "", full: bool = False) -> dict[str, Any]:
        """Scan one host you own or are authorized to test, and report everything found:
        open TCP ports, the service and banner on each, TLS certificates, HTTP headers, and
        DNS. `target` is a single IP or hostname (not a range). `ports` is optional: port
        numbers such as "22 80 443" (ranges are not supported - list them); empty scans the
        common ports. full=true scans ports 1 to max_ports (2048 by default; slower,
        noisier), not all 65535. Asks you to confirm
        you are authorized before anything is sent, and never attacks - it only gathers
        info."""
        host = self._one_host(target)
        ips = await asyncio.to_thread(self._resolve, host)  # a slow lookup must not block
        ip = ips[0]
        wanted = self._port_list(ports, full)
        desc = "the common ports" if not ports.strip() and not full else f"{len(wanted)} ports"
        await self._authorize(host, ip, desc)
        self.ctx.kill_switch.check()

        dns = await self._dns(ips)
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

    async def _dns(self, ips: list[str]) -> dict[str, Any]:
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
            cmd = ["ping", "-n", "1", "-w", "1500", ip]  # -w: milliseconds
        else:
            cmd = ["ping", "-c", "1", "-W", "2", ip]  # -W: seconds
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        except (OSError, TimeoutError):
            return False
        return echo_reply(proc.returncode, out.decode(errors="replace"))

    async def _scan_ports(self, ip: str, ports: list[int]) -> list[tuple[int, str]]:
        service = dict(COMMON)
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
        except (OSError, TimeoutError):
            return {}
        try:
            writer.write(request)
            await asyncio.wait_for(writer.drain(), self.config.read_timeout)
            raw = await asyncio.wait_for(reader.read(MAX_BANNER), self.config.read_timeout)
        except (OSError, TimeoutError):
            return {}
        finally:
            writer.close()
            with contextlib.suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 1)
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
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port, ssl=unverified_context(), server_hostname=host),
                self.config.connect_timeout,
            )
        except (OSError, TimeoutError) as exc:  # a failed handshake (SSLError) is an OSError
            return {"tls": f"handshake failed: {type(exc).__name__}"}
        try:
            ssl_object = writer.get_extra_info("ssl_object")
            # unverified, getpeercert() is empty: parse the raw certificate instead
            cert = decode_der(ssl_object.getpeercert(binary_form=True)) if ssl_object else None
        except (AttributeError, ValueError):
            cert = None
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), 1)
        return {"tls": self._cert_summary(cert)}

    @staticmethod
    def _cert_summary(cert: Any) -> dict[str, Any]:
        if not cert:
            return {"note": "the server sent no certificate"}

        def name(parts: Any) -> str:
            flat = {k: v for rdn in parts for k, v in rdn}
            return flat.get("commonName") or flat.get("organizationName") or ""

        expires = cert.get("notAfter")
        summary: dict[str, Any] = {
            "subject": name(cert.get("subject", [])),
            "issuer": name(cert.get("issuer", [])),
            "expires": expires,
        }
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
        return await self._dns(await asyncio.to_thread(self._resolve, host))
