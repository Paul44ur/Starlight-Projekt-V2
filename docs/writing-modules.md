# Writing a module

A module is a folder in `modules/`. The core finds it at start-up; you never edit the core.

## 1. Create it from the template

```bash
starlight modules new weather
```

This copies `modules/_template/` to `modules/weather/` and renames the class.

```
modules/weather/
  module.yaml   manifest
  module.py     code
  __init__.py
  README.md
```

## 2. The manifest (`module.yaml`)

```yaml
name: weather              # must match the folder name: lowercase, digits, _
version: 0.1.0
description: Current weather for a city
entrypoint: module:WeatherModule   # file:class
permissions: [safe]        # the highest permission any of its tools needs
dependencies: []           # modules that must load first
enabled_by_default: true
```

## 3. The code (`module.py`)

```python
from typing import ClassVar

from pydantic import BaseModel

from starlight.core import Event, Module, ModuleContext, Permission, tool


class Config(BaseModel):  # read from config/modules/weather.yaml
    units: str = "metric"


class WeatherModule(Module):
    Config = Config
    subscriptions: ClassVar[list[str]] = ["task.completed"]

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    @tool
    async def current(self, city: str) -> str:
        """Current weather for a city."""  # the AI sees this description
        ...

    @tool(permission=Permission.CONFIRM)
    def subscribe(self, city: str) -> str:
        """Send a daily forecast. Asks you first."""
        ...

    async def on_event(self, event: Event) -> None: ...

    async def shutdown(self) -> None: ...
```

The interface:

| Method | When it runs |
| --- | --- |
| `setup(ctx)` | once, after loading |
| `@tool` methods | when the AI calls them. Type hints become the argument schema, the docstring becomes the description |
| `on_event(event)` | for each event matching `subscriptions` (glob patterns like `web.*`) |
| `shutdown()` | on disable, reload and core shutdown |

`ctx` gives you:

- `ctx.config`: your validated config
- `ctx.logger`: writes to `logs/modules/<name>.log` and the main log
- `ctx.publish(name, data)`: send an event. Modules never import each other; they use events
  or `ctx.call` (below)
- `ctx.kill_switch`: call `ctx.kill_switch.check()` inside long loops, and register
  `ctx.kill_switch.add_cleanup(fn, owner=ctx.name)` for anything that must be released
  on a kill (keyboard, mouse, browser sessions)
- `ctx.data_dir`: `data/modules/<name>/`, your own storage
- `ctx.http`: the shared polite HTTP client. Use it for every web request so the global
  limit (5 requests per second), robots.txt, back-off and caching apply:
  `await ctx.http.get(url)`
- `ctx.vault`: secrets from the OS keyring, e.g. `ctx.vault.require("mymodule.api_key")`.
  Never log or return them
- `ctx.ui`: ask you something while a task runs: `await ctx.ui.confirm("...")`,
  `await ctx.ui.ask("2FA code?")`, `await ctx.ui.notify("...")`
- `ctx.stats`: metrics for the statistics viewer: `ctx.stats.record("mymodule.items", 3)`
- `ctx.model`: the main model, e.g. to summarize something inside a tool
- `ctx.model_for("code")`: the model the core routes a kind of work to (`roles:` in the
  config), falling back to the main model
- `ctx.deliver(text, title="")`: tell the user something now (an alert, a reminder) on
  every open channel: the terminal, every web chat, a desktop notification and WhatsApp
- `ctx.every(seconds, fn, "name")`: run `fn` (sync or async) on a timer, and
  `ctx.background(coro_fn, "name")` for a long-running task. Both run only in the primary
  Starlight process (so two open Starlights never do the work twice), skip a round while
  the kill switch is engaged, log failures and stop when the module unloads
- `await ctx.call("weather.now", {"place": "Vienna"})`: use another module's tool. It goes
  through the same permission rules, kill switch and audit log as a call from the model;
  `ctx.has_tool(name)` tells you whether it is loaded and `ctx.permission_of(name)` what
  would happen (safe, confirm or blocked)
- `await ctx.run_request("...")`: ask the assistant something (one-shot) and get its answer
- `ctx.embedder`: the embedding model memory uses: `await ctx.embedder.embed(["text"])`
- `ctx.output_dir`: your Starlight folder for files made for the user

Shared helpers:

- `starlight.core.safepaths.PathGuard(ctx.paths.root, ctx.output_dir, trusted)`:
  `.resolve(text)` turns a name or path from the chat into a path. Starlight's own folder
  is refused, `..` is refused, a bare name lands in the Starlight folder, and `.free` tells
  you whether you may write without asking first
- `starlight.core.schedule`: `parse_when("tomorrow 9am", now)` and
  `parse_schedule("weekdays 7:30")` (also "mon,wed,fri 18:00", "every 2 hours", "every
  month on the 1st at 9:00"), whose `.next_after(now)` gives the next time
- Event handlers that send something slow can subscribe with
  `ctx.bus.subscribe(pattern, handler, owner=ctx.name, background=True)` so the publisher
  is not held up

For a headless browser, use `starlight.browser.Browser(ctx.http)`: its requests count
against the same limits.

## 4. Permissions

Tag each tool:

- `Permission.SAFE`: runs without asking
- `Permission.CONFIRM`: asks you first (logins, input emulation, running code, changing servers)
- `Permission.BLOCKED`: never runs

You can override tags in `config/starlight.yaml` under `permissions:` with glob patterns
(`auto_approve`, `confirm`, `blocked`). In `starlight run`, answering `a` approves a tool
for the rest of the session.

## 5. Try it

```bash
starlight tools                                   # is your tool listed?
starlight ask '!weather.current {"city": "Wien"}' # call it directly (echo provider)
starlight modules disable weather
starlight modules enable weather
```

In `starlight run`, `/reload weather` hot-reloads your code without restarting.

If your module raises an exception in `setup`, a tool or `on_event`, the error goes to the
logs and the module (or that one call) fails. The core and the other modules keep running.
