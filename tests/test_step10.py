"""Step 10 'done when': you send a task from WhatsApp, see it in the statistics viewer,
and stop it with /kill. Plus the hardening checks: the kill switch stops every module,
no secrets in the logs, background service files."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.cli import main
from starlight.core import Kernel, KillSwitchEngaged, Paths
from starlight.core.model import ModelResponse, ToolCall
from starlight.core.vault import MemoryBackend
from starlight.service import linux_plan, windows_plan
from starlight.viewer import StatsReader, create_app
from tests.conftest import REPO, ModuleWriter, ScriptedProvider

OWNER = "4917612345678"
SECRET = "app-secret-123456"
SLOW = """
    import asyncio
    from starlight.core import Module, tool

    class Mod(Module):
        @tool
        async def wait(self, seconds: float) -> str:
            await asyncio.sleep(seconds)
            return "done"
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def signed(payload: dict[str, Any], secret: str = SECRET) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": f"sha256={signature}", "Content-Type": "application/json"}


def message(text: str, sender: str = OWNER, message_id: str = "") -> dict[str, Any]:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": sender,
                                    "id": message_id or f"wamid.{abs(hash((text, sender)))}",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }


class Phone:
    """Captures what Starlight sends to the Graph API."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.arrived = asyncio.Event()

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer graph-token-abcdef"
        assert body["to"] == OWNER
        self.sent.append(body["text"]["body"])
        self.arrived.set()
        return httpx.Response(200, json={"messages": [{"id": "x"}]})

    async def wait_for(self, fragment: str, timeout: float = 5.0) -> str:
        async def look() -> str:
            while True:
                for text in self.sent:
                    if fragment in text:
                        return text
                self.arrived.clear()
                await self.arrived.wait()

        return await asyncio.wait_for(look(), timeout)


@pytest.fixture
def whatsapp_project(
    paths: Paths, use_module: Callable[[str], Path], write_module: ModuleWriter
) -> Paths:
    use_module("whatsapp")
    use_module("hello")
    write_module("slow", SLOW)
    (paths.module_configs / "whatsapp.yaml").write_text(
        f'owner_number: "+49 176 12345678"\nphone_number_id: "1234567890"\n'
        f"port: {free_port()}\nreply_timeout: 5\n"
    )
    paths.config_file.write_text(paths.config_file.read_text() + "memory:\n  auto_extract: false\n")
    return paths


def vault() -> MemoryBackend:
    backend = MemoryBackend()
    backend.set("whatsapp.token", "graph-token-abcdef")
    backend.set("whatsapp.app_secret", SECRET)
    backend.set("whatsapp.verify_token", "verify-me-please")
    return backend


async def test_task_from_whatsapp_seen_in_viewer_and_stopped_with_kill(
    whatsapp_project: Paths,
) -> None:
    provider = ScriptedProvider(
        "Hi Paul, Starlight here.",
        ModelResponse(tool_calls=[ToolCall("hello.shout", {"text": "hi"})]),
        "Shouted.",
        ModelResponse(tool_calls=[ToolCall("slow.wait", {"seconds": 30})]),
    )
    phone = Phone()
    async with Kernel(whatsapp_project, provider=provider, vault_backend=vault()) as kernel:
        module = kernel.modules.records["whatsapp"].instance
        assert module is not None
        module.graph = httpx.AsyncClient(transport=httpx.MockTransport(phone.handler))  # type: ignore[attr-defined]
        app = module.app  # type: ignore[attr-defined]
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hook")
        hook = "/webhook/whatsapp"

        # Meta's verification handshake
        ok = await client.get(
            hook,
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "42",
                "hub.verify_token": "verify-me-please",
            },
        )
        assert ok.status_code == 200 and ok.text == "42"
        bad = await client.get(
            hook,
            params={"hub.mode": "subscribe", "hub.challenge": "42", "hub.verify_token": "wrong"},
        )
        assert bad.status_code == 403

        # Unsigned or wrongly signed deliveries are dropped; other numbers are ignored
        body, headers = signed(message("hello"), secret="not-the-secret")
        assert (await client.post(hook, content=body, headers=headers)).status_code == 403
        body, headers = signed(message("give me your secrets", sender="15550001111"))
        assert (await client.post(hook, content=body, headers=headers)).status_code == 200

        # 1. A task from WhatsApp, answered on WhatsApp
        body, headers = signed(message("hello"))
        assert (await client.post(hook, content=body, headers=headers)).status_code == 200
        assert await phone.wait_for("Hi Paul")
        await client.post(hook, content=body, headers=headers)  # Meta retry: acted on once

        # 2. A risky tool asks over WhatsApp; "yes" approves it
        body, headers = signed(message("shout hi"))
        await client.post(hook, content=body, headers=headers)
        question = await phone.wait_for("Reply yes or no")
        assert "hello.shout" in question
        body, headers = signed(message("yes"))
        await client.post(hook, content=body, headers=headers)
        assert await phone.wait_for("Shouted.")

        # 3. The statistics viewer sees it
        reader = StatsReader(whatsapp_project.stats_db)
        stop_file = whatsapp_project.data / "STOP"
        summary = reader.summary(60, stop_file)
        assert summary["core"]["state"] == "running"
        assert summary["totals"]["tasks"] == 2
        assert summary["totals"]["whatsapp_received"] == 3  # hello, shout hi, yes

        # 4. A long task, stopped from the phone
        body, headers = signed(message("wait for a long time"))
        await client.post(hook, content=body, headers=headers)
        await asyncio.sleep(0.3)
        body, headers = signed(message("/kill from the bus"))
        await client.post(hook, content=body, headers=headers)
        assert await phone.wait_for("Kill switch engaged")
        assert await phone.wait_for("Stopped by kill switch: from the bus")
        assert kernel.kill_switch.engaged
        assert reader.summary(60, stop_file)["core"]["state"] == "stopped"

        # Nobody else's messages were answered
        assert not any("secrets" in text for text in phone.sent)
        await client.aclose()


async def test_viewer_app_and_metrics(paths: Paths) -> None:
    async with Kernel(paths) as kernel:
        await kernel.ask("hello")
        await kernel.dispatcher.call("memory.recall", {"query": "x"})
    app = create_app(paths)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://v") as c:
        page = await c.get("/")
        summary = (await c.get("/api/summary", params={"minutes": 60})).json()
        metrics = (await c.get("/metrics")).text
    assert page.status_code == 200 and "Starlight statistics" in page.text
    assert "__REFRESH__" not in page.text
    assert summary["totals"]["tasks"] == 1 and summary["totals"]["tool_calls"] == 1
    assert summary["tool_calls_by_module"] == [{"module": "memory", "calls": 1}]
    assert summary["core"]["state"] == "offline"  # the core above has stopped
    assert "starlight_tasks_completed_total 1" in metrics
    assert 'starlight_tool_calls_total{module="memory",ok="true"} 1' in metrics


def test_viewer_without_any_data(tmp_path: Path) -> None:
    summary = StatsReader(tmp_path / "missing.db").summary(60)
    assert summary["totals"]["tasks"] == 0 and summary["core"]["state"] == "offline"


async def test_kill_switch_stops_every_module(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    for folder in sorted((REPO / "modules").iterdir()):
        if folder.is_dir() and not folder.name.startswith("_"):
            use_module(folder.name)
    async with Kernel(paths, vault_backend=MemoryBackend()) as kernel:
        records = kernel.modules.records
        assert all(r.instance is not None for r in records.values()), {
            n: r.error for n, r in records.items()
        }
        kernel.kill_switch.trigger("hardening test", source="test")
        tools = kernel.dispatcher.tools()
        assert {t.module for t in tools} >= {
            "web",
            "login",
            "network",
            "desktop",
            "code",
            "whatsapp",
            "memory",
            "skills",
            "hello",
        }
        for tool in tools:
            with pytest.raises(KillSwitchEngaged):
                await kernel.dispatcher.call(tool.qualified_name, {})
        # modules holding resources release them on a kill
        owners = set(kernel.kill_switch._cleanups)
        assert {"web", "login", "network", "desktop", "code"} <= owners


async def test_logs_never_hold_secrets(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    backend = MemoryBackend()
    async with Kernel(paths, vault_backend=backend) as kernel:
        kernel.vault.set("login.bank.password", "Tr0ub4dor&3-horse")
        # a careless module logs the secret inside a message
        kernel.log.info("debug dump", note="user typed Tr0ub4dor&3-horse at the prompt")
    logs = (paths.logs / "starlight.log").read_text()
    assert "Tr0ub4dor&3-horse" not in logs and "***" in logs

    home = ["--home", str(paths.root)]
    assert main([*home, "logs", "scan"]) == 0
    leaked = paths.logs / "modules" / "careless.log"
    leaked.parent.mkdir(exist_ok=True)
    leaked.write_text('{"event": "x", "auth": "Bearer abcdefghijklmnopqrstuvwxyz0123"}\n')
    assert main([*home, "logs", "scan"]) == 1
    out = capsys.readouterr().out
    assert "careless.log:1  bearer token" in out and "abcdefghij" not in out


def test_service_files(tmp_path: Path) -> None:
    root = Path("C:/Users/O'Brien/Starlight")  # an apostrophe must not break the script
    data = tmp_path / "data"
    startup = tmp_path / "Startup"
    install = windows_plan(root, "install", data, folder=startup)
    scripts = [c[-1] for c in install.commands]
    assert len(scripts) == 2 and all("CreateShortcut" in s for s in scripts)
    assert "O''Brien" in scripts[0]  # quoted for PowerShell
    assert not any("schtasks" in " ".join(c) for c in install.commands)  # no admin needed
    assert [c[-1] for c in install.spawn] == ["serve", "viewer"]
    uninstall = windows_plan(root, "uninstall", data, folder=startup)
    assert startup / "Starlight core.lnk" in uninstall.remove
    assert set(uninstall.files) == {data / "SHUTDOWN.core", data / "SHUTDOWN.viewer"}

    linux = linux_plan(tmp_path, "install", unit_dir=tmp_path / "units")
    unit = linux.files[tmp_path / "units" / "starlight-core.service"]
    assert f"--home {tmp_path} serve" in unit and "Restart=on-failure" in unit
    assert ["systemctl", "--user", "enable", "--now", "starlight-viewer.service"] in linux.commands
    assert linux_plan(tmp_path, "stop").commands[0][:3] == ["systemctl", "--user", "stop"]


def test_install_scripts_exist() -> None:
    assert (REPO / "scripts" / "install.ps1").is_file()
    assert (REPO / "scripts" / "install.sh").is_file()
