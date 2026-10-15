# Code cleanup sweep, October 2026

A sweep for bad or useless code: dead code, real bugs, duplicated logic, stale comments and
docs, useless tests. How it was done, what was fixed, what changed for users, and what was
left alone on purpose.

## How

1. **Scan.** Twelve scanners each read one area in full (core; web, viewer, cli, tui;
   models, memory, skills, sandbox; launcher; six groups of modules; the first half of the
   tests; docs, config and dependencies), seeded with `vulture` (unused code) and `ruff` with
   extra rules (unused arguments, commented-out code, complexity). 145 candidates came back:
   5 high, 50 medium, 90 low. Scanners had to back each one with evidence from grep or reading.
2. **Verify and fix.** One agent per area re-checked every candidate and tried to refute it
   (the whole repo, incl. dynamic use: `@tool` names, config keys, framework callbacks),
   fixed what held up, and added a regression test for every behaviour change. For most areas the new
   tests were also run against the unfixed code, where the ones for fixes fail.
3. **Check.** Full test suite, `ruff`, `mypy` (Linux, as Windows, modules) and `mypy --strict`
   for the launcher (both platforms).

The second half of the tests (`test_m*` to `test_w*`, `conftest.py`) was **not** scanned: that
scanner stopped on a usage limit. A second pass over it is open.

## Fixed, by weight

### Security and data safety
- **Sandbox** (`starlight/sandbox`): the process sandbox did not block `~/.ssh`, although its
  docs said so. It computed the path from the sandbox's own `HOME`. It now resolves the real
  folder before `HOME` is replaced.
- **Mail**: IMAP and SMTP never verified the server certificate (Python 3.12's stdlib default
  is unverified). They now do, with `verify_certificate: false` as a documented opt-out and an
  error that names it. The password is not sent when verification fails. Both connections
  have a timeout. `mail.configure` also keeps hand-set options.
- **Calendar**: any read error counted as an empty calendar, and the next add or import saved
  over the real file. Only a missing file is empty now; other errors refuse the write.
- **Read-only shell check** (`network.readonly`): `git --output` (writes a file) and programs
  run by path (`/tmp/x/cat`) were treated as read-only. Not any more.
- **Office files**: reading `.docx` / `.xlsx` outside the Starlight folder now asks first, like
  PDFs and `files.read` always did (the documented rule).
- **Damaged state files** (automations, apps, skills log, `seen.json`, `approved.json`): read
  with a BOM tolerated, written atomically, and a corrupt one is moved aside instead of
  crashing or being silently ignored.
- **Skills store**: a skill update could leave stale `example.md`/code behind, and a path like
  `../SKILL.md` inside the code list could overwrite `SKILL.md`.
- **WhatsApp webhook**: a non-ASCII signature or token gave a 500 (now 403); the body is capped
  at 1 MB; a failed send no longer leaves a question waiting 300 s; the dead attempt to switch
  off uvicorn's signal handling is replaced by a working one.
- **systemd unit** (`starlight service`): paths with spaces, `%` or `$` were not quoted.

### Wrong behaviour
- **Core**: a module that failed to load left its event subscriptions and kill-switch cleanups
  behind (duplicate handlers on every retry); a bad `Retry-After` raised a raw `ValueError`;
  the kill switch broke on a locked or vanished STOP file; "timed out after Ns" was reported
  for any `TimeoutError`; a typo in `logging.level` crashed with a traceback (now a config
  error with file and line); a finished Regenerate leaked its set-aside messages and pinned
  the conversation in memory; `gpu_sample_interval` above 30 was ignored and one failing
  heartbeat ended the loop.
- **Launcher**: the window's message loop died when one handler raised; the Windows light-theme
  lookup used a doubled-backslash registry path and could never work; local calls went through
  system proxies; `launcher.json` with a BOM; a pointless second "Continue?" popup; "Try again"
  after a settings error did not re-read the settings; a restart could start a second polling
  chain.
- **Backup**: `verify` crashed on a damaged archive; an unreadable folder was silently left
  out.
