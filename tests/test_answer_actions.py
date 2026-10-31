"""Web chat answer actions: ask the last question again (regenerate), how fast an answer
was written, and a conversation downloaded as Markdown."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest

from starlight.core import Kernel, Paths
from starlight.web.app import answer_stats
from tests.test_web_chat import LOCAL, _until_answer, client, web_home


def test_regenerate_replaces_the_last_answer(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c, c.websocket_connect("/ws", headers=LOCAL) as ws:
        ws.send_json({"type": "ask", "text": "first"})
        _until_answer(ws)
        ws.send_json({"type": "ask", "text": "second"})
        conversation = _until_answer(ws)["conversation"]
        ws.send_json({"type": "regenerate", "conversation": conversation})
        again = _until_answer(ws)
        assert again["text"] == "You said: second" and again["conversation"] == conversation
        assert set(again["stats"]) >= {"seconds", "tokens"} and again["failed"] is False
        messages = c.get(f"/api/conversations/{conversation}", headers=LOCAL).json()["messages"]
    # The old exchange was replaced, not added to.
    assert [m["content"] for m in messages] == [
        "first",
        "You said: first",
        "second",
        "You said: second",
    ]


def test_regenerate_with_nothing_asked_yet(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c, c.websocket_connect("/ws", headers=LOCAL) as ws:
        ws.send_json({"type": "regenerate"})
        m = ws.receive_json()
    assert m == {"type": "error", "text": "nothing to ask again"}


async def test_a_taken_back_exchange_is_put_back_if_asking_again_fails(tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    paths.ensure()
    paths.config_file.write_text("model:\n  provider: echo\nkill_switch:\n  hotkey: null\n")
    async with Kernel(paths) as k:
        await k.ask("one", keep_history=True)
        await k.ask("two", keep_history=True)
        name = k.conversations.current
        before = [m.content for m in k.history]
        on_disk = k.conversations.messages(name)
        taken = await k.take_back()
        assert taken is not None and taken.content == "two"
        assert [m.content for m in k.history] == before[:2]
        # Only set aside in memory: a crash or a stop now loses nothing.
        assert k.conversations.messages(name) == on_disk
        await k.put_back()  # the new answer was stopped: the old one comes back
        assert [m.content for m in k.history] == before
        await k.put_back()  # only once
        assert [m.content for m in k.history] == before

        await k.take_back()
        await k.ask("two", keep_history=True)  # it worked this time
        await k.put_back()  # so there is nothing to put back
        assert [m.content for m in k.history] == before
        with pytest.raises(ValueError, match="changed since"):
            await k.take_back(expected="one")  # the page is behind: "two" came after it
        assert [m.content for m in k.history] == before
    async with Kernel(paths) as k:  # and it was saved that way
        assert [m.content for m in k.history] == before


def test_regenerate_from_a_page_that_is_behind_changes_nothing(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c, c.websocket_connect("/ws", headers=LOCAL) as ws:
        ws.send_json({"type": "ask", "text": "first"})
        conversation = _until_answer(ws)["conversation"]
        ws.send_json({"type": "ask", "text": "from another window", "conversation": conversation})
        _until_answer(ws)
        ws.send_json({"type": "regenerate", "conversation": conversation, "question": "first"})
        m = ws.receive_json()
        messages = c.get(f"/api/conversations/{conversation}", headers=LOCAL).json()["messages"]
    assert m["type"] == "error" and "changed since" in m["text"]
    assert [x["content"] for x in messages][-1] == "You said: from another window"


def test_regenerate_sends_the_picture_again(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c, c.websocket_connect("/ws", headers=LOCAL) as ws:
        kernel = c.app.state.kernel  # type: ignore[attr-defined]
        seen: list[int] = []
        real = kernel.agent.provider.chat

        async def spy(messages: Any, tools: Any, **kw: Any) -> Any:
            seen.append(len(messages[-1].images))
            return await real(messages, tools)

        kernel.agent.provider.chat = spy
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
        upload = c.post(
            "/api/upload", headers={**LOCAL, "x-filename": "cat.png"}, content=png
        ).json()  # type: ignore[attr-defined]
        ws.send_json({"type": "ask", "text": "what is this?", "attachments": [upload["id"]]})
        conversation = _until_answer(ws)["conversation"]
        ws.send_json(
            {"type": "regenerate", "conversation": conversation, "question": "what is this?"}
        )
        _until_answer(ws)
    assert seen == [1, 1]


async def test_stop_while_the_last_words_are_sent_still_answers(tmp_path: Path) -> None:
    from starlight.core.agent import AgentResult
    from starlight.web.app import WebUI, _run_ask

    sent: list[dict[str, Any]] = []

    async def send(obj: dict[str, Any]) -> None:
        if obj["type"] == "token":
            await asyncio.sleep(0.01)  # a slow link (the phone over Tailscale)
        sent.append(obj)

    class Conversations:
        current = "aaaaaaaaaaaa"

        def messages(self, name: str) -> list[Any]:
            return []

    class FinishedKernel:
        conversations = Conversations()

        async def ask(self, text: str, on_token: Any = None, **kw: Any) -> AgentResult:
            for i in range(100):
                on_token(f"t{i} ")
            return AgentResult("r", answer="done")

    task = asyncio.create_task(_run_ask(FinishedKernel(), WebUI(send), send, "hi"))  # type: ignore[arg-type]
    await asyncio.sleep(0.1)  # the answer is complete (and kept); tokens still going out
    task.cancel()  # "stop"
    await task
    assert sent[-1]["type"] == "answer" and sent[-1]["text"] == "done"  # the page is told


def test_a_conversation_downloads_as_markdown(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c:
        with c.websocket_connect("/ws", headers=LOCAL) as ws:
            ws.send_json({"type": "ask", "text": "Grüße aus Wien 日本"})
            conversation = _until_answer(ws)["conversation"]
        got = c.get(f"/api/conversations/{conversation}/export", headers=LOCAL)
        missing = c.get("/api/conversations/nope/export", headers=LOCAL)
    assert got.status_code == 200 and got.headers["content-type"].startswith("text/markdown")
    assert got.text.startswith("# Grüße aus Wien 日本\n")
    assert "**You:**\n\nGrüße aus Wien 日本" in got.text
    assert "**Starlight:**\n\nYou said: Grüße aus Wien 日本" in got.text
    disposition = got.headers["content-disposition"]
    assert 'filename="Grusse aus Wien.md"' in disposition  # plain ASCII for old browsers
    assert unquote(disposition.split("filename*=UTF-8''")[1]) == "Grüße aus Wien 日本.md"
    assert missing.status_code == 404


def test_answer_stats_add_up_every_step() -> None:
    usage = [
        {"duration_s": 1.25, "completion_tokens": 10, "tokens_per_second": 30.0},
        {"duration_s": 2.0, "completion_tokens": 50, "tokens_per_second": 41.26},
    ]
    assert answer_stats(usage) == {"seconds": 3.2, "tokens": 60, "tokens_per_second": 41.3}
    assert answer_stats([]) == {"seconds": 0.0, "tokens": 0}
