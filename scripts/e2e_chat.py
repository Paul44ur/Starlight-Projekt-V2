"""End-to-end check of a running Starlight (used by .github/workflows/launcher.yml on a
clean Windows machine, after Starlight.exe has started it with a small real model):

* the web chat answers a question (real model, streamed),
* a tool is offered to the model (reported, not required: a 0.5B model may not call it),
* Regenerate replaces the last answer instead of adding one,
* the kill switch can be engaged and released through the web API.

Only the standard library and `websockets` (installed with Starlight's web chat).
Prints "e2e chat ok" or exits with the first problem.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

WEB = "http://127.0.0.1:8700"
MODEL = "http://127.0.0.1:8080/health"
ORIGIN = {"Origin": WEB}


def fetch(url: str, method: str = "GET") -> dict[str, object] | int:
    request = urllib.request.Request(
        url, method=method, headers=ORIGIN, data=b"" if method == "POST" else None
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8", "replace")
            return json.loads(body) if body.strip().startswith("{") else response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return 0


def wait_until_ready(seconds: float = 900) -> None:
    """The model answers its health check (200) and the chat page is up."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        model_up = fetch(MODEL) in (200, {"status": "ok"})
        if model_up and isinstance(fetch(WEB + "/api/status"), dict):
            return
        time.sleep(3)
    sys.exit(f"Starlight was not ready after {seconds:.0f} s")


async def turn(ws: websockets.ClientConnection, message: dict[str, object]) -> dict[str, object]:
    await ws.send(json.dumps(message))
    while True:
        reply = json.loads(await asyncio.wait_for(ws.recv(), 600))
        if reply["type"] in ("answer", "error", "stopped"):
            return reply  # type: ignore[no-any-return]


def conversation(name: str) -> list[dict[str, str]]:
    data = fetch(f"{WEB}/api/conversations/{name}")
    assert isinstance(data, dict), data
    return data["messages"]  # type: ignore[return-value]


def tool_ran(tool: str) -> bool:
    """The audit log (logs/audit.log in the folder this runs from) shows the tool's result."""
    log = Path("logs") / "audit.log"
    if not log.is_file():
        return True  # run from somewhere else: nothing to check
    for line in log.read_text("utf-8", "replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("event") == "tool.result" and entry.get("tool") == tool and entry.get("ok"):
            return True
    return False


async def main() -> None:
    wait_until_ready()
    async with websockets.connect("ws://127.0.0.1:8700/ws", origin=WEB, max_size=None) as ws:
        first = await turn(ws, {"type": "ask", "text": "Reply with one short friendly sentence."})
        assert first["type"] == "answer" and not first["failed"], first
        assert str(first["text"]).strip(), "the model answered nothing"
        name = str(first["conversation"])

        # A tool through the real model. A 0.5B model does not always choose to call one, so
        # this is reported, not required (the tool path itself is covered by the unit tests).
        greeted = await turn(
            ws,
            {
                "type": "ask",
                "text": "Greet Paul by calling the hello.greet tool with the name Paul.",
                "conversation": name,
            },
        )
        assert greeted["type"] == "answer" and not greeted["failed"], greeted
        called = "ran" if tool_ran("hello.greet") else "was not called by this small model"
        print(f"note: the hello.greet tool {called}")

        question = "Say hi in a few words."
        await turn(ws, {"type": "ask", "text": question, "conversation": name})
        again = await turn(ws, {"type": "regenerate", "question": question, "conversation": name})
        assert again["type"] == "answer" and not again["failed"], again
        messages = conversation(name)
        asked = [m for m in messages if m["role"] == "user" and m["content"].startswith("Say hi")]
        assert len(asked) == 1, f"Regenerate added a question instead of replacing: {messages}"
        assert messages[-1]["role"] == "assistant" and messages[-1]["content"].strip()

    assert isinstance(fetch(WEB + "/api/kill", "POST"), dict)
    status = fetch(WEB + "/api/status")
    assert isinstance(status, dict) and status["kill"]["engaged"] is True, status  # type: ignore[index]
    assert isinstance(fetch(WEB + "/api/resume", "POST"), dict)
    status = fetch(WEB + "/api/status")
    assert isinstance(status, dict) and status["kill"]["engaged"] is False, status  # type: ignore[index]
    print("e2e chat ok")


if __name__ == "__main__":
    asyncio.run(main())
