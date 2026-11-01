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
starlight/notebook/ notes as a notebook (docs/plan-notes-app.md): history and safe writing so far
starlight/skills/   skill drafting, approval, git history
starlight/sandbox/  Docker and process sandboxes for generated code
starlight/viewer/   the statistics viewer (separate, read-only app)
starlight/web/      the local web chat (FastAPI + a WebSocket chat page); client.py is a
                    terminal that joins the same backend, so --both shares one brain
launcher/           the source of Starlight.exe (standard library only; the window is launcher_gui.py)
assets/logo/        the logo (SVG, PNG); launcher/starlight.ico and web/static/icon.svg are made from it
starlight/browser.py, text.py   headless browser and HTML-to-text helpers for modules
starlight/tui.py    the colored terminal chat (rich), with a plain fallback
starlight/cli.py    the `starlight` command
modules/            hello, web, login, network, recon, cve, certs, breach, desktop,
                    system, clipboard, weather, news, finance, filesearch, notify,
                    reminders, mail, pdf, code, files, access, whatsapp, todo,
                    calendar, briefing, calc, translate, automations, apps, media,
                    commands, office, dev, research, docs, notes, transcripts,
                    voice, home, lan, backup, watch, focus (+ _template)
skills/             skills it learns (one folder each)
scripts/            install.ps1, install.sh, e2e_chat.py (the end-to-end chat test CI runs)
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

**Step 6, web.** One shared HTTP client for every module: a cap on requests in any
one-second window (a sliding window, so bursts can never exceed it), a gap between
requests to the same site (both in the `http` section of `config/starlight.yaml`; shipped:
10 per second, 0.5 s), robots.txt and crawl-delay, back-off on 429/503 with Retry-After, and a page cache.
The headless browser counts each of its requests against the same cap. `web.search`
(SearXNG or Brave), `web.fetch`, `web.fetch_many`, `web.links`. The `login` module fills
login forms with credentials from the vault, asks you for 2FA codes, asks before the first
login to each site and saves the session so later visits skip the login.

