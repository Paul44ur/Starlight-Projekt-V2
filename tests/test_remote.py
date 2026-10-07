"""The web chat beyond one browser tab: saved conversations, files dropped into the chat,
the settings and memory pages' APIs, and use from your phone through Tailscale - where
every device must be paired with a one-time code, some things stay on the PC, and some
tools always ask on the phone."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

from starlight.core import Kernel, Paths
from tests.conftest import REPO

TAILNET = "my-pc.tail1234.ts.net"
LOCAL = {"host": "127.0.0.1:8700"}
# What a request through `tailscale serve` looks like when it reaches Starlight.
PHONE = {
    "host": TAILNET,
    "x-forwarded-for": "100.101.102.103",
    "x-forwarded-host": TAILNET,
    "origin": f"https://{TAILNET}",
}


def home(root: Path, remote: bool = True) -> Path:
    (root / "config" / "modules").mkdir(parents=True)
    config = "model:\n  provider: echo\nkill_switch:\n  hotkey: null\n"
    if remote:
        config += (
            f"web:\n  remote:\n    enabled: true\n    hosts: ['{TAILNET}']\n"
            "    confirm: ['hello.*']\n"
        )
    (root / "config" / "starlight.yaml").write_text(config, encoding="utf-8")
    (root / "config" / "servers.yaml").write_text("servers: {}\n")
    for name in ("hello", "access"):
        shutil.copytree(REPO / "modules" / name, root / "modules" / name)
    return root


def client(root: Path) -> Any:
    from fastapi.testclient import TestClient

    from starlight.web.app import create_app

    return TestClient(create_app(Paths(root)), base_url="http://127.0.0.1:8700")


def pair(c: Any) -> str:
    code = c.post("/api/pair/code", headers=LOCAL).json()["code"]
    response = c.post("/api/pair", headers=PHONE, json={"code": code, "name": "Pixel"})
    assert response.status_code == 200, response.text
    token = response.cookies.get("starlight_device")
    assert token
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    return str(token)


def phone(token: str) -> dict[str, str]:
    return {**PHONE, "cookie": f"starlight_device={token}"}


def answer_of(ws: Any, approve: bool = True) -> tuple[dict[str, Any], list[str]]:
    asked: list[str] = []
    while True:
        m = ws.receive_json()
        if m["type"] == "confirm":
            asked.append(m["question"])
            ws.send_json({"type": "reply", "id": m["id"], "value": "yes" if approve else "no"})
        elif m["type"] in ("answer", "error"):
            return m, asked


# -- pairing --------------------------------------------------------------------------------


def test_an_unpaired_phone_gets_nothing(tmp_path: Path) -> None:
    with client(home(tmp_path / "h")) as c:
        page = c.get("/", headers=PHONE, follow_redirects=False)
        assert page.status_code == 303 and page.headers["location"] == "/pair"
        assert c.get("/pair", headers=PHONE).status_code == 200
        for path in ("/api/status", "/api/conversations", "/api/memory", "/settings"):
            assert c.get(path, headers=PHONE).status_code == 403, path
        with pytest.raises(WebSocketDisconnect), c.websocket_connect("/ws", headers=PHONE) as ws:
            ws.receive_json()
        # a phone can't make itself a code, and a stranger host is refused outright
        assert c.post("/api/pair/code", headers=PHONE).status_code == 403
        stranger = {**PHONE, "host": "evil.example", "x-forwarded-host": "evil.example"}
        assert c.get("/api/status", headers=stranger).status_code == 403


def test_pairing_works_once_and_the_device_can_chat(tmp_path: Path) -> None:
    root = home(tmp_path / "h")
    with client(root) as c:
        code = c.post("/api/pair/code", headers=LOCAL).json()["code"]
        first = c.post("/api/pair", headers=PHONE, json={"code": code, "name": "Pixel"})
        assert first.status_code == 200
        again = c.post("/api/pair", headers=PHONE, json={"code": code, "name": "Thief"})
        assert again.status_code == 403  # one use only
        token = first.cookies["starlight_device"]
        status = c.get("/api/status", headers=phone(token)).json()
        assert status["local"] is False and status["device"] == "Pixel"
        assert status["home"] is None and status["access"] == {}  # nothing about the PC
        with c.websocket_connect("/ws", headers=phone(token)) as ws:
            ws.send_json({"type": "ask", "text": "hello from the phone"})
            reply, _ = answer_of(ws)
        assert reply["text"] == "You said: hello from the phone"
    stored = (root / "data" / "devices" / "devices.json").read_text()
    assert token not in stored  # only a hash is kept


def test_codes_expire_and_guessing_locks_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with client(home(tmp_path / "h")) as c:
        code = c.post("/api/pair/code", headers=LOCAL).json()["code"]
        real_time = time.time
        monkeypatch.setattr(time, "time", lambda: real_time() + 301)
        assert c.post("/api/pair", headers=PHONE, json={"code": code}).status_code == 403
        monkeypatch.setattr(time, "time", real_time)

        code = c.post("/api/pair/code", headers=LOCAL).json()["code"]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            assert c.post("/api/pair", headers=PHONE, json={"code": wrong}).status_code == 403
        # locked out now: even the right code is refused from that address
        assert c.post("/api/pair", headers=PHONE, json={"code": code}).status_code == 403
        other = {**PHONE, "x-forwarded-for": "100.64.0.9"}
        assert c.post("/api/pair", headers=other, json={"code": code}).status_code == 200


def test_the_pc_keeps_control(tmp_path: Path) -> None:
    with client(home(tmp_path / "h")) as c:
        token = pair(c)
        me = phone(token)
        assert c.post("/api/kill", headers=me).status_code == 200  # stopping is fine
        assert c.post("/api/resume", headers=me).status_code == 403  # resuming is not
        assert c.post("/api/resume", headers=LOCAL).status_code == 200
        changes = [
            ("post", "/api/permissions", {"pattern": "hello.*", "setting": "safe"}),
            ("post", "/api/modules/hello", {"enabled": False}),
            ("get", "/api/devices", None),
            ("post", "/api/skills/x/approve", None),
        ]
        for method, path, body in changes:
            response = getattr(c, method)(path, headers=me, **({"json": body} if body else {}))
            assert response.status_code == 403, path
        devices = c.get("/api/devices", headers=LOCAL).json()["devices"]
        assert [d["name"] for d in devices] == ["Pixel"]
        assert c.delete(f"/api/devices/{devices[0]['id']}", headers=LOCAL).status_code == 200
        assert c.get("/api/status", headers=me).status_code == 403  # revoked


def test_risky_tools_ask_on_the_phone_even_if_they_normally_do_not(tmp_path: Path) -> None:
    with client(home(tmp_path / "h")) as c:
        token = pair(c)
        with c.websocket_connect("/ws", headers=phone(token)) as ws:
            ws.send_json({"type": "ask", "text": '!hello.greet {"name": "Paul"}'})
            reply, asked = answer_of(ws, approve=False)
        assert asked and "hello.greet" in asked[0]  # safe at home, asks from the phone
        assert "not confirmed" in reply["text"]
        with c.websocket_connect("/ws", headers=LOCAL) as ws:
            ws.send_json({"type": "ask", "text": '!hello.greet {"name": "Paul"}'})
            reply, asked = answer_of(ws)
        assert asked == [] and "Paul" in reply["text"]  # on the PC: as before


def test_a_proxied_request_is_never_treated_as_local(tmp_path: Path) -> None:
    """If Tailscale (or anything) forwards a request, it must not count as the PC itself,
    even when its Host says 127.0.0.1."""
    with client(home(tmp_path / "h", remote=False)) as c:
        sneaky = {"host": "127.0.0.1:8700", "x-forwarded-for": "100.64.0.2"}
        assert c.get("/api/status", headers=sneaky).status_code == 403
        assert c.post("/api/pair/code", headers=sneaky).status_code == 403


# -- conversations, files, memory ------------------------------------------------------------


def test_conversations_are_saved_listed_and_reopened(tmp_path: Path) -> None:
    root = home(tmp_path / "h", remote=False)
    with client(root) as c:
        with c.websocket_connect("/ws", headers=LOCAL) as ws:
            ws.send_json({"type": "ask", "text": "first chat"})
            first, _ = answer_of(ws)
            second_id = c.post("/api/conversations", headers=LOCAL).json()["id"]
            ws.send_json({"type": "ask", "text": "second chat", "conversation": second_id})
            second, _ = answer_of(ws)
        assert first["conversation"] != second["conversation"] == second_id
        listed = c.get("/api/conversations", headers=LOCAL).json()["conversations"]
        assert [x["title"] for x in listed] == ["second chat", "first chat"]
        found = c.get("/api/conversations?search=first", headers=LOCAL).json()["conversations"]
        assert [x["title"] for x in found] == ["first chat"]
        c.patch(f"/api/conversations/{second_id}", headers=LOCAL, json={"title": "Renamed"})
        opened = c.get(f"/api/conversations/{second_id}", headers=LOCAL).json()
        assert opened["title"] == "Renamed"
        assert [m["content"] for m in opened["messages"]] == [
            "second chat",
            "You said: second chat",
        ]
        assert c.delete(f"/api/conversations/{second_id}", headers=LOCAL).status_code == 200
        assert c.get(f"/api/conversations/{second_id}", headers=LOCAL).status_code == 404


async def test_a_conversation_survives_a_restart(paths: Paths) -> None:
    async with Kernel(paths) as k:
        await k.ask("remember the blue door", keep_history=True)
        conversation = k.conversations.current
    async with Kernel(paths) as k:
        said = [m.content for m in k.history if m.role == "user"]
        assert said == ["remember the blue door"] and k.conversations.current == conversation
        k.new_conversation()
        assert k.history == []


def test_dropped_files_go_with_the_message(tmp_path: Path) -> None:
    with client(home(tmp_path / "h", remote=False)) as c:
        doc = c.post(
            "/api/upload",
            headers={**LOCAL, "x-filename": "notes%20today.txt"},
            content=b"The meeting moved to Thursday.",
        ).json()
        assert doc["kind"] == "document" and doc["name"] == "notes today.txt"
        bad = c.post("/api/upload", headers={**LOCAL, "x-filename": "virus.exe"}, content=b"MZ")
        assert bad.status_code == 415
        with c.websocket_connect("/ws", headers=LOCAL) as ws:
            ws.send_json({"type": "ask", "text": "summarize", "attachments": [doc["id"]]})
            reply, _ = answer_of(ws)
        assert "The meeting moved to Thursday." in reply["text"]


def test_memory_and_skills_from_the_browser(tmp_path: Path) -> None:
    root = home(tmp_path / "h", remote=False)
    (root / "memory").mkdir()
    (root / "memory" / "coffee.md").write_text(
        "---\ntopic: coffee\nsource: user\n---\nPaul drinks his coffee black.\n"
    )
    with client(root) as c:
        memories = c.get("/api/memory", headers=LOCAL).json()["memories"]
        assert memories[0]["content"].startswith("Paul drinks")
        entry = memories[0]["id"]
        c.put(
            f"/api/memory/{entry}", headers=LOCAL, json={"content": "Paul drinks oat flat whites."}
        )
        assert "oat" in (root / "memory" / "coffee.md").read_text()
        assert c.get("/api/memory?search=flat", headers=LOCAL).json()["count"] == 1
        assert c.put(
            "/api/memory/../config/starlight", headers=LOCAL, json={"content": "x"}
        ).status_code in (400, 404)
        assert c.delete(f"/api/memory/{entry}", headers=LOCAL).status_code == 200
        assert not (root / "memory" / "coffee.md").exists()
        assert c.get("/api/skills", headers=LOCAL).json() == {"skills": []}
        perms = c.get("/api/permissions", headers=LOCAL).json()
        assert any(t["name"] == "hello.shout" and t["now"] == "confirm" for t in perms["tools"])
        c.post(
            "/api/permissions", headers=LOCAL, json={"pattern": "hello.shout", "setting": "safe"}
        )
        assert (
            "hello.shout"
            in c.get("/api/permissions", headers=LOCAL).json()["rules"]["auto_approve"]
        )
        assert "hello.shout" in (root / "config" / "permissions.chat.yaml").read_text()


def test_pages_and_app_files_load(tmp_path: Path) -> None:
    with client(home(tmp_path / "h", remote=False)) as c:
        for path in ("/", "/settings", "/memory", "/style.css", "/common.js", "/icon.svg",
                     "/manifest.webmanifest"):  # fmt: skip
            assert c.get(path, headers=LOCAL).status_code == 200, path
