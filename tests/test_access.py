"""Managing access from the chat: add a server, a login and a secret, and change what runs
without asking — all without editing project files."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.core.config import read_yaml
from starlight.core.vault import MemoryBackend


class User:
    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.questions: list[str] = []

    async def confirm(self, question: str) -> bool:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


def kernel(paths: Paths, user: User) -> Kernel:
    return Kernel(paths, ui=user, vault_backend=MemoryBackend())


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    for name in ("access", "network", "login", "hello"):
        use_module(name)
    (paths.config / "servers.yaml").write_text("servers: {}\n")
    return paths


async def call(k: Kernel, tool: str, **args: Any) -> Any:
    return await k.dispatcher.call(tool, args)


async def test_add_a_server_from_chat_then_it_is_reachable(project: Paths) -> None:
    user = User(True)  # confirms the allow
    async with kernel(project, user) as k:
        before = await call(k, "network.status", server="10.0.0.5")
        assert not before.ok and "not in" in str(before.error)  # refused until allowed

        added = await call(k, "network.allow", host="10.0.0.5", description="test box")
        assert added.ok and added.output["allowed"] == "10-0-0-5"
        listed = await call(k, "network.servers")
        assert any(s["host"] == "10.0.0.5" for s in listed.output)  # live, no restart

        forgotten = await call(k, "network.forget", name="10.0.0.5")
        assert forgotten.ok
        assert await call(k, "network.servers") and (await call(k, "network.servers")).output == []
    # written to the real config file, not hand-edited
    assert "10.0.0.5" not in (project.config / "servers.yaml").read_text()


async def test_add_a_login_keeps_the_password_out_of_files_and_logs(project: Paths) -> None:
    async with kernel(project, User(True)) as k:
        result = await call(
            k,
            "login.add_site",
            site="github",
            url="https://github.com/login",
            username="paul",
            password="hunter2-secret-value",
        )
        assert result.ok and result.output["added"] == "github"
        sites = await call(k, "login.sites")
        assert any(s["site"] == "github" for s in sites.output)
        assert k.vault.get("login.github.username") == "paul"
        assert k.vault.get("login.github.password") == "hunter2-secret-value"
    # the password is in neither the config file nor the logs
    assert "hunter2-secret-value" not in (project.config / "modules" / "login.yaml").read_text()
    logs = (project.logs / "starlight.log").read_text() + (project.logs / "audit.log").read_text()
    assert "hunter2-secret-value" not in logs


async def test_set_a_secret_from_chat(project: Paths) -> None:
    async with kernel(project, User(True)) as k:
        result = await call(k, "access.set_secret", key="web.brave_api_key", secret="abc123def456")
        assert result.ok
        assert k.vault.get("web.brave_api_key") == "abc123def456"
        status = await call(k, "access.status")
        assert "web.brave_api_key" in status.output["secrets"]
    audit = (project.logs / "audit.log").read_text()
    assert "abc123def456" not in audit  # the value never reaches the log
    assert "access.set_secret" in audit


async def test_change_what_runs_without_asking_and_it_persists(project: Paths) -> None:
    async with kernel(project, User(True, True)) as k:
        blocked = await call(k, "access.block_action", pattern="hello.greet")
        assert blocked.ok
        denied = await call(k, "hello.greet", name="x")
        assert not denied.ok and "blocked" in str(denied.error)
        assert "hello.greet" in k.policy.rules()["blocked"]
    # persisted to the user overlay, so it survives a restart
    overlay = read_yaml(project.config / "starlight.local.yaml")
    assert "hello.greet" in overlay["permissions"]["blocked"]
    async with kernel(project, User()) as k:
        again = await call(k, "hello.greet", name="x")
        assert not again.ok  # still blocked after "restart"
        cleared = await call(k, "access.clear_rule", pattern="hello.greet")
        assert cleared.ok
        assert (await call(k, "hello.greet", name="x")).ok


async def test_granting_access_always_asks_even_if_auto_approved(project: Paths) -> None:
    """A grant must never be silent, even if the user auto-approved the access tools."""
    (project.config_file).write_text(
        (project.config_file).read_text() + "permissions:\n  auto_approve: ['access.*']\n"
    )
    user = User(False)  # says no to the grant
    async with kernel(project, user) as k:
        result = await call(k, "access.set_secret", key="x.key", secret="nope123")
    assert not result.ok and "not confirmed" in str(result.error)
    assert user.questions  # it still asked


async def test_recent_errors_are_readable_from_the_chat(
    paths: Paths, use_module: Callable[[str], Path], write_module: Callable[..., Path]
) -> None:
    use_module("access")
    write_module(
        "boom",
        """
        from starlight.core import Module, tool

        class Mod(Module):
            @tool
            def break_it(self) -> str:
                raise ValueError("the printer caught fire")
        """,
    )
    async with kernel(paths, User()) as k:
        await k.dispatcher.call("boom.break_it")  # fails, logged with a traceback
        shown = await k.dispatcher.call("access.recent_errors", {"count": 5})
    errors = shown.output["errors"]
    assert any("boom" in e["where"] and "the printer caught fire" in e["cause"] for e in errors), (
        errors
    )


def test_logs_show_command(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    import json

    from starlight.cli import main

    (paths.logs).mkdir(exist_ok=True)
    entry = {
        "timestamp": "2026-10-06T12:00:00Z",
        "level": "error",
        "logger": "starlight.module.x",
        "event": "tool failed",
        "tool": "x.do",
        "exception": "Traceback...\nRuntimeError: nope",
    }
    (paths.logs / "starlight.log").write_text(json.dumps(entry) + "\n")
    assert main(["--home", str(paths.root), "logs", "show"]) == 0
    out = capsys.readouterr().out
    assert "ERROR" in out and "x.do" in out and "RuntimeError: nope" in out
