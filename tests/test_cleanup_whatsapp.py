"""WhatsApp cleanup: a question whose send fails leaves nothing behind, untrusted headers
cannot crash the webhook, huge bodies are refused, and the webhook server leaves Ctrl+C to
the core."""

from __future__ import annotations

import asyncio
import signal
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.core import Kernel, Paths
from starlight.core.vault import MemoryBackend


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("whatsapp")
    (paths.module_configs / "whatsapp.yaml").write_text(
        f'owner_number: "4917612345678"\nphone_number_id: "1234567890"\n'
        f"port: {free_port()}\nreply_timeout: 1\n"
    )
    paths.config_file.write_text(paths.config_file.read_text() + "memory:\n  auto_extract: false\n")
    return paths


def vault() -> MemoryBackend:
    backend = MemoryBackend()
    backend.set("whatsapp.token", "graph-token-abcdef")
    backend.set("whatsapp.app_secret", "app-secret-123456")
    backend.set("whatsapp.verify_token", "verify-me-please")
    return backend


async def test_question_whose_send_raises_leaves_no_dead_future(project: Paths) -> None:
    async with Kernel(project, vault_backend=vault()) as kernel:
        module: Any = kernel.modules.records["whatsapp"].instance

        async def broken(text: str) -> bool:
            raise httpx.ConnectError("offline")

        module.send = broken
        with pytest.raises(httpx.ConnectError):
            await module.ui.confirm("Delete it?")
        assert module.pending == []  # the next message of yours is a task, not an answer


async def test_question_that_could_not_be_sent_is_not_waited_for(project: Paths) -> None:
    async with Kernel(project, vault_backend=vault()) as kernel:
        module: Any = kernel.modules.records["whatsapp"].instance

        async def refused(text: str) -> bool:
            return False

        module.send = refused
        started = asyncio.get_running_loop().time()
        assert await module.ui.confirm("Delete it?") is False
        assert await module.ui.ask("Your code?") is None
        assert asyncio.get_running_loop().time() - started < 0.5  # not reply_timeout (1 s)
        assert module.pending == []


async def test_odd_headers_and_huge_bodies_get_a_clean_refusal(project: Paths) -> None:
    async with Kernel(project, vault_backend=vault()) as kernel:
        module: Any = kernel.modules.records["whatsapp"].instance
        transport = httpx.ASGITransport(app=module.app, raise_app_exceptions=True)
        async with httpx.AsyncClient(transport=transport, base_url="http://hook") as client:
            hook = "/webhook/whatsapp"
            odd = {"X-Hub-Signature-256": "sha256=café".encode("latin-1")}
            assert (await client.post(hook, content=b"{}", headers=odd)).status_code == 403
            challenge = {"hub.mode": "subscribe", "hub.verify_token": "café"}
            assert (await client.get(hook, params=challenge)).status_code == 403
            big = b"x" * (2 * 1024**2)
            assert (await client.post(hook, content=big)).status_code == 413


async def test_webhook_server_leaves_ctrl_c_to_the_core(project: Paths) -> None:
    before = signal.getsignal(signal.SIGINT)
    async with Kernel(project, vault_backend=vault()) as kernel:
        module: Any = kernel.modules.records["whatsapp"].instance
        assert module._server is not None
        for _ in range(100):
            if module._server.started:
                break
            await asyncio.sleep(0.05)
        assert module._server.started
        assert signal.getsignal(signal.SIGINT) is before  # uvicorn did not take it over
    assert module._server_task is not None and module._server_task.done()
