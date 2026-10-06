"""Step 7 'done when': "is my server up and what is its disk usage?" is answered over SSH."""

from __future__ import annotations

import sys
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Event, Kernel, Paths
from starlight.core.model import ModelResponse, ToolCall
from starlight.core.modules import discover, import_module_class
from starlight.core.stats import Stats
from starlight.core.vault import MemoryBackend
from tests.conftest import REPO, ScriptedProvider

asyncssh = pytest.importorskip("asyncssh")

DF = """Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1        98G   41G   52G  45% /
tmpfs           3.9G     0  3.9G   0% /dev/shm
/dev/sdb1       3.6T  2.9T  700G  81% /mnt/data
"""


class Health(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
        self.wfile.write(b"ok")


@pytest.fixture
def http_port() -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


class SshServer:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.server: Any = None
        self.port = 0
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")
        self.client_key = asyncssh.generate_private_key("ssh-ed25519")

    async def start(self) -> None:
        authorized = asyncssh.import_authorized_keys(self.client_key.export_public_key().decode())

        def handle(process: Any) -> None:
            self.commands.append(process.command)
            if process.command == "df -hP":
                process.stdout.write(DF)
            else:
                process.stdout.write(f"ran: {process.command}\n")
            process.exit(0)

        self.server = await asyncssh.create_server(
            asyncssh.SSHServer,
            "127.0.0.1",
            0,
            server_host_keys=[self.host_key],
            authorized_client_keys=authorized,
            process_factory=handle,
        )
        self.port = self.server.sockets[0].getsockname()[1]

    def known_hosts(self, path: Path) -> Path:
        path.write_text(f"[127.0.0.1]:{self.port} {self.host_key.export_public_key().decode()}")
        return path

    def close(self) -> None:
        self.server.close()


@pytest.fixture
async def ssh() -> AsyncIterator[SshServer]:
    server = SshServer()
    await server.start()
    yield server
    server.close()


@pytest.fixture
def network(
    paths: Paths, use_module: Callable[[str], Path], ssh: SshServer, http_port: int
) -> MemoryBackend:
    use_module("network")
    known = ssh.known_hosts(paths.root / "known_hosts")
    (paths.config / "servers.yaml").write_text(f"""
servers:
  homeserver:
    host: 127.0.0.1
    ports: [{ssh.port}, {http_port}]
    http: http://127.0.0.1:{http_port}/health
    ssh:
      user: paul
      port: {ssh.port}
      key_vault: ssh.homeserver
      known_hosts: {known.as_posix()}
""")
    vault = MemoryBackend()
    vault.set("ssh.homeserver", ssh.client_key.export_private_key().decode())
    return vault


async def test_server_up_and_disk_usage_over_ssh(
    paths: Paths, network: MemoryBackend, ssh: SshServer
) -> None:
    provider = ScriptedProvider(
        ModelResponse(
            tool_calls=[
                ToolCall("network.status", {"server": "homeserver"}),
                ToolCall("network.disk_usage", {"server": "homeserver"}),
            ]
        ),
        "homeserver is up; / is 45% full and /mnt/data 81%.",
    )
    async with Kernel(paths, provider=provider, vault_backend=network) as kernel:
        result = await kernel.ask("Is my server up and what is its disk usage?")
    status, disks = (r.output for r in result.tool_results)
    assert all(r.ok for r in result.tool_results), [r.error for r in result.tool_results]
    assert status["up"] is True
    checks = {
        c.get("port", c.get("method", "dns" if "addresses" in c else "http")): c
        for c in status["checks"]
    }
    assert checks[ssh.port]["open"] is True
    assert any(c.get("healthy") for c in status["checks"])
    assert disks["disks"] == [
        {
            "filesystem": "/dev/sda1",
            "size": "98G",
            "used": "41G",
            "available": "52G",
            "use_percent": "45%",
            "mount": "/",
        },
        {
            "filesystem": "/dev/sdb1",
            "size": "3.6T",
            "used": "2.9T",
            "available": "700G",
            "use_percent": "81%",
            "mount": "/mnt/data",
        },
    ]
    assert ssh.commands == ["df -hP"]
    stats = Stats(paths.stats_db)  # latency is recorded for the statistics viewer
    checks_seen = {labels["check"] for _, _, labels in stats.query("network.latency_ms")}
    stats.close()
    assert {"ssh", "dns", "http", f"tcp:{ssh.port}"} <= checks_seen


async def test_allow_list_and_confirmation(
    paths: Paths, network: MemoryBackend, ssh: SshServer
) -> None:
    async with Kernel(paths, vault_backend=network) as kernel:
        call = kernel.dispatcher.call
        refused = await call("network.port_check", {"server": "8.8.8.8", "port": 53})
        assert not refused.ok and "not in servers.yaml" in (refused.error or "")

        ok = await call("network.ssh_check", {"server": "homeserver", "command": "uptime"})
        assert ok.ok and ok.output["stdout"] == "ran: uptime\n"

        write = await call(
            "network.ssh_check", {"server": "homeserver", "command": "rm -rf /tmp/x"}
        )
        assert not write.ok and "read-only" in (write.error or "")

        asked = await call(
            "network.ssh_run", {"server": "homeserver", "command": "systemctl restart nginx"}
        )
        assert asked.error == "not confirmed by user"  # changes need your confirmation
        assert "systemctl restart nginx" not in ssh.commands

        by_host = await call("network.dns_lookup", {"server": "127.0.0.1"})
        assert by_host.ok and "127.0.0.1" in by_host.output["addresses"]


async def test_watcher_alerts_when_a_server_goes_down(
    paths: Paths, network: MemoryBackend, ssh: SshServer
) -> None:
    events: list[Event] = []
    async with Kernel(paths, vault_backend=network) as kernel:
        kernel.bus.subscribe("network.*", events.append)
        module = kernel.modules.records["network"].instance
        assert (await module.check_all()) == {"homeserver": True}  # type: ignore[union-attr]

        async def no_ping_answer(host: str) -> dict[str, Any]:  # the whole host is gone
            return {"up": False, "method": "icmp"}

        module._ping = no_ping_answer  # type: ignore[union-attr]
        ssh.close()
        await ssh.server.wait_closed()
        assert (await module.check_all()) == {"homeserver": False}  # type: ignore[union-attr]
    assert [e.name for e in events] == ["network.server_down"]


def network_code(name: str) -> Any:
    """Import a file of the network module the way the module loader does."""
    found, _ = discover(REPO / "modules")
    manifest, folder = found["network"]
    import_module_class(manifest, folder)
    return sys.modules[f"starlight_modules.network.{name}"]


@pytest.mark.parametrize(
    ("command", "read_only"),
    [
        ("df -h", True),
        ("uptime", True),
        ("systemctl status nginx", True),
        ("docker ps -a", True),
        ("journalctl -u nginx -n 50", True),
        ("systemctl restart nginx", False),
        ("rm -rf /", False),
        ("df -h; reboot", False),
        ("cat /etc/hosts > /tmp/x", False),
        ("sudo df", False),
        ("ls $(reboot)", False),
        ("tail -f /var/log/syslog", False),
        ("docker stats", False),
        ("docker stats --no-stream", True),
        ("", False),
        ("echo hi | sh", False),
    ],
)
def test_read_only_classifier(command: str, read_only: bool) -> None:
    assert network_code("readonly").is_read_only(command) is read_only


def test_ping_output_parsing() -> None:
    pattern = network_code("module").PING_TIME
    english = "64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=0.412 ms"
    german = "Antwort von 192.168.1.20: Bytes=32 Zeit=3ms TTL=64"
    windows_fast = "Reply from 127.0.0.1: bytes=32 time<1ms TTL=128"
    assert [m.group(1) for m in map(pattern.search, (english, german, windows_fast)) if m] == [
        "0.412",
        "3",
        "1",
    ]
