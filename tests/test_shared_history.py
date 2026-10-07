"""With --both, the browser and the terminal share one conversation. Two requests at the
same moment must both end up in it; before, the later finisher overwrote the other."""

from __future__ import annotations

import asyncio

from starlight.core import Kernel, Paths
from starlight.core.model import Message, ModelResponse


class SlowEcho:
    name = "slow"

    async def chat(self, messages: list[Message], tools: list[dict[str, object]]) -> ModelResponse:
        await asyncio.sleep(0.05)
        last = [m.content for m in messages if m.role == "user"][-1]
        return ModelResponse(content=f"re: {last}")


async def test_two_front_ends_asking_at_once_keep_both_exchanges(paths: Paths) -> None:
    async with Kernel(paths, provider=SlowEcho()) as k:
        await asyncio.gather(
            k.ask("from the browser", keep_history=True),
            k.ask("from the terminal", keep_history=True),
        )
        said = [m.content for m in k.history if m.role in ("user", "assistant")]
    assert "from the browser" in said and "from the terminal" in said
    assert "re: from the browser" in said and "re: from the terminal" in said
