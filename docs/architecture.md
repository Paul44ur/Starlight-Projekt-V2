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
starlight/app_window.py  `starlight app`: the web pages in Starlight's own window (WebView2)
starlight/web/      the local web chat (FastAPI + a WebSocket chat page); client.py is a
                    terminal that joins the same backend, so --both shares one brain
launcher/           the source of Starlight.exe (standard library only; the window is launcher_gui.py)
assets/logo/        the logo (SVG, PNG); launcher/starlight.ico and web/static/icon.svg are made from it
bundles/            builds of the web pages' third-party code (editor, graph, mermaid) and
                    the app icons (icons/: PNGs drawn from web/static/icon.svg)
starlight/browser.py, text.py   headless browser and HTML-to-text helpers for modules
starlight/tui.py    the colored terminal chat (rich), with a plain fallback
starlight/cli.py    the `starlight` command
modules/            hello, web, login, network, recon, cve, certs, breach, desktop,
                    system, clipboard, weather, news, finance, filesearch, notify,
                    reminders, mail, pdf, code, files, access, whatsapp, todo,
                    calendar, briefing, calc, translate, automations, apps, media,
                    commands, office, dev, research, docs, notes, transcripts,
                    voice, home, lan, backup, watch, focus, boards (+ _template)
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
- *Security tools (`absolution`, off by default):* a cockpit over security programs the user
  already has (`modules/absolution/tools.py` is a data catalogue: scanners, recon, cloud and
  code checks, capture and memory analysis, reverse-engineering and web-proxy GUIs). Three
  invariants: `absolution.run` always calls `ctx.ui.confirm` with the exact argv (no
  auto-approve path); a network target must be in the module's own scope
  (`absolution.authorize`, warns on public targets, stored in `data/modules/absolution/
  scope.json`, separate from `servers.yaml`); nothing is installed (`shutil.which` only).
  Argv is built as a list (never a shell); the target is validated by kind (host/URL/path/
  image/query) and extra flags must each be one safe token. The offensive credential-theft /
  lateral-movement / C2 / evasion cluster is refused by name (`tools.BLOCKED`), not merely
  omitted. The launcher's `--security-tools` reports which are installed; it fetches none.
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
  primary core; an automation started by another one stops at depth 3. A condition written
  the old way (one `field op value` with spaces around `== != < <= > >= ~`, or a field
  alone) is checked as it always was; one with and, or, not (und, oder, nicht), brackets or
  the words is, has, is empty goes to the notes' query language: `query.parse`, then
  `query.evaluate` on the event's fields or the tool's result (dotted names, numbers as
  numbers, dates as dates, text without case; `==` and `~` are its aliases of is and has).
  A new rule whose condition or `only_if` cannot be read is refused; one already saved just
  never holds. Template `weekly-open-notes` runs `notes.query` on Fridays.

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

**One look.** The web pages take their colours, spacing, corners, type sizes, shadows and
motion from tokens in `style.css` (`--accent`, `--on-accent`, `--danger`, `--s-*`, `--r-*`,
`--fs-*`, `--shadow-*`); pages use these instead of their own numbers. The launcher window's
`Palette` (`launcher_gui.py`) holds the same colours. A test (`tests/test_launcher_look.py`)
keeps the two in step and checks that text on every filled colour is readable (WCAG AA,
4.5:1). Both show a focus ring only while the keyboard is used. The plan for the rest of this
work is `docs/plan-ui.md`.

**The chat page's layout** (after the Claude app's, in Starlight's look): the sidebar holds
the actions, the pages (`.side-nav`, the same links the phone's drawer shows) and the
conversations; the bar shows the open one's name (`showTitle`, its menu is the list's
`conversationMenu`); on a wide screen the menu button hides the sidebar (`body.no-sidebar`,
kept in localStorage). The context meter comes from `answer_stats`: `context_tokens` is what
the model read plus wrote in the answer's last step, out of `/api/status`'s `context_window`.
`_run_ask` keeps that number with the conversation (`ConversationStore.set_context`, in memory only
for a private one), `get()` returns it and the page shows it when the conversation is opened.
A branch and an old conversation have none until their next answer.

**The launcher window's error page.** `error_hints()` (starlight_launcher.py) matches the
words of a problem's message and details against `ERROR_HINTS` (video memory, disk space,
network, a busy port, blocked files, launcher.json, a slow model server, damaged downloads,
LM Studio) and returns at most three things to try; the page shows them in a card, folds the
technical details away (open at once when there is no advice), and has a Settings button.
`model_label()` gives the model's name from `MODEL_INFO`. Enter presses the page's main
button (`_main_button`), or the focused one; Esc cancels Settings.

