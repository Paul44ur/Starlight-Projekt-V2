"""The approval queue: things Starlight prepared that wait for your one click - a reply it
drafted overnight, a step an automation was not allowed to take by itself. Approving runs
exactly what is shown, through the usual permission rules, kill switch and audit log;
dismissing drops it. Items older than two weeks are dropped too.

Other modules add to it with ``ctx.call("approvals.add", ...)``; the web chat shows it on
the Activity page.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.jsonfile import load_json, save_json
from starlight.core.ui import current_ui

MAX_ITEMS = 100


class Config(BaseModel):
    keep_days: int = Field(default=14, ge=1)


class ApprovedUI:
    """The user already said yes to one item: yes to running that one tool, no to all else."""

    def __init__(self, tool: str) -> None:
        self.tool = tool

    async def confirm(self, question: str) -> bool:
        match = re.search(r"Run (\S+) with", question)
        return match is not None and match.group(1) == self.tool

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


def describe(item: dict[str, Any]) -> str:
    args = json.dumps(item.get("args") or {}, ensure_ascii=False)
    return f"{item['tool']} {args if len(args) <= 300 else args[:299] + '…'}"


class ApprovalsModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.file = ctx.data_dir / "queue.json"

    def items(self) -> list[dict[str, Any]]:
        """What waits, oldest first (old items dropped)."""
        cutoff = (datetime.now() - timedelta(days=self.config.keep_days)).isoformat()
        found = load_json(self.file, [])
        kept = [i for i in found if str(i.get("created", "")) >= cutoff]
        if len(kept) != len(found):
            save_json(self.file, kept)
        return kept

    def _take(self, id: str) -> dict[str, Any]:
        items = self.items()
        item = next((i for i in items if i["id"] == id.strip()), None)
        if item is None:
            raise ToolError(f"nothing with the id {id} waits for approval (approvals.pending)")
        save_json(self.file, [i for i in items if i is not item])
        return item

    async def run_item(self, id: str) -> dict[str, Any]:
        """Run an item the user approved (the web page's button, or approvals.approve)."""
        item = self._take(id)
        token = current_ui.set(ApprovedUI(item["tool"]))
        try:
            outcome = await self.ctx.call(item["tool"], dict(item.get("args") or {}))
        finally:
            current_ui.reset(token)
        if not outcome.ok:
            return {"approved": item["title"], "ok": False, "error": outcome.error}
        return {"approved": item["title"], "ok": True, "result": outcome.output}

    @tool
    def add(
        self, title: str, tool: str, args: dict[str, Any] | None = None, detail: str = ""
    ) -> dict[str, Any]:
        """Put something in the queue for the user to approve later with one click: a
        short title, the tool to run and its arguments, and details to read first (e.g.
        the text of a drafted reply). Nothing runs until it is approved."""
        if not title.strip() or not tool.strip():
            raise ToolError("give a title and the tool to run")
        if tool.strip().startswith("approvals."):
            raise ToolError("the queue cannot approve itself")
        if not self.ctx.has_tool(tool.strip()):
            raise ToolError(f"there is no tool '{tool}' (is its module enabled?)")
        items = self.items()
        if len(items) >= MAX_ITEMS:
            raise ToolError(f"{MAX_ITEMS} things already wait for approval; clear some first")
        item = {
            "id": uuid.uuid4().hex[:8],
            "title": " ".join(title.split())[:200],
            "detail": detail.strip()[:8000],
            "tool": tool.strip(),
            "args": args or {},
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        save_json(self.file, [*items, item])
        return {"waiting": item["id"], "title": item["title"]}

    @tool
    def pending(self) -> dict[str, Any]:
        """What waits for the user's approval: each item's id, title, details and what
        approving it would run."""
        waiting = self.items()
        return {"count": len(waiting), "items": [{**i, "runs": describe(i)} for i in waiting]}

    @tool
    async def approve(self, id: str) -> dict[str, Any]:
        """Run one waiting item (by its id), after the user says yes to exactly what it
        runs."""
        item = next((i for i in self.items() if i["id"] == id.strip()), None)
        if item is None:
            raise ToolError(f"nothing with the id {id} waits for approval (approvals.pending)")
        if not await self.ctx.ui.confirm(f"Approve '{item['title']}'? It runs {describe(item)}."):
            raise ToolError("not approved; it still waits")
        return await self.run_item(id)

    @tool
    def dismiss(self, id: str = "", all: bool = False) -> dict[str, Any]:
        """Drop a waiting item without running it (or every item, with all)."""
        if all:
            count = len(self.items())
            save_json(self.file, [])
            return {"dismissed": count}
        return {"dismissed": self._take(id)["title"]}
