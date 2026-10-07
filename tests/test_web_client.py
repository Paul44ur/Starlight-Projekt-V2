"""The terminal client that drives the web backend: its turn-pump sends an ask, renders the
streamed reply, and answers confirmations back over the socket (so both front-ends share one
brain)."""

from __future__ import annotations

from typing import Any

from starlight.web.client import run_turn


class FakeView:
    def __init__(self, confirm: bool = True, answer: str = "code-123") -> None:
        self._confirm = confirm
        self._answer = answer
        self.events: list[tuple[str, Any]] = []
        self.tokens: list[str] = []

    def on_token(self) -> Any:
        self.events.append(("on_token", None))
        return self.tokens.append

    def end_answer(self, streamed: bool) -> None:
        self.events.append(("end_answer", streamed))

    def answer(self, text: str) -> None:
        self.events.append(("answer", text))

    def info(self, text: str) -> None:
        self.events.append(("info", text))

    def error(self, text: str) -> None:
        self.events.append(("error", text))

    async def confirm(self, question: str) -> bool:
        self.events.append(("confirm", question))
        return self._confirm

    async def ask(self, question: str, secret: bool = False) -> str | None:
        self.events.append(("ask", (question, secret)))
        return self._answer


def pump(messages: list[dict[str, Any]]) -> tuple[Any, list[dict[str, Any]]]:
    queue = list(messages)
    sent: list[dict[str, Any]] = []

    async def send(obj: dict[str, Any]) -> None:
        sent.append(obj)

    async def recv() -> dict[str, Any]:
        return queue.pop(0)

    return (send, recv), sent


async def test_streamed_answer() -> None:
    view = FakeView()
    (send, recv), sent = pump(
        [
            {"type": "start"},
            {"type": "token", "text": "Hel"},
            {"type": "token", "text": "lo"},
            {"type": "answer", "text": "Hello", "stopped": False},
        ]
    )
    await run_turn("hi", send, recv, view)
    assert sent[0] == {"type": "ask", "text": "hi"}
    assert view.tokens == ["Hel", "lo"]
    assert ("end_answer", True) in view.events
    assert ("answer", "Hello") not in view.events  # not printed twice when streamed


async def test_non_streamed_answer_is_printed() -> None:
    view = FakeView()
    (send, recv), _ = pump([{"type": "answer", "text": "Full answer", "stopped": False}])
    await run_turn("hi", send, recv, view)
    assert ("answer", "Full answer") in view.events


async def test_confirmation_is_answered_over_the_socket() -> None:
    view = FakeView(confirm=True)
    (send, recv), sent = pump(
        [
            {"type": "start"},
            {"type": "confirm", "question": "Run files.save?", "id": "c1"},
            {"type": "answer", "text": "done", "stopped": False},
        ]
    )
    await run_turn("save it", send, recv, view)
    assert {"type": "reply", "id": "c1", "value": "yes"} in sent
    assert ("confirm", "Run files.save?") in view.events


async def test_prompt_is_answered() -> None:
    view = FakeView(answer="123456")
    (send, recv), sent = pump(
        [
            {"type": "prompt", "question": "2FA code?", "secret": True, "id": "p1"},
            {"type": "answer", "text": "ok", "stopped": False},
        ]
    )
    await run_turn("log in", send, recv, view)
    assert {"type": "reply", "id": "p1", "value": "123456"} in sent


async def test_error_is_shown() -> None:
    view = FakeView()
    (send, recv), _ = pump([{"type": "start"}, {"type": "error", "text": "boom"}])
    await run_turn("x", send, recv, view)
    assert ("error", "boom") in view.events