- **Docs search**: one bad file aborted the whole reindex; writes ran on the event loop.
- **Dev (git)**, **voice**, **watch**, **weather**, **web**, **clipboard**, **code**,
  **commands**, **calc**, **translate**, **notes**, **media**, **notify**, **recon**, **system**,
  **desktop**, **home**, **filesearch**, **login**: smaller bugs, each with a test; see the git
  history of the sweep.
- **Terminal chat**: Ctrl+C at the prompt hung until Enter was pressed (an abandoned `input()`
  thread holds libc's stdin lock). Found while testing a different fix.
- **Dependencies**: `websockets` was only in the `network` extra, so on a plain `uv sync` the
  web chat's `/ws` and `starlight connect` failed. It is a core dependency now.

### Dead and duplicated code
Removed, each checked repo-wide: `schedule.next_occurrence`, `PoliteHttp.permit`,
`EventBus.unsubscribe`, `ToolRouter.scores`, `ModuleManager.load`, `Services.skills`,
`ContextManager.fit`, `Dispatcher.schemas`, `Tool.invoke` (tests rewritten), `Profile.describe`,
`bench.gpu_memory`, `service.ACTIONS`, `tui.ChatView.read`, launcher `App.PAGES`/`working`/
`exit_code`, `Services.reused_model_server`, `POWER_ACTIONS`, files' `Target`/`_where`
wrappers, empty `Config` classes (breach, access), unused parameters in several modules.
Duplicated logic merged: the calendar/todo time-of-day check, the weather fetch, the
launcher's chat/stats URLs.

### Docs
README, `docs/*.md` and the YAML comments were compared with the code: wrong claims about
what asks first, the servers allow-list (`any_host`), the 5-per-second cap (the shipped cap is
10; the statistics page now reads the configured value), a link, module names in the guide, a
missing module in the layout list, and how `config/` files are overwritten on update.

### Tests
Long-run test: the restart branch was almost never reached (now asserted); a housekeeping test
that could not fail; assertions that were always true (`ran.get("ok", True)`, a truthy
dataclass, an unreachable `invalid arguments` case); a hard-coded restore instead of
`monkeypatch`; unused leftovers; a Ctrl+C test that depended on the shell's signal settings;
about 10 s of pure waiting per run in local HTTP servers (`serve_forever` polling) and in the
finance tests (a real second between canned requests).

## What users will notice
- **Mail:** a server with a self-signed certificate needs `verify_certificate: false`.
- **Office:** reading a Word or Excel file outside the Starlight folder asks first.
- **Linux windows** (`apps.windows`, minimize/maximize): an error when `wmctrl` fails, where
  before an empty list or silent success.
- **`backup.verify`:** `ok: false` with details for a damaged archive.
- **`system.close_app`:** matches the exact program name first ("chrome" no longer takes
  `chromedriver` with it); partial names still work as a fallback; Starlight's own process is
  never closed.
- **Event IDs of imported calendar events** change (an 8-character hash for foreign UIDs);
  Starlight's own events keep theirs.
- **`starlight ask`** exits with 1 when the model failed.

## Left alone on purpose
- **Shared helpers across areas** (a single `size_text`/`free_name` in `fsutil`; one module
  scaffold for `skills promote` and `cli._new_module`; the model helper copies in mail, docs
  and code). They need coordinated changes in core; each copy works.
- **Test helper duplication:** the same fake user and the `call`/`fails`/`instance` helpers
  are copied into many test files, and tests reach module code in three different ways. A
  shared `tests/helpers.py` is worth doing as its own change.
- **Launcher:** merging `start()` with `Services.start()` (changes text-mode behaviour);
  `winget` stays outside `run_checked`.
- **Smaller:** `skills promote` does not check the module name pattern; the `nvidia-smi` parse
  uses only the first GPU; `finance` calls `response.json()` twice; `sync_state` retries every
  failed module on every request (it no longer piles up subscriptions); the stream filter is
  quadratic in the length of one answer (answers are capped).
- **Config fields and tool names** were never removed or renamed, so existing config files
  keep working.
