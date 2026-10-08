# How Starlight works

For developers: what is where, how a request is handled, and what each step of the build
plan added. To install and use Starlight, see the [README](../README.md).

## How a request flows

1. The date and time, where saved files go and the user's profile
   (`core/situation.py`), relevant memories and approved skills are added to the prompt.
2. The tool router (`core/toolroute.py`) picks the tools that fit the request: the best
   few modules by words and meaning, tools named in the request, the modules used a
   moment ago in the conversation, and `memory.*`, `files.save` and `tools.find`. The
   model can call `tools.find` to get more for the rest of the request.
3. `Agent.run()` sends the conversation and those tools to the model (summarizing old
   turns first if the context is full).
4. Each tool call goes through the one `Dispatcher`: kill switch, permissions
   (safe / confirm / blocked), argument validation, timeout, audit log, statistics.
5. Results go back to the model (cut to `tools.max_result_chars`) until it answers. Afterwards, the model picks what to
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
starlight/web/      the local web chat (FastAPI + a WebSocket chat page); client.py is a
                    terminal that joins the same backend, so --both shares one brain
launcher/           the source of Starlight.exe (standard library only)
starlight/browser.py, text.py   headless browser and HTML-to-text helpers for modules
starlight/tui.py    the colored terminal chat (rich), with a plain fallback
starlight/cli.py    the `starlight` command
modules/            hello, web, login, network, recon, cve, certs, breach, desktop,
                    system, clipboard, weather, news, finance, filesearch, notify,
                    reminders, mail, pdf, code, files, access, whatsapp, todo,
                    calendar, briefing, calc, translate, automations, apps, media,
                    commands, office, dev, research, docs, notes, transcripts,
                    voice, home, lan, backup (+ _template)
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
`models/output.py` keeps replies clean when a model file is faulty: requests carry stop
sequences, the stream is cut at an end-of-turn marker (so the model can't write the user's
next message), and tool calls written as text (```json blocks, `<tool_call>` tags) naming a
known tool are run as real calls and held back from the chat while they stream.

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

**Add-on modules.** Beyond the build plan, a set of everyday, security and productivity
modules follow the same pattern (config, `@tool`, confirmations, the shared HTTP client,
the kill switch) and are enabled by default. Free or local, no API keys except where noted:

- *Everyday:* `weather` (Open-Meteo), `news` (RSS/Atom feeds), `finance` (stooq for stocks,
  frankfurter for currency, CoinGecko for crypto), `clipboard`, `system` (PC health),
  `filesearch` (find files on disk, read-only), `notify` (desktop notifications).
- *Security (read-only, pair with `recon`):* `cve` (NVD vulnerability lookup), `certs` (TLS
  certificate expiry, reads `config/servers.yaml`), `breach` (Have I Been Pwned range API —
  only a hash prefix is sent, the password never leaves the PC).
- *Productivity:* `reminders` (one-off and recurring: daily, weekdays, chosen days, every
  N minutes/hours, monthly; delivered to every open channel, saved to disk), `mail` (IMAP/SMTP with an app-password in
  the vault; reading is free, sending asks first), `pdf` (read text from PDFs and make simple
  ones; needs the `pdf` extra — pypdf and reportlab).

- *Daily life:* `todo` (lists, due dates, priorities), `calendar` (a standard `.ics` file,
  repeating events, read-only ICS feeds whose secret address is kept in the vault, alerts
  before events), `briefing` (pulls calendar, to-dos, reminders, weather, news, mail and
  certificate warnings together through `ctx.call`), `calc` (a safe formula evaluator - the
  text is parsed, never executed - plus units and date maths), `translate` (the
  `translate` role's model, offline).
- *Automations:* rules in `config/modules/automations.yaml` with a schedule (`when`) or an
  event (`on`, with a `condition`), and actions (`tool`, `ask`, `say`, each optionally
  `only_if`). They run through the dispatcher like any tool call; while a rule runs, an
  `AutomationUI` answers confirmations - yes only for the tools approved for that rule
  (`allow`), so nothing that normally asks first runs unapproved. Rules run only in the
  primary core; an automation started by another one stops at depth 3.

- *Your PC and tech:* `apps` (Start-menu programs via `os.startfile`, windows via the Win32
  API through ctypes - no extra packages; the first start of each program asks, the yes is
  remembered; files are opened only if they are not programs or scripts), `media` (media
  and volume keys; playerctl/pactl on Linux), `commands` (only your named commands from
  `config/modules/commands.yaml`, argument lists without a shell, `{placeholders}` filled
  as single arguments that can't start with "-", a time limit and the kill switch),
  `office` (python-docx/openpyxl in the `office` extra; cells starting with = + - @ are
  written as text), `dev` (git with no password prompts, Docker, listening ports via
  psutil; changes are CONFIRM tools), `research` (plans searches with the model, reads
  pages through the `web` module and writes a report citing numbered sources).

- *Knowledge:* `docs` (folders you add are split into overlapping parts, embedded with
  memory's embedder via `ctx.embedder` and kept in `data/modules/docs/index.sqlite`; only
  new or changed files are read again, deleted ones dropped; `ask` answers from the best
  parts citing file and page), `notes` (Markdown files in `<Starlight folder>/Notes`,
  indexed by `docs`), `transcripts` (YouTube captions via the `transcripts` extra, with
  [mm:ss] marks, summaries and questions).

**Web chat, conversations and remote use.** Conversations are saved in
`data/conversations/<id>.json` (`core/conversations.py`); `Kernel.ask(...,
conversation=id)` continues one, with a lock per conversation, and the terminal continues
the current one. The web app (`web/app.py`) adds the conversation list, file uploads
(images go to the model as pictures, documents as text), the settings page (permission
rules through `save_rule`, modules, devices) and the memory & skills page. A `Gate` decides
for every request whether it is the local user (a connection from 127.0.0.1/::1, a
loopback Host and matching Origin, and no proxy headers) or a paired device (`web/auth.py`: one-time 6-digit codes, SHA-256 hashed
256-bit tokens in an HttpOnly/Secure/SameSite=Strict cookie, lockout after 5 failures).
Remote use is meant only through `tailscale serve` (`web/remote.py`, `starlight remote`),
which keeps Starlight on 127.0.0.1 (`starlight web` refuses any other `web.host`);
`web.remote.confirm` patterns are added as always-ask rules and `web.remote.blocked` as
never-run rules for the duration of a remote request (`permissions.extra_confirm`,
`extra_blocked`), every response forbids framing (X-Frame-Options, CSP), uploads are
capped while streaming and Office files are checked for unpacked size, and
changing permissions, modules, devices or skills, and releasing the kill switch, are
local-only.

**Home and devices.** `home` talks to Home Assistant's REST API with a long-lived token
from the vault; `free_domains` (lights, switches, fans, media players, scenes) switch
without asking, other domains ask, and `call_service` is a CONFIRM tool; watched entities
publish `home.changed`. `lan` reads the neighbour table (`arp -a`, `/proc/net/arp`, `ip
neigh`), optionally pings only the PC's own private /24 networks, keeps names in
`data/modules/lan/devices.json` and, with `watch`, publishes `lan.new_device`. `backup`
writes `<target>/<name>/<name> <date>.zip` with a SHA-256 manifest inside (a `.partial`
file until complete), keeps `keep` versions, verifies against the manifest and restores
without overwriting unless asked (`restore` is a CONFIRM tool; zip paths are checked).

**Voice.** `Starlight.exe --voice` downloads whisper.cpp's `whisper-server` with a
multilingual model and Piper with a German and an English voice into `tools/`. The `voice`
module starts `whisper-server` on 127.0.0.1 the first time speech is transcribed and keeps
it running, so the model stays loaded; Piper runs once per answer. The web chat records
16 kHz WAV in the browser (no converter needed) and posts it to `/api/transcribe`;
`/api/speak` returns a WAV to play. The terminal's `/talk` records with the `voice` extra
(sounddevice).

**Shared building blocks for modules.** `ctx.deliver` sends a message to every open
channel at once: it calls the current interface's `notify` and publishes a `deliver` event
that every web chat page, the `notify` module (desktop pop-up) and the `whatsapp` module
pick up. `ctx.every`/`ctx.background` run background work only in the primary core (the
one holding `data/core.lock`), pause it while the kill switch is engaged and cancel it on
unload. Event handlers can be `background=True`, so a slow one (a WhatsApp send) never
holds up the event's publisher. `core/safepaths.py` is the one place that decides which of
your paths a module may use, and `core/schedule.py` understands times and repeating
schedules for reminders and automations.

**Faster and smarter (round 2).** Sending all 215 tool schemas cost about 18,600 tokens
per model call; the router sends what fits, typically 5-20 tools and 500-2,000 tokens
(lexical scoring with English/German stop words and a rough stemmer, module `keywords`,
plus the memory embedder's similarity; `agent.tool_routing: false` sends everything).
Module code is kept compiled as hash-checked `.pyc` files, so a start no longer compiles
every module, and an edit is still picked up on hot-reload. `memory/profile.md` holds the
user's profile; memory extraction fills it in, and it is never merged with other facts.
(A draft model for speculative decoding was tried and removed again: the launcher deletes
an old `models/draft` folder, and the `draft_*` server settings older config files name
are ignored.)

**New features (round 2).** `watch` keeps its watches in `data/modules/watch/watches.json`
and checks due ones every minute in the primary core through `ctx.http` (no cache);
prices are read with a currency-aware regex (`1.234,56 €`, `$1,234.56`), and a hit fires
once per crossing, publishing `watch.price` / `watch.changed`. `mail` now names messages
by IMAP UID, selects the inbox read-only and fetches with `BODY.PEEK`, so nothing is
marked read; `summary` and `draft_reply` use the `summary` role's model; `reply` sets
`In-Reply-To`/`References`; with `watch: true` it publishes `mail.new` for unread UIDs not
seen before (per UIDVALIDITY, in `data/modules/mail/seen.json`). Focus mode lives in the
core (`core/focus.py`, `data/focus.json`): `Kernel.deliver` holds non-urgent messages
while it is on, and the heartbeat delivers one digest when it ends. `system` gained
lock/sleep/shutdown/restart through one command runner per platform; a delayed shutdown
registers a kill-switch cleanup that cancels it. `memory.duplicates`/`memory.tidy` merge
near-duplicates found with the index's vectors (never the profile or summaries).

**Fewer questions (safeguards, round 1).** The shipped `config/starlight.yaml` lists the
tools that no longer ask under `permissions.auto_approve` (desktop.*, code.run_python /
write_code, apps.close, system.close_app, network.http_request / ssh_run / tcp_send /
websocket_send, mail.send / reply); the tools' own CONFIRM tags are unchanged, so removing
a line restores the question. `access.*` asks regardless. `network.any_host` lets the
network tools reach unlisted hosts; `web.remote.confirm` defaults to empty (the phone asks
like the PC; `web.remote.blocked` is unchanged); `web.respect_robots_on_fetch: false`
lets `web.fetch` read the page asked for like a browser while `fetch_many` (research),
page watches and news follow robots.txt; a robots.txt answering 401/403 means no rules
(RFC 9309); `apps.ask_first_open: false`; `agent.max_steps` is 15.

**Round 2.** `commands.shell` runs any command line (`powershell -NoProfile -Command` on
Windows, `/bin/sh -c` elsewhere) through the same runner, kill-switch cleanup and output
cap as named commands; it is CONFIRM, needs `shell: true` in commands.yaml, and is in
`web.remote.blocked` (a shell could rewrite Starlight's own config). `output.write_anywhere`
makes `PathGuard` mark new files anywhere but Starlight's own folder as `new_free`:
files.save (new or renamed), pdf.to_pdf and office's new files need no question, while
replacing, appending and reading outside the Starlight folder still ask. `http` allows 10
requests/s with 0.5 s per site (a site's crawl-delay still wins), and page watches may run
every 5 minutes.

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
