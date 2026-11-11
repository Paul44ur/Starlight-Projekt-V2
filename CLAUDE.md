# Starlight V2: notes for Claude sessions

A local AI assistant: Python core (`starlight/`), modules (`modules/`), a FastAPI web chat
(`starlight/web/`), and a standard-library Windows launcher (`launcher/`). The user is German,
on Windows 11; the pages have German translations (`starlight/web/static/i18n.js`).

## Plans
No plan is open. The two earlier plans are finished (every block done) and are kept as a
reference for why things are the way they are:
- `docs/plan-notes-app.md`: the notes app (notes, journal, memory, to-dos, the Notes page):
  the user's decisions and what was verified. Read it before changing that area.
- `docs/plan-ui.md`: the look of the pages and the launcher window.

New work goes on `Starlight_Fable` (below). For a larger piece, agree a plan with the user
first and keep it in `docs/plan-<topic>.md` with a Status table, as these two do.

## How we work
- Branch `Starlight_Fable` (started 2026-11-10 from `main`; `main` and
  `Starlight-V2-LM-Studio` stay as they are until the user says to merge); commit and push
  when a piece of work is done; no pull request unless asked. GitHub Actions minutes are used
  up: don't trigger CI runs.
- A fresh container: `uv python install 3.12 && uv sync --extra all --locked --python 3.12
  --python-preference only-managed` (the system's Python has no tkinter, and the launcher
  window's tests are skipped without it).
- Checks before pushing: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`
  and `uv run mypy modules` (each also with `--platform win32`), `uv run mypy --strict
  launcher/starlight_launcher.py launcher/launcher_gui.py launcher/launcher_tray.py` (both
  platforms), then the full suite:
  `STARLIGHT_CHROMIUM=/opt/pw-browsers/chromium timeout 1500 xvfb-run -a
  -s "-screen 0 1366x768x24" uv run pytest -p no:cacheprovider -q -n 4`. A run without `-n` is
  the same tests one after another (slower, 12 to 18 minutes); the files that must not
  overlap (the tkinter windows) are grouped in `tests/conftest.py`.
- Every behaviour change gets a test; run new tests against the previous commit (a git
  worktree) to show they fail on the old behaviour.
- In a worktree, run the project's `.venv` Python directly with the worktree on `PYTHONPATH`,
  and without `UV_PROJECT_ENVIRONMENT` (with it, the launcher's setup test runs a real
  `uv sync` into the project's `.venv` and points its Starlight install at a temporary
  folder): `git worktree add --detach <dir> <commit> && cd <dir> && env -u
  UV_PROJECT_ENVIRONMENT -u UV_CACHE_DIR PYTHONPATH=$PWD STARLIGHT_CHROMIUM=... xvfb-run -a -s
  "-screen 0 1366x768x24" <repo>/.venv/bin/python -m pytest -p no:cacheprovider -q -n 4`. If
  `python -m starlight` stops working, `uv pip install --no-deps -e . --offline` repairs it.
- New page texts need German entries; check new pages with a screenshot, light and dark.
- Safety first: keep earlier versions of the user's files before changing them, ask before
  changes to several items, treat outside content as untrusted, settings change only at the PC.
- Docs (README, `docs/architecture.md`, `docs/*.md`) in plain, short sentences.
- Work handed to a smaller model: point it at `docs/guide-for-helpers.md` (the look, the
  shared helpers, German, tests, a checklist for a UI change, how to report). The lead
  writes the brief, reviews the diff and merges.
