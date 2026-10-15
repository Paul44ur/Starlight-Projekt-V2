from __future__ import annotations

import json
from typing import Any

import pytest

from starlight.core import Event, EventBus, Permission, PermissionPolicy, tool
from starlight.core.config import PermissionsConfig
from starlight.core.logging import get_logger, module_logger, redact, setup_logging
from starlight.core.tools import collect_tools


async def test_event_bus_patterns_and_failing_handler() -> None:
    bus = EventBus()
    seen: list[str] = []

    def broken(event: Event) -> None:
        raise RuntimeError("boom")

    async def record(event: Event) -> None:
        seen.append(event.name)

    bus.subscribe("web.*", broken, owner="bad")
    bus.subscribe("web.*", record, owner="good")
    await bus.publish("web.fetched", {"url": "x"})
    await bus.publish("message.received")
    assert seen == ["web.fetched"]

    bus.unsubscribe_owner("good")
    await bus.publish("web.fetched")
    assert seen == ["web.fetched"]


def test_permission_policy_order() -> None:
    policy = PermissionPolicy(
        PermissionsConfig(auto_approve=["hello.*"], confirm=["hello.danger"], blocked=["web.*"])
    )
    assert policy.resolve("hello.greet", Permission.CONFIRM) is Permission.SAFE
    assert policy.resolve("hello.danger", Permission.SAFE) is Permission.CONFIRM
    assert policy.resolve("web.login", Permission.SAFE) is Permission.BLOCKED
    assert policy.resolve("x.y", Permission.BLOCKED) is Permission.BLOCKED
    assert policy.resolve("x.y", Permission.CONFIRM) is Permission.CONFIRM
    policy.allow_for_session("x.y")
    assert policy.resolve("x.y", Permission.CONFIRM) is Permission.SAFE


class Sample:
    @tool
    def add(self, a: int, b: int = 2) -> int:
        """Add two numbers."""
        return a + b

    @tool(name="sleepy", permission=Permission.CONFIRM)
    async def nap(self, seconds: float) -> str:
        return "ok"

    def not_a_tool(self) -> None: ...


async def test_tools_schema_and_validation() -> None:
    tools = {t.name: t for t in collect_tools(Sample(), "sample")}
    assert set(tools) == {"add", "sleepy"}
    schema = tools["add"].json_schema()
    assert schema["name"] == "sample.add"
    assert schema["description"] == "Add two numbers."
    assert schema["parameters"]["required"] == ["a"]
    assert await tools["add"].run(tools["add"].validate({"a": 1})) == 3
    assert await tools["add"].run(tools["add"].validate({"a": "4", "b": 1})) == 5
    assert tools["sleepy"].permission is Permission.CONFIRM
    with pytest.raises(Exception, match="extra"):
        tools["add"].validate({"a": 1, "c": 2})


def test_redact() -> None:
    data: dict[str, Any] = {
        "user": "paul",
        "password": "x",
        "api_key": "y",
        "headers": {"Authorization": "z"},
        "items": [{"session_cookie": "c"}],
        "tokens_per_second": 42,
    }
    assert redact(data) == {
        "user": "paul",
        "password": "***",
        "api_key": "***",
        "headers": {"Authorization": "***"},
        "items": [{"session_cookie": "***"}],
        "tokens_per_second": 42,
    }


def test_logs_are_json_per_module_and_redacted(tmp_path: Any) -> None:
    setup_logging(tmp_path)
    get_logger("core").info("hello", password="secret")
    module_logger("web").info("fetched", url="https://example.org")
    main = [json.loads(line) for line in (tmp_path / "starlight.log").read_text().splitlines()]
    web = [json.loads(line) for line in (tmp_path / "modules" / "web.log").read_text().splitlines()]
    assert main[0]["event"] == "hello" and main[0]["password"] == "***"
    assert web == [main[1]]
    assert web[0]["module"] == "web"
