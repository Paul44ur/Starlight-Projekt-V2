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


def client(root: Path) -> object:
    from fastapi.testclient import TestClient

    from starlight.core import Paths
    from starlight.web.app import create_app

    return TestClient(create_app(Paths(root)))


def test_page_and_status(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c:
        page = c.get("/")
        assert page.status_code == 200 and "Starlight" in page.text
        status = c.get("/api/status").json()
        assert status["model"] == "echo" and status["kill"]["engaged"] is False
        assert "servers" in status["access"]  # the access sidebar data is present


def test_chat_streams_an_answer(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c, c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ask", "text": "Hello there"})
        answer = _until_answer(ws)
    assert answer["text"] == "You said: Hello there"


def test_an_action_is_approved_from_the_browser(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c, c.websocket_connect("/ws") as ws:
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
    with client(web_home(tmp_path / "h")) as c, c.websocket_connect("/ws") as ws:
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


def _until_answer(ws: object) -> dict:
    while True:
        m = ws.receive_json()  # type: ignore[attr-defined]
        if m["type"] == "answer":
            return m
        if m["type"] == "error":
            pytest.fail(m["text"])
