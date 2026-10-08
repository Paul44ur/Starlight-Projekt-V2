"""The shipped configuration lets Starlight do more without asking (desktop, code, closing
programs, network tasks, mail; any server; the phone asks like the PC; web.fetch reads the
page asked for like a browser), while the questions that stay - shutdown, shell commands,
writing outside the Starlight folder, granting access - still come, and the code-level
defaults are unchanged."""

from __future__ import annotations

import shutil
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.core import Kernel, Paths
from starlight.core.config import RemoteConfig, load_config
from starlight.core.permissions import Permission, PermissionPolicy
from tests.conftest import REPO
from tests.test_local_programs import FakeDesktop


def shipped(tmp_path: Path) -> Paths:
    """A project with the repository's own config files."""
    shutil.copytree(
        REPO / "config",
        tmp_path / "config",
        ignore=shutil.ignore_patterns("*.local.yaml", "*.launcher.yaml", "*.remote.yaml"),
    )
    paths = Paths(tmp_path)
    paths.ensure()
    return paths


def test_what_runs_without_asking_and_what_still_asks(tmp_path: Path) -> None:
    config = load_config(shipped(tmp_path))
    policy = PermissionPolicy(config.permissions)
    for tool in (
        "desktop.click",
        "desktop.type_text",
        "code.run_python",
        "apps.close",
        "system.close_app",
        "network.ssh_run",
        "network.http_request",
        "mail.send",
        "mail.reply",
    ):
        assert policy.resolve(tool, Permission.CONFIRM) is Permission.SAFE, tool
    for tool in ("system.shutdown", "system.restart", "system.sleep", "memory.forget"):
        assert policy.resolve(tool, Permission.CONFIRM) is Permission.CONFIRM, tool
    assert config.agent.max_steps == 15
    assert config.web.remote.confirm == []  # the phone asks exactly like the PC
    assert "access.allow_action" in config.web.remote.blocked  # rules change on the PC only
    assert RemoteConfig().confirm == []


class Yes:
    def __init__(self) -> None:
        self.questions: list[str] = []

    async def confirm(self, question: str) -> bool:
        self.questions.append(question)
        return True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    for name in ("apps", "network", "web", "access"):
        use_module(name)
    (paths.config / "servers.yaml").write_text("servers: {}\n")
    return paths


async def test_granting_access_still_asks_with_the_shipped_rules(project: Paths) -> None:
    project.config_file.write_text(
        project.config_file.read_text()
        + "permissions:\n  auto_approve: ['access.*', 'desktop.*']\n"
    )
    user = Yes()
    async with Kernel(project, ui=user) as k:
        result = await k.dispatcher.call("access.allow_action", {"pattern": "commands.*"})
        assert result.ok, result.error
        assert user.questions  # asked, although access.* is auto-approved


def instance(k: Kernel, name: str) -> Any:
    module = k.modules.records[name].instance
    assert module is not None
    return module


async def test_programs_open_without_a_first_time_question(project: Paths) -> None:
    (project.module_configs / "apps.yaml").write_text("ask_first_open: false\n")
    user = Yes()
    async with Kernel(project, ui=user) as k:
        desktop = FakeDesktop()
        instance(k, "apps")._backend = desktop
        result = await k.dispatcher.call("apps.open", {"name": "steam"})
        assert result.ok and desktop.started == ["Steam"]
        assert user.questions == []


async def test_any_host_only_when_switched_on(project: Paths) -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        async with Kernel(project) as k:
            refused = await k.dispatcher.call(
                "network.port_check", {"server": "127.0.0.1", "port": port}
            )
            assert not refused.ok and "servers.yaml" in (refused.error or "")
        (project.module_configs / "network.yaml").write_text("any_host: true\n")
        async with Kernel(project) as k:
            checked = await k.dispatcher.call(
                "network.port_check", {"server": "127.0.0.1", "port": port}
            )
            assert checked.ok, checked.error
            assert checked.output["open"] is True


def site(robots_status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            if robots_status != 200:
                return httpx.Response(robots_status)
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        body = f"<html><title>Page</title><main>Hello from {request.url.path}</main></html>"
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.MockTransport(handler)


async def test_web_fetch_reads_the_page_asked_for(project: Paths) -> None:
    (project.module_configs / "web.yaml").write_text(
        "respect_robots_on_fetch: false\nbrowser_fallback: false\n"
    )
    async with Kernel(project, http_transport=site()) as k:
        page = await k.dispatcher.call("web.fetch", {"url": "https://shop.example/private"})
        assert page.ok and "Hello from /private" in page.output["text"]
        many = await k.dispatcher.call(
            "web.fetch_many", {"urls": ["https://shop.example/private/other"]}
        )
        assert many.ok and "error" in many.output[0]  # crawling still follows robots.txt


async def test_robots_txt_behind_a_login_means_no_rules(project: Paths) -> None:
    (project.module_configs / "web.yaml").write_text("browser_fallback: false\n")
    async with Kernel(project, http_transport=site(robots_status=403)) as k:
        page = await k.dispatcher.call("web.fetch", {"url": "https://shop.example/a"})
        assert page.ok and "Hello from /a" in page.output["text"]