**Starlight's own window.** `starlight app` (`app_window.py`) shows the web chat in a
pywebview window: WebView2 on Windows (the `app` extra, part of `all`; Windows only).
`why_unavailable()` checks for pywebview, .NET and WebView2 first (pywebview would otherwise
fall back to the old Internet Explorer engine), and the command ends with exit code 3, which
the launcher answers by opening the browser. The window keeps its data in `data/app-window`
(`private_mode=False`, so the pages' localStorage stays), allows downloads (a save dialog),
sends other sites to the browser, and starts in debug mode for the right-click menu, Ctrl+F
and F5 (the developer tools stay closed). A page of the same Starlight that would open in a new
tab goes through `window.pywebview.api.open_window` (common.js; the `Bridge` checks the
origin) to a new Starlight window; `file://` addresses are off. The launcher's `ChatWindow`
starts it in the process group once the chat answers (else it would start a chat of its own on
the same port), brings an open one to the front (its pid is in `data/app-window/window.pid`;
a window removes only its own), and opens the browser if it ends within 15 seconds with an
error (the reason is the last line it wrote to `logs/app.log`). After that, Open chat goes
straight to the browser. "Save and restart" ends the window with the rest; it opens again when
the chat is ready. (The chat page's own `open()` was renamed
`openConversation()`: a global function named `open` replaced `window.open`, so printing a
conversation opened nothing. A test checks that no page script declares a top-level function
or `var` with a name the window already has; `scroll`, `status` and `when` were renamed too.)

**The terminal chat's look.** `starlight/termstyle.py` holds the web's colours as ANSI codes,
at the depth the terminal has (rich's `color_system`: 24-bit, 256 or the basic 16), dark or
light (`STARLIGHT_TERMINAL_THEME`, `COLORFGBG`); NO_COLOR keeps bold and drops colour.
`MarkdownStream` renders an answer a piece at a time: line starts are held until their kind
is clear (a heading, a list item, a fence, a rule), words until their end (to wrap between
words), tables until their last row; the result is the same however the text is split.
`ChatView` (`tui.py`) uses it for answers, shows a spinner until the first word (it steps
aside for questions and notices, and names the running tool from the dispatcher's report),
and a status line and prompt with the modes. Pipes keep the plain output.

**Questions.** A page never uses the browser's own boxes (`confirm`, `prompt`, `alert`):
they look different on every system and name the address ("127.0.0.1:8700 says"). It asks
with `askYesNo(text, {yes, no, danger})` and `askText(label, value, {yes})` from `common.js`,
which show a small window in the page's look and resolve to the answer (`true`/`false`, or
the text and `null` when cancelled). A text with a blank line in it shows as the question
and its details. Esc and a click beside the window cancel. A dangerous answer (`danger`) is
red, and Enter does not take it: the keyboard starts on Cancel. Keys pressed in a question
stay in it, so the chat's Esc (stop the answer) is not set off. `tests/test_questions.py`
checks that no page calls the browser's boxes; page tests answer a question with `answer`
or answer all of them with `YES` (both in `tests/test_chat_page.py`).

**Icons.** Buttons without words show line icons from `ICONS` in `common.js` (24 px SVG
paths in the text colour): markup says `data-icon="mic"` and `drawIcons()` fills it in;
code calls `setIcon(button, name)`, which marks an icon `.alone` (drawn a little larger)
when its button has no words. Icons never take a click themselves: it is the button's. Each
page's head has a light and a dark `theme-color` meta, right for the system's theme from
the first paint; `paintBar()` copies `--panel` into them whenever the theme changes (a
meta marked `data-fixed`, on the Kill page, keeps its colour). The app icons a phone puts on its home screen are PNGs in
`static/icons`, drawn from `icon.svg` and `kill-icon.svg` by `bundles/icons/make_icons.py`
and served from `/icons/{name}` (only the names in `APP_ICONS`); `icons.sha256` records
the logos they came from, and a test fails when a logo changes without them.

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
Block B2 reads the notes. `notebook/markdown.py` is pure functions: `split` (the properties
block, read without YAML aliases, dates as ISO text), `parse` (headings, `[[links]]` with
`|`, `#Heading`, `#^id` and embeds, Markdown links to local files, tags, block ids, tasks
with plain-word fields in English or German, fenced code and `query` blocks), `section`
and `block` (parts for embeds), and `set_property` / `remove_property`, which change only
that property's lines. Code, inline code, addresses and escaped characters never count.
Memory and skills read their properties with `split`. `notebook/links.py` resolves a link:
the name without case, a folder in the link narrows it, the linking note's folder first,
then the shortest path; aliases when no name matches. `notebook/index.py` keeps
`data/notebook/index.sqlite` (a cache): files, links, tags, properties, tasks and FTS5
(or a plain scan when this SQLite has no FTS5; a change of mode rebuilds it, and so does a
broken file). `refresh` walks the Starlight folder (not `code/` or hidden folders) at most
every 5 s and reads only changed files; at most 20,000 files and 1 MB per note.
`History.listeners` hear about every write, restore and deletion (`write_note`,
`append_note`, `files.save` too), so `Kernel.notebook_index` (made on first use) updates a
note at once with `touch`.
Block B3 adds the Notes page's API (`web/notes_api.py`), all PC only (`local_only`):
`GET /api/notes` (folders and notes, pinned ones marked), `/api/notes/search?q=`,
`/api/notes/trash` and `POST /api/notes/trash/restore`, `POST /api/notes/pins` (kept in
`data/notebook/pinned.json`), `POST /api/notes/pictures?name=` (PNG, JPEG, GIF or WebP by
their first bytes, 10 MB, into `Notes/Attachments`), `GET /api/notes/file/{path}` (pictures
with `nosniff` and a strict CSP; an SVG only as a download), and for one note
`GET|PUT|DELETE /api/note?path=`, `POST /api/note` (new), `/api/note/rename`,
`/api/note/versions`, `/api/note/diff` and `POST /api/note/restore`. Paths are relative to
the Starlight folder and checked by `relative_path`: no `..`, full paths, hidden or
Windows-reserved names, no `code/`, and nothing whose real path (after links) is outside.
A save sends the `stamp` it read; a change made meanwhile gives 409 with both texts. Saves
keep at most one version per 5 minutes; rename and delete keep one first. Undo from the
chat stays `POST /api/changes/{id}/undo` (A3).
Block B4 draws notes syntax in rendered Markdown: `markdown.js` has `mdHooks` (wikilink,
text, quote, item) that `static/render.js` fills in. `[[Note]]`, `[[Note|text]]` and
`[[Note#Heading]]` link to `/notes?open=…`; `![[photo.png]]` shows a picture from
`/api/notes/file/` (a bare name is in `Notes/Attachments`; no `..`, full paths or
addresses), `![[Note]]` is a link chip until C1 embeds notes; `#tags` are pills linking to
`/notes?tag=…`; `> [!tip] Title` is a callout (note, tip, warning, quote, todo; `-` folded,
`+` open); `- [ ]` items get read-only boxes. All of it stays `textContent`. Without
render.js the syntax stays plain text. Styles are in `style.css` under `.md`.
Block B5 is the Notes page, `/notes` (`static/notes.html`, PC only; the other pages link
to it, and `common.js` hides the link on a phone). Three areas: the sidebar (search,
Today, Inbox, Tasks and Graph, pinned notes, the folder tree), the reading column (title,
the note's own tags and properties as chips, the body drawn by `markdown.js` and
`render.js`), and the info panel (outline, backlinks, properties; it folds away under
1100 px). The URL says what is shown: `?path=`, `?open=<name>&heading=` (resolved by
`GET /api/notes/resolve`), `?tag=` (`GET /api/notes?tag=`), `?q=` or `?view=`; links in a
note stay on the page and Back works. Ctrl+K is a quick switcher (letters in order, word
starts count more, aliases too). `POST /api/notes/welcome` writes the welcome note
(German or English, as the page speaks) once ever; `data/notebook/welcomed` remembers.
Note text is marked `translate="no"`, so i18n never changes it.
Block B6 adds editing. `static/notes-editor.js` (an ES module, imported only on the first
Edit, so reading never loads the 625 KB bundle) builds the editor from `/vendor/editor.js`:
Markdown with styled headings, bold, links and dimmed marks, a parser extension that shows
the properties block as one dimmed block, `[[links]]` and `#tags` marked, a "/" menu
(autocompletion from after the slash, in the page's language), Ctrl+B, Ctrl+I, Ctrl+Enter
(tick a task), and paste or drop of pictures. `notes.html` saves one second after typing
stops with `PUT /api/note` and the stamp; the first save of each editing session sends
`first` (the server then keeps a version even inside the 5-minute gap, so where each
session started is kept). A 409 opens the conflict dialog (keep mine, take theirs, keep
both). There are new, rename (the title field), delete (to the trash), a Trash view with
Bring back, a history dialog (versions, a coloured diff, put back), Ctrl+E, Ctrl+S, Ctrl+N,
and a last save with `keepalive` when the page closes.
Block C1 connects notes. `notebook/rename.py`: `plan(index, old, new)` finds every
[[link]] by the old name that resolves to the note (`markdown.wikilink_spans` gives their
places outside code; `links.retarget` keeps a folder, `#part` and `|shown text`; links by an
alias stay, the alias goes along) and returns the new texts; `rename` moves the file and
writes each changed note through `write_note` (a version each). The notes module gets
`notes.rename` (asks first when other notes change), `notes.links`, `notes.backlinks` (with
the linking line) and `notes.read` with links; `notes._find` also finds a note by an alias.
Modules reach the index through `ctx.notebook` (`Services.notebook`). The API's rename takes
`fix_links`, and `GET /api/notes/embed?name=&source=&heading=` returns a note, a section or
a `^block`. The page marks links to missing notes (click: "Write it"), shows embeds (at most
three deep, a note shown higher up is not shown again), backlink cards with the line,
previews on hover, `[[` suggestions in the editor (names and aliases), and asks before a
rename changes other notes.
Block C2 sorts notes. `markdown.add_tag` / `remove_tag` change only the `tags` property (a
`#tag` written in the text stays); `Index.tag_tree` counts each tag and every level above it
(distinct notes), `Index.property_names` lists the names in use. Notes tools: `notes.tags`,
`notes.by_tag` (nested), `notes.tag`, `notes.untag`, `notes.properties`, `notes.set_property`
(numbers, true/false, dates and lists written as such; an empty value removes it).
`memory.recall` takes a `tag` (nested too; no query: the newest with that tag) and returns
each memory's tags. API: `GET /api/notes/facets` (tags and property names) and
`POST /api/note/property` (one property, with the stamp; null removes it). The page lists
the tags in the sidebar, makes the properties panel editable in reading mode (typed inputs,
add, remove), and the editor suggests tags after `#` and property names in the properties
block.
Block C3 lets the assistant use the structure. `Memory.enrich` adds what the recalled
memories link to (`Memory.linked`): another memory (by id or topic) or a note (through
`Memory.notebook`, the kernel's index; `[[Note#Heading]]` brings that section). One step
only, at most `LINKED_MAX` (5) items, `LINKED_ONE` (600) characters each and `LINKED_CHARS`
(2000) in all; a note comes once. `notes.read` returns the path, properties, links and
backlinks, and `notes._find` takes a path in the Starlight folder ("Journal/2026-11-01"),
so notes outside Notes can be read; a rename stays in the note's folder. The Notes page's
"✨ Ask" menu opens `/?ask=<request>`: the chat puts the request in the box of a new
conversation and sends nothing until Send.
Block C4 adds templates. `notebook/templates.py`: the shipped Meeting, Daily and Project
(German or English, by the page's language or the PC's locale) are copied to
`Templates/` the first time; `render` fills `{{title}}`, `{{date}}`, `{{time}}`,
`{{date:FORMAT}}` (YYYY, MMMM, dddd ... in the template's language), `{{yesterday}}`, and
`{{events}}`, `{{todo}}`, `{{weather}}` through `calendar.agenda`, `todo.due` and
`weather.now` (only those; empty when a module is off or fails). Tools: `notes.templates`,
`notes.from_template`, `journal.today` (a day's page from Daily.md; module variables only
for today), `journal.month` and `journal.year` (reviews in `Journal/Monthly` and
`Journal/Yearly`). A new journal day starts from Daily.md. Automation templates
`monthly-review` and `weekly-plan`. API: `GET /api/notes/templates`, `POST /api/note` with
`template`, `POST /api/notes/today`. The page: ＋ opens a menu (empty or a template), Today
writes the day's page, and journal days show a two-week calendar strip.
Block D1 is capture. The dispatcher also raises the injection guard when a result says
`untrusted: true` (`Dispatcher._outside`), and `notes.clip` is in `untrusted_sources`.
`notes.clip(url)` fetches through `ctx.http` (polite, robots.txt), keeps the title, a model
summary (told never to follow the page's instructions), the text (20,000 characters) and
`source`, `date`, `tags: [clipped]` in `Clippings/`, and returns `untrusted: true`;
`notes.read` of any note with a `source:` property returns `untrusted: true` too.
`notes.highlight` appends a `> [!quote]` callout. `notes.note` without a title appends to
`Inbox/Inbox.md`; `notes.inbox` lists its items and `notes.sort_inbox` asks the model for a
target note per item, asks the user, then appends each item there and removes it from the
inbox (versions kept). The Inbox view has a "Sort my inbox" button (it opens the chat with
the request).
Block D2 is voice memos. `voice.memo(seconds, to)` records at the PC's microphone (two
minutes at most), transcribes, and keeps only the text through `notes.note` (the inbox) or
`journal.write`; the page's microphone button records in the browser (`startRecording` and `wav`, moved from
chat.html to `common.js`), sends the WAV to `/api/transcribe`, then the text to
`POST /api/notes/memo` (the inbox, or the open journal day when not editing it).
Block D3 makes tasks in notes count as to-dos. `notebook/markdown.py` keeps where each task
field is (`Task.spans`), reads dates of more than one word ("next friday", "in 3 Tagen")
and has `natural_date` and `iso_dates`. `iso_dates` writes typed dates as ISO dates; it
runs when `notes.note`, `notes.append`, `journal.write` and the page's `PUT /api/note` save
(the page leaves lines that were already there alone and gets the saved text back, which
the editor shows). `notebook/tasks.py`: `note_tasks(index)` lists every task (not
Templates, not notes with a `source:`), with an id from the note and its text ("n" and six
characters; the same text twice is told apart by its order); `tick` (by line, refused when
the line changed) and `tick_by_id` tick or open a task again, keep a version, keep the
file's line endings and its BOM, write `done:`, and add the next one of a repeating task
below. The to-do module lists them (`todo.list`, `todo.due`, marked with `note` and `path`),
ticks them (`todo.done`, also by a word, and `todo.reopen`), and sends `todo.change` and
`todo.remove` to the note; `note_tasks: false` leaves them out, and so does a Starlight
folder that cannot be found (a wrong `output.folder`: the list's own tasks still work). The briefing, reminders and
`{{todo}}` get them through `todo.due`.
Block D4 puts tasks on the page. `GET /api/note` lists the note's tasks with their lines as
written (`tasks: [{line, status, text, raw}]`); `POST /api/note/task` ticks one by
`{path, line, expected}` (the reading view) or `{path, id}` (the Tasks view), with `done`
true or false, and answers 409 when the task changed. `GET /api/notes/tasks` gives the open
tasks grouped `overdue`, `today`, `upcoming`, `none`, and the `date`. The page's save also
runs `tasks.with_next_ones`: a repeating task ticked in the editor gets its next one.
render.js shows a task's fields as chips (`taskFields`, `taskChip`; due red when overdue,
amber today). On the Notes page the reading view's boxes are matched to the note's tasks in
order (not those in quotes or callouts; when the count or the ticks differ, none is
enabled); the editor's Ctrl+Enter writes or removes `done:` itself; the Tasks view lists
the groups with a box, the chips and the note.
Block E1 is the search. `notebook/search.py`: `parse` reads words, `"phrases"`, `-word`,
`-"words"`, `tag:`/`schlagwort:`, `path:`/`pfad:` (and `-tag:`, `-path:`); `fts_query` turns
it into an FTS5 query with every word quoted (the last as a prefix, `NOT` for left-out ones);
`search(index, text)` lets FTS5 find and rank (bm25, title five times) or, without FTS5 or
with only `tag:`/`path:`, scans (`Index.scan_paths`, then the exact test in Python). Results
carry a snippet (Markdown marks left out) and `marks`/`title_marks` (where the words are),
found on text folded letter for letter (`fold`: no case, no accents, the same length).
`Index.search`, `GET /api/notes/search` and `notes.find` use it; `notes.find` returns the
matching lines, searches the whole Starlight folder, and says `untrusted` when a clipping is
among them. The page marks the hits and shows the search tips.
Block E2 finds what belongs together. `notebook/related.py`: `by_structure` (links either
way count 3, each shared tag 2, with the reasons), `by_meaning` (turns `docs.similar`'s
answer into notes of the notebook), `mentions` (lines of other notes that name the note or
an alias, three letters at least, as whole words outside code, links and web addresses;
not in its properties, not in notes that already link to it, not in clippings) and
`link_mention` (`[[Name]]` or `[[Name|as written]]` on that one line, through
`read_for_change` and `write_note`; refused when the line changed; the link is the note's
name, or its path when the name would lead elsewhere). The docs module has `docs.similar`
(the mean of a file's part vectors against all parts, best part per file) and OCR
(`modules/docs/ocr.py`: Tesseract as a program, found on PATH, in its Windows folder or
`ocr_command`; pictures up to `ocr_max_mb`, PDF pages with almost no text up to
`ocr_max_pages`, `ocr_seconds` each). Tools `notes.related`, `notes.mentions`; API
`GET /api/note/related`, `GET /api/note/mentions`, `POST /api/note/link-mention`; the page
shows both lists in the right panel.
Block E3 is the query language. `notebook/query.py`: `parse` reads a query into a tree
(`Cond`, `Not`, `AllOf`, `AnyOf`; and binds closer than or; values are text, numbers, dates
with `today±N`, or yes/no; German keywords too; at most 2,000 characters and 30 brackets
deep) with `QueryError` messages in plain words; `to_sql` writes fixed SQL pieces only, every
field name and value a parameter: a property through `json_each` on the index's JSON values
(`is` compares text without case, numbers as numbers, dates by day; `has` is "contains" for
text and "holds" for lists; `is not` and `is empty` match notes without it), and `tag`,
`folder`, `path`, `name`, `modified`, `words` on their own columns. `order_by` sorts with
missing values last. `run` executes through `Index.select`, which stops after a few seconds
(an SQLite progress handler), and returns `{fields, notes, count}`. Tool `notes.query(where,
sort, fields, limit)` (`untrusted` when a clipping is in the table).
Block E4 shows query blocks as tables. `query.parse_block` reads a block's lines (`where:`,
`sort:`, `fields:`, `limit:`; German `wo:`, `sortieren:`, `felder:`, `anzahl:`; lines
without a key belong to the condition; `#` lines are comments). `POST /api/notes/query
{text}` runs it (400 with the reason). markdown.js got a `code` hook; render.js's
`queryTable` answers it for ```` ```query ```` only where `window.starlightQueryTables` is set
(the Notes page; the chat keeps the block as code, as phones cannot reach the notes API):
names link to the notes, a click on a header sorts in the page (empty cells last either
way), the round-arrow button asks again, and mistakes show in a `.query-error` box.
Block F1 draws the links. `notebook/graph.py`: `graph(index, around, depth, limit)` makes
points (notes) and lines (a link once between two notes, either way; not to pictures,
missing notes or itself) from `Index.link_pairs` and the resolver; `around` keeps the notes
up to `depth` links away (at most 3), and at most `limit` points stay (nearest, then most
links). `GET /api/notes/graph`. The layout is d3-force, shipped as
`static/vendor/graph.js` (15 KB, ISC; built in `bundles/graph/` like the editor, with
`graph.sha256` and `graph-LICENSES.txt`; served by `/vendor/`). The Notes page loads it on
first use and draws the note and its neighbours as SVG in the right panel; a point opens
its note (click or Enter).
Block F2 is the whole graph. `graph.graph` also takes `folder=` (that folder and below),
`tag=` (with the tags below it) and `memories=[(id, topic, text)]` (points "memory:<id>"
in the "Memories" folder, linked by their [[links]] to notes and to other memories); the
endpoint passes `kernel.memory.store` when `memories=true`. The page's Graph view
(`showGraph`, `drawWhole` in notes.html) lays out up to 1,500 points with d3-force, draws
SVG up to 300 points (points reachable with Tab, Enter opens) and a canvas beyond; one set
of pointer handlers pans, drags points (hit test by distance) and opens a point on a click
without moving; the wheel and + − zoom around the pointer or the centre; names show from
a zoom of 1.3 (or always for 25 points or fewer). Colours by top folder, size by links.
Block F3 is boards. `notebook/boards.py` reads and writes `.canvas` files (JSON Canvas 1.0,
as Obsidian writes them, with tabs): `load` and `check` refuse what is not a board in plain
words (ids present and unique, a type, whole-number place and size, arrows between cards
that exist) and keep every field they do not know, on the board, its cards and its arrows;
`dump` writes it back; `add_card` places a card to the right of the others or under a card
(with an arrow), with a new 16-hex id; `summary` tells the assistant the cards (and the
group each is in) and the arrows in words; `find` lists the boards in the Starlight folder.
The new `boards` module has `boards.list`, `boards.read` and `boards.add_card` (a note card
by the note's name; a new board in `Boards/` when there is none); writes go through
`write_note`, so versions are kept. API: `GET /api/boards`, `GET/PUT /api/board?path=`
(with stamps: 409 on a conflict; 422 when it is not a board), `POST /api/board`; paths must
end in `.canvas` and stay in the Starlight folder (`relative_path`).
Block F4 shows boards on the page. `static/board.js` (an ES module served at `/board.js`,
loaded on first use) draws a board as HTML cards in a transformed "world" layer with an SVG
layer for the arrows: text cards through markdown.js (text only), note cards with the
note's start from `/api/note`, pictures from `/api/notes/file/`, link cards as links only
for http(s) (anything else stays text), groups behind the rest. One set of pointer
handlers moves cards (a group with the cards inside it; a 20-point grid unless Alt is
held), resizes them, draws arrows from a side dot to the card under the pointer (the target
side is the nearest), and pans; the wheel zooms around the pointer; double-click writes in
a text card or makes one; Delete removes the selection. Changes are saved 0.7 s later with
`PUT /api/board` and the stamp; a 409 keeps the board unsaved and offers "Load it again".
notes.html: a Boards view (`showBoards`), `?board=` opens one (`openBoardView`), and
leaving a board saves its last change first (`stopEditing`).
Block G1 exports a note. `starlight/web/export.py` now holds both pages that work on their
own: the conversation export (moved from app.py) and `note_html` (the note's Markdown drawn
by markdown.js and render.js inside the file; links between notes and tags become plain
text; pictures come from `notebook/export.pictures`, read from the Starlight folder as
`data:` addresses, PNG, JPEG, GIF and WebP only, 10 MB each and 25 MB together; a first
heading that only repeats the title is left out). `notebook/export.to_pdf` makes a PDF with
reportlab: headings, paragraphs (`inline` turns bold, italic, code and struck text into
reportlab's markup, everything else escaped), lists and tasks, quotes and callouts, tables,
code blocks, rules and pictures; the font is Segoe UI or Arial on Windows, DejaVu or
reportlab's own Vera elsewhere, so umlauts show. `GET /api/note/export?path=&format=html|pdf
&show=` (show: opened inline, printing itself); the Notes page's export menu; the tool
`notes.export(title, format)` saves into `Exports/` (never over an earlier file).
Block G2 is the task board. A task's `status:` field (`doing`, or `läuft`, `aktiv`, `in
Arbeit`, `in progress` ...; `open`) is `Task.stage`; the index's tasks table got a `stage`
column (schema step 2, which also makes every note be read again once). `tasks.move(file,
path, id, to)` puts a task in open (no status, not ticked), doing (`status: doing`, not
ticked) or done (ticked with the day, no status; a repeating one gets its next one), only
that line, a version first, refused when its text changed. The to-do module keeps `stage`
on its own items and has `todo.move(id, to)` for both kinds. `GET /api/notes/kanban` gives
the columns (note tasks from the index, the list's own through `todo.list`; done: the last
30), `POST /api/notes/kanban/move` moves a card (with `path`: in the note; else
`todo.move`). The Tasks view has List and Columns (remembered in the browser); cards move by
HTML drag and drop or their buttons; render.js shows `status: doing` as a ▶ chip.
Block G3 is flashcards. `markdown.parse` finds `Question::Answer` lines (`Note.cards`; `:::`
asks both ways; not in code, tables, headings or tasks, and `::` inside code, a link or an
address does not count). `notebook/flashcards.py` reads the cards of the notes tagged
`#flashcards`, `#lernkarten` or `#karteikarten` (not clipped notes with `source`, not
Templates); a subtag names the deck, else the note's title. The index stores no cards: the
tagged notes are read when asked. A card's id comes from its deck and question. When each
card comes back is in `data/notebook/flashcards.json` (next to the index), by deck and
question, under a lock: a small SM-2 by days (again: today, the gap starts over; new cards
1, 2 or 4 days; then 1.2 times, the ease (2.5 at first) or 1.3 times the ease; the ease
drops after again and hard, never below 1.3; at most ten years). `plan` gives the due cards
(longest due first) and as many new ones as `new_per_day` still allows. `rename.rename`
calls `flashcards.renamed`, so a deck named after a note follows a rename. The new
`flashcards` module has `flashcards.decks`, `.due` (with the answers, and the gap after each
grade), `.grade` (English, German or 1 to 4) and `.add` (a line in the deck's note, or a new
note in `Notes/Flashcards`; versions kept). The API goes through the module, so its settings
count: `GET /api/notes/flashcards`, `GET /api/notes/flashcards/due?deck=`, `POST
/api/notes/flashcards/grade` (503 when the module is off). The Notes page has a Flashcards
view (decks, then one card at a time; Space and 1 to 4). The automation template
`daily-flashcards` says at 19:00 when cards are waiting.
Block G4 is live preview. `notes-editor.js` has a ViewPlugin (in a Compartment, so
`editor.preview(on)` switches it) that walks the syntax tree of the visible lines and hides,
with replace decorations, the marks on every line the cursor or the selection is not on:
ATX `HeaderMark` and its space, `EmphasisMark`, `StrikethroughMark`, inline `CodeMark`, a
link's first `LinkMark` and `](address)`; a regular expression does `[[`, `Note|` and `]]`.
A bullet's `ListMark` becomes a • widget; a task's `ListMark` hides and its `TaskMarker`
becomes a checkbox widget, whose mousedown ticks that line with `toggleTask(view, [line])`
(the selection stays). A picture embed (`![[photo.png|300]]`) becomes an image widget whose
address comes from the page (`options.pictureSrc`, render.js's `notePictureSrc`: only from
inside the Starlight folder); nothing changes it once drawn (the editor would read a change
as typing). Fenced code, the properties block, Markdown images and other `![[embeds]]` are
left alone. The document never changes. The toolbar's eye button keeps the choice in `localStorage`
(`starlight.editor.preview`); the bundle needed nothing new.
Block G6 is deep links. `starlight://note/<path>` (or `/<name>`, `#heading`),
`starlight://notes` and `starlight://chat` only ever open a page of the web chat.
`launcher.link_target` turns a link into that page (`/notes?path=` for a `.md` path, else
`/notes?open=`); `..`, empty parts, drive letters, backslashes, control characters and any
other kind are refused. `Starlight.exe --open <link>` opens the page in the browser when
port 8700 answers (it waits up to 20 s for `/api/status`), else it starts as usual with
`Settings.first_page`: the text start and the window open that page instead of the chat
(the window's Open chat button still opens the chat). `ensure_links` (from `setup`, only
when frozen, so script runs and tests never touch the registry) calls `register_links`:
`HKCU\Software\Classes\starlight` with `URL Protocol` and `shell\open\command` =
`"<exe>" --open "%1"`, written only where it differs; `"links": false` in launcher.json
deletes the keys. `notebook.links.deep_link(path, heading)` makes a link; `notes.link(title)`
gives one to the assistant. `markdown.js` reads `starlight://` links (in `[text](…)` and
bare) with the same rules and makes them note links on this site (same tab; the Notes page
opens them in place). The Notes page's link button copies a note's link.
Block G5 is Mermaid diagrams. `bundles/mermaid` builds `static/vendor/mermaid.js` (Mermaid
12.2.0 and what it uses, about 3.7 MB, `mermaid.sha256`, `mermaid-LICENSES.txt`) with esbuild,
like the other bundles; a plugin puts `elk-stub.js` in place of elkjs (1.5 MB, EPL-2.0), and
the page sets `layout: "dagre"` (also for state diagrams), since Mermaid 12 would use ELK.
`/vendor/` serves the two files. render.js's `mdHooks.code` turns a ```mermaid block into a
diagram where `window.starlightDiagrams` is set (only notes.html): the library is imported on
first use, the blocks are drawn one after another, with `securityLevel: "strict"`,
`htmlLabels: false`, `suppressErrorRendering`, at most 50,000 characters, and the page's
colours (dark when the page's background is). Mermaid's SVG is parsed with DOMParser, given
its size from the viewBox and shown as an `<img>` with a data URI, so nothing in it can run,
react to clicks or load anything. "Show the code" (a details element) shows the source; an
error shows Mermaid's reason and opens the code. Chat answers and exported notes keep the
block as code.
A code review of the notes app (2026-11-03) brought these fixes. `write_note` keeps an
existing file's line endings and byte order mark unless `newline` says otherwise (the
page's saves and property changes, tags, renames and the inbox no longer turn every line
into CRLF on Windows). `rename.plan` records each note's stamp and `rename.rename` refuses
before renaming anything when one changed meanwhile, then writes with those stamps;
`History.moved(old, new)` moves the kept versions to the new name. `undo_change` refuses to
remove a new file while versions are switched off (the trash would keep nothing).
`append_note` calls a file new only when it did not exist (an empty one was there). The
inbox is sorted from what was read, with its stamp: unclear targets stop it before the
question, a change meanwhile stops it after, and the inbox loses exactly the items that
reached a note (also when a later one fails). Recall leaves out notes with a `source`
property. The task board's move answers a locked note with 409. `notes._find` falls back to
the name when a title with "/" is no path. On the page, a save's changes (ISO dates, a
repeating task's next one) reach the editor also when typing went on: the editor records
the changes since the save started (`mark`) and maps the save's changes over them
(`rebase`, CodeMirror's `ChangeSet.map`).
The Notes page is in parts (2026-11-03): `notes.html` holds only the page, `notes.css` its
look, and six plain scripts its workings, loaded in this order and sharing their top-level
names: `notes-page.js` (state, helpers, the list, the address, one note), `notes-tasks.js`
(Tasks and the task board), `notes-info.js` (the right side, lists and the start page),
`notes-views.js` (boards, flashcards, the whole graph), `notes-find.js` (Ctrl+K, keys, a new
note) and `notes-edit.js` (writing, the journal strip, Ask Starlight, links, memos,
versions, the start). Where this file says "notes.html", it means the page with these
parts. The app serves `/notes.css` and the `/notes-….js` files listed in `NOTES_SCRIPTS`
(also `notes-editor.js`), nothing else of that name.

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
