# Three worked module examples

[Writing a module](writing-modules.md) explains the parts. Here are three small modules
built from them, each a little more involved than the one before. Every file below is
complete: copy it into `modules/<name>/` (with an empty `__init__.py`) and it runs. The
tests (`tests/test_module_examples.py`) load these very files, so they stay correct.

1. **Quotes**: tools with arguments, a config file and your own data.
2. **Plants**: work on a timer, a message to the user, an event, another module's tool.
3. **Old downloads**: a tool that asks first, safe paths and the kill switch.

## 1. Quotes: tools, a config and your own data

"Give me a quote about courage", "remember this quote: ...". The manifest says what the
module is for; the `keywords` help Starlight pick it for requests that do not use the
word "quote".

<!-- file: modules/quotes/module.yaml -->
```yaml
name: quotes
version: 0.1.0
description: Quotes - a saying for the moment, and the ones you collect
keywords: ["saying", "wisdom", "Zitat", "Spruch"]
entrypoint: module:QuotesModule
permissions: [safe]
dependencies: []
enabled_by_default: true
```

The code: a `Config` read from `config/modules/quotes.yaml` (with defaults, so the file is
optional), and two tools. Type hints become the arguments the model fills in; the first
sentence of each docstring is what it reads to decide when to use the tool. What you
collect goes in `ctx.data_dir` (`data/modules/quotes/`), never in the module's folder.

<!-- file: modules/quotes/module.py -->
```python
"""Quotes: a saying for the moment, and the ones you collect."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.jsonfile import load_json, save_json


class Config(BaseModel):
    quotes: list[str] = Field(
        default_factory=lambda: [
            "Courage is grace under pressure. - Ernest Hemingway",
            "Simplicity is prerequisite for reliability. - Edsger Dijkstra",
        ]
    )


class QuotesModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.file = ctx.data_dir / "mine.json"

    def _all(self) -> list[str]:
        return [*self.config.quotes, *load_json(self.file, [])]

    @tool
    def quote(self, topic: str = "") -> dict[str, Any]:
        """A quote, about a topic if one is given (e.g. "courage")."""
        found = [q for q in self._all() if topic.lower() in q.lower()]
        if not found:
            raise ToolError(f"no quote about {topic} yet; add one with quotes.remember")
        return {"quote": found[0], "of": len(found)}

    @tool
    def remember(self, text: str, author: str = "") -> dict[str, Any]:
        """Keep a quote you like, with who said it."""
        if not text.strip():
            raise ToolError("which quote?")
        mine = load_json(self.file, [])
        mine.append(f"{text.strip()} - {author.strip()}" if author.strip() else text.strip())
        save_json(self.file, mine)
        return {"kept": mine[-1], "you_have": len(mine)}
```

`ToolError` is the way to say "that did not work, and here is why": the model reads the
message and can tell you, or try something else.

## 2. Plants: a timer, a message, an event and another module's tool

