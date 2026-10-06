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
| 4 | Local model on the GPU | next. Uses the built-in `echo` test model until then |
| 5 | Memory and the Memory folder | |
| 6 | Web: search, scraping (max 5 req/s) and logins | |
| 7 | Network: ping and interact with servers | |
| 8 | Desktop: read the screen, emulate input | |
| 9 | Writing code and learning skills | |
| 10 | WhatsApp, statistics viewer and hardening | |

## Quick start (Windows 11 or Linux)

Install [uv](https://docs.astral.sh/uv/), then:

```bash
uv sync --extra hotkey      # Python 3.12 venv + dependencies (+ pynput for Ctrl+Alt+K)
uv run starlight --version
uv run starlight run        # interactive session; /help lists commands
```

Until Step 4 the model is a test stand-in: it echoes text, and `!module.tool {json}` calls
a tool directly:

```
you> !hello.greet {"name": "Paul"}
starlight> Hello, Paul!
```

## Commands

| Command | |
| --- | --- |
| `starlight run` | interactive session, kill hotkey active |
| `starlight ask "..."` | one request, prints the answer (`-y` approves confirmations) |
| `starlight kill [reason]` | engage the kill switch; a running core stops within 0.2 s |
| `starlight resume` | release the kill switch |
| `starlight status` | config, kill switch and module overview |
| `starlight tools` | tools the AI can call, with their permission |
| `starlight modules list / enable NAME / disable NAME / new NAME` | manage modules |

## Layout

```
starlight/core/   kernel, agent loop, dispatcher, event bus, config, logging, kill switch
starlight/cli.py  the `starlight` command
modules/          one folder per capability (_template is the starting point)
memory/           what the AI learns (Step 5)
skills/           skills it learns (Step 9)
config/           starlight.yaml, modules/<name>.yaml, secrets vault (later)
logs/             starlight.log, audit.log, modules/<name>.log (JSON, rotated)
data/             stats.db (Step 10), STOP file, per-module data
tests/
```

## How a request flows

1. `Agent.run()` sends your request and the tool list to the model provider.
2. Each tool call goes through the one `Dispatcher`, which checks the kill switch,
   resolves permissions (safe / confirm / blocked), validates arguments, enforces a
   timeout, writes the audit log and publishes `tool.called`.
3. Results go back to the model until it answers, or until `agent.max_steps`.

The kill switch has three layers: the global hotkey (`Ctrl+Alt+K`, needs the `hotkey`
extra), the `data/STOP` file written by `starlight kill`, and later a WhatsApp `/kill`.
On trigger it cancels the running task, runs the cleanup callbacks modules registered
(release keyboard and mouse, close browsers) and logs the reason to `logs/audit.log`.
It stays engaged until `starlight resume`.

## Writing modules

See [docs/writing-modules.md](docs/writing-modules.md). Short version:

```bash
starlight modules new weather   # copy the template, then edit modules/weather/module.py
```

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

## Notes for Step 4 (your hardware: RX 9060 XT, 16 GB VRAM, Windows 11)

- 16 GB fits a 14B model at Q4_K_M (about 9 GB) with a 32k context and q8 KV cache,
  all layers on the GPU. 7B to 8B models leave room for a second, small routing model.
- On Windows, the llama.cpp Vulkan build is the most dependable way to use an AMD card.
  Check whether your Ollama or llama.cpp ROCm/HIP build supports RDNA 4 (gfx1200)
  before choosing it.
- Providers plug in through `starlight.core.register_provider()` and are selected with
  `model.provider` in `config/starlight.yaml`.
