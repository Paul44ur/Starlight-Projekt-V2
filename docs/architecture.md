# How Starlight works

For developers: what is where, how a request is handled, and what each step of the build
plan added. To install and use Starlight, see the [README](../README.md).

## How a request flows

1. Relevant memories are recalled and added to the prompt.
2. `Agent.run()` sends the conversation and the tool list to the model (summarizing old
   turns first if the context is full).
3. Each tool call goes through the one `Dispatcher`: kill switch, permissions
   (safe / confirm / blocked), argument validation, timeout, audit log, statistics.
4. Results go back to the model until it answers. Afterwards, the model picks what to
   remember.

## Layout

```
starlight/core/     kernel, agent loop, dispatcher, events, config, logging, kill switch, banner,
                    context (summaries), http (rate limits), vault, stats, ui
starlight/models/   Ollama, llama.cpp, router, serve, bench
starlight/memory/   Memory folder, embeddings, vector index
starlight/skills/   skill drafting, approval, git history
starlight/sandbox/  Docker and process sandboxes for generated code
starlight/viewer/   the statistics viewer (separate, read-only app)
starlight/web/      the local web chat (FastAPI + a WebSocket chat page)
launcher/           the source of Starlight.exe (standard library only)
starlight/browser.py, text.py   headless browser and HTML-to-text helpers for modules
starlight/tui.py    the colored terminal chat (rich), with a plain fallback
starlight/cli.py    the `starlight` command
modules/            hello, web, login, network, recon, desktop, code, files, access,
                    whatsapp (+ _template)
skills/             skills it learns (one folder each)
scripts/            install.ps1, install.sh
memory/             what the AI learns (one Markdown file per fact)
config/             starlight.yaml, servers.yaml, modules/<name>.yaml
logs/               starlight.log, audit.log, modules/<name>.log (JSON, rotated)
data/               stats.db, caches, sessions, STOP file
```

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

**Step 9, code and skills.** The core routes code work to a coding model
(`roles: code:`). `code.write_code` writes a program with tests, runs the tests in the
sandbox, feeds errors back and retries a few times, then reports; `code.run_python` runs a
snippet. Both ask first. The sandbox is a Docker container (no network, read-only, capped
memory/CPU/processes, no privileges) when Docker runs, otherwise a separate process with a
time limit and audit hooks that block network, the vault, Starlight's private folders and
starting programs. When the same kind of task succeeds again, the model drafts a skill
(`skills/<name>/SKILL.md`, with the tested code). Drafts are never used; approved skills
are added to matching prompts. Every change is committed to `data/skills.git`, so
`starlight skills rollback` undoes a bad one, and `promote` turns a skill into a module.

**Step 10, WhatsApp, viewer, hardening.** The `whatsapp` module uses the official
WhatsApp Business Cloud API: a webhook on this computer behind a Cloudflare Tunnel that
exposes only that path, Meta's signature checked on every delivery, and only your own
number accepted. Text becomes a task and the answer comes back; confirmations are asked
on the phone (reply yes/no); `/kill` stops everything, `/status` reports; server alerts
and new draft skills are sent to you. The statistics viewer is a separate web app that
reads `data/stats.db` read-only: tasks, tool calls per module, errors, web requests (with
the busiest second against the 5/s cap), tokens per second, GPU use, VRAM, memory size
and skills, plus Prometheus metrics at `/metrics`. Hardening: end-to-end tests per
module, a test that the kill switch stops every tool of every module, a load test of the
5 requests/s cap, vault values masked in every log line, and `starlight logs scan`.

## Build plan status

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
| 9 | Writing code and learning skills | done |
| 10 | WhatsApp, statistics viewer and hardening | done |

Everything is tested with mock servers, a local SSH server, a real headless browser, a
real sandbox process and fake screens. What needs your PC (GPU speed, Windows UI
Automation, Docker, your accounts, servers and WhatsApp number) is in
[docs/setup-windows.md](docs/setup-windows.md).

## Writing modules

See [writing-modules.md](writing-modules.md):

```bash
uv run starlight modules new weather   # copy the template, then edit modules/weather/module.py
```

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy && uv run mypy modules
```
