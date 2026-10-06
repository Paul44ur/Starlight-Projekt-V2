# Starlight V2

A local AI assistant built as a small Python core plus plug-in modules. Every capability
(web, WhatsApp, screen, input, servers, code) is a folder in `modules/` that you can add,
remove or disable without touching the core.

## Status

| Step | | Status |
| --- | --- | --- |
| 1 | Foundations and project layout | done |
| 2 | Core kernel, logging and kill switch | done (WhatsApp `/kill` comes with Step 10) |
| 3 | Module system | done |
| 4 | Local model on the GPU | done: Ollama and llama.cpp providers, router, summarizing, streaming, benchmark |
| 5 | Memory and the Memory folder | done |
| 6 | Web: search, scraping (max 5 req/s) and logins | done |
| 7 | Network: ping and interact with servers | done |
| 8 | Desktop: read the screen, emulate input | done |
| 9 | Writing code and learning skills | |
| 10 | WhatsApp, statistics viewer and hardening | |

Steps 4 to 8 are tested with mock servers, a local SSH server, a real headless browser
and fake screens. What needs your PC (GPU speed, Windows UI Automation, your accounts and
servers) is in [docs/setup-windows.md](docs/setup-windows.md).

## Quick start (Windows 11 or Linux)

Install [uv](https://docs.astral.sh/uv/), then:

```bash
uv sync --extra all                 # Python 3.12 venv + every optional module dependency
uv run playwright install chromium  # headless browser for JavaScript pages and logins
uv run starlight --version
uv run starlight run                # interactive session; /help lists commands
```

Out of the box the model is a test stand-in (`model.provider: echo`): it echoes text, and
`!module.tool {json}` calls a tool directly. Switch to your GPU model in
`config/starlight.yaml` (see [docs/setup-windows.md](docs/setup-windows.md)).

```
you> !hello.greet {"name": "Paul"}
starlight> Hello, Paul!
```

## Commands

| Command | |
| --- | --- |
| `starlight run` | interactive session, kill hotkey active, answers stream in |
| `starlight ask "..."` | one request, prints the answer (`-y` approves confirmations) |
| `starlight kill [reason]` / `resume` | engage / release the kill switch |
| `starlight status` | config, kill switch and module overview |
| `starlight tools` | tools the AI can call, with their permission |
| `starlight modules list / enable / disable / new NAME` | manage modules |
| `starlight model info / serve / bench` | show model config, start the model server, measure tokens/s and VRAM |
| `starlight memory list / show / add / search / forget / export / reindex` | the Memory folder |
| `starlight vault set / list / delete KEY` | secrets in the Windows Credential Manager (OS keyring) |

## What each step added

**Step 4, local model.** `ModelProvider` implementations for Ollama (native API: keep-alive,
all layers on the GPU, VRAM report) and llama.cpp's `llama-server` (OpenAI-compatible,
prompt-prefix caching). Both do tool calling, streaming and images. `router` sends simple
requests to a small fast model and the rest to the large one. When a conversation outgrows
the context window, older turns are summarized (by the fast model when routing) instead of
cut off, and the summary is saved to `memory/conversations/`. `starlight model serve`
starts the server with full GPU offload, flash attention and a q8 KV cache;
`starlight model bench` records tokens/s, time to first token and VRAM in `data/stats.db`.

**Step 5, memory.** One Markdown file per fact in `memory/` with a small header (topic,
source, date, confidence). A vector index (embeddings cached in SQLite, rebuilt from the
folder, so editing or deleting a file by hand just works) feeds the most relevant memories
into each prompt. After each task the model picks what is worth keeping; a fact on an
existing topic updates that file instead of adding a new one. Tools: `memory.remember`,
`memory.recall`, `memory.forget` (asks first).

**Step 6, web.** One shared HTTP client for every module: at most 5 requests in any
one-second window (a sliding window, so bursts can never exceed it), 1 per second per
site, robots.txt and crawl-delay, back-off on 429/503 with Retry-After, and a page cache.
The headless browser counts each of its requests against the same cap. `web.search`
(SearXNG or Brave), `web.fetch`, `web.fetch_many`, `web.links`. The `login` module fills
login forms with credentials from the vault, asks you for 2FA codes, asks before the first
login to each site and saves the session so later visits skip the login.

**Step 7, network.** `config/servers.yaml` is the allow-list; anything else is refused.
Ping, TCP port, DNS and HTTP checks with latency recorded for statistics; SSH (keys from
the vault), HTTP/REST, TCP and WebSocket. Read-only SSH commands (`df`, `uptime`,
`systemctl status`, ...) run freely; anything that could change the server asks first.
An optional watcher alerts you when a server goes down.

**Step 8, desktop.** Screenshots (mss), OCR (Tesseract), a local vision model to describe
the screen and locate buttons, mouse and keyboard (pynput), and Windows UI Automation to
click controls by name. Every action takes a new screenshot afterwards to confirm the
screen changed (and, with `expect_text`, that the right text appeared). Safety: a mouse in
a screen corner triggers the kill switch, typing stops between two characters when the
hotkey is pressed, keys are released on a kill, a red banner shows while Starlight has
control, and screenshots stay in memory and only go to a model on this computer.

## Layout

```
starlight/core/     kernel, agent loop, dispatcher, events, config, logging, kill switch,
                    context (summaries), http (rate limits), vault, stats, ui
starlight/models/   Ollama, llama.cpp, router, serve, bench
starlight/memory/   Memory folder, embeddings, vector index
starlight/browser.py, text.py   headless browser and HTML-to-text helpers for modules
starlight/cli.py    the `starlight` command
modules/            hello, web, login, network, desktop (+ _template)
memory/             what the AI learns (one Markdown file per fact)
config/             starlight.yaml, servers.yaml, modules/<name>.yaml
logs/               starlight.log, audit.log, modules/<name>.log (JSON, rotated)
data/               stats.db, caches, sessions, STOP file
```

## How a request flows

1. Relevant memories are recalled and added to the prompt.
2. `Agent.run()` sends the conversation and the tool list to the model (summarizing old
   turns first if the context is full).
3. Each tool call goes through the one `Dispatcher`: kill switch, permissions
   (safe / confirm / blocked), argument validation, timeout, audit log, statistics.
4. Results go back to the model until it answers. Afterwards, the model picks what to
   remember.

## Writing modules

See [docs/writing-modules.md](docs/writing-modules.md):

```bash
starlight modules new weather   # copy the template, then edit modules/weather/module.py
```

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy && uv run mypy modules
```
