"""Research a question: plan a few searches, read the best pages and write a short report
with numbered sources [1], [2] ... that you can check. Pages are read through the web
module, so its limits (5 requests a second, robots.txt) apply. `save` puts the report in
your Starlight folder (Research/).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from starlight.core import Message, Module, ModuleContext, ToolError, tool

PLAN = (
    "You plan web searches. Reply with only a JSON list of {n} short search queries that "
    "together answer the user's question (different angles, no duplicates)."
)
WRITE = (
    "Answer the question using ONLY the numbered sources below. Write a clear, short report "
    "in the language of the question: first a direct answer, then the key points. After each "
    "fact put its source number like [2]. If the sources disagree, say so; if they do not "
    "answer the question, say that plainly. Do not invent sources or facts."
)


class Config(BaseModel):
    quick_pages: int = Field(default=5, ge=1, le=20)
    deep_pages: int = Field(default=12, ge=2, le=40)
    chars_per_page: int = Field(default=4000, ge=500, le=20000)


class ResearchModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    async def _model(self, system: str, text: str) -> str:
        model = self.ctx.model_for("research")
        if model is None:
            raise ToolError("no model is configured")
        reply = await model.chat([Message("system", system), Message("user", text)], [])
        return reply.content.strip()

    async def _queries(self, question: str, count: int) -> list[str]:
        if count <= 1:
            return [question]
        try:
            raw = await self._model(PLAN.format(n=count), question)
            found = json.loads(raw[raw.index("[") : raw.rindex("]") + 1])
            queries = [str(q).strip() for q in found if str(q).strip()]
        except (ValueError, TypeError):
            queries = []
        return list(dict.fromkeys([question, *queries]))[: count + 1]

    async def _tool(self, name: str, **args: Any) -> Any:
        result = await self.ctx.call(name, args)
        if not result.ok:
            raise ToolError(f"{name} failed: {result.error}")
        return result.output

    @tool
    async def research(
        self, question: str, depth: Literal["quick", "deep"] = "quick", save: bool = False
    ) -> dict[str, Any]:
        """Look something up on the web and get a short report with numbered sources.
        `depth`: "quick" (a few pages) or "deep" (more searches and pages). `save` writes
        the report to your Starlight folder. Show the report and its sources to the user."""
        if not question.strip():
            raise ToolError("what should I research?")
        limit = self.config.deep_pages if depth == "deep" else self.config.quick_pages
        queries = await self._queries(question.strip(), 4 if depth == "deep" else 1)
        urls: list[str] = []
        titles: dict[str, str] = {}
        for query in queries:
            for hit in await self._tool("web.search", query=query, max_results=8):
                url = hit.get("url", "")
                if url.startswith("http") and url not in titles:
                    titles[url] = hit.get("title", "")
                    urls.append(url)
            if len(urls) >= limit * 2:
                break
        if not urls:
            raise ToolError("the search found nothing; try other words")
        pages = await self._tool(
            "web.fetch_many", urls=urls[: limit + 3], max_chars_each=self.config.chars_per_page
        )
        sources = [
            {
                "title": p.get("title") or titles.get(p["url"], ""),
                "url": p["url"],
                "text": p["text"],
            }
            for p in pages
            if p.get("text") and len(p["text"].strip()) > 200
        ][:limit]
        if not sources:
            raise ToolError("none of the pages could be read; try again later or other words")
        numbered = "\n\n".join(
            f"[{i}] {s['title']} ({s['url']})\n{s['text']}" for i, s in enumerate(sources, 1)
        )
        report = await self._model(WRITE, f"Question: {question}\n\nSources:\n\n{numbered}")
        listing = [{"n": i, "title": s["title"], "url": s["url"]} for i, s in enumerate(sources, 1)]
        result: dict[str, Any] = {"question": question, "report": report, "sources": listing}
        if save:
            result["saved"] = self._save(question, report, listing)
        return result

    def _save(self, question: str, report: str, sources: list[dict[str, Any]]) -> str:
        folder = self.ctx.output_dir / "Research"
        folder.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:60] or "research"
        path = folder / f"{datetime.now():%Y-%m-%d} {slug}.md"
        counter = 2
        while path.exists():
            path = folder / f"{datetime.now():%Y-%m-%d} {slug} ({counter}).md"
            counter += 1
        lines = [f"# {question}", "", report, "", "## Sources", ""]
        lines += [f"{s['n']}. [{s['title'] or s['url']}]({s['url']})" for s in sources]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)
