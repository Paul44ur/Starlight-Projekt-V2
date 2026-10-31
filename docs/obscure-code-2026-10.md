# Obscure-code sweep, October 2026

A sweep for code that is hard to read or that says something other than what it does:
unexplained numbers, names that mislead, docstrings that are wrong, two places that must
agree without saying so, control flow that hides an action. Unlike the cleanup sweep
([cleanup-2026-10.md](cleanup-2026-10.md)), nothing was removed for being unused; the goal
was that a reader understands the code at first sight. Real bugs found on the way were fixed
too, each with a test.

## How

1. **Review.** Five read-only reviews, one per area: `starlight/core/`; the rest of
   `starlight/`; modules `access` to `desktop`; modules `docs` to `whatsapp`; the launcher,
   the scripts and the workflows. Each review ranked about 25 findings and listed the real
   bugs it saw on the way: about 125 findings and 25 bugs in all. Every finding names the
   lines, why they are obscure, a clearer version, and how sure the reviewer was that the
   clearer version behaves the same.
2. **A first attempt was thrown away.** It applied everything in one go without checking
   (commit `a76ab8a`) and was reverted (`4e8a8b4`). Nothing of it was kept unread.
3. **Apply, file by file.** Each change was read again against today's code (which had moved
   on by several batches of features) and merged by hand. Behaviour changes got a test, and
   the tests were run against the old code too, where they fail.
4. **Check.** Full test suite, `ruff`, `mypy` (Linux, as Windows, modules), `mypy --strict`
   for the launcher on both platforms.

The work landed in two commits: part 1 (`0b11c52`, the core, the viewer and most modules)
and part 2 (the rest of `starlight/`, the remaining modules, the launcher window, the
scripts and the workflows).

## Real bugs fixed

### Part 1
- **Context:** when the conversation was shortened, the request's own context (date and
  time, recalled memories) could be folded into the summary of old turns. It now stays at the
  top, and only real turns are summarized.
- **Schedules:** "weekly at 9:00" ran every day. It now asks which day.
- **Logs:** camelCase keys (`accessToken`, `clientSecret`) and a key called `secrets` were not
  masked.
- **HTTP:** the per-host list dropped the first host ever seen, not the one used longest ago.
- **lan, recon:** a router's "destination unreachable" answer counted as the host being up.
- **files:** UTF-16 text (what PowerShell 5 writes) was read as binary.
- **login:** a `login.yaml` with a mistake could be written over. It is refused instead.
- **Launcher:** the free-space check counted downloads that would not happen (llama.cpp when
  only the model was missing) and missed the voice files.
- **system:** a damaged `pending_shutdown.json` stopped the module from loading.
- **Config:** "did you mean" did not work for keys inside `roles.<name>`.

### Part 2
- **`starlight ask`:** a confirmation answered "j" or "ja" counted as no, though the chat
  and the launcher take it as yes. One `console.YES` is now used everywhere.
- **`/talk 999`** recorded for 999 seconds. It is capped at two minutes.
- **Pictures** were always sent to the model as PNG. JPEG, GIF and WebP now get their own
  type.
- **Downloads** of a conversation named "Straße" were called "Strae.md". "ß" becomes "ss".
- **`starlight doctor`** did not notice a password store that cannot store anything: it looked
  for "fail" in the class name, which is "Keyring" in every backend. It now reads the module
  name (`keyring.backends.fail` / `.null`).
- **Read-only commands:** `top -b` counted as read-only, though batch mode alone runs until
  stopped. It now needs a number of rounds (`-n 1`, `-bn1`).
- **dev:** only `127.0.0.1` counted as this PC. Any loopback address (`127.0.0.53`, `::1`)
  does now.
- **notes:** a folder named like a note (`Ideas.md/`) made `notes.note` and `notes.append`
  call each other until Python gave up. It is now refused with a clear message.
- **Launcher window:** the kill-switch and restart buttons read `self.services` again in their
  thread. "Save and restart" may clear it meanwhile, and the error died silently. They now
  keep what they had when clicked.
- **`scripts/install.ps1`:** a failing `uv sync` or `playwright install` still ended with
  "Done.". Windows PowerShell 5.1 does not stop on a failing program, so each one is checked.
  A failed uv install is noticed by looking for uv, because winget also reports a failure
  when uv was there already.
- **`scripts/e2e_chat.py`:** it reported "the hello.greet tool ran" when there was no audit log
  to look in. It now says it could not be checked.

## Made clearer (a selection)

- **Named constants** for numbers that meant something: the picture and output checks
  (`SCAN_STRIDE`, `LOOP_CHECK_EVERY`), research depth (`DEEP_QUERIES`, `HITS_PER_SEARCH`,
  `SPARE_PAGES`, `MIN_PAGE_CHARS`), the memory limits (`MAX_CHARS` per profile field, shared by
  the profile and the memory service), the chat page's WAV header and input heights, the
  launcher's download sizes (`FIRST_RUN_SIZE`, `LLAMA_SIZE`, `VOICE_SIZE`, `SPARE_SPACE`, now
  also used by the window instead of its own copies), the window's `MAX_LOG_LINES` and the
  Windows DPI setting.
- **Names that said too little:** `_tidy_candidates` → `_is_ordinary_memory`,
  `TASKS` → `SUBCOMMAND_FOR_KIND`, `elsewhere(m)` → `answerIsElsewhere()`, `MODEL` →
  `MODEL_HEALTH`; `link_name()`, `unit_name()` and `SHUTDOWN_REQUEST` for the names and the
  file text `starlight service` writes, instead of strings repeated in two files.
- **Couplings now stated on both sides:** the kernel's "Run <tool> with ..." question that
  automations and approvals parse; the audit log fields `e2e_chat.py` reads; the core's
  "is not available" text the launcher workflow looks for; the `files` of `code.write_code`
  that skills keep; `model_url` winning over `model` in `launcher.json`.
- **Comments where the code relies on something unsaid:** why the window's thread events are
  routed by name, and which attributes threads share without a lock; why the launcher module
  is passed to the window instead of imported; PowerShell's code page and `$input`; the
  workflow's size thresholds, `--icon` path and exit-code reset; `install.sh`'s `|| true`.
- **Docstrings that were wrong:** the window's "one worker thread" (there are nine kinds),
  office headings, and more in part 1.

## Not taken over, and why

- **The launcher window and the chat page.** Both were rebuilt after the review. The first
  attempt's edits to them conflicted in 29 and 9 places. They were dropped and each finding
  checked again against the new code; the ones that still applied are in part 2.
- **Renames that tests or other files use:** `on_setup_go` / `on_setup_cancel` (called by the
  LM Studio tests), `APPROX_GB` (checked against the presets by a test; it got a comment
  instead), and the window's short attribute names (`self.L`, `self.c`, `self.f`), which
  would touch hundreds of lines for no change in behaviour.
- **`GuiUi.ask`'s question box** was removed rather than documented: nothing could reach it.
  The setup page asks everything, so launcher questions get their default (as a test checks).
- **Findings whose clearer version was not identical,** where the difference mattered
  (restructuring the launcher's `main()` would lose the pause after an unexpected error).
- **Two type errors in `scripts/e2e_chat.py`** that were there before (the `websockets` origin
  type, a JSON value returned as is). Scripts are not in the project's `mypy` run.
