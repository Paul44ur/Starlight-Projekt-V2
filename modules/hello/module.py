"""Example module, created with `starlight modules new hello`."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from starlight.core import Event, Module, ModuleContext, Permission, tool


class Config(BaseModel):
    """Settings from config/modules/hello.yaml (all fields need defaults)."""

    greeting: str = "Hello"


class HelloModule(Module):
    Config = Config
    subscriptions: ClassVar[list[str]] = ["task.completed"]  # events delivered to on_event()

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.completed = 0
        ctx.logger.info("ready", greeting=self.config.greeting)

    @tool
    def greet(self, name: str) -> str:
        """Greet someone by name."""
        return f"{self.config.greeting}, {name}!"

    @tool(permission=Permission.CONFIRM)
    def shout(self, text: str, times: int = 1) -> str:
        """Repeat text in capitals. Asks for confirmation first (example of a risky tool)."""
        return " ".join([text.upper()] * times)

    async def on_event(self, event: Event) -> None:
        self.completed += 1

    async def shutdown(self) -> None:
        self.ctx.logger.info("stopping", tasks_seen=self.completed)
