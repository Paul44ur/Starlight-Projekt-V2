# Plan: the notes app built into Starlight (v3, checked)

A memo for whoever continues this work, most likely a future Claude session without the
conversation that produced it. It holds the decisions, what was verified, how we work, and
the plan itself. Keep the **Status** section current as phases land.

## Status

| Milestone | Phases | State |
|---|---|---|
| A: Safety net | P1 | **next** (not started) |
| B: The app | P2 | open |
| C: Smarter notes | P3, P4 | open |
| D: Everyday use | P5, P6 | open |
| E: Finding | P7, P8 | open |
| F: Overview | P9, P10 | open |
| G: Extras | P11 | open, each item optional |

Last updated: 2026-11-01 (plan agreed, nothing implemented yet).

## For my future self

**Where this came from.** The user (Paul, German, Windows 11, non-technical, uses the web
chat and Starlight.exe) asked which Obsidian features Starlight could "steal". Obsidian
itself will **not** be used: everything ships inside Starlight. The plan went through three
rounds; this is the final, checked version.

**The user's decisions (do not reopen them):**
1. Editor: a **shipped editor library**, CodeMirror 6 (built once, version-pinned, in the repo).
2. Tasks: **plain words** (`due:`, `priority:`, `repeat:`, `start:`, `done:`), not Obsidian's emojis.
3. Phone: **ignore it for now**. All notes interfaces are PC only (like settings), no phone layout.
4. Graph view and board view: **build both**.

**Verified on 2026-11-01** (re-check anything that matters before relying on it):
- npm is reachable from the cloud environment. A trial bundle (`esbuild --bundle --minify
  --format=esm`) of `@codemirror/view` 6.43, `state`, `commands`, `lang-markdown`, `language`,
  `autocomplete`, `search` and `@lezer/highlight` came to **569 KB (194 KB gzipped)**.
  `d3-force` 3.0.0 (ISC licence) bundles to **14 KB**. esbuild 0.28.
- Python's SQLite here has **FTS5** (SQLite 3.53.1). Not yet checked on Windows Python: keep a
  fallback (plain scan) and test it.
- Today's note writes are **not atomic and keep no versions**: `modules/notes/module.py`
  (`write_text` in `note`, `open("a")` in `append`), `modules/journal/module.py` (`open("a")`,
  weekly review `write_text`), `modules/recipes/module.py` (`write_text`). Memory uses
  `write_atomic` (`starlight/memory/store.py`) but keeps no versions.
- `starlight/web/static/markdown.js` is Starlight's own renderer: safe (only `textContent`,
  links only http(s)/mailto), already does headings, lists, tables, code highlighting and
  formulas. Extend it (`render.js`), do not replace it.
- The automations' condition checker (`matches()` in `modules/automations/module.py`) handles
  **one** condition (`field op value`), no and/or.
- The injection guard marks a request as untrusted **only by tool name**
  (`starlight/core/dispatcher.py`, `policy.is_untrusted_source(name)` after a tool result).
- The knowledge base (`modules/docs`) already indexes the Notes folder: table `parts (path,
  page, text, vector)` in `data/modules/docs/index.sqlite`.
- Memory files already have YAML properties (`topic`, `source`, `date`, `confidence`, `tags`).
- The to-do module keeps tasks in `data/modules/todo/todo.json`; priorities are low, normal and high.
- Web pages share `style.css` (colour tokens for dark and light) and `common.js` (`el`, `api`,
  `toast`); German texts come from `i18n.js` (exact-text map plus patterns; texts that are
  already German are marked `translate="no"`).

**How we work in this repo (from the whole project so far):**
- Branch `Starlight-V2-LM-Studio`; commit and push after each phase or milestone. No pull
  request unless the user asks. GitHub Actions minutes are used up: don't trigger CI runs.
