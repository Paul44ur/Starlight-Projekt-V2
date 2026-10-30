"""The web chat's extras: the tools an answer used, editing your last question, branching,
pinning, folders and the archive, the / commands, code colours, maths, and exports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from starlight.core.conversations import ConversationStore
from starlight.core.model import Message, ToolCall
from tests.test_chat_page import SEND, open_chat, static_site  # noqa: F401  (a fixture)
from tests.test_web_chat import LOCAL, _until_answer, client, web_home


def store_with_two_turns(folder: Path) -> tuple[ConversationStore, str]:
    store = ConversationStore(folder)
    name = store.create()
    call = ToolCall("weather.now", {"place": "Kiel"}, "c1")
    store.save(
        name,
        [
            Message("user", "weather in Kiel?"),
            Message("assistant", "", tool_calls=[call]),
            Message("tool", "ERROR: offline", tool_call_id="c1", name="weather.now"),
            Message("assistant", "I could not reach the weather service."),
            Message("user", "and tomorrow?"),
            Message("assistant", "Still offline."),
        ],
    )
    return store, name


def test_answers_carry_the_tools_they_used(tmp_path: Path) -> None:
    store, name = store_with_two_turns(tmp_path)
    shown = store.get(name)["messages"]
    assert [m["role"] for m in shown] == ["user", "assistant", "user", "assistant"]
    assert shown[1]["tools"] == [{"tool": "weather.now", "ok": False}]
    assert "tools" not in shown[3]


def test_a_branch_keeps_the_first_exchanges_and_the_original_stays(tmp_path: Path) -> None:
    store, name = store_with_two_turns(tmp_path)
    branch = store.branch(name, 1)
    copied = store.get(branch)
    assert copied["title"].endswith("(branch)")
    assert [m["content"] for m in copied["messages"]] == [
        "weather in Kiel?",
        "I could not reach the weather service.",
    ]
    assert len(store.messages(branch)) == 4  # the tool call and its result came along
    assert len(store.get(name)["messages"]) == 4  # the original is unchanged
    assert len(store.messages(store.branch(name, 2))) == 6
    with pytest.raises(KeyError):
        store.branch(name, 3)
    private = store.create(private=True)
    store.save(private, [Message("user", "a"), Message("assistant", "b")])
    assert store.is_private(store.branch(private, 1))  # a private one's branch stays private


def test_pinned_first_archived_apart_and_folders_kept(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    old, middle, new = (store.create(title) for title in ("old", "middle", "new"))
    for name in (old, middle, new):
        store.save(name, [Message("user", name)])
    assert [c["id"] for c in store.list()] == [new, middle, old]
    assert store.organize(old, pinned=True) == {"pinned": True, "archived": None, "folder": None}
    store.organize(middle, archived=True)
    store.organize(new, folder="  Work   stuff ")
    assert [c["id"] for c in store.list()] == [old, new]
    assert [c["id"] for c in store.list(archived=True)] == [middle]
    assert store.get(new)["folder"] == "Work stuff" and store.get(old)["pinned"]
    store.save(new, [Message("user", "more")])  # an answer later keeps its place in the folder
    assert store.get(new)["folder"] == "Work stuff"


# -- through the web app ---------------------------------------------------------------------


def test_organize_branch_and_export_over_http(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c, c.websocket_connect("/ws", headers=LOCAL) as ws:  # type: ignore[attr-defined]
        ws.send_json({"type": "ask", "text": "first </script><b>x</b>"})
        _until_answer(ws)
        ws.send_json({"type": "ask", "text": "second"})
        _until_answer(ws)
        name = c.get("/api/conversations").json()["current"]
        changed = c.patch(f"/api/conversations/{name}", json={"pinned": True, "folder": "Work"})
        assert changed.json() == {"id": name, "pinned": True, "archived": None, "folder": "Work"}
        listed = c.get("/api/conversations").json()["conversations"]
        assert listed[0]["pinned"] and listed[0]["folder"] == "Work"
        c.patch(f"/api/conversations/{name}", json={"archived": True})
        assert name not in {x["id"] for x in c.get("/api/conversations").json()["conversations"]}
        assert c.get("/api/conversations?archived=true").json()["conversations"][0]["id"] == name
        branch = c.post(f"/api/conversations/{name}/branch", json={"turns": 1}).json()["id"]
        assert len(c.get(f"/api/conversations/{branch}").json()["messages"]) == 2
        assert c.post(f"/api/conversations/{name}/branch", json={"turns": 9}).status_code == 404
        assert c.post(f"/api/conversations/{name}/branch", json={}).status_code == 400
        page = c.get(f"/api/conversations/{name}/export?format=html")
        shown = c.get(f"/api/conversations/{name}/export?format=html&show=true")
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["content-disposition"].startswith("attachment;")
    assert shown.headers["content-disposition"].startswith("inline;")
    data = page.text.split('id="data">', 1)[1].split("</script>", 1)[0]
    payload = json.loads(data)
    assert payload["messages"][0]["content"] == "first </script><b>x</b>"  # whole, and inert
    assert payload["print"] is False and '"print": true' in shown.text
    assert "function renderMarkdown" in page.text and "<b>x</b>" not in page.text.split("<body>")[1]


def test_editing_the_last_question_replaces_that_exchange(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c, c.websocket_connect("/ws", headers=LOCAL) as ws:  # type: ignore[attr-defined]
        ws.send_json({"type": "ask", "text": "one"})
        _until_answer(ws)
        ws.send_json({"type": "ask", "text": "two"})
        _until_answer(ws)
        name = c.get("/api/conversations").json()["current"]
        ws.send_json({"type": "edit", "question": "two", "text": "three", "conversation": name})
        assert _until_answer(ws)["text"] == "You said: three"
        shown = [m["content"] for m in c.get(f"/api/conversations/{name}").json()["messages"]]
        assert shown == ["one", "You said: one", "three", "You said: three"]
        ws.send_json({"type": "edit", "question": "not it", "text": "four", "conversation": name})
        while ws.receive_json()["type"] != "error":  # the page was behind: refused
            pass
        assert "three" in str(c.get(f"/api/conversations/{name}").json())


def test_each_tool_is_reported_as_it_runs(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c, c.websocket_connect("/ws", headers=LOCAL) as ws:  # type: ignore[attr-defined]
        ws.send_json({"type": "ask", "text": '!hello.greet {"name": "Paul"}'})
        seen: list[dict[str, Any]] = []
        while not seen or seen[-1]["type"] != "answer":
            seen.append(ws.receive_json())
        name = c.get("/api/conversations").json()["current"]
        saved = c.get(f"/api/conversations/{name}").json()["messages"]
    steps = [(m["tool"], m["state"]) for m in seen if m["type"] == "tool"]
    assert steps == [("hello.greet", "running"), ("hello.greet", "done")]
    assert saved[-1]["tools"] == [{"tool": "hello.greet", "ok": True}]


# -- in the browser -------------------------------------------------------------------------


async def test_the_page_shows_tools_edits_commands_code_and_maths(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_chat(playwright, static_site)
        try:
            await page.evaluate(
                "() => { window.sent = []; window.sockets.at(-1).readyState = 1;"
                " window.sockets.at(-1).send = m => window.sent.push(JSON.parse(m)); }"
            )
            # the tools an answer used
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            await page.evaluate(
                SEND, {"type": "tool", "tool": "trains.connections", "state": "running"}
            )
            chip = page.locator(".toolstep").first
            assert (await chip.text_content() or "").startswith("⋯ trains.connections")
            await page.evaluate(
                SEND, {"type": "tool", "tool": "trains.connections", "state": "done", "ms": 1530}
            )
            assert await chip.text_content() == "✓ trains.connections 1.5 s"
            await page.evaluate(SEND, {"type": "tool", "tool": "mail.send", "state": "refused"})
            text = "```python\ndef f(x):\n    return 42  # yes\n```\n\nIt is $x^2$, not $5 or $10."
            await page.evaluate(SEND, {"type": "answer", "text": text, "conversation": "c1"})
            assert await page.locator(".toolstep").count() == 2
            assert await page.locator(".tok-k").first.text_content() == "def"
            assert await page.locator(".tok-c").text_content() == "# yes"
            assert await page.locator(".md .math").all_text_contents() == ["x²"]
            # edit the last question: it goes back as an edit of that exchange
            await page.evaluate("() => addMsg('you', 'old question')")
            await page.evaluate(
                "() => { addMsg('bot', 'old answer').parentElement.dataset.kept = '1'; }"
            )
            await page.locator(".msg.you.latest .edit").click()
            assert await page.locator("#input").input_value() == "old question"
            await page.fill("#input", "new question")
            await page.press("#input", "Enter")
            sent = await page.evaluate("() => window.sent.at(-1)")
            assert sent["type"] == "edit" and sent["question"] == "old question"
            assert sent["text"] == "new question"
            assert await page.locator(".msg.you .body").last.text_content() == "new question"
            assert "old answer" not in (await page.locator("#log").text_content() or "")
            # the / menu
            await page.evaluate(SEND, {"type": "answer", "text": "ok", "conversation": "c1"})
            await page.fill("#input", "/st")
            menu = page.locator(".popmenu")
            assert await menu.locator("button").count() == 1
            assert "starfall" in (await menu.text_content() or "")
            await page.press("#input", "Enter")
            assert await page.locator(".star").count() > 0 and await menu.count() == 0
            await page.fill("#input", "/")
            assert await menu.locator("button").count() > 8
            await page.press("#input", "Escape")
            assert await menu.count() == 0
            await page.fill("#input", "/nonsense")
            await page.press("#input", "Enter")
            assert (
                await page.evaluate("() => window.sent.at(-1).text") == "new question"
            )  # not sent
        finally:
            await browser.close()
