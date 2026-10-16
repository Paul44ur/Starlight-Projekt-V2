"""Ping and interact with your own servers.

``config/servers.yaml`` lists your servers: names, SSH logins, health checks. With
``any_host: true`` in config/modules/network.yaml (the shipped setting) any other host can
be pinged, checked and contacted as well; with ``any_host: false`` only the listed hosts can
be touched and anything else is refused. SSH always needs the login settings listed here:

    servers:
      homeserver:
        host: 192.168.1.10
        ports: [22, 443]
        http: https://192.168.1.10/health
        ssh:
          user: paul
          key_vault: ssh.homeserver        # private key text in the vault, or
          # key_file: ~/.ssh/id_ed25519
          known_hosts: ~/.ssh/known_hosts

Read-only checks run freely. Commands that change a server (restart, write, delete) and
arbitrary requests ask first by default (the shipped config/starlight.yaml auto-approves
them: remove them from ``permissions: auto_approve`` to be asked).
"""

from __future__ import annotations

import asyncio
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from starlight.core import Module, ModuleContext, Permission, ToolError, tool
from starlight.core.config import ConfigError, read_yaml, write_yaml_config

from .readonly import is_read_only

SERVERS_FILE = "servers.yaml"
PING_TIME = re.compile(r"(?:time|zeit)\s*[=<]\s*([\d.,]+)\s*ms", re.IGNORECASE)


class NotAllowed(ToolError):
    pass


class SshConfig(BaseModel):
    user: str
    port: int = 22
    key_file: str | None = None
    key_vault: str | None = None  # vault key holding the private key text
    password_vault: str | None = None  # vault key holding a password (keys are better)
    known_hosts: str | None = "~/.ssh/known_hosts"
    verify_host_key: bool = True


class Server(BaseModel):
    host: str
    description: str = ""
    ports: list[int] = Field(default_factory=list)
    http: str | None = None  # health-check URL
    websocket: str | None = None
    ssh: SshConfig | None = None


class ServersFile(BaseModel):
    servers: dict[str, Server] = Field(default_factory=dict)

    @field_validator("servers", mode="before")
    @classmethod
    def _nothing_listed(cls, value: Any) -> Any:
        return {} if value is None else value  # "servers:" with only comments below it


def how_to_add(host: str) -> str:
    """The lines that allow ``host``, ready to paste into servers.yaml."""
    key = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-") or "server"
    return f"servers:\n  {key}:\n    host: {host}"


class Config(BaseModel):
    timeout: float = 5.0
    # True: any host may be pinged, checked and contacted, not only the servers listed in
    # servers.yaml (SSH still needs the login settings there).
    any_host: bool = False
    watch: bool = False  # ping every server on a schedule and alert when one goes down
    watch_interval: float = 60.0


class NetworkModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        # Open SSH connections, by (name, host, port, user): a changed setting reconnects.
        self._ssh: dict[tuple[str, str, int, str], Any] = {}
        self._client = httpx.AsyncClient(timeout=self.config.timeout, verify=True)
        self._up: dict[str, bool] = {}
        ctx.kill_switch.add_cleanup(self._close_ssh, owner=ctx.name)
        if self.config.watch:  # runs in the primary core only: one watcher, not one per process
            ctx.every(self.config.watch_interval, self.check_all, "watch", first_delay=0)

    async def shutdown(self) -> None:
        await self._close_ssh()
        await self._client.aclose()

    async def _close_ssh(self) -> None:
        for conn in self._ssh.values():
            conn.close()
        self._ssh.clear()

    def _drop_ssh(self, name: str) -> None:
        """Forget (and close) the connections to one server, so the next call reconnects."""
        for key in [k for k in self._ssh if k[0] == name]:
            self._ssh.pop(key).close()

    # -- allow-list --------------------------------------------------------------------

    def servers_file(self) -> Path:
        return self.ctx.paths.config / SERVERS_FILE

    def _servers(self) -> dict[str, Server]:
        path = self.servers_file()
        try:
            return ServersFile.model_validate(read_yaml(path)).servers
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            raise ConfigError(
                f"{path}: {problems}. Every server needs at least a host, indented like this "
                f"(spaces, no tabs):\n{how_to_add('192.168.1.10')}"
            ) from exc

    def _server(self, name: str) -> tuple[str, Server]:
        servers = self._servers()
        if name in servers:
            return name, servers[name]
        for key, server in servers.items():
            if server.host == name:
                return key, server
        if self.config.any_host and name.strip():
            return name.strip(), Server(host=name.strip())
        raise NotAllowed(
            f"'{name}' is not in {SERVERS_FILE} (Starlight's config folder), so Starlight will "
            "not touch it. To allow it, the user can add it to that file, indented exactly "
            f"like this (spaces, no tabs), and ask again; no restart needed:\n{how_to_add(name)}"
        )

    # -- reachability ------------------------------------------------------------------

    @tool
    def servers(self) -> list[dict[str, Any]]:
        """The servers you allowed in servers.yaml."""
        return [
            {
                "name": n,
                "host": s.host,
                "description": s.description,
                "ports": s.ports,
                "ssh": s.ssh is not None,
                "http": s.http,
            }
            for n, s in sorted(self._servers().items())
        ]

    SERVERS_HEADER = (
        "# Your servers (Step 7), with SSH logins and health checks. Other hosts work too while\n"
        "# any_host is true in config/modules/network.yaml; with false, only these are allowed.\n"
        "# Starlight edits this from the chat (network.allow / network.forget); you may also\n"
        "# edit it by hand (two spaces per level, no tabs). SSH keys go in the vault.\n"
        "#\n"
        "# Example (remove the '# ' to use it, keep the indentation):\n"
        "#   homeserver:\n"
        "#     host: 192.168.1.10\n"
        "#     description: NAS in the basement\n"
        "#     ports: [22, 443]\n"
        "#     http: https://192.168.1.10/health\n"
        "#     ssh:\n"
        "#       user: paul\n"
        "#       key_vault: ssh.homeserver"
    )

    def _write_servers(self, servers: dict[str, Server]) -> None:
        data = {
            "servers": {
                n: s.model_dump(exclude_none=True, exclude_defaults=True)
                for n, s in servers.items()
            }
        }
        write_yaml_config(self.servers_file(), data, self.SERVERS_HEADER)

    @tool(permission=Permission.CONFIRM)
    def allow(
        self,
        host: str,
        name: str = "",
        description: str = "",
        ports: list[int] | None = None,
        http: str = "",
    ) -> dict[str, Any]:
        """Allow Starlight to reach a server or host, by adding it to the allow-list
        (servers.yaml). Use this when the user asks to access, ping or scan a host that is
        not listed yet. host is an IP or hostname. Asks the user first."""
        host = host.strip()
        if not host:
            raise ToolError("give a host (an IP address or hostname)")
        key = name.strip() or re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-") or "server"
        servers = self._servers()
        existing = servers.get(key)
        if existing is not None:
            # Update in place: keep its SSH, WebSocket and other settings, add new ports, and
            # only replace the description or health URL when a new one is given.
            server = existing.model_copy(deep=True)
            server.host = host
            server.description = description.strip() or server.description
            server.ports = server.ports + [p for p in ports or [] if p not in server.ports]
            note = f"Updated {key} ({host}); its other settings are unchanged."
        else:
            server = Server(host=host, description=description.strip(), ports=ports or [])
            note = f"Starlight may now reach {key} ({host})."
        if http.strip():
            server.http = http.strip()
        servers[key] = server
        self._write_servers(servers)
        self.ctx.logger.info("server allowed", name=key, host=host, updated=existing is not None)
        return {"allowed": key, "host": host, "note": note}

    @tool(permission=Permission.CONFIRM)
    def forget(self, name: str) -> dict[str, Any]:
        """Remove a server from the allow-list so Starlight can no longer reach it."""
        servers = self._servers()
        # The name or the host of a listed server (not any_host's stand-in for unlisted ones).
        by_host = next((k for k, s in servers.items() if s.host == name), None)
        key = name if name in servers else by_host
        if key is None:
            raise NotAllowed(f"'{name}' is not on the list in {SERVERS_FILE}, nothing to forget")
        del servers[key]
        self._write_servers(servers)
        self._drop_ssh(key)
        self.ctx.logger.info("server forgotten", name=key)
        return {"forgot": key}

    @tool
    async def ping(self, server: str) -> dict[str, Any]:
        """ICMP ping a server once. Returns whether it answered and the latency."""
        name, srv = self._server(server)
        result = await self._ping(srv.host)
        self._latency(name, "ping", result.get("latency_ms"))
        return {"server": name, **result}

    async def _ping(self, host: str) -> dict[str, Any]:
        timeout = self.config.timeout
        if sys.platform == "win32":
            cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
        else:
            cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), host]
        started = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=0x08000000 if sys.platform == "win32" else 0,  # no console window
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout + 2)
        except (OSError, TimeoutError):
            return {"up": False, "method": "icmp", "error": "ping unavailable or timed out"}
        text = out.decode(errors="replace")
        match = PING_TIME.search(text)
        up = proc.returncode == 0 and ("ttl=" in text.lower() or match is not None)
        latency = float(match.group(1).replace(",", ".")) if match else None
        if up and latency is None:
            latency = round((time.perf_counter() - started) * 1000, 1)
        return {"up": up, "method": "icmp", "latency_ms": latency}

    @tool
    async def port_check(self, server: str, port: int) -> dict[str, Any]:
        """Check whether a TCP port on a server accepts connections."""
        name, srv = self._server(server)
        result = await self._port(srv.host, port)
        self._latency(name, f"tcp:{port}", result.get("latency_ms"))
        return {"server": name, "port": port, **result}

    async def _port(self, host: str, port: int) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), self.config.timeout
            )
        except (OSError, TimeoutError) as exc:
            return {"open": False, "error": type(exc).__name__}
        writer.close()
        latency = round((time.perf_counter() - started) * 1000, 1)
        return {"open": True, "latency_ms": latency}

    @tool
    async def dns_lookup(self, server: str) -> dict[str, Any]:
        """Resolve a server's host name."""
        name, srv = self._server(server)
        started = time.perf_counter()
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(srv.host, None)
        except socket.gaierror as exc:
            return {"server": name, "resolved": False, "error": str(exc)}
        addresses = sorted({str(info[4][0]) for info in infos})
        latency = round((time.perf_counter() - started) * 1000, 1)
        self._latency(name, "dns", latency)
        return {"server": name, "resolved": True, "addresses": addresses, "latency_ms": latency}

    @tool
    async def http_check(self, server: str) -> dict[str, Any]:
        """Call the server's configured health-check URL."""
        name, srv = self._server(server)
        if not srv.http:
            return {"server": name, "error": "no http health-check URL configured"}
        started = time.perf_counter()
        try:
            response = await self._client.get(srv.http)
        except httpx.HTTPError as exc:
            return {"server": name, "healthy": False, "error": f"{type(exc).__name__}: {exc}"}
        latency = round((time.perf_counter() - started) * 1000, 1)
        self._latency(name, "http", latency)
        return {
            "server": name,
            "healthy": response.is_success,
            "status": response.status_code,
            "latency_ms": latency,
        }

    @tool
    async def status(self, server: str) -> dict[str, Any]:
        """Everything at once: ping, DNS, configured ports and health check."""
        name, srv = self._server(server)
        checks = [self.ping(name), self.dns_lookup(name)]
        checks += [self.port_check(name, p) for p in srv.ports]
        if srv.http:
            checks.append(self.http_check(name))
        results = await asyncio.gather(*checks)
        up = bool(results[0].get("up")) or any(r.get("open") for r in results[2:])
        return {"server": name, "up": up, "checks": results}

    def _latency(self, server: str, check: str, value: float | None) -> None:
        if value is not None and self.ctx.stats is not None:
            self.ctx.stats.record("network.latency_ms", value, server=server, check=check)

    # -- interaction -------------------------------------------------------------------

    def _url(self, srv: Server, path: str) -> str:
        if path.startswith(("http://", "https://")):
            if httpx.URL(path).host != srv.host:
                raise NotAllowed("URL host does not match the server's host")
            return path
        base = srv.http or f"http://{srv.host}"
        root = str(httpx.URL(base).copy_with(path="/", query=None, fragment=None)).rstrip("/")
        return f"{root}/{path.lstrip('/')}"

    @tool
    async def http_get(self, server: str, path: str = "/") -> dict[str, Any]:
        """GET a path on one of your servers (read-only)."""
        name, srv = self._server(server)
        response = await self._client.get(self._url(srv, path))
        return {"server": name, "status": response.status_code, "body": response.text[:8000]}

    @tool(permission=Permission.CONFIRM)
    async def http_request(
        self,
        server: str,
        method: str,
        path: str,
        body: str = "",
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Any HTTP/REST call (POST, PUT, DELETE ...) to one of your servers. Asks first unless
        auto-approved."""
        name, srv = self._server(server)
        response = await self._client.request(
            method.upper(), self._url(srv, path), content=body or None, headers=headers
        )
        return {"server": name, "status": response.status_code, "body": response.text[:8000]}

    async def _connect(self, name: str, srv: Server) -> Any:
        if srv.ssh is None:
            raise NotAllowed(f"no ssh settings for '{name}' in {SERVERS_FILE}")
        key = (name, srv.host, srv.ssh.port, srv.ssh.user)
        cached = self._ssh.get(key)
        if cached is not None and not cached.is_closed():
            return cached
        self._drop_ssh(name)  # dropped (the server rebooted, the network went away) or changed
        try:
            import asyncssh
        except ImportError as exc:
            raise RuntimeError("install the 'network' extra (asyncssh)") from exc
        cfg = srv.ssh
        options: dict[str, Any] = {"username": cfg.user, "port": cfg.port}
        if cfg.key_vault:
            options["client_keys"] = [
                asyncssh.import_private_key(self.ctx.vault.require(cfg.key_vault))
            ]
        elif cfg.key_file:
            options["client_keys"] = [str(Path(cfg.key_file).expanduser())]
        if cfg.password_vault:
            options["password"] = self.ctx.vault.require(cfg.password_vault)
        if not cfg.verify_host_key:
            self.ctx.logger.warning("ssh host key not verified", server=name)
            options["known_hosts"] = None
        elif cfg.known_hosts:
            options["known_hosts"] = str(Path(cfg.known_hosts).expanduser())
        conn = await asyncio.wait_for(
            asyncssh.connect(srv.host, **options), self.config.timeout * 3
        )
        self._ssh[key] = conn
        return conn

    async def _ssh_exec(self, server: str, command: str, timeout: float) -> dict[str, Any]:
        name, srv = self._server(server)
        conn = await self._connect(name, srv)
        self.ctx.kill_switch.check()
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(conn.run(command, check=False), timeout)
        except Exception:
            self._drop_ssh(name)  # it may be dead (or hung): connect afresh next time
            raise
        latency = round((time.perf_counter() - started) * 1000, 1)
        self._latency(name, "ssh", latency)
        return {
            "server": name,
            "command": command,
            "exit_status": result.exit_status,
            "stdout": str(result.stdout or "")[-8000:],
            "stderr": str(result.stderr or "")[-2000:],
        }

    @tool
    async def ssh_check(self, server: str, command: str) -> dict[str, Any]:
        """Run a read-only command over SSH (df, uptime, free, systemctl status, docker ps,
        journalctl ...). Commands that could change something are refused: use ssh_run."""
        if not is_read_only(command):
            raise NotAllowed("not a read-only command; use network.ssh_run")
        return await self._ssh_exec(server, command, 60)

    @tool(permission=Permission.CONFIRM, own_timeout=True)  # stops itself after `timeout`
    async def ssh_run(self, server: str, command: str, timeout: float = 300) -> dict[str, Any]:
        """Run any command over SSH (restart, write, delete ...). Asks first unless
        auto-approved."""
        return await self._ssh_exec(server, command, timeout)

    @tool
    async def disk_usage(self, server: str) -> dict[str, Any]:
        """Disk usage per filesystem, over SSH."""
        result = await self._ssh_exec(server, "df -hP", 60)
        disks = []
        for line in result["stdout"].splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 6 and not parts[0].startswith(("tmpfs", "devtmpfs", "overlay")):
                disks.append(
                    {
                        "filesystem": parts[0],
                        "size": parts[1],
                        "used": parts[2],
                        "available": parts[3],
                        "use_percent": parts[4],
                        "mount": " ".join(parts[5:]),
                    }
                )
        return {"server": result["server"], "disks": disks}

    @tool(permission=Permission.CONFIRM)
    async def tcp_send(self, server: str, port: int, message: str) -> dict[str, Any]:
        """Send a line over a raw TCP connection and read the reply. Asks first unless
        auto-approved."""
        name, srv = self._server(server)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(srv.host, port), self.config.timeout
        )
        try:
            writer.write(message.encode() + (b"" if message.endswith("\n") else b"\n"))
            await writer.drain()
            reply = await asyncio.wait_for(reader.read(8192), self.config.timeout)
        finally:
            writer.close()
        return {"server": name, "port": port, "reply": reply.decode(errors="replace")}

    @tool(permission=Permission.CONFIRM)
    async def websocket_send(self, server: str, message: str) -> dict[str, Any]:
        """Send one WebSocket message to the server's configured endpoint and read the
        reply. Asks first unless auto-approved."""
        name, srv = self._server(server)
        if not srv.websocket:
            raise NotAllowed(f"no websocket URL configured for '{name}'")
        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:
            raise RuntimeError("install the 'network' extra (websockets)") from exc
        async with connect(srv.websocket, open_timeout=self.config.timeout) as ws:
            await ws.send(message)
            reply = await asyncio.wait_for(ws.recv(), self.config.timeout)
        return {"server": name, "reply": reply if isinstance(reply, str) else reply.hex()}

    # -- watcher -----------------------------------------------------------------------

    async def check_all(self) -> dict[str, bool]:
        """One round of the watcher: publish an event when a server goes down or up."""
        states: dict[str, bool] = {}
        for name, srv in self._servers().items():
            up = bool((await self._ping(srv.host)).get("up"))
            if not up and srv.ports:
                up = bool((await self._port(srv.host, srv.ports[0])).get("open"))
            states[name] = up
            before = self._up.get(name)
            self._up[name] = up
            if before is None or before == up:
                continue
            event = "network.server_up" if up else "network.server_down"
            self.ctx.logger.warning(event, server=name, host=srv.host)
            await self.ctx.publish(event, {"server": name, "host": srv.host})
            await self.ctx.deliver(f"Server {name} is {'back up' if up else 'DOWN'}", urgent=not up)
        return states