- Before pushing: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`,
  `uv run mypy --platform win32`, `uv run mypy modules` (both platforms), `uv run mypy
  --strict launcher/starlight_launcher.py launcher/launcher_gui.py` (both platforms), and the full
  suite: `STARLIGHT_CHROMIUM=/opt/pw-browsers/chromium-1194/chrome-linux/chrome timeout 1500
  xvfb-run -a -s "-screen 0 1366x768x24" uv run pytest -p no:cacheprovider -q`.
- Every behaviour change gets a test, and new tests are run against the code before the change
  (a git worktree of the previous commit) to prove they fail on the old behaviour.
- Pages: German translations for every new text, accessibility (labels), light and dark, and a
  screenshot review (Playwright, see the Safety card test in `tests/test_safety_limits.py`).
- Safety first: anything that changes the user's files keeps an earlier version; changes to
  several items ask first; outside content counts as untrusted; settings change only at the PC.
- Write docs as plain, short sentences (README section, `docs/architecture.md`).

## 1. Goal and ground rules
- **Goal:** a notes app inside Starlight (a Notes page in the web chat, on the PC) with links,
  tags, templates, tasks, search, history, a graph and boards. The assistant works with the
  same structure (links, tags, properties, tasks feed its answers and actions).
- **Plain Markdown** in `Documents\Starlight` (Notes, Journal, Recipes, Clippings, Inbox),
  readable in any editor.
- **Shipped code allowed** when built locally, version-pinned, licensed and checked by a test
  (CodeMirror, Lezer, d3-force). Nothing is loaded from the internet at runtime.
- **Safety:** a version before every change; changes to several notes ask first; outside
  content counts as untrusted; one-click undo in the chat.
- **One shared base** (`starlight/notebook/`) for notes, journal, recipes, memory, to-do, page.
- **Each phase ships on its own:** tools and page together, tests (also against the old code),
  screenshots (light and dark, German), documentation, all checks, commit and push.

## 2. Syntax

| What | Syntax |
|---|---|
| Links | `[[Note]]`, `[[Note\|text]]`, `[[Note#Heading]]`, `[[Note#^id]]` |
| Embeds and pictures | `![[Note]]`, `![[photo.png]]` |
| Tags | `#tag`, `#work/projects`, `tags:` in properties |
| Properties | YAML at the top (`--- … ---`): `aliases`, `tags`, `source`, your own keys |
| Tasks | `- [ ] Call Tom due: 2026-11-05 priority: high repeat: every month start: …`; done: `- [x] … done: 2026-11-04`. German keys are read too (`fällig:`, `priorität: hoch/normal/niedrig`, `wiederholen:`, `erledigt:`). Starlight writes English keys. Typed natural dates (`due: tomorrow`, `fällig: Freitag`) become ISO dates on save. |
| Templates | `{{title}}`, `{{date}}`, `{{time}}`, `{{date:YYYY-MM-DD}}`, plus `{{events}}`, `{{todo}}`, `{{weather}}`, `{{yesterday}}` |
| Callouts | `> [!note] Title` (note, tip, warning, quote, todo) |
| Queries | a ` ```query ` block with `where:`, `sort:`, `fields:` lines, e.g. `where: status is open and tag is work` |
| Boards | `.canvas` files in the open JSON Canvas format (jsoncanvas.org, MIT) |
| Flashcards | `Question::Answer` |

## 3. Architecture: `starlight/notebook/`
(Not "vault": `starlight/core/vault.py` is already the password store.)

| Part | What it does |
|---|---|
| `markdown.py` | Pure functions: properties, headings/sections, links, tags, tasks, query blocks; ignores code blocks and URLs. Edits only the lines concerned (keeps comments and key order). Replaces memory's own parser (`FRONT` in `starlight/memory/store.py`). |
| `index.py` | `data/notebook/index.sqlite`: tables files, links, tags, properties, tasks, plus an FTS5 table. Re-reads only changed files (stat walk at most every 5 s, immediately after Starlight's own writes; no file-watcher dependency). Caps the file count (20,000). Fallback search without FTS5. |
| `links.py` | Resolves a link: file name or alias, case-insensitive, same folder first, then shortest unique path. Backlinks from the index. |
| `history.py` | Gzip copies in `data/history/` with rows in the index: list, compare (difflib), restore, trash. Keeps 20 versions or 90 days per file and 200 MB in all. While typing on the page: at most one version per 5 minutes, plus one on leaving the note. |
| `write.py` | `write_note(path, text, reason, expected_stamp)`: version first, refuse if the file changed since it was read, atomic write, index update. Everything writes through it. |
| `query.py` | One safe condition language (and, or, not, comparisons, `has`, dates, numbers), never evaluated as code, translated to SQL on the index. Automations can switch to it later (stay backward compatible with `matches()`). |
| `templates.py`, `boards.py` | Templates; reading and changing `.canvas` boards. |
| `static/render.js` | Extends `markdown.js`: links, embeds, pictures, callouts, task boxes, tags, query tables, hover previews. Used by chat answers and the Notes page alike. |
| `static/vendor/` | `editor.js` (CodeMirror bundle), `graph.js` (d3-force), licence texts. Built by `tools/editor/` (package.json with pinned versions, lockfile, one build command); a test checks the shipped files' hashes against the build. |

## 4. Look and feel
- Same style as the other pages: `style.css` colours (dark and light), the same header and buttons.
- Layout:

```
┌ Header: Chat · Notes · Memory · Activity · Automations · Settings ───────────┐
│ ┌ Sidebar ─────┐ ┌ Note (reading column, about 760 px) ┐ ┌ Info panel ────┐  │
│ │ Search        │ │ Title                               │ │ Outline        │  │
│ │ Today · Inbox │ │ Properties as chips                 │ │ Backlinks      │  │
│ │ Tasks · Graph │ │ Text: easy-to-read type,            │ │ Properties     │  │
│ │ Pinned        │ │ callouts, task boxes, pictures      │ │ Related notes  │  │
│ │ Folders, Tags │ │ [Edit] [Ask Starlight] [History]    │ │ (folds away)   │  │
│ └──────────────┘ └─────────────────────────────────────┘ └────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────┘
```

- **Reading view first**, Edit on demand. In editing, CodeMirror shows *styled* Markdown
  (larger headings, bold shown bold, links coloured, markers dimmed, task lines with boxes).
- Callouts with a coloured edge and icon; tags as pills; properties as chips; due dates as
  chips (red overdue, amber today); backlinks as cards with the line that links; hover previews.
- No Markdown knowledge needed: toolbar, a `/` menu (heading, task, table, link, picture,
  template, date), pictures by drag and drop or paste, saving on its own with a "Saved" sign.
- Keyboard: Ctrl+K quick switcher, Ctrl+N new note, Ctrl+E edit/read, Ctrl+Enter tick a task.
- First visit: a "Willkommen" note with examples (itself an ordinary note).
- Screenshot review at every phase: light and dark, German and English.
- "Ask Starlight" on a note: summarise, find tasks, suggest links and tags, translate.

## 5. Phases

**P1 · History and the safe writing path · M**
- You see: "undo the last change to Shopping", versions of a note, notes back from the trash.
  After each note change by the assistant, the chat shows a chip **Changed: Shopping · Open · Undo**.
- How: `history.py`, `write.py`; notes, journal, recipes, `files.save` (text in the Starlight
  folder) and memory switched over. Tools `files.versions`, `files.restore`, `memory.versions`,
  `memory.undo`. New safety limits (Settings → Safety limits, `starlight/core/safety.py`):
  "Keep earlier versions" and how many.
- Done when: no write path skips history; trimming by count, age and size; conflicts refused;
  two changes in the same second both kept; the chip and its Undo work (browser test).

**P2 · The Notes page · L**
- You see: the layout from section 4; list, quick switcher, pinned notes, reading view;
  CodeMirror editor with styled Markdown, toolbar and `/` menu; saving on its own; conflict
  dialog for two tabs; new, rename, delete (trash); history panel (differences, restore);
  welcome note; pictures (in `Notes/Attachments/`, shown inline); `[[Note]]` in chat answers opens the page.
- How: `/api/notes/*`, PC only (`local_only`). Pictures: allowed image types only, size cap,
  served with nosniff (SVG only as a download). The editor build in `tools/editor/` + hash test.
- Done when: API and browser tests for every action and the conflict dialog; screenshots.

**P3 · Structure: links, embeds, backlinks, tags, properties · M**
- You see: `[[` and `#` suggest names; clickable links (a missing note is created on click);
  embeds; backlinks panel; properties panel (edit fields directly); tags sidebar. In the chat:
  "what links to X?", "show #work notes", "set status of X to done". Renames fix every link.
- How: `links.py`, index tables; tools `notes.links`, `backlinks`, `rename` (asks first when
  other notes change), `tag`, `untag`, `by_tag`, `tags`, `set_property`, `properties`. Memory
  recall follows links (`starlight/memory/service.py`): one step, at most 5, size budget.
- Done when: syntax samples, resolution rules, renames, embed loops, context cap, panels tested.

**P4 · Templates, daily notes, calendar strip · S–M**
- You see: "New from template" (Meeting, Daily, Project; German and English); a Today button
  for the day's journal note; a calendar strip with days that have entries; monthly and yearly
  reviews; "start a meeting note for the Müller call" in the chat.
- How: `templates.py`; tools `notes.from_template`, `templates`, `journal.month`,
  `journal.year`; automation templates (monthly review, weekly plan).
- Done when: variables, date formats, missing modules, the daily template are tested.

**P5 · Capture: clipper, highlights, inbox, voice memo · S–M**
- You see: "save this page as a note" (summary, `source:` link, in `Clippings/`); an Inbox view
  and "sort my inbox" (proposes, asks before moving); a 🎤 voice-note button on the page (text
  only); highlights as quote callouts.
- How: tools `notes.clip`, `highlight`, `inbox`, `sort_inbox`, `voice.memo`. Guard:
  `notes.clip` counts as outside content, and so does reading any note with a `source:`
  property (dispatcher: also taint when a tool result says `untrusted`).
- Done when: mocked web answers; the guard after a clip and after re-reading it; inbox sorting
  asks first; fake speech-to-text.

**P6 · Tasks inside notes · M**
- You see: tickable task boxes with date chips; a Tasks view (overdue, today, upcoming, no
  date); "what's due", reminders and the briefing include note tasks; "done: call Tom" ticks it.
- How: plain-word parser, natural dates turned into ISO on save, the index's task table; the
  to-do module includes note tasks (its own stay in `todo.json`); task ID from note + text;
  conflicts refused; a version before ticking.
- Done when: format variants, German keys, write-back, conflicts, reminders tested.

**P7 · Search and finding · S–M**
- You see: `tag:#work`, `path:Clippings`, `"exact phrase"`, `-word` with highlighted hits; a
  related-notes panel; "mentioned without a link" with one-click linking; text in pictures and
  scanned PDFs findable (when Tesseract is installed).
- How: FTS5 plus columns; a new `docs.similar(path)` tool on the existing vectors, falling back
  to shared tags and links; OCR text in the knowledge base (switchable, with limits).
- Done when: parser, related notes with and without the embedder, OCR with a fake backend tested.

**P8 · Queries · M**
- You see: ` ```query ` blocks become live tables (click a column to sort); "make me a table of
  my open work notes" makes the assistant write the block.
- How: `query.py` translated to SQL on the index, with a time limit.
- Done when: the language, sorting, missing properties, large folders tested.

**P9 · Graph view · M–L**
- You see: a small graph around each note in the info panel; the whole graph (zoom, drag,
  filter by tag or folder, colour by folder, size by links, labels when zoomed in, click to
  open; memories optionally shown).
- How: `/api/notes/graph` from the index; d3-force; SVG up to about 300 points, canvas beyond.
- Done when: graph data (filters, caps), rendering and clicks in the browser, screenshots.

**P10 · Board view · M–L**
- You see: boards with text cards, note cards (showing the note), pictures, links, groups and
  arrows; drag, resize, zoom, snap grid, saving on its own; "what's on my project board?",
  "add the article to the board".
- How: `.canvas` (JSON Canvas), `boards.py`, own rendering (HTML cards + SVG arrows, text only),
  versions like every file, tools `boards.read`, `boards.add_card`.
- Done when: format read/write, versions, browser tests for dragging and connecting, screenshots.

**P11 · Extras, each optional**
Export a note as HTML or PDF (from the existing conversation export, `export_html` in
`starlight/web/app.py`); Kanban for tasks; spaced repetition (`Question::Answer`); live preview
that hides Markdown markers; Mermaid diagrams (library about 2–3 MB: only if wanted); deep links
(`starlight://note/…`).

## 6. Order
A (P1) → B (P2) → C (P3, P4) → D (P5, P6) → E (P7, P8) → F (P9, P10) → G (P11). The order
avoids rework: base and writing path first, then the renderer, then the page frame, then the
features that slot into it.

## 7. Across all phases
- **Permissions:** single-note changes by the assistant run on their own (history and undo
  protect them); changes to several notes ask first; the user's own edits on the page never ask;
  outside content is untrusted; all notes interfaces are PC only.
- **Performance:** index re-reads only changed files; search and queries in SQLite; editor and
  graph load only on the Notes page; caps on files, note size (1 MB, as now), pictures and graph
  points; time-limit tests with large folders.
- **Quality:** a round-trip test (tools change only what they mean to), German texts,
  accessibility, screenshots at every phase.
- **Licences:** CodeMirror and Lezer (MIT), d3-force (ISC): licence texts in `static/vendor/`
  and a "third-party code" note in the README.

## 8. Risks

| Risk | Handling |
|---|---|
| Two tabs or Notepad change a note at once | Version stamp on every save; conflict dialog |
| The assistant changes the wrong things | Versions, the Undo chip, ask-first for several notes, the kill switch |
| Hidden instructions in clipped pages | Clipping and re-reading count as untrusted; the guard applies |
| The editor library ages | Pinned versions, one build command, hash test |
| Windows Python without FTS5 | Fallback search; check on the Windows build |
| A large graph gets slow | Caps, canvas beyond 300 points, filters |
| History and index grow | Caps by count, age and size; cleanup |
