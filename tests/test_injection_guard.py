"""The injection guard: once a request has read something from outside (a web page, an
email ...), tools that ask first by their nature ask again, even those set to run on their
own, so a page cannot quietly talk the model into sending mail or typing on the desktop."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from starlight.core.config import PermissionsConfig
from starlight.core.dispatcher import Dispatcher
from starlight.core.events import EventBus
from starlight.core.kernel import question_for
from starlight.core.killswitch import KillSwitch
from starlight.core.permissions import (
    Permission,
    PermissionPolicy,
    Taint,
    confirm_reason,
    request_taint,
)
from starlight.core.tools import Tool


class NoArgs(BaseModel):
    pass


def tool(module: str, name: str, permission: Permission) -> Tool:
    async def run(**_: Any) -> str:
        return f"{module}.{name} ran"

    return Tool(module, name, "", permission, run, NoArgs)


def setup(guard: bool = True) -> tuple[Dispatcher, list[str]]:
    config = PermissionsConfig(
        auto_approve=["mail.send"], untrusted_sources=["web.*"], guard_after_untrusted=guard
    )
    asked: list[str] = []

    async def confirmer(t: Tool, args: dict[str, Any]) -> bool:
        asked.append(question_for(t, args))
        return False

    switch = KillSwitch(Path(tempfile.mkdtemp()) / "STOP")
    dispatcher = Dispatcher(switch, PermissionPolicy(config), EventBus(), confirmer=confirmer)
    for t in (
        tool("web", "fetch", Permission.SAFE),
        tool("mail", "send", Permission.CONFIRM),  # asks by nature, set to run on its own
        tool("calc", "calculate", Permission.SAFE),
    ):
        dispatcher.register(t)
    return dispatcher, asked


async def test_after_reading_a_page_a_tool_set_to_run_on_its_own_asks_again() -> None:
    dispatcher, asked = setup()
    token = request_taint.set(Taint())
    try:
        assert (await dispatcher.call("mail.send")).ok  # nothing read yet: it just runs
        assert asked == []
        assert (await dispatcher.call("web.fetch")).ok
        refused = await dispatcher.call("mail.send")
        assert not refused.ok and refused.error == "not confirmed by user"
        assert len(asked) == 1 and "read outside content (web.fetch)" in asked[0]
        assert (await dispatcher.call("calc.calculate")).ok  # harmless tools are not asked
        assert len(asked) == 1
        assert confirm_reason.get() == ""  # the reason is only for that one question
    finally:
        request_taint.reset(token)


async def test_the_guard_is_per_request_and_can_be_turned_off() -> None:
    dispatcher, asked = setup()
    token = request_taint.set(Taint())
    await dispatcher.call("web.fetch")
    request_taint.reset(token)
    token = request_taint.set(Taint())  # the next request starts clean
    assert (await dispatcher.call("mail.send")).ok and asked == []
    request_taint.reset(token)

    dispatcher, asked = setup(guard=False)
    token = request_taint.set(Taint())
    await dispatcher.call("web.fetch")
    assert (await dispatcher.call("mail.send")).ok and asked == []
    request_taint.reset(token)


async def test_outside_a_request_nothing_changes() -> None:
    dispatcher, asked = setup()
    await dispatcher.call("web.fetch")  # (an automation, a direct call)
    assert (await dispatcher.call("mail.send")).ok and asked == []


def test_the_shipped_list_covers_the_ways_in() -> None:
    policy = PermissionPolicy(PermissionsConfig())
    for name in ("web.fetch", "mail.read", "pdf.read_pdf", "office.read_docx", "login.open"):
        assert policy.is_untrusted_source(name), name
    for name in ("calc.calculate", "files.save", "mail.send"):
        assert not policy.is_untrusted_source(name), name