"The basil needs water every 3 days." Every hour the module looks whether a plant is due
and tells you - on every channel that is open, or in the focus-mode summary. It also
sends an event, so an automation can do more ("when a plant is thirsty, turn on the
balcony light"), and it asks the weather module, if it is on, whether rain will do the
watering.

<!-- file: modules/plants/module.yaml -->
```yaml
name: plants
version: 0.1.0
description: Plants - reminds you to water them, and skips a day when it rains
keywords: ["water", "watering", "basil", "gießen", "Pflanze", "Blumen"]
entrypoint: module:PlantsModule
permissions: [safe]
dependencies: []
enabled_by_default: true
```

<!-- file: modules/plants/module.py -->
```python
"""Plants: a note when one needs water - unless the forecast says it will rain."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.jsonfile import load_json, save_json


class Config(BaseModel):
    plants: dict[str, int] = Field(default_factory=dict)  # name -> water every N days
    outdoors: list[str] = Field(default_factory=list)  # rain waters these


class PlantsModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.file = ctx.data_dir / "watered.json"
        # Only in the primary Starlight (never twice), skipped while the kill switch is on.
        ctx.every(3600, self.check, "thirsty", first_delay=60)

    def _today(self) -> dt.date:
        return dt.date.today()

    def _due(self) -> list[str]:
        watered = load_json(self.file, {})
        today = self._today()
        due = []
        for plant, days in self.config.plants.items():
            last = dt.date.fromisoformat(watered.get(plant, "2000-01-01"))
            if (today - last).days >= days:
                due.append(plant)
        return due

    async def _rain_today(self) -> bool:
        if not self.ctx.has_tool("weather.forecast"):
            return False  # the weather module is off: just remind
        out = await self.ctx.call("weather.forecast", {"days": 1})  # asks, logs, as usual
        if not out.ok:
            return False
        chance = str(out.output["days"][0].get("rain_chance", "0")).rstrip("%")
        return chance.isdigit() and int(chance) >= 70

    async def check(self) -> list[str]:
        """Tell the user which plants need water now; returns them."""
        due = self._due()
        if due and await self._rain_today():
            due = [p for p in due if p not in self.config.outdoors]
        if due:
            await self.ctx.deliver("Water: " + ", ".join(due), title="Plants")
            await self.ctx.publish("plants.thirsty", {"plants": due})
        return due

    @tool
    def watered(self, plant: str) -> dict[str, Any]:
        """Note that a plant was watered today."""
        if plant not in self.config.plants:
            raise ToolError(f"no plant called {plant}; known: {', '.join(self.config.plants)}")
        watered = load_json(self.file, {})
        watered[plant] = self._today().isoformat()
        save_json(self.file, watered)
        return {"watered": plant}
```

The pieces: `ctx.every` for the timer (not your own `asyncio` loop: this one stops with the
module and respects the kill switch), `ctx.deliver` to tell you, `ctx.publish` for the
event, `ctx.has_tool` and `ctx.call` to use another module without importing it.

The plants live in `config/modules/plants.yaml`:

```yaml
plants: {basil: 3, cactus: 14}
outdoors: [basil]
```

## 3. Old downloads: asking first, safe paths and the kill switch

"Clear out my old downloads." Listing is harmless, so `old_downloads` runs on its own.
Moving files is not, so `tidy` is tagged `Permission.CONFIRM`: Starlight shows you what it
will run and waits for your yes (unless you allowed it under `permissions: auto_approve:`).
The files are moved into a folder, never deleted, and the loop checks the kill switch so
one press stops it half-way.

<!-- file: modules/downloads/module.yaml -->
```yaml
name: downloads
version: 0.1.0
description: Downloads - finds old files in a folder and moves them out of the way
keywords: ["downloads", "clean up", "tidy", "aufräumen", "Downloads"]
entrypoint: module:DownloadsModule
permissions: [safe, confirm]
dependencies: []
enabled_by_default: true
```

<!-- file: modules/downloads/module.py -->
```python
"""Old downloads: find files nobody touched for a while and move them into one folder."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from starlight.core import Module, Permission, ToolError, tool
from starlight.core.safepaths import expand


class DownloadsModule(Module):  # no setup needed: self.ctx is there already
    def _old(self, folder: Path, days: int) -> list[Path]:
        if not folder.is_dir():
            raise ToolError(f"{folder} is not a folder")
        cutoff = time.time() - days * 86400
        return sorted(p for p in folder.iterdir() if p.is_file() and p.stat().st_mtime < cutoff)

    @tool
    def old_downloads(self, folder: str = "~/Downloads", days: int = 30) -> dict[str, Any]:
        """The files in a folder not changed for `days` days, biggest first."""
        old = self._old(expand(folder), days)
        old.sort(key=lambda p: -p.stat().st_size)
        return {"count": len(old), "files": [p.name for p in old[:50]]}

    @tool(permission=Permission.CONFIRM)
    def tidy(self, folder: str = "~/Downloads", days: int = 30) -> dict[str, Any]:
        """Move the files older than `days` days into "Old downloads" in that folder.
        Nothing is deleted. Asks first."""
        where = expand(folder)
        target = where / "Old downloads"
        moved = []
        for path in self._old(where, days):
            self.ctx.kill_switch.check()  # one press of the kill switch stops it here
            target.mkdir(exist_ok=True)
            shutil.move(str(path), target / path.name)
            moved.append(path.name)
        return {"moved": len(moved), "to": str(target)}
```

`expand` turns `~` and `%USERPROFILE%`-style names into a real path. For a tool that
*writes* a file you name, use `PathGuard` instead (see [Writing a module](writing-modules.md)):
it keeps Starlight's own folder off limits and tells you when to ask first.

## Trying them

```bash
starlight modules enable quotes
starlight ask '!quotes.quote {"topic": "courage"}'
starlight ask '!downloads.old_downloads {"days": 60}'
```

In the terminal chat, `/reload plants` loads your changes without restarting.