**Step 7, network.** `config/servers.yaml` lists your servers (SSH logins, ports, health
checks); with `network.any_host: false` it is an allow-list and anything else is refused,
and the shipped config sets `any_host: true`.
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
snippet. Both ask first (the shipped config lists them under `permissions.auto_approve`,
see "Fewer questions" below). The sandbox is a Docker container (no network, read-only, capped
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
the busiest second against the requests-per-second cap), tokens per second, GPU use, VRAM, memory size
and skills, plus Prometheus metrics at `/metrics`. Hardening: end-to-end tests per
module, a test that the kill switch stops every tool of every module, a load test of the
requests-per-second cap, vault values masked in every log line, and `starlight logs scan`.

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
  the vault; reading is free, sending asks first unless `mail.send` is in
  `permissions.auto_approve`, as in the shipped config), `pdf` (read text from PDFs and make simple
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
  API through ctypes - no extra packages; with `ask_first_open: true` the first start of
  each program asks and the yes is remembered, the shipped config sets it to false; files
  are opened only if they are not programs or scripts), `media` (media
  and volume keys; playerctl/pactl on Linux), `commands` (only your named commands from
  `config/modules/commands.yaml`, argument lists without a shell, `{placeholders}` filled
  as single arguments that can't start with "-", a time limit and the kill switch),
  `office` (python-docx/openpyxl in the `office` extra; cells starting with = are
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

**The notebook base (`starlight/notebook/`, block A1 of `docs/plan-notes-app.md`).**
`history.History` keeps the earlier versions of files Starlight changes in
`data/history/history.sqlite` (gzip blobs with path, time, size, reason and a deleted flag;
the same content is not kept twice; `min_gap` keeps one version per few minutes while
typing). It compares versions (unified diff), restores one (keeping the current file as a
version first), and keeps deleted files in a trash. Cleanup after each save: at most
`notebook.versions` per file, none older than `notebook.days`, all together under
`notebook.max_mb` (oldest first; incremental vacuum). This database is the only copy of
those versions, unlike the notebook index planned for block B2, which is a cache.
`write.write_note` / `append_note` are the one safe way to change such a file: refuse when
the file's stamp (mtime and size) differs from the one it was read with (`NoteConflict`),
keep a version, write atomically (`core.fsutil.write_atomic_bytes`; appending keeps the
file's bytes and line endings). `notebook/db.py` opens a SQLite file and applies its
migrations by `PRAGMA user_version`. Block A2 switched the writers over: the kernel makes
one `History` (`Kernel.file_history`, with the live `notebook` settings) and hands it to
modules (`ctx.history`) and to memory (`MemoryStore(root, history)`: every write keeps a
version, `delete` goes to the trash). Notes, journal and recipes write only through
`write_note` / `append_note` and return `changed: {path, title, version, new}` (what an undo
needs); `files.save` keeps a version before replacing or appending (text up to 10 MB).
Tools: `files.versions`, `files.changes`, `files.restore` (version 0: before the last
change; outside the free folders it asks), `notes.trash`, `notes.restore_deleted`,
`memory.versions`, `memory.undo`. `notebook.keep_versions` and `notebook.versions` are
safety limits. Block A3 shows these changes in the chat: the dispatcher's tool report
carries a result's `changed`; the web app keeps it in `RecentChanges` under a random id and
sends the page `{"type": "changed", "id", "title", "path", "new", "undoable"}`. The page
draws a chip under the answer; Undo posts only the id to `POST /api/changes/{id}/undo`
(PC only, once per change, audited as `web.undo`), never a path. `notebook.undo_change`
does the work: a new file goes to the trash, a changed one gets its version back. Changes
made from a phone are shown without Undo and are not kept. The terminal collects the same
reports and prints "Changed: X (undo: /undo)"; `/undo` goes back one change at a time.
Block B1 ships the Notes page's editor: `static/vendor/editor.js` is CodeMirror 6 (state,
view, commands with history, language and highlighting, Markdown with GFM, autocomplete,
search) built into one ES module by `bundles/editor/build.mjs` (esbuild, pinned versions,
`npm ci` from the lock). The build also writes `editor.sha256` and `LICENSES.txt` (every
bundled package, all MIT). `/vendor/{name}` serves only the files in `VENDOR`.
`.gitattributes` keeps git from changing the file's bytes; a test checks the hash.

**Safety limits you set (`core/safety.py`).** Settings -> Safety limits chooses a level
(strict, balanced, relaxed) and any of 29 switches on top, kept in
`config/starlight.safety.yaml`. Each switch names the real setting it changes, in
`starlight.yaml` (`permissions.guard_after_untrusted`, `output.protect_starlight_folder`,
`web.remote.require_pairing` ...) or in a module's settings (`desktop.failsafe`,
`code.sandbox_protects_folders`, `access.ask_before_changes` ...); a value is said as the
protection (True: on), with `invert` where the setting means the opposite (`allow_network`).
`load_config` applies the choice after the remote overlay and before `starlight.local.yaml`
(`safety.core_layer`, replacing values rather than merging, so a Strict limits table is
the whole table), and `load_module_config` does the same for module files
(`safety.module_layer`). Balanced writes nothing. A change on the page saves the file and
then `safety.refresh` copies the freshly loaded values into the running config objects (the
kernel's and the web app's, which the gate and the phone rules read) and into each running
module's settings object, so most switches apply at once; the permission rules made tool by
tool (`permissions.chat.yaml`) are applied again afterwards. Protections that used to be
fixed in code now read these settings when they act: the path guard (`safepaths.use_rules`,
fed the kernel's `output` settings while it runs; all on without one), the process
sandbox (`protect_folders`, `block_programs`, set by the code module before each run and
passed to `guard.py`), the gate (`require_pairing`, `allowed_origins`, with CORS answers and
`frame-ancestors` for those pages only) and `Devices` (pairing minutes, wrong codes).
Always on, by design: the kill switch, secret masking, the audit log, and that the safety
limits change only at the PC (`POST /api/safety` is local only; a phone may read them).

**Round 2.** `commands.shell` runs any command line (`powershell -NoProfile -Command` on
Windows, `/bin/sh -c` elsewhere) through the same runner, kill-switch cleanup and output
cap as named commands; it is CONFIRM, needs `shell: true` in commands.yaml, and is in
`web.remote.blocked` (a shell could rewrite Starlight's own config). `output.write_anywhere`
makes `PathGuard` mark new files anywhere but Starlight's own folder as `new_free`:
files.save (new or renamed), pdf.to_pdf and office's new files need no question, while
replacing, appending and reading outside the Starlight folder still ask. `http` allows 10
requests/s with 0.5 s per site (a site's crawl-delay still wins), and page watches may run
every 5 minutes.

**Stability.** Every save goes through `starlight/core/fsutil.py` (`write_atomic`,
`replace_file`): a temporary file next to the target, then one replace that waits up to
about two seconds while Windows reports the target as busy (antivirus, OneDrive), so a turn
or a list change is not lost to a passing lock. List files modules keep by hand-editable
JSON go through `core/jsonfile.py`, which also reads a BOM and moves an unreadable file
aside instead of overwriting it. The launcher's `ModelServerWatchdog` starts llama-server
again when it exits by itself (4 restarts per 10 minutes at most). `OpenAICompatProvider`
stops reading after `idle_timeout` (90 s) without a new piece once the answer has begun,
returns what came (`finish_reason: stalled`, never a half-written tool call) and the agent
adds a note. `starlight doctor` (`starlight/doctor.py`) checks the model and server, settings,
folders, disk, password store, modules and moved-aside files; it changes nothing.

**Growth.** What would otherwise grow for weeks is trimmed. `Kernel._housekeeping` runs at
start and every six hours on the primary core: `Stats.maintain` deletes statistics past
`stats.retention_days` and compacts the file (`VACUUM`, WAL checkpoint); `ResponseCache.purge`
drops cached web pages older than a day and keeps at most 2000; `Memory.prune_summaries`
keeps the newest `memory.summary_keep` (60; 0 = all) conversation summaries - facts and the
profile are never touched. The core keeps at most 20 conversations' histories in memory
(`MAX_CACHED_HISTORIES`; the rest are read from their files), robots/pacing state for 500
sites, and the login throttle forgets expired addresses. The memory index is re-synced when
any file's name, date or size changes (not only count/total), and a damaged vector table is
dropped and rebuilt. A backup removes `.partial` leftovers when it starts, and a cancelled
`backup.run` stops its thread and waits for it before another run may begin. A recent
message that alone fills the context window no longer makes every tool step summarize the
older turns again (`ContextManager.compact`; `shorten` cuts the big message instead).

**Tests beyond examples.** `tests/test_fuzz.py` uses Hypothesis to feed everything that reads
outside text random input (formulas, dates, prices and watch conditions, news feeds, calendar
files, mail headers and bodies, the text a model streams): a parser may answer or raise its
documented `ToolError`, nothing else, and not hang. It found, for example, a price of
`"9" * 400` read as infinity, a watch interval of `0` (a busy loop), and a line break in an
event title that cut the calendar entry short; each is now an `@example`. `STARLIGHT_FUZZ=deep`
runs ten times as many cases. `tests/test_longrun.py` plays 200 mixed operations (questions,
tool calls, Regenerate, stop through the kill switch, a failing model, conversation changes,
facts, restarts) per seed and then checks the files on disk. On Windows CI,
`scripts/e2e_chat.py` drives the real exe with a real (small) model: a chat turn, a tool offered to the model (reported only),
Regenerate and the kill switch.

**Updates and config errors.** `--update` first copies `config/`, `data/` and `memory/` to
`backups/update-<time>/` (newest 3 kept; databases and caches left out), moves the installed
uv and llama.cpp to `tools/previous/` before replacing them (and moves them back if the
download or unpacking fails), and records the download URL in `source.txt`. `--rollback`
swaps the previous and current folders (so a second call goes forward) and, if asked,
copies the settings and data from the newest backup over the current ones after taking a
fresh copy. `core/config.py` turns Pydantic's and YAML's errors into one block per problem:
the file that wins for that setting (the last overlay that writes it), its line (found by
composing the YAML), the setting path, what is wrong with what was written, and a
"did you mean" for a mistyped name. Module settings and manifests use the same
(`describe_errors`).

**Limits found later.** A note stops at 1 MB (`notes.append` says to start another);
`lan` forgets unnamed devices not seen for `forget_unnamed_after_days` (90; 0 = never);
a backup writes each file in 1 MB pieces and looks for a stop between them, with the
checksum taken from the bytes written; and a reply that nests JSON 60 000 deep after
`{"name"` is plain text (it used to raise `RecursionError` in the stream filter), with
a still-unfinished tool call rescanned only when something that can end it arrives.

## Build plan status

| Step | | Status |
| --- | --- | --- |
| 1 | Foundations and project layout | done |
| 2 | Core kernel, logging and kill switch | done (WhatsApp `/kill` comes with Step 10) |
| 3 | Module system | done |
| 4 | Local model on the GPU | done: Ollama and llama.cpp providers, router, summarizing, streaming, benchmark |
| 5 | Memory and the Memory folder | done |
| 6 | Web: search, scraping (rate-limited) and logins | done |
| 7 | Network: ping and interact with servers | done |
| 8 | Desktop: read the screen, emulate input | done |
| 9 | Writing code and learning skills | done |
| 10 | WhatsApp, statistics viewer and hardening | done |

Everything is tested with mock servers, a local SSH server, a real headless browser, a
real sandbox process and fake screens. What needs your PC (GPU speed, Windows UI
Automation, Docker, your accounts, servers and WhatsApp number) is in
[setup-windows.md](setup-windows.md).

## Writing modules

See [writing-modules.md](writing-modules.md):

```bash
uv run starlight modules new my_weather   # copy the template, then edit modules/my_weather/module.py
```

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy && uv run mypy modules
```
