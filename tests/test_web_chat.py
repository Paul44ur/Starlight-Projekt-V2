"""The web chat: the page loads, status reports the model, messages stream over the
WebSocket, and an action that needs confirmation is approved from the browser."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.conftest import REPO


def web_home(root: Path) -> Path:
    (root / "config" / "modules").mkdir(parents=True)
    (root / "config" / "starlight.yaml").write_text(
        "model:\n  provider: echo\nkill_switch:\n  hotkey: null\n", encoding="utf-8"
    )
    (root / "config" / "servers.yaml").write_text("servers: {}\n")
    for name in ("hello", "access"):
        shutil.copytree(REPO / "modules" / name, root / "modules" / name)
    return root


# The test client always says "testserver" as the WebSocket host; a real browser says this.
LOCAL = {"host": "127.0.0.1:8700"}


def client(root: Path) -> object:
    from fastapi.testclient import TestClient

    from starlight.core import Paths
    from starlight.web.app import create_app

    return TestClient(
        create_app(Paths(root)), base_url="http://127.0.0.1:8700", client=("127.0.0.1", 50000)
    )


def test_page_and_status(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c:
        page = c.get("/")
        assert page.status_code == 200 and "Starlight" in page.text
        status = c.get("/api/status").json()
        assert status["model"] == "echo" and status["kill"]["engaged"] is False
        assert "servers" in status["access"]  # the access sidebar data is present


def test_chat_streams_an_answer(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c, c.websocket_connect("/ws", headers=LOCAL) as ws:
        ws.send_json({"type": "ask", "text": "Hello there"})
        answer = _until_answer(ws)
    assert answer["text"] == "You said: Hello there"


def test_an_action_is_approved_from_the_browser(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c, c.websocket_connect("/ws", headers=LOCAL) as ws:
        ws.send_json({"type": "ask", "text": '!hello.shout {"text": "hi"}'})
        asked = []
        while True:
            m = ws.receive_json()
            if m["type"] == "confirm":
                asked.append(m["question"])
                ws.send_json({"type": "reply", "id": m["id"], "value": "yes"})
            elif m["type"] == "answer":
                break
        assert any("hello.shout" in q for q in asked)  # it asked before running
        assert m["text"] == "HI"  # and then ran it


def test_denying_an_action_from_the_browser(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c, c.websocket_connect("/ws", headers=LOCAL) as ws:
        ws.send_json({"type": "ask", "text": '!hello.shout {"text": "hi"}'})
        while True:
            m = ws.receive_json()
            if m["type"] == "confirm":
                ws.send_json({"type": "reply", "id": m["id"], "value": "no"})
            elif m["type"] == "answer":
                break
    assert "not confirmed" in m["text"]  # denial is reported, nothing ran


def test_kill_and_resume_from_the_web(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c:
        assert c.post("/api/kill").json()["engaged"] is True
        assert c.get("/api/status").json()["kill"]["engaged"] is True
        assert c.post("/api/resume").json()["engaged"] is False


def test_other_websites_cannot_drive_starlight(tmp_path: Path) -> None:
    """A page on another site can open a WebSocket to localhost or POST to it; both are
    refused, as is a page that rebinds its own domain to 127.0.0.1."""
    from starlette.websockets import WebSocketDisconnect

    with client(web_home(tmp_path / "h")) as c:
        evil = {"origin": "https://evil.example"}
        with (
            pytest.raises(WebSocketDisconnect),
            c.websocket_connect("/ws", headers={**LOCAL, **evil}) as ws,
        ):
            ws.receive_json()
        assert c.post("/api/resume", headers=evil).status_code == 403
        assert c.post("/api/kill", headers=evil).status_code == 403
        rebound = {"host": "evil.example:8700", "origin": "http://evil.example:8700"}
        assert c.post("/api/kill", headers=rebound).status_code == 403
        assert c.get("/api/status").json()["kill"]["engaged"] is False  # nothing happened
        # the page itself (same origin) and the terminal client (no Origin) still work
        own = {"origin": "http://127.0.0.1:8700"}
        assert c.post("/api/kill", headers=own).json()["engaged"] is True
        with c.websocket_connect("/ws", headers={**LOCAL, **own}):
            pass


def _until_answer(ws: object) -> dict:
    while True:
        m = ws.receive_json()  # type: ignore[attr-defined]
        if m["type"] == "answer":
            return m
        if m["type"] == "error":
            pytest.fail(m["text"])


def test_status_polling_leaves_no_audit_trail(tmp_path: Path) -> None:
    """The page polls /api/status every few seconds; that must not write an audit entry
    and a statistic each time."""
    root = web_home(tmp_path / "h")
    with client(root) as c:
        for _ in range(5):
            assert "servers" in c.get("/api/status").json()["access"]
    audit = root / "logs" / "audit.log"
    assert "access.status" not in (audit.read_text() if audit.exists() else "")


def test_a_delivered_message_reaches_every_open_chat_once(tmp_path: Path) -> None:
    """Reminders and alerts show up in every open page; the page that caused it gets it
    once (as the answer's notice), not twice."""
    home = web_home(tmp_path / "h")
    module = home / "modules" / "tell"
    module.mkdir()
    (module / "module.yaml").write_text("name: tell\nentrypoint: module:Mod\n")
    (module / "module.py").write_text(
        "from starlight.core import Module, tool\n\n"
        "class Mod(Module):\n"
        "    @tool\n"
        "    async def it(self, text: str) -> str:\n"
        "        await self.ctx.deliver(text)\n"
        "        return 'told'\n"
    )
    with (
        client(home) as c,
        c.websocket_connect("/ws", headers=LOCAL) as watcher,
        c.websocket_connect("/ws", headers=LOCAL) as asker,
    ):
        asker.send_json({"type": "ask", "text": '!tell.it {"text": "tea is ready"}'})
        seen: list[dict[str, str]] = []
        while True:
            m = asker.receive_json()
            seen.append(m)
            if m["type"] == "answer":
                break
        assert [m["text"] for m in seen if m["type"] == "notify"] == ["tea is ready"]
        assert watcher.receive_json() == {"type": "notify", "text": "tea is ready"}


def test_the_model_s_thinking_is_shown_apart_and_not_kept(tmp_path: Path) -> None:
    from typing import Any

    from starlight.core.model import (
        Message,
        ModelResponse,
        TokenCallback,
        emit_thought,
        register_provider,
    )

    class Thinker:
        name = "thinker"
        supports_streaming = True

        async def chat(
            self, messages: list[Message], tools: list[Any], on_token: TokenCallback | None = None
        ) -> ModelResponse:
            emit_thought("The user says hello. ")
            emit_thought("Answer kindly.")
            if on_token is not None:
                on_token("Hello!")
            return ModelResponse(content="Hello!")

    register_provider("thinker", lambda options: Thinker())
    root = web_home(tmp_path / "h")
    config = root / "config" / "starlight.yaml"
    config.write_text(config.read_text().replace("provider: echo", "provider: thinker"))
    with client(root) as c, c.websocket_connect("/ws", headers=LOCAL) as ws:  # type: ignore[attr-defined]
        ws.send_json({"type": "ask", "text": "hello"})
        seen: list[dict[str, Any]] = []
        while not seen or seen[-1]["type"] != "answer":
            seen.append(ws.receive_json())
        kept = c.get("/api/conversations").json()
        name = kept["current"]
        messages = c.get(f"/api/conversations/{name}").json()
    thoughts = [m["text"] for m in seen if m["type"] == "thought"]
    assert thoughts == ["The user says hello. ", "Answer kindly."]
    assert [m["type"] for m in seen].index("thought") < [m["type"] for m in seen].index("token")
    assert seen[-1]["text"] == "Hello!" and "Hello!" in str(messages)
    assert "Answer kindly" not in str(messages)
